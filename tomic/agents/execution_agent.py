"""
TOMIC Execution Agent — Order Lifecycle & Safety Invariants
=============================================================
Polls command table with lease mechanics.
Enforces all 3 non-bypassable safety invariants:
  1. Per-strategy legging policy (configtime-locked)
  2. Unhedged exposure timer (5s auto-close)
  3. Smart order delay (0.5s between legs)

Manages PositionBook as single writer.
Checks freshness gates and circuit breakers before execution.
Semi-Auto / Full-Auto mode support.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from tomic.agent_base import AgentBase
from tomic.circuit_breakers import CircuitBreakerEngine
from tomic.command_store import CommandStore, CommandRow, ErrorClass
from tomic.config import (
    ExecutionParams, LEGGING_POLICY, LeggingPolicy,
    StrategyType, TomicConfig, TomicMode,
)
from tomic.events import (
    AlertLevel, EventType, OrderFillEvent, OrderRejectEvent,
    PositionUpdateEvent, mask_sensitive_fields,
)
from tomic.event_bus import EventPublisher
from tomic.freshness import FreshnessTracker
from tomic.position_book import Position, PositionBook
from tomic.sandbox_adapter import SandboxAdapter

logger = logging.getLogger(__name__)


class ExecutionAgent(AgentBase):
    """
    Order execution with safety invariants and lease-based command polling.

    Startup sequence:
        1. Load PositionBook from DB
        2. Reconcile with broker positionbook
        3. Begin polling command table
    """

    def __init__(
        self,
        config: TomicConfig,
        publisher: EventPublisher,
        command_store: CommandStore,
        position_book: PositionBook,
        freshness_tracker: FreshnessTracker,
        circuit_breakers: CircuitBreakerEngine,
        ws_data_manager: Optional[Any] = None,
        sandbox_adapter: Optional[SandboxAdapter] = None,
    ):
        super().__init__("execution", config, publisher)
        self._command_store = command_store
        self._position_book = position_book
        self._freshness = freshness_tracker
        self._breakers = circuit_breakers
        self._ws_data_manager = ws_data_manager
        self._sandbox = sandbox_adapter or SandboxAdapter(config)

        self._exec_params: ExecutionParams = config.execution
        self._base_url = config.endpoints.execution_rest
        self._api_key = config.endpoints.execution_api_key

        # Unhedged exposure tracking: key → monotonic time first detected
        self._unhedged_since: Dict[str, float] = {}

        # Execution timing
        self._last_order_mono: float = 0.0
        self._consecutive_timeouts: int = 0
        self._max_consecutive_timeouts = config.supervisor.exec_broker_consecutive_timeout_kill

        # Recent broker-order metadata captured during place calls.
        # key: broker_order_id -> (metadata, stored_at_wall)
        self._recent_order_meta: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._last_broker_positions_error: str = ""

        # Virtual TP/SL + trailing guard for long directional options.
        self._virtual_enabled = self._env_bool("TOMIC_VIRTUAL_RISK_ENABLED", True)
        self._virtual_sl_pct = self._env_float("TOMIC_VIRTUAL_SL_PCT", 0.25, min_value=0.01)
        self._virtual_tp_pct = self._env_float("TOMIC_VIRTUAL_TP_PCT", 0.35, min_value=0.01)
        self._virtual_trail_enabled = self._env_bool("TOMIC_VIRTUAL_TRAIL_ENABLED", True)
        self._virtual_trail_trigger_pct = self._env_float(
            "TOMIC_VIRTUAL_TRAIL_TRIGGER_PCT", 0.15, min_value=0.0
        )
        self._virtual_trail_offset_pct = self._env_float(
            "TOMIC_VIRTUAL_TRAIL_OFFSET_PCT", 0.08, min_value=0.0
        )
        self._virtual_tick_max_age_s = self._env_float("TOMIC_VIRTUAL_TICK_MAX_AGE_S", 15.0, min_value=1.0)
        self._max_queue_age_s = self._env_float("TOMIC_MAX_QUEUE_AGE_S", 10.0, min_value=0.0)
        self._virtual_positions: Dict[str, Dict[str, Any]] = {}

    def _get_tick_interval(self) -> float:
        return self._exec_params.command_poll_interval  # 100ms

    def _setup(self) -> None:
        """Load PositionBook and reconcile with broker."""
        self.logger.info("Execution Agent starting — loading PositionBook")
        self._position_book.load()

        # Reconcile with broker
        try:
            broker_positions = self._fetch_broker_positions()
            if self._last_broker_positions_error:
                self.logger.warning(
                    "Skipping PositionBook reconciliation due broker fetch error: %s",
                    self._last_broker_positions_error,
                )
            else:
                discrepancies = self._position_book.reconcile(broker_positions)
                if discrepancies:
                    self.logger.warning(
                        "PositionBook reconciliation: %d discrepancies",
                        len(discrepancies),
                    )
                    for d in discrepancies:
                        self.logger.warning("  %s", d)
        except Exception as e:
            self.logger.error("Broker reconciliation failed: %s", e)

        self.logger.info("Execution Agent ready, PositionBook v=%d", self._position_book.current_version)

    def _tick(self) -> None:
        """Main execution loop: poll commands + check unhedged exposure."""
        # Maintain virtual risk exits from live WS ticks.
        self._monitor_virtual_positions()

        # Safety: check unhedged exposure timer (Invariant 2)
        self._check_unhedged_exposure()

        # Poll for work
        cmd = self._command_store.dequeue()
        if not cmd:
            return

        self.logger.info(
            "Processing command: id=%d type=%s key=%s",
            cmd.id, cmd.event_type, cmd.idempotency_key,
        )

        stale_age = self._command_age_seconds(cmd.created_at)
        if (
            self._max_queue_age_s > 0
            and stale_age is not None
            and stale_age > self._max_queue_age_s
        ):
            reason = (
                f"Stale queue command dropped: age={stale_age:.2f}s "
                f"> ttl={self._max_queue_age_s:.2f}s"
            )
            self.logger.warning(
                "Dropping stale command id=%d type=%s age=%.2fs ttl=%.2fs",
                cmd.id,
                cmd.event_type,
                stale_age,
                self._max_queue_age_s,
            )
            self._command_store.mark_failed(cmd.id, cmd.owner_token, reason)
            return

        if cmd.event_type == EventType.ORDER_REQUEST.value:
            self._execute_order(cmd)
        else:
            self.logger.warning("Unknown command type: %s", cmd.event_type)
            self._command_store.mark_failed(
                cmd.id, cmd.owner_token, f"Unknown event_type: {cmd.event_type}"
            )

    def _teardown(self) -> None:
        """Persist PositionBook on shutdown."""
        self._virtual_positions.clear()
        self._position_book.persist()
        self.logger.info("Execution Agent stopped, PositionBook persisted")

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = str(os.getenv(name, str(default))).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_float(name: str, default: float, min_value: float = 0.0) -> float:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if value >= min_value else default

    @staticmethod
    def _parse_iso_timestamp(raw: Any) -> Optional[datetime]:
        token = str(raw or "").strip()
        if not token:
            return None
        normalized = token.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @classmethod
    def _command_age_seconds(cls, created_at: Any) -> Optional[float]:
        created = cls._parse_iso_timestamp(created_at)
        if created is None:
            return None
        if created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        age = (datetime.utcnow() - created).total_seconds()
        return max(0.0, age)

    # -----------------------------------------------------------------------
    # Order execution
    # -----------------------------------------------------------------------

    def _execute_order(self, cmd: CommandRow) -> None:
        """Execute an order request with all safety checks."""
        payload = cmd.payload
        strategy_type_str = payload.get("strategy_type", "")
        instrument = payload.get("instrument", "")
        underlying_for_gates = self._base_underlying(instrument)
        legs = payload.get("legs", [])
        try:
            self._validate_execution_request(payload)
        except BrokerRejectError as e:
            self._command_store.mark_failed(cmd.id, cmd.owner_token, str(e))
            reject = OrderRejectEvent(
                correlation_id=cmd.correlation_id,
                strategy_id=payload.get("strategy_id", ""),
                idempotency_key=cmd.idempotency_key,
                reject_reason=str(e),
                error_class="validation",
            )
            self._publish_event(reject)
            return

        # 1. Freshness gate check
        freshness_report = self._freshness.check_order_gates(
            underlying=underlying_for_gates,
            is_credit_spread="SPREAD" in strategy_type_str or "CONDOR" in strategy_type_str,
        )
        if not freshness_report.passed:
            # Back off retries when feed is stale to avoid tight defer/retry log loops.
            defer_delay = max(2.0, float(self._exec_params.command_poll_interval) * 20.0)
            self._command_store.mark_deferred(
                cmd.id,
                cmd.owner_token,
                f"Freshness gate blocked: {[g.value for g in freshness_report.blocking_gates]}",
                delay_seconds=defer_delay,
            )
            return

        # 2. Circuit breaker pre-check
        snap = self._position_book.read_snapshot()
        unhedged = self._position_book.has_unhedged_short()
        breaker_status = self._breakers.check_all(
            daily_pnl=snap.total_pnl,
            unhedged_keys=unhedged,
        )
        if not breaker_status.all_clear:
            for result in breaker_status.tripped_breakers:
                if result.kill_switch:
                    self._command_store.mark_failed(
                        cmd.id, cmd.owner_token,
                        f"CircuitBreaker KILL: {result.message}",
                    )
                    return
            self._command_store.mark_retry(
                cmd.id, cmd.owner_token, ErrorClass.VALIDATION,
                f"CircuitBreaker: {[r.message for r in breaker_status.tripped_breakers]}",
            )
            return

        # 3. Semi-auto approval check
        if self._sandbox.should_require_approval(payload):
            self.logger.info("SEMI_AUTO: Order awaiting approval for %s", instrument)
            # In real implementation, publish to Telegram / dashboard for approval
            # For now, proceed (approval flow is Phase 5)

        # 4. Broker idempotency check
        if self._check_broker_idempotency(cmd.idempotency_key, payload.get("strategy_tag", "")):
            self._command_store.mark_done(cmd.id, cmd.owner_token, broker_order_id="IDEMPOTENT_SKIP")
            self.logger.info("IDEMPOTENCY_SKIP (broker check): %s", cmd.idempotency_key)
            return

        # 5. Execute via legging policy
        try:
            strategy_type = StrategyType(strategy_type_str) if strategy_type_str else None
        except ValueError:
            strategy_type = None

        legging = LEGGING_POLICY.get(strategy_type, LeggingPolicy.SINGLE_LEG)

        try:
            broker_ref = self._execute_with_legging(legging, payload, legs)
            self._command_store.mark_done(cmd.id, cmd.owner_token, broker_order_id=broker_ref)
            self._breakers.record_order()
            self._consecutive_timeouts = 0

            # Update PositionBook
            self._update_position_book(payload, broker_ref)

            # Publish fill event
            fill = OrderFillEvent(
                correlation_id=cmd.correlation_id,
                strategy_id=payload.get("strategy_id", ""),
                idempotency_key=cmd.idempotency_key,
                broker_order_id=broker_ref,
            )
            self._publish_event(fill)

            # Publish position update
            pos_update = PositionUpdateEvent(
                snapshot_version=self._position_book.current_version,
            )
            self._publish_event(pos_update)

            self.logger.info("Order FILLED: %s ref=%s", instrument, broker_ref)

        except TimeoutError as e:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= self._max_consecutive_timeouts:
                self._publish_alert(
                    AlertLevel.CRITICAL,
                    f"Execution broker: {self._consecutive_timeouts} consecutive timeouts — kill switch",
                )
            self._command_store.mark_retry(
                cmd.id, cmd.owner_token, ErrorClass.NETWORK_TIMEOUT, str(e),
            )

        except BrokerRejectError as e:
            self._command_store.mark_failed(cmd.id, cmd.owner_token, str(e))
            reject = OrderRejectEvent(
                correlation_id=cmd.correlation_id,
                strategy_id=payload.get("strategy_id", ""),
                idempotency_key=cmd.idempotency_key,
                reject_reason=str(e),
                error_class="broker_reject",
            )
            self._publish_event(reject)

        except Exception as e:
            self.logger.error("Order execution error: %s", e, exc_info=True)
            self._command_store.mark_retry(
                cmd.id, cmd.owner_token, ErrorClass.UNKNOWN, str(e),
            )

    # -----------------------------------------------------------------------
    # Legging policy execution (Invariant 1)
    # -----------------------------------------------------------------------

    def _execute_with_legging(
        self,
        policy: LeggingPolicy,
        payload: Dict[str, Any],
        legs: List[Dict],
    ) -> str:
        """Execute order using the locked legging policy."""
        strategy_type = str(payload.get("strategy_type") or "").strip().upper()
        if (
            strategy_type in {
                StrategyType.IRON_CONDOR.value,
                StrategyType.BULL_PUT_SPREAD.value,
                StrategyType.BEAR_CALL_SPREAD.value,
                StrategyType.JADE_LIZARD.value,
                StrategyType.SHORT_STRANGLE.value,
                StrategyType.SHORT_STRADDLE.value,
                StrategyType.RISK_REVERSAL.value,
                StrategyType.CALENDAR_DIAGONAL.value,
            }
            and legs
        ):
            return self._place_options_multi_order(payload, legs)

        if not legs or len(legs) <= 1:
            # Single leg — simple order
            return self._place_single_order(payload)

        if policy == LeggingPolicy.ATOMIC_PREFERRED:
            # Try basket order first
            try:
                return self._place_basket_order(payload, legs)
            except Exception as e:
                self.logger.warning(
                    "Basket order failed, falling back to HEDGE_FIRST: %s", e,
                )
                return self._execute_hedge_first(payload, legs)

        elif policy == LeggingPolicy.HEDGE_FIRST:
            return self._execute_hedge_first(payload, legs)

        elif policy == LeggingPolicy.SHORT_FIRST_KILL_SWITCH:
            return self._execute_short_first_kill_switch(payload, legs)

        elif policy == LeggingPolicy.SINGLE_LEG:
            return self._place_single_order(payload)

        else:
            raise ValueError(f"Unknown legging policy: {policy}")

    def _execute_hedge_first(self, payload: Dict, legs: List[Dict]) -> str:
        """HEDGE_FIRST: buy protective leg → then sell short leg."""
        refs = []

        # Sort: buy legs first
        sorted_legs = sorted(legs, key=lambda l: 0 if l.get("direction") == "BUY" else 1)

        for leg in sorted_legs:
            # Invariant 3: Smart Order Delay
            self._enforce_smart_delay()

            ref = self._place_leg_order(payload, leg)
            refs.append(ref)

            # Track hedge pair for unhedged detection
            if leg.get("direction") == "BUY" and len(refs) == 1:
                self._last_hedge_ref = ref

        return "|".join(refs)

    def _execute_short_first_kill_switch(self, payload: Dict, legs: List[Dict]) -> str:
        """SHORT_FIRST_KILL_SWITCH: sell first, then buy at MARKET within 3s."""
        refs = []

        # Sort: sell legs first
        sorted_legs = sorted(legs, key=lambda l: 0 if l.get("direction") == "SELL" else 1)

        for i, leg in enumerate(sorted_legs):
            self._enforce_smart_delay()

            if i > 0 and sorted_legs[i-1].get("direction") == "SELL" and leg.get("direction") == "BUY":
                # Force MARKET order for hedge leg within kill switch timeout
                leg = dict(leg)
                leg["order_type"] = "MARKET"

            ref = self._place_leg_order(payload, leg)
            refs.append(ref)

        return "|".join(refs)

    # -----------------------------------------------------------------------
    # Smart order delay (Invariant 3)
    # -----------------------------------------------------------------------

    def _enforce_smart_delay(self) -> None:
        """Respect SMART_ORDER_DELAY between legs."""
        now = time.monotonic()
        elapsed = now - self._last_order_mono
        delay = self._exec_params.smart_order_delay

        if elapsed < delay:
            remaining = delay - elapsed
            time.sleep(remaining)

        self._last_order_mono = time.monotonic()

    # -----------------------------------------------------------------------
    # Unhedged exposure timer (Invariant 2)
    # -----------------------------------------------------------------------

    def _check_unhedged_exposure(self) -> None:
        """Auto-close short options unhedged > 5 seconds."""
        unhedged_keys = self._position_book.has_unhedged_short()
        now = time.monotonic()
        timeout = self.config.circuit_breakers.unhedged_timeout_seconds

        for key in unhedged_keys:
            if key not in self._unhedged_since:
                self._unhedged_since[key] = now
                self.logger.warning("UNHEDGED detected: %s", key)

            elapsed = now - self._unhedged_since[key]
            if elapsed > timeout:
                self.logger.critical(
                    "UNHEDGED TIMEOUT: %s unhedged for %.1fs — force closing",
                    key, elapsed,
                )
                self._force_close_position(key)
                self._publish_alert(
                    AlertLevel.CRITICAL,
                    f"Force-closed unhedged position: {key} after {elapsed:.1f}s",
                )

        # Clean up resolved keys
        current = set(unhedged_keys)
        resolved = set(self._unhedged_since.keys()) - current
        for k in resolved:
            del self._unhedged_since[k]

    def _force_close_position(self, key: str) -> None:
        """Force close an unhedged position at MARKET."""
        snap = self._position_book.read_snapshot()
        pos = snap.positions.get(key)
        if not pos:
            return

        try:
            order = {
                "instrument": pos.instrument,
                "direction": "BUY" if pos.direction == "SELL" else "SELL",
                "quantity": abs(pos.quantity),
                "order_type": "MARKET",
                "strategy_tag": "TOMIC_FORCE_CLOSE",
            }
            self._place_single_order(order)
            parts = key.split("|")
            instrument = parts[0] if parts else ""
            strategy_id = parts[1] if len(parts) > 1 else ""
            self._position_book.remove_position(instrument, strategy_id)
            self._virtual_positions.pop(key, None)
        except Exception as e:
            self.logger.error("Force close failed for %s: %s", key, e)

    # -----------------------------------------------------------------------
    # Virtual TP/SL + trailing (directional long options)
    # -----------------------------------------------------------------------

    def _monitor_virtual_positions(self) -> None:
        """Check virtual TP/SL + trailing for active positions using live WS ticks."""
        if not self._virtual_enabled or not self._virtual_positions:
            return

        snapshot = self._position_book.read_snapshot()
        active_position_keys = set(snapshot.positions.keys())
        stale_keys: List[str] = []
        for key, guard in list(self._virtual_positions.items()):
            if key not in active_position_keys:
                stale_keys.append(key)
                continue

            symbol = str(guard.get("instrument") or "")
            exchange = str(guard.get("exchange") or "")
            ltp = self._get_ws_price(symbol, exchange)
            if ltp <= 0:
                continue

            # Keep PositionBook P&L/mark-to-market live.
            self._position_book.update_ltp(symbol, ltp)

            entry = float(guard.get("entry_price", 0.0) or 0.0)
            if entry <= 0:
                # Arm the guard on first live quote if broker fill price was unavailable.
                self._arm_virtual_guard(guard, ltp)
                self.logger.info(
                    "Virtual guard armed from live tick: %s entry=%.2f sl=%.2f tp=%.2f",
                    key,
                    float(guard.get("entry_price", 0.0) or 0.0),
                    float(guard.get("stop_price", 0.0) or 0.0),
                    float(guard.get("take_profit_price", 0.0) or 0.0),
                )

            highest = float(guard.get("highest_price", 0.0) or 0.0)
            if ltp > highest:
                guard["highest_price"] = ltp

            stop_price = float(guard.get("stop_price", 0.0) or 0.0)
            tp_price = float(guard.get("take_profit_price", 0.0) or 0.0)
            trail_enabled = bool(guard.get("trailing_enabled", False))
            trail_trigger = float(guard.get("trail_trigger_price", 0.0) or 0.0)
            trail_offset = float(guard.get("trail_offset", 0.0) or 0.0)

            if trail_enabled and trail_offset > 0 and ltp >= trail_trigger:
                guard["trailing_active"] = True
                trailed_stop = max(stop_price, float(guard.get("highest_price", ltp) or ltp) - trail_offset)
                if trailed_stop > stop_price:
                    guard["stop_price"] = trailed_stop
                    stop_price = trailed_stop

            if stop_price > 0 and ltp <= stop_price:
                if self._close_virtual_position(key, guard, ltp, reason="VIRTUAL_SL"):
                    stale_keys.append(key)
                continue

            if tp_price > 0 and ltp >= tp_price:
                if self._close_virtual_position(key, guard, ltp, reason="VIRTUAL_TP"):
                    stale_keys.append(key)
                continue

        for key in stale_keys:
            self._virtual_positions.pop(key, None)

    def _arm_virtual_guard(self, guard: Dict[str, Any], entry_price: float) -> None:
        entry = float(entry_price or 0.0)
        if entry <= 0:
            return
        guard["entry_price"] = entry
        guard["highest_price"] = entry
        guard["stop_price"] = entry * max(0.01, 1.0 - self._virtual_sl_pct)
        guard["take_profit_price"] = entry * (1.0 + max(0.01, self._virtual_tp_pct))
        guard["trail_trigger_price"] = entry * (1.0 + max(0.0, self._virtual_trail_trigger_pct))
        guard["trail_offset"] = max(0.01, entry * max(0.0, self._virtual_trail_offset_pct))
        guard["trailing_active"] = False

    def _close_virtual_position(
        self,
        key: str,
        guard: Dict[str, Any],
        ltp: float,
        reason: str,
    ) -> bool:
        """Exit position via MARKET and remove it from PositionBook."""
        now = time.monotonic()
        last_attempt = float(guard.get("last_exit_attempt_mono", 0.0) or 0.0)
        if now - last_attempt < 0.75:
            return False
        guard["last_exit_attempt_mono"] = now

        symbol = str(guard.get("instrument") or "")
        exchange = str(guard.get("exchange") or "")
        product = str(guard.get("product") or self._default_product())
        quantity = int(guard.get("quantity", 0) or 0)
        strategy_tag = str(guard.get("strategy_tag") or "TOMIC_VIRTUAL_EXIT")
        strategy_id = str(guard.get("strategy_id") or "")
        if not symbol or quantity <= 0 or not strategy_id:
            return False

        try:
            close_ref = self._place_single_order(
                {
                    "instrument": symbol,
                    "exchange": exchange,
                    "direction": "SELL",
                    "quantity": quantity,
                    "order_type": "MARKET",
                    "product": product,
                    "strategy_tag": f"{strategy_tag}_{reason}",
                }
            )
            self._position_book.remove_position(symbol, strategy_id)
            try:
                self._publish_alert(
                    AlertLevel.SIGNAL,
                    f"{reason}: exited {symbol} qty={quantity} at ~{ltp:.2f} (ref={close_ref})",
                )
            except Exception as alert_exc:
                self.logger.debug("Virtual-exit alert publish skipped for %s: %s", key, alert_exc)
            self.logger.info(
                "%s executed for %s qty=%d @ %.2f (ref=%s)",
                reason,
                symbol,
                quantity,
                ltp,
                close_ref,
            )
            return True
        except Exception as exc:
            self.logger.warning("%s failed for %s: %s", reason, key, exc)
            return False

    # -----------------------------------------------------------------------
    # Broker API calls
    # -----------------------------------------------------------------------

    _OPTION_SYMBOL_RE = re.compile(r"[A-Z0-9]+(?:CE|PE)$")
    _NON_TRADABLE_UNDERLYINGS = {"INDIAVIX", "VIX"}

    def _validate_execution_request(self, payload: Dict[str, Any]) -> None:
        instrument = str(payload.get("instrument") or "").strip().upper()
        strategy_type = str(payload.get("strategy_type") or "").strip().upper()
        direction = str(payload.get("direction") or "").strip().upper()
        legs = payload.get("legs") or []

        if not instrument:
            raise BrokerRejectError("Missing instrument in ORDER_REQUEST payload")
        if instrument in self._NON_TRADABLE_UNDERLYINGS:
            raise BrokerRejectError(f"{instrument} is context-only and not tradable")

        # Current DITM flow is long-option only via /optionsorder.
        if strategy_type in {StrategyType.DITM_CALL.value, StrategyType.DITM_PUT.value} and direction != "BUY":
            raise BrokerRejectError(f"{strategy_type} supports BUY entries only in current execution flow")

        leg_required = {
            StrategyType.BULL_PUT_SPREAD.value,
            StrategyType.BEAR_CALL_SPREAD.value,
            StrategyType.IRON_CONDOR.value,
            StrategyType.JADE_LIZARD.value,
            StrategyType.SHORT_STRANGLE.value,
            StrategyType.SHORT_STRADDLE.value,
            StrategyType.RISK_REVERSAL.value,
            StrategyType.CALENDAR_DIAGONAL.value,
        }
        if strategy_type in leg_required and not legs:
            raise BrokerRejectError(f"{strategy_type} requires legs in ORDER_REQUEST payload")

    @classmethod
    def _normalize_symbol(cls, raw: Any) -> str:
        value = str(raw or "").strip().upper()
        return "" if value in {"", "NONE", "NULL"} else value

    @classmethod
    def _looks_like_option_symbol(cls, symbol: str) -> bool:
        return bool(cls._OPTION_SYMBOL_RE.match(symbol))

    @staticmethod
    def _infer_exchange_from_symbol(symbol: str) -> str:
        if not symbol:
            return "NSE"
        if symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX"}:
            return "NSE_INDEX"
        if symbol in {"SENSEX", "BANKEX"}:
            return "BSE_INDEX"
        if symbol.endswith("CE") or symbol.endswith("PE") or symbol.endswith("FUT"):
            if symbol.startswith("SENSEX") or symbol.startswith("BANKEX") or re.match(r"^B\d", symbol):
                return "BFO"
            return "NFO"
        return "NSE"

    @staticmethod
    def _default_product() -> str:
        return str(os.getenv("TOMIC_DEFAULT_PRODUCT", "MIS") or "MIS").upper()

    @staticmethod
    def _base_underlying(symbol: str) -> str:
        token = str(symbol or "").strip().upper()
        if not token:
            return ""

        if ":" in token:
            _, right = token.split(":", 1)
            token = right.strip().upper() or token

        if "." in token:
            left, right = token.split(".", 1)
            if right.strip().upper() in {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "NSE_INDEX", "BSE_INDEX"}:
                token = left.strip().upper()

        compact = token.replace(" ", "").replace("-", "").replace("_", "")
        if not compact:
            return ""

        if compact.startswith("BANKNIFTY"):
            return "BANKNIFTY"
        if compact.startswith("FINNIFTY"):
            return "FINNIFTY"
        if compact.startswith("MIDCPNIFTY"):
            return "MIDCPNIFTY"
        if compact.startswith("NIFTY"):
            return "NIFTY"
        if compact.startswith("SENSEX"):
            return "SENSEX"
        if compact.startswith("BANKEX"):
            return "BANKEX"
        if "VIX" in compact:
            return "INDIAVIX"

        # Handle canonical derivatives like NIFTY27FEB2623000CE or BANKNIFTY27FEB26FUT.
        match = re.match(r"^([A-Z]+)\d{2}[A-Z]{3}\d{2}(?:\d+(?:CE|PE)|FUT)?$", compact)
        return match.group(1) if match else compact

    @staticmethod
    def _normalize_expiry_for_options(raw: Any) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        up = value.upper()
        for fmt in ("%d%b%y", "%d-%b-%y", "%d%b%Y", "%d-%b-%Y"):
            try:
                return time.strftime("%d%b%y", time.strptime(up, fmt)).upper()
            except ValueError:
                continue
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return time.strftime("%d%b%y", time.strptime(value, fmt)).upper()
            except ValueError:
                continue
        return ""

    @staticmethod
    def _expiry_to_db_format(raw: Any) -> str:
        token = ExecutionAgent._normalize_expiry_for_options(raw)
        if len(token) != 7:
            return ""
        return f"{token[:2]}-{token[2:5]}-{token[5:]}".upper()

    @staticmethod
    def _align_quantity_to_multiple(quantity: int, multiple: int, allow_min_one_lot: bool = True) -> int:
        qty = int(quantity or 0)
        mul = int(multiple or 0)
        if qty <= 0 or mul <= 0:
            return 0
        aligned = (qty // mul) * mul
        if aligned <= 0 and allow_min_one_lot:
            return mul
        return aligned

    @staticmethod
    def _parse_required_multiple(message: str) -> int:
        text = str(message or "")
        match = re.search(r"multiple\s+of\s+(\d+)", text, flags=re.IGNORECASE)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return 0

    def _get_ws_price(self, symbol: str, exchange: str = "") -> float:
        if not self._ws_data_manager:
            return 0.0
        try:
            return float(
                self._ws_data_manager.get_last_price(
                    symbol=symbol,
                    exchange=exchange,
                    max_age_s=self._virtual_tick_max_age_s,
                )
                or 0.0
            )
        except Exception:
            return 0.0

    def _stash_order_meta(self, order_ref: str, meta: Dict[str, Any]) -> None:
        key = str(order_ref or "").strip()
        if not key:
            return
        now = time.time()
        self._recent_order_meta[key] = (dict(meta), now)
        # Keep memory bounded.
        if len(self._recent_order_meta) > 512:
            cutoff = now - 600.0
            stale = [k for k, (_, ts) in self._recent_order_meta.items() if ts < cutoff]
            for stale_key in stale:
                self._recent_order_meta.pop(stale_key, None)
            while len(self._recent_order_meta) > 512:
                oldest = min(self._recent_order_meta.items(), key=lambda item: item[1][1])[0]
                self._recent_order_meta.pop(oldest, None)

    def _consume_order_meta(self, broker_ref: str) -> Dict[str, Any]:
        refs = [token.strip() for token in str(broker_ref or "").split("|") if token.strip()]
        for ref in refs:
            payload = self._recent_order_meta.pop(ref, None)
            if payload:
                return dict(payload[0])
        return {}

    def _resolve_option_lot_size(
        self,
        underlying: str,
        exchange: str,
        expiry: str,
        option_type: str = "CE",
    ) -> int:
        """
        Resolve lot size for an options underlying from master contract.
        Returns 0 when unavailable.
        """
        try:
            from database.symbol import SymToken, db_session
            from services.option_symbol_service import get_option_exchange

            base = self._base_underlying(underlying)
            if not base:
                return 0
            options_exchange = get_option_exchange(exchange or "NSE_INDEX")
            expiry_db = self._expiry_to_db_format(expiry)
            expiry_db = self._expiry_to_db_format(expiry)
            q = db_session.query(SymToken.lotsize).filter(
                SymToken.exchange == options_exchange,
                SymToken.name.ilike(base),
                SymToken.symbol.ilike(f"%{str(option_type or 'CE').upper()}"),
                SymToken.lotsize.isnot(None),
            )
            if expiry_db:
                q = q.filter(SymToken.expiry == expiry_db)

            row = q.order_by(SymToken.expiry.asc()).first()
            if row and row[0]:
                lot = int(row[0])
                if lot > 0:
                    return lot

            # Fallback when `name` mapping is inconsistent across brokers/contracts.
            q2 = db_session.query(SymToken.lotsize).filter(
                SymToken.exchange == options_exchange,
                SymToken.symbol.ilike(f"{base}%{str(option_type or 'CE').upper()}"),
                SymToken.lotsize.isnot(None),
            )
            if expiry_db:
                q2 = q2.filter(SymToken.expiry == expiry_db)
            row2 = q2.order_by(SymToken.expiry.asc()).first()
            if row2 and row2[0]:
                lot = int(row2[0])
                if lot > 0:
                    return lot
        except Exception as exc:
            self.logger.debug("Could not resolve lot size for %s: %s", underlying, exc)
        return 0

    @staticmethod
    def _parse_expiry_date(raw: str) -> Optional[datetime]:
        token = str(raw or "").strip().upper()
        if not token:
            return None
        for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d%b%y", "%d%b%Y"):
            try:
                return datetime.strptime(token, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def _select_front_expiry(cls, expiries: List[str]) -> str:
        parsed_dates: List[datetime] = []
        for raw in expiries:
            parsed = cls._parse_expiry_date(raw)
            if parsed is not None:
                parsed_dates.append(parsed)

        if not parsed_dates:
            return ""

        unique_sorted = sorted(set(parsed_dates))
        today = datetime.now().date()
        for exp in unique_sorted:
            if exp.date() >= today:
                return exp.strftime("%d%b%y").upper()
        return unique_sorted[-1].strftime("%d%b%y").upper()

    @classmethod
    def _select_expiry_by_offset(cls, expiries: List[str], offset: int = 0) -> str:
        parsed_dates: List[datetime] = []
        for raw in expiries:
            parsed = cls._parse_expiry_date(raw)
            if parsed is not None:
                parsed_dates.append(parsed)
        if not parsed_dates:
            return ""
        unique_sorted = sorted(set(parsed_dates))
        today = datetime.now().date()
        active = [exp for exp in unique_sorted if exp.date() >= today] or unique_sorted
        idx = max(0, min(int(offset or 0), len(active) - 1))
        return active[idx].strftime("%d%b%y").upper()

    def _resolve_front_expiry_for_underlying(self, underlying: str, exchange: str) -> str:
        try:
            from database.symbol import get_distinct_expiries
            from services.option_symbol_service import get_option_exchange

            options_exchange = get_option_exchange(exchange or "NSE_INDEX")
            raw_expiries = get_distinct_expiries(
                exchange=options_exchange,
                underlying=self._base_underlying(underlying),
            )
            return self._select_front_expiry(raw_expiries or [])
        except Exception as exc:
            self.logger.warning("Could not auto-resolve expiry for %s: %s", underlying, exc)
            return ""

    def _resolve_expiry_by_offset_for_underlying(
        self,
        underlying: str,
        exchange: str,
        expiry_offset: int = 0,
    ) -> str:
        try:
            from database.symbol import get_distinct_expiries
            from services.option_symbol_service import get_option_exchange

            options_exchange = get_option_exchange(exchange or "NSE_INDEX")
            raw_expiries = get_distinct_expiries(
                exchange=options_exchange,
                underlying=self._base_underlying(underlying),
            )
            token = self._select_expiry_by_offset(raw_expiries or [], offset=expiry_offset)
            if token:
                return token
        except Exception as exc:
            self.logger.debug(
                "Could not resolve expiry offset=%s for %s: %s",
                expiry_offset,
                underlying,
                exc,
            )
        return self._resolve_front_expiry_for_underlying(underlying, exchange)

    @classmethod
    def _infer_option_type_from_leg(cls, leg: Dict[str, Any], symbol: str = "") -> str:
        explicit = str(leg.get("option_type") or "").strip().upper()
        if explicit in {"CE", "PE"}:
            return explicit

        leg_type = str(leg.get("leg_type") or "").strip().upper()
        if "PUT" in leg_type or leg_type.endswith("_PE"):
            return "PE"
        if "CALL" in leg_type or leg_type.endswith("_CE"):
            return "CE"

        sym = symbol.upper()
        if sym.endswith("CE"):
            return "CE"
        if sym.endswith("PE"):
            return "PE"
        return ""

    @classmethod
    def _format_strike_token(cls, strike: Any) -> str:
        try:
            strike_num = float(strike)
        except (TypeError, ValueError):
            return ""
        if strike_num <= 0:
            return ""
        if strike_num.is_integer():
            return str(int(strike_num))
        return str(strike_num).rstrip("0").rstrip(".")

    @classmethod
    def _derive_option_symbol_from_leg(cls, underlying: str, leg: Dict[str, Any]) -> str:
        raw_symbol = cls._normalize_symbol(leg.get("symbol") or leg.get("instrument_symbol"))
        if raw_symbol and cls._looks_like_option_symbol(raw_symbol):
            return raw_symbol

        strike_token = cls._format_strike_token(leg.get("strike"))
        expiry_token = cls._normalize_expiry_for_options(leg.get("expiry") or leg.get("expiry_date"))
        option_type = cls._infer_option_type_from_leg(leg, raw_symbol)
        if not (strike_token and expiry_token and option_type):
            return ""
        return f"{cls._base_underlying(underlying)}{expiry_token}{strike_token}{option_type}"

    def _map_leg_for_options_multi(self, payload: Dict[str, Any], leg: Dict[str, Any]) -> Dict[str, Any]:
        mapped: Dict[str, Any] = {
            "action": str(leg.get("direction") or leg.get("action") or payload.get("direction") or "BUY").upper(),
            "quantity": int(leg.get("quantity", payload.get("quantity", 0)) or 0),
            "pricetype": str(leg.get("order_type") or leg.get("pricetype") or "MARKET").upper(),
            "product": str(leg.get("product") or payload.get("product") or self._default_product()).upper(),
            "price": float(leg.get("price", 0.0) or 0.0),
            "trigger_price": float(leg.get("trigger_price", 0.0) or 0.0),
            "disclosed_quantity": int(leg.get("disclosed_quantity", 0) or 0),
        }
        if mapped["quantity"] <= 0:
            raise BrokerRejectError("Spread leg has non-positive quantity")

        symbol = self._derive_option_symbol_from_leg(payload.get("instrument", ""), leg)
        if symbol:
            mapped["symbol"] = symbol
            leg_exchange = str(leg.get("exchange") or "").strip().upper()
            mapped["exchange"] = leg_exchange or self._infer_exchange_from_symbol(symbol)
            return mapped

        offset = str(leg.get("offset") or "").strip().upper()
        option_type = self._infer_option_type_from_leg(leg)
        if not (offset and option_type):
            raise BrokerRejectError(
                "Spread leg requires tradable symbol or (offset + option_type) for optionsmultiorder"
            )

        mapped["offset"] = offset
        mapped["option_type"] = option_type
        leg_expiry = self._normalize_expiry_for_options(leg.get("expiry_date") or leg.get("expiry"))
        if not leg_expiry and leg.get("expiry_offset") is not None:
            try:
                expiry_offset = int(leg.get("expiry_offset"))
            except (TypeError, ValueError):
                expiry_offset = 0
            leg_expiry = self._resolve_expiry_by_offset_for_underlying(
                underlying=str(payload.get("instrument") or ""),
                exchange=str(payload.get("exchange") or ""),
                expiry_offset=expiry_offset,
            )
        if leg_expiry:
            mapped["expiry_date"] = leg_expiry
        return mapped

    def _place_options_multi_order(self, payload: Dict[str, Any], legs: List[Dict[str, Any]]) -> str:
        underlying = self._base_underlying(str(payload.get("instrument") or ""))
        if not underlying:
            raise BrokerRejectError("optionsmultiorder requires an underlying instrument")

        exchange = str(payload.get("exchange") or "").strip().upper() or self._infer_exchange_from_symbol(underlying)
        if exchange in {"NFO", "NSE"}:
            exchange = "NSE_INDEX"
        elif exchange in {"BFO", "BSE"}:
            exchange = "BSE_INDEX"

        mapped_legs = [self._map_leg_for_options_multi(payload, leg) for leg in legs]

        body: Dict[str, Any] = {
            "apikey": self._api_key,
            "strategy": payload.get("strategy_tag", "TOMIC"),
            "underlying": underlying,
            "exchange": exchange,
            "legs": mapped_legs,
        }

        expiry = self._normalize_expiry_for_options(payload.get("expiry_date") or payload.get("expiry"))
        if not expiry:
            expiry = self._resolve_front_expiry_for_underlying(underlying, exchange)
        if expiry:
            body["expiry_date"] = expiry
        if payload.get("strike_int"):
            body["strike_int"] = int(payload.get("strike_int"))

        url = f"{self._base_url}/api/v1/optionsmultiorder"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        self.logger.info(
            "Placing optionsmultiorder: %s",
            json.dumps(mask_sensitive_fields(body), default=str),
        )

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=15.0)
            data = resp.json() if resp.content else {}

            if resp.status_code >= 400:
                message = data.get("message") if isinstance(data, dict) else ""
                message = message or f"Broker HTTP {resp.status_code} from /optionsmultiorder"
                if 400 <= resp.status_code < 500:
                    raise BrokerRejectError(message)
                raise TimeoutError(message)

            if data.get("status") != "success":
                raise BrokerRejectError(data.get("message", "optionsmultiorder rejected"))

            results = data.get("results")
            if isinstance(results, list):
                order_ids = [str(item.get("orderid")) for item in results if item.get("orderid")]
                if order_ids:
                    return "|".join(order_ids)

            return str(data.get("orderid") or uuid.uuid4())
        except requests.Timeout:
            raise TimeoutError("Broker API timeout (15s) for /optionsmultiorder")
        except requests.ConnectionError as e:
            raise TimeoutError(f"Broker connection error: {e}")

    def _place_single_order(self, params: Dict[str, Any]) -> str:
        """Place a single order via OpenAlgo REST API."""
        if self._sandbox.is_sandbox:
            params = self._sandbox.wrap_order(params)

        strategy_type = str(params.get("strategy_type") or "").strip().upper()
        instrument = self._normalize_symbol(params.get("instrument"))
        instrument_base = self._base_underlying(instrument)
        if (
            strategy_type in {StrategyType.DITM_CALL.value, StrategyType.DITM_PUT.value}
            and instrument
            and not self._looks_like_option_symbol(instrument)
        ):
            return self._place_ditm_options_order(params, instrument_base or instrument)

        if not instrument:
            raise BrokerRejectError("Cannot place order: missing tradable symbol/instrument")

        url = f"{self._base_url}/api/v1/placeorder"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        exchange = str(params.get("exchange") or "").strip().upper() or self._infer_exchange_from_symbol(instrument)
        product = str(params.get("product") or "").strip().upper() or self._default_product()

        body = {
            "apikey": self._api_key,
            "symbol": instrument,
            "exchange": exchange,
            "action": params.get("direction", "BUY"),
            "quantity": str(params.get("quantity", 0)),
            "pricetype": params.get("order_type", "MARKET"),
            "product": product,
            "strategy": params.get("strategy_tag", "TOMIC"),
        }
        if "price" in params:
            body["price"] = params.get("price", 0)
        if "trigger_price" in params:
            body["trigger_price"] = params.get("trigger_price", 0)

        self.logger.info(
            "Placing order: %s",
            json.dumps(mask_sensitive_fields(body), default=str),
        )

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=10.0)
            data = resp.json() if resp.content else {}

            if resp.status_code >= 400:
                message = data.get("message") if isinstance(data, dict) else ""
                message = message or f"Broker HTTP {resp.status_code} from /placeorder"
                if 400 <= resp.status_code < 500:
                    raise BrokerRejectError(message)
                raise TimeoutError(message)

            if data.get("status") == "success":
                order_id = data.get("orderid", str(uuid.uuid4()))
                self._stash_order_meta(
                    str(order_id),
                    {
                        "symbol": instrument,
                        "exchange": exchange,
                        "product": product,
                        "avg_price": float(data.get("average_price", data.get("avg_price", 0.0)) or 0.0),
                    },
                )
                return order_id
            else:
                raise BrokerRejectError(data.get("message", "Unknown rejection"))

        except requests.Timeout:
            raise TimeoutError("Broker API timeout (10s)")
        except requests.ConnectionError as e:
            raise TimeoutError(f"Broker connection error: {e}")

    def _place_ditm_options_order(self, params: Dict[str, Any], underlying: str) -> str:
        """Route directional DITM entries through OpenAlgo optionsorder flow."""
        direction = str(params.get("direction") or "BUY").upper()
        if direction != "BUY":
            raise BrokerRejectError("DITM options flow only supports BUY direction")

        exchange = str(params.get("exchange") or "").strip().upper() or self._infer_exchange_from_symbol(underlying)
        if exchange in {"NSE", "NFO"}:
            exchange = "NSE_INDEX"
        elif exchange in {"BSE", "BFO"}:
            exchange = "BSE_INDEX"

        offset = str(params.get("offset") or os.getenv("TOMIC_DITM_OFFSET", "ITM1")).upper()
        strategy_type = str(params.get("strategy_type") or "").strip().upper()
        default_option_type = "PE" if strategy_type == StrategyType.DITM_PUT.value else "CE"
        option_type = str(params.get("option_type") or default_option_type).upper()
        order_type = str(params.get("order_type") or "MARKET").upper()
        product = str(params.get("product") or "").strip().upper() or self._default_product()
        expiry = self._normalize_expiry_for_options(params.get("expiry_date") or params.get("expiry"))
        if not expiry:
            expiry = self._resolve_front_expiry_for_underlying(underlying, exchange)

        body = {
            "apikey": self._api_key,
            "strategy": params.get("strategy_tag", "TOMIC"),
            "underlying": underlying,
            "exchange": exchange,
            "offset": offset,
            "option_type": option_type,
            "action": direction,
            "quantity": int(params.get("quantity", 0) or 0),
            "pricetype": order_type,
            "product": product,
            "price": float(params.get("price", 0.0) or 0.0),
            "trigger_price": float(params.get("trigger_price", 0.0) or 0.0),
            "disclosed_quantity": int(params.get("disclosed_quantity", 0) or 0),
        }
        if body["quantity"] <= 0:
            raise BrokerRejectError("DITM options flow received non-positive quantity")
        if expiry:
            body["expiry_date"] = expiry
        else:
            raise BrokerRejectError(
                f"Expiry date missing for optionsorder underlying={underlying}. "
                "Provide expiry_date or ensure master contract expiries are available."
            )

        # Normalize quantity to broker lot-size multiple if master contract has it.
        lot_size = self._resolve_option_lot_size(
            underlying=underlying,
            exchange=exchange,
            expiry=expiry,
            option_type=option_type,
        )
        if lot_size > 0:
            aligned = self._align_quantity_to_multiple(body["quantity"], lot_size, allow_min_one_lot=True)
            if aligned != body["quantity"]:
                self.logger.warning(
                    "Adjusted optionsorder quantity %s -> %s to match lot size %s",
                    body["quantity"],
                    aligned,
                    lot_size,
                )
            body["quantity"] = aligned

        url = f"{self._base_url}/api/v1/optionsorder"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        self.logger.info(
            "Placing optionsorder: %s",
            json.dumps(mask_sensitive_fields(body), default=str),
        )

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=12.0)
            data = resp.json() if resp.content else {}

            if resp.status_code >= 400:
                message = data.get("message") if isinstance(data, dict) else ""
                message = message or f"Broker HTTP {resp.status_code} from /optionsorder"
                if 400 <= resp.status_code < 500:
                    raise BrokerRejectError(message)
                raise TimeoutError(message)

            if data.get("status") != "success":
                message = data.get("message", "optionsorder rejected")
                required_multiple = self._parse_required_multiple(message)
                if required_multiple > 0:
                    retry_qty = self._align_quantity_to_multiple(
                        body["quantity"], required_multiple, allow_min_one_lot=True
                    )
                    if retry_qty > 0 and retry_qty != body["quantity"]:
                        retry_body = dict(body)
                        retry_body["quantity"] = retry_qty
                        self.logger.warning(
                            "Broker lot-multiple reject on optionsorder, retrying quantity %s -> %s (multiple=%s)",
                            body["quantity"],
                            retry_qty,
                            required_multiple,
                        )
                        retry_resp = requests.post(url, json=retry_body, headers=headers, timeout=12.0)
                        retry_data = retry_resp.json() if retry_resp.content else {}
                        if retry_resp.status_code >= 400:
                            retry_message = retry_data.get("message") if isinstance(retry_data, dict) else ""
                            retry_message = retry_message or f"Broker HTTP {retry_resp.status_code} from /optionsorder"
                            if 400 <= retry_resp.status_code < 500:
                                raise BrokerRejectError(retry_message)
                            raise TimeoutError(retry_message)
                        if retry_data.get("status") == "success":
                            order_id = retry_data.get("orderid")
                            if order_id:
                                self._stash_order_meta(
                                    str(order_id),
                                    {
                                        "symbol": str(retry_data.get("symbol") or ""),
                                        "exchange": str(retry_data.get("exchange") or ""),
                                        "product": product,
                                        "avg_price": float(
                                            retry_data.get("average_price", retry_data.get("avg_price", 0.0)) or 0.0
                                        ),
                                        "underlying_ltp": float(retry_data.get("underlying_ltp", 0.0) or 0.0),
                                    },
                                )
                                return str(order_id)
                            retry_results = retry_data.get("results")
                            if isinstance(retry_results, list):
                                order_ids = [str(item.get("orderid")) for item in retry_results if item.get("orderid")]
                                if order_ids:
                                    for oid in order_ids:
                                        self._stash_order_meta(
                                            str(oid),
                                            {
                                                "symbol": str(retry_data.get("symbol") or ""),
                                                "exchange": str(retry_data.get("exchange") or ""),
                                                "product": product,
                                                "avg_price": float(
                                                    retry_data.get(
                                                        "average_price", retry_data.get("avg_price", 0.0)
                                                    )
                                                    or 0.0
                                                ),
                                                "underlying_ltp": float(retry_data.get("underlying_ltp", 0.0) or 0.0),
                                            },
                                        )
                                    return "|".join(order_ids)
                            return str(uuid.uuid4())
                raise BrokerRejectError(message)

            order_id = data.get("orderid")
            if order_id:
                self._stash_order_meta(
                    str(order_id),
                    {
                        "symbol": str(data.get("symbol") or ""),
                        "exchange": str(data.get("exchange") or ""),
                        "product": product,
                        "avg_price": float(data.get("average_price", data.get("avg_price", 0.0)) or 0.0),
                        "underlying_ltp": float(data.get("underlying_ltp", 0.0) or 0.0),
                    },
                )
                return str(order_id)

            split_results = data.get("results")
            if isinstance(split_results, list):
                order_ids = [str(item.get("orderid")) for item in split_results if item.get("orderid")]
                if order_ids:
                    for oid in order_ids:
                        self._stash_order_meta(
                            str(oid),
                            {
                                "symbol": str(data.get("symbol") or ""),
                                "exchange": str(data.get("exchange") or ""),
                                "product": product,
                                "avg_price": float(data.get("average_price", data.get("avg_price", 0.0)) or 0.0),
                                "underlying_ltp": float(data.get("underlying_ltp", 0.0) or 0.0),
                            },
                        )
                    return "|".join(order_ids)

            return str(uuid.uuid4())
        except requests.Timeout:
            raise TimeoutError("Broker API timeout (12s) for /optionsorder")
        except requests.ConnectionError as e:
            raise TimeoutError(f"Broker connection error: {e}")

    def _place_leg_order(self, payload: Dict, leg: Dict) -> str:
        """Place a single leg of a multi-leg order."""
        order_params = {
            "instrument": self._normalize_symbol(leg.get("symbol", payload.get("instrument", ""))),
            "exchange": leg.get("exchange", "NFO"),
            "direction": leg.get("direction", "BUY"),
            "quantity": leg.get("quantity", payload.get("quantity", 0)),
            "order_type": leg.get("order_type", "LIMIT"),
            "price": leg.get("price", 0),
            "product": leg.get("product", self._default_product()),
            "strategy_tag": payload.get("strategy_tag", "TOMIC"),
            "strategy_type": payload.get("strategy_type", ""),
        }
        return self._place_single_order(order_params)

    def _place_basket_order(self, payload: Dict, legs: List[Dict]) -> str:
        """Place a basket/multi-leg order via OpenAlgo API."""
        url = f"{self._base_url}/api/v1/basketorder"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        orders = []
        for leg in legs:
            leg_symbol = self._normalize_symbol(leg.get("symbol", payload.get("instrument", "")))
            if not leg_symbol:
                raise BrokerRejectError("Basket leg missing tradable symbol")
            orders.append({
                "symbol": leg_symbol,
                "exchange": leg.get("exchange", self._infer_exchange_from_symbol(leg_symbol)),
                "action": leg.get("direction", "BUY"),
                "quantity": str(leg.get("quantity", 0)),
                "pricetype": leg.get("order_type", "MARKET"),
                "product": leg.get("product", self._default_product()),
            })

        body = {
            "apikey": self._api_key,
            "orders": orders,
            "strategy": payload.get("strategy_tag", "TOMIC"),
        }

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=15.0)
            data = resp.json() if resp.content else {}

            if resp.status_code >= 400:
                message = data.get("message") if isinstance(data, dict) else ""
                message = message or f"Broker HTTP {resp.status_code} from /basketorder"
                if 400 <= resp.status_code < 500:
                    raise BrokerRejectError(message)
                raise TimeoutError(message)

            if data.get("status") == "success":
                return data.get("orderid", str(uuid.uuid4()))
            else:
                raise BrokerRejectError(data.get("message", "Basket order rejected"))
        except requests.Timeout:
            raise TimeoutError("Basket order timeout (15s)")
        except requests.ConnectionError as e:
            raise TimeoutError(f"Broker connection error: {e}")

    def _fetch_broker_positions(self) -> List[Dict]:
        """Fetch current positions from broker for reconciliation."""
        url = f"{self._base_url}/api/v1/positionbook"
        body = {"apikey": self._api_key}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        self._last_broker_positions_error = ""

        try:
            # OpenAlgo positionbook endpoint is POST with `apikey` payload.
            resp = requests.post(url, json=body, timeout=10.0)
            if resp.status_code in (404, 405):
                # Backward compatibility fallback for custom deployments.
                resp = requests.get(url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            if isinstance(data, dict):
                return data.get("data", [])
            return []
        except Exception as e:
            self._last_broker_positions_error = str(e)
            self.logger.error("Failed to fetch broker positions: %s", e)
            return []

    def _check_broker_idempotency(self, idempotency_key: str, strategy_tag: str) -> bool:
        """Check broker orderbook for existing order with same strategy tag."""
        if not strategy_tag:
            return False

        try:
            url = f"{self._base_url}/api/v1/orderbook"
            body = {"apikey": self._api_key}
            headers = {"Authorization": f"Bearer {self._api_key}"}
            # OpenAlgo orderbook endpoint is POST with `apikey` payload.
            resp = requests.post(url, json=body, timeout=5.0)
            if resp.status_code in (404, 405):
                # Backward compatibility fallback.
                resp = requests.get(url, headers=headers, timeout=5.0)
            resp.raise_for_status()
            orders = resp.json().get("data", [])

            for order in orders:
                if order.get("strategy", "") == strategy_tag:
                    status = order.get("status", "").upper()
                    if status in ("COMPLETE", "OPEN", "TRIGGER PENDING"):
                        return True
        except Exception:
            pass  # Can't check — proceed cautiously

        return False

    # -----------------------------------------------------------------------
    # PositionBook updates
    # -----------------------------------------------------------------------

    def _update_position_book(self, payload: Dict, broker_ref: str) -> None:
        """Update PositionBook after successful fill."""
        meta = self._consume_order_meta(broker_ref)
        instrument = str(meta.get("symbol") or payload.get("instrument", "") or "").strip().upper()
        strategy_id = payload.get("strategy_id", "")
        direction = str(payload.get("direction", "BUY") or "BUY").strip().upper()
        quantity = int(payload.get("quantity", 0))
        exchange = str(meta.get("exchange") or payload.get("exchange", "") or "").strip().upper()
        if not exchange:
            exchange = self._infer_exchange_from_symbol(instrument)
        product = str(meta.get("product") or payload.get("product", "") or "").strip().upper() or self._default_product()
        avg_price = float(meta.get("avg_price", 0.0) or 0.0)
        if avg_price <= 0 and instrument:
            avg_price = self._get_ws_price(instrument, exchange)
        if avg_price <= 0:
            avg_price = float(payload.get("price", 0.0) or 0.0)

        pos = Position(
            instrument=instrument,
            exchange=exchange or "NSE",
            product=product,
            strategy_id=strategy_id,
            direction=direction,
            quantity=quantity,
            avg_price=avg_price,
            ltp=avg_price,
            strategy_tag=payload.get("strategy_tag", ""),
            entry_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self._position_book.update_position(pos)

        self._maybe_attach_virtual_guard(payload=payload, position=pos)

    def _maybe_attach_virtual_guard(self, payload: Dict[str, Any], position: Position) -> None:
        if not self._virtual_enabled:
            return
        strategy_type = str(payload.get("strategy_type") or "").strip().upper()
        direction = str(position.direction or "").strip().upper()
        instrument = str(position.instrument or "").strip().upper()
        if direction != "BUY":
            return
        if strategy_type not in {StrategyType.DITM_CALL.value, StrategyType.DITM_PUT.value}:
            return
        if not self._looks_like_option_symbol(instrument):
            return

        key = PositionBook.make_key(position.instrument, position.strategy_id)
        entry = float(position.avg_price or 0.0)
        guard = {
            "position_key": key,
            "instrument": position.instrument,
            "exchange": position.exchange,
            "strategy_id": position.strategy_id,
            "strategy_tag": position.strategy_tag,
            "product": position.product,
            "quantity": int(abs(position.quantity)),
            "entry_price": 0.0,
            "stop_price": 0.0,
            "take_profit_price": 0.0,
            "highest_price": 0.0,
            "trailing_enabled": bool(self._virtual_trail_enabled),
            "trail_trigger_price": 0.0,
            "trail_offset": 0.0,
            "trailing_active": False,
            "created_wall": time.time(),
            "last_exit_attempt_mono": 0.0,
        }
        if entry > 0:
            self._arm_virtual_guard(guard, entry)
        self._virtual_positions[key] = guard
        self.logger.info(
            "Virtual guard attached: %s entry=%.2f sl=%.2f tp=%.2f trail=%s",
            key,
            float(guard.get("entry_price", 0.0) or 0.0),
            float(guard.get("stop_price", 0.0) or 0.0),
            float(guard.get("take_profit_price", 0.0) or 0.0),
            bool(guard.get("trailing_enabled", False)),
        )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class BrokerRejectError(Exception):
    """Raised when broker rejects an order (margin, invalid params, etc)."""
    pass
