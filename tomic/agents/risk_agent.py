"""
TOMIC Risk Agent — 8-Step Sizing Chain + Black Swan Hedge
==========================================================
Reads regime state (AtomicRegimeState), reads PositionBook snapshot,
applies the full deterministic sizing pipeline, and enqueues
ORDER_REQUEST into the durable command table.

Sizing chain (each step can only reduce, never increase):
  1. Carver Volatility Target: raw = (Capital × 20%) / (35d EWMA vol)
  2. Half-Kelly Cap: kelly = (Win% − Loss%/RR) / 2 × Capital
  3. 2% Rule: max_risk = (Capital × 2%) / SL_distance
  4. VIX Overlay: if VIX > 30 → 50% reduction
  5. IDM: if correlation > 0.7 → 0.7× factor
  6. Sector Heat Cap: if sector_margin > 20% → REJECT
  7. Position Cap: if total_positions >= 10 → REJECT
  8. Margin Reserve Gate: if free_margin < 25% → REJECT

Black Swan Hedge bootstrap:
  - < 30 trades: ₹5,000/month fixed
  - ≥ 30 trades: 1-2% of rolling monthly profit
  - Always 5-delta NIFTY puts, monthly roll
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from tomic.agent_base import AgentBase
from tomic.agents.regime_agent import AtomicRegimeState, RegimeSnapshot
from tomic.command_store import CommandStore
from tomic.config import (
    BlackSwanParams,
    RegimePhase,
    SizingParams,
    StrategyType,
    TomicConfig,
    VIXRules,
)
from tomic.events import (
    AlertLevel,
    EventType,
    OrderRequestEvent,
    SignalEvent,
)
from tomic.event_bus import EventPublisher
from tomic.position_book import PositionBook, PositionSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sizing Chain Step Results (for journaling)
# ---------------------------------------------------------------------------

@dataclass
class SizingStep:
    """One step in the 8-step sizing chain."""
    step: int
    name: str
    input_size: float
    output_size: float
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "name": self.name,
            "input": self.input_size,
            "output": self.output_size,
            "reason": self.reason,
        }


@dataclass
class SizingResult:
    """Final result of the sizing chain."""
    approved: bool
    final_lots: int
    chain: List[SizingStep] = field(default_factory=list)
    reject_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "final_lots": self.final_lots,
            "reject_reason": self.reject_reason,
            "chain": [s.to_dict() for s in self.chain],
        }


# ---------------------------------------------------------------------------
# Sizing Chain Functions (pure, deterministic)
# ---------------------------------------------------------------------------

def step_1_volatility_target(
    capital: float,
    target_vol: float,
    instrument_vol: float,
) -> Tuple[float, str]:
    """
    Step 1: Carver Volatility Target.
    raw_size = (Capital × target_vol%) / instrument_vol
    """
    if instrument_vol <= 0:
        return 0.0, "REJECT: zero instrument volatility"
    raw = (capital * target_vol) / instrument_vol
    return raw, f"vol_target: {capital:.0f}*{target_vol:.2f}/{instrument_vol:.4f}={raw:.2f}"


def step_2_half_kelly(
    capital: float,
    win_rate: float,
    reward_risk_ratio: float,
) -> Tuple[float, str]:
    """
    Step 2: Half-Kelly Cap.
    kelly_fraction = (Win% - (1-Win%) / RR) / 2
    """
    if reward_risk_ratio <= 0:
        return 0.0, "REJECT: zero RR ratio"

    loss_rate = 1.0 - win_rate
    kelly = (win_rate - loss_rate / reward_risk_ratio) / 2.0

    if kelly <= 0:
        return 0.0, f"REJECT: negative kelly={kelly:.4f}"

    kelly_size = kelly * capital
    return kelly_size, f"half_kelly={kelly:.4f} → {kelly_size:.2f}"


def step_3_two_pct_rule(
    capital: float,
    sl_distance: float,
    max_risk_pct: float = 0.02,
) -> Tuple[float, str]:
    """
    Step 3: 2% Rule.
    max_size = (Capital × 2%) / SL_distance
    """
    if sl_distance <= 0:
        return 0.0, "REJECT: zero SL distance"

    max_size = (capital * max_risk_pct) / sl_distance
    return max_size, f"2%_rule: {capital:.0f}*{max_risk_pct}/{sl_distance:.2f}={max_size:.2f}"


def step_4_vix_overlay(
    size: float,
    vix: float,
    half_size_above: float = 30.0,
) -> Tuple[float, str]:
    """
    Step 4: VIX Overlay.
    If VIX > threshold: size × 0.5
    """
    if vix > half_size_above:
        reduced = size * 0.5
        return reduced, f"VIX={vix:.1f}>{half_size_above} → halved to {reduced:.2f}"
    return size, f"VIX={vix:.1f} OK"


def step_5_idm(
    size: float,
    correlation: float,
    threshold: float = 0.7,
    reduction_factor: float = 0.7,
) -> Tuple[float, str]:
    """
    Step 5: Instrument Diversification Multiplier.
    If correlation > 0.7: size × 0.7
    """
    if correlation > threshold:
        reduced = size * reduction_factor
        return reduced, f"corr={correlation:.2f}>{threshold} → IDM {reduction_factor}×, {reduced:.2f}"
    return size, f"corr={correlation:.2f} OK"


def step_6_sector_heat(
    size: float,
    sector_margin_pct: float,
    limit: float = 0.20,
) -> Tuple[float, str]:
    """
    Step 6: Sector Heat Cap.
    If sector_margin > 20%: REJECT
    """
    if sector_margin_pct > limit:
        return 0.0, f"REJECT: sector_margin={sector_margin_pct:.1%}>{limit:.0%}"
    return size, f"sector_margin={sector_margin_pct:.1%} OK"


def step_7_position_cap(
    size: float,
    total_positions: int,
    max_positions: int = 10,
) -> Tuple[float, str]:
    """
    Step 7: Position Cap.
    If total_positions >= max: REJECT
    """
    if total_positions >= max_positions:
        return 0.0, f"REJECT: positions={total_positions}>={max_positions}"
    return size, f"positions={total_positions}<{max_positions} OK"


def step_8_margin_reserve(
    size: float,
    free_margin_pct: float,
    reserve: float = 0.25,
) -> Tuple[float, str]:
    """
    Step 8: Margin Reserve Gate.
    If free_margin < 25%: REJECT
    """
    if free_margin_pct < reserve:
        return 0.0, f"REJECT: free_margin={free_margin_pct:.1%}<{reserve:.0%}"
    return size, f"free_margin={free_margin_pct:.1%} OK"


def round_to_lots(size: float, lot_size: int = 50) -> int:
    """Round down to nearest lot size."""
    if size <= 0 or lot_size <= 0:
        return 0
    return int(size // lot_size) * lot_size


def run_sizing_chain(
    capital: float,
    instrument_vol: float,
    win_rate: float,
    reward_risk_ratio: float,
    sl_distance: float,
    vix: float,
    correlation: float,
    sector_margin_pct: float,
    total_positions: int,
    free_margin_pct: float,
    lot_size: int = 50,
    params: Optional[SizingParams] = None,
    vix_rules: Optional[VIXRules] = None,
) -> SizingResult:
    """
    Run the full 8-step deterministic sizing chain.
    Each step can only reduce size; never increase.
    Returns SizingResult with full chain log for journaling.
    """
    if params is None:
        params = SizingParams()
    if vix_rules is None:
        vix_rules = VIXRules()

    chain: List[SizingStep] = []
    current = float("inf")

    # Step 1: Volatility Target
    s1, r1 = step_1_volatility_target(capital, params.target_vol, instrument_vol)
    current = min(current, s1)
    chain.append(SizingStep(1, "volatility_target", float("inf"), current, r1))
    if current <= 0:
        return SizingResult(False, 0, chain, r1)

    # Step 2: Half-Kelly
    s2, r2 = step_2_half_kelly(capital, win_rate, reward_risk_ratio)
    prev = current
    current = min(current, s2)
    chain.append(SizingStep(2, "half_kelly", prev, current, r2))
    if current <= 0:
        return SizingResult(False, 0, chain, r2)

    # Step 3: 2% Rule
    s3, r3 = step_3_two_pct_rule(capital, sl_distance, params.max_risk_per_trade)
    prev = current
    current = min(current, s3)
    chain.append(SizingStep(3, "two_pct_rule", prev, current, r3))
    if current <= 0:
        return SizingResult(False, 0, chain, r3)

    # Step 4: VIX Overlay
    prev = current
    s4, r4 = step_4_vix_overlay(current, vix, vix_rules.half_size_above)
    current = s4
    chain.append(SizingStep(4, "vix_overlay", prev, current, r4))
    if current <= 0:
        return SizingResult(False, 0, chain, r4)

    # Step 5: IDM
    prev = current
    s5, r5 = step_5_idm(
        current, correlation,
        params.idm_correlation_threshold,
        params.idm_reduction_factor,
    )
    current = s5
    chain.append(SizingStep(5, "idm", prev, current, r5))
    if current <= 0:
        return SizingResult(False, 0, chain, r5)

    # Step 6: Sector Heat
    prev = current
    s6, r6 = step_6_sector_heat(current, sector_margin_pct, params.sector_heat_limit)
    current = s6
    chain.append(SizingStep(6, "sector_heat", prev, current, r6))
    if current <= 0:
        return SizingResult(False, 0, chain, r6)

    # Step 7: Position Cap
    prev = current
    s7, r7 = step_7_position_cap(current, total_positions, params.max_positions)
    current = s7
    chain.append(SizingStep(7, "position_cap", prev, current, r7))
    if current <= 0:
        return SizingResult(False, 0, chain, r7)

    # Step 8: Margin Reserve
    prev = current
    s8, r8 = step_8_margin_reserve(current, free_margin_pct, params.margin_reserve)
    current = s8
    chain.append(SizingStep(8, "margin_reserve", prev, current, r8))
    if current <= 0:
        return SizingResult(False, 0, chain, r8)

    # Round to lots
    lots = round_to_lots(current, lot_size)
    if lots <= 0:
        return SizingResult(False, 0, chain, f"rounded to 0 lots (size={current:.2f}, lot={lot_size})")

    return SizingResult(True, lots, chain)


# ---------------------------------------------------------------------------
# Black Swan Hedge Calculator
# ---------------------------------------------------------------------------

def compute_black_swan_budget(
    total_trades: int,
    monthly_profit_estimate: float,
    params: Optional[BlackSwanParams] = None,
) -> float:
    """
    Compute monthly Black Swan hedge budget.
    < 30 trades: ₹5,000 fixed. ≥ 30: 1-2% of expected monthly profit.
    """
    if params is None:
        params = BlackSwanParams()

    if total_trades < params.min_trades_for_pct:
        return params.bootstrap_budget_inr

    # Use midpoint of 1-2% range
    mid_pct = (params.profit_pct_min + params.profit_pct_max) / 2
    budget = monthly_profit_estimate * mid_pct
    return max(budget, params.bootstrap_budget_inr)  # never below bootstrap


# ---------------------------------------------------------------------------
# Risk Agent
# ---------------------------------------------------------------------------

class RiskAgent(AgentBase):
    """
    Position-sizing gatekeeper.

    Listens for SIGNAL events (via ZeroMQ or internal queue),
    reads AtomicRegimeState and PositionBook snapshots,
    applies 8-step sizing chain, and enqueues ORDER_REQUEST to command table.

    Tick interval: 1 second (polls for pending signals).
    """

    def __init__(
        self,
        config: TomicConfig,
        publisher: EventPublisher,
        command_store: CommandStore,
        position_book: PositionBook,
        regime_state: AtomicRegimeState,
        capital: float = 1_000_000.0,
        margin_fetcher: Optional[Callable[[], float]] = None,
    ):
        super().__init__(name="risk_agent", config=config, publisher=publisher)

        self._command_store = command_store
        self._position_book = position_book
        self._regime_state = regime_state
        self._capital = capital
        self._margin_fetcher = margin_fetcher  # callable → free_margin_pct

        # Signal queue (populated by telemetry subscriber or direct call)
        self._pending_signals: List[Dict[str, Any]] = []
        self._signal_lock = threading.Lock()

        # Real-time telemetry for observability UI.
        self._telemetry_lock = threading.Lock()
        self._recent_evaluations: deque[Dict[str, Any]] = deque(maxlen=200)
        self._eval_counters: Dict[str, int] = {
            "evaluated": 0,
            "blocked_regime": 0,
            "blocked_position": 0,
            "invalid_signal": 0,
            "rejected_sizing": 0,
            "enqueued": 0,
            "duplicate": 0,
        }

        # Track stats
        self._total_trades: int = 0
        self._monthly_profit_estimate: float = 0.0
        self._last_position_version: int = 0
        self._allow_pyramiding = str(os.getenv("TOMIC_ALLOW_PYRAMIDING", "false")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    # -------------------------------------------------------------------
    # Signal ingestion
    # -------------------------------------------------------------------

    def enqueue_signal(self, signal: Dict[str, Any]) -> None:
        """Add a signal for evaluation. Thread-safe."""
        with self._signal_lock:
            self._pending_signals.append(signal)
            self.logger.info("Signal enqueued: %s %s",
                             signal.get("instrument", "?"),
                             signal.get("strategy_type", "?"))

    def pending_signal_count(self) -> int:
        with self._signal_lock:
            return len(self._pending_signals)

    def get_recent_evaluations(self, limit: int = 25) -> List[Dict[str, Any]]:
        capped = max(1, min(int(limit or 1), 200))
        with self._telemetry_lock:
            return list(self._recent_evaluations)[:capped]

    def get_telemetry_summary(self, limit: int = 25) -> Dict[str, Any]:
        return {
            "pending_signals": self.pending_signal_count(),
            "counters": dict(self._eval_counters),
            "recent_evaluations": self.get_recent_evaluations(limit=limit),
        }

    def _record_evaluation(self, payload: Dict[str, Any]) -> None:
        with self._telemetry_lock:
            self._recent_evaluations.appendleft(payload)
            result = str(payload.get("result", "")).strip().lower()
            self._eval_counters["evaluated"] += 1
            if result in self._eval_counters:
                self._eval_counters[result] += 1

    @staticmethod
    def _safe_float_for_json(value: Any) -> Optional[float]:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        return num if math.isfinite(num) else None

    def _serialize_chain_for_telemetry(self, chain: List[SizingStep]) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for step in chain:
            serialized.append(
                {
                    "step": step.step,
                    "name": step.name,
                    "input": self._safe_float_for_json(step.input_size),
                    "output": self._safe_float_for_json(step.output_size),
                    "reason": step.reason,
                }
            )
        return serialized

    def _on_signal_event(self, event_data: Dict[str, Any]) -> None:
        """Callback for ZeroMQ telemetry SIGNAL events."""
        self.enqueue_signal(event_data)

    # -------------------------------------------------------------------
    # Agent lifecycle
    # -------------------------------------------------------------------

    def _setup(self) -> None:
        self.logger.info(
            "Risk Agent initialized, capital=%.0f, sizing=%s",
            self._capital, self.config.sizing,
        )

    def _tick(self) -> None:
        """Process pending signals through the sizing chain."""
        # Drain signal queue
        with self._signal_lock:
            if not self._pending_signals:
                return
            signals = list(self._pending_signals)
            self._pending_signals.clear()

        for signal in signals:
            try:
                self._evaluate_signal(signal)
            except Exception as e:
                self.logger.error("Error evaluating signal %s: %s",
                                  signal.get("instrument", "?"), e, exc_info=True)
                self._publish_alert(
                    AlertLevel.RISK,
                    f"Sizing error for {signal.get('instrument', '?')}: {e}",
                )

    def _teardown(self) -> None:
        self.logger.info("Risk Agent stopped, %d trades processed", self._total_trades)

    def _get_tick_interval(self) -> float:
        return 1.0

    # -------------------------------------------------------------------
    # Core: signal evaluation + sizing
    # -------------------------------------------------------------------

    def _evaluate_signal(self, signal: Dict[str, Any]) -> None:
        """
        Evaluate a single signal through the full pipeline:
        1. Regime filter
        2. 8-step sizing chain
        3. Enqueue ORDER_REQUEST
        """
        instrument = signal.get("instrument", "")
        strategy_type = signal.get("strategy_type", "")
        direction = signal.get("direction", "BUY")
        sl_distance = float(signal.get("stop_price", 0) or 0)
        entry_price = float(signal.get("entry_price", 0) or 0)

        self.logger.info("Evaluating signal: %s %s %s", instrument, strategy_type, direction)

        valid_signal, validation_reason = self._validate_signal(instrument, strategy_type, direction, signal)
        if not valid_signal:
            self.logger.info("Signal BLOCKED by validation: %s", validation_reason)
            self._record_evaluation({
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "instrument": instrument,
                "strategy_type": strategy_type,
                "direction": direction,
                "result": "invalid_signal",
                "reason": validation_reason,
            })
            return

        # --- 1. Regime filter (Master Filter) ---
        regime = self._regime_state.read_snapshot()
        trace_base = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "instrument": instrument,
            "strategy_type": strategy_type,
            "direction": direction,
            "regime_phase": regime.phase.value,
            "regime_vix": regime.vix,
        }

        regime_allowed, regime_reason = self._regime_allows_signal(regime, direction, strategy_type)
        if not regime_allowed:
            self.logger.info(
                "Signal BLOCKED by regime: phase=%s score=%d, signal=%s %s",
                regime.phase.value, regime.score, direction, strategy_type,
            )
            self._record_evaluation({
                **trace_base,
                "result": "blocked_regime",
                "reason": regime_reason,
            })
            return

        # --- 2. Compute SL distance ---
        if sl_distance > 0 and entry_price > 0:
            actual_sl_dist = abs(entry_price - sl_distance)
        else:
            # Fallback: use % of entry
            actual_sl_dist = entry_price * 0.02 if entry_price > 0 else 100.0

        # --- 3. Position snapshot ---
        pos_snapshot = self._position_book.read_snapshot()
        position_gate_ok, position_gate_reason = self._position_gate_allows_signal(
            pos_snapshot=pos_snapshot,
            instrument=instrument,
            strategy_type=strategy_type,
            direction=direction,
        )
        if not position_gate_ok:
            self._record_evaluation({
                **trace_base,
                "result": "blocked_position",
                "reason": position_gate_reason,
            })
            self.logger.info("Signal BLOCKED by position gate: %s", position_gate_reason)
            return
        total_positions = pos_snapshot.total_positions

        # --- 4. Margin ---
        free_margin_pct = self._get_free_margin()

        # --- 5. Run sizing chain ---
        result = run_sizing_chain(
            capital=self._capital,
            instrument_vol=signal.get("instrument_vol", 0.25),  # default 25% annual
            win_rate=signal.get("win_rate", 0.55),               # default 55%
            reward_risk_ratio=signal.get("rr_ratio", 2.0),       # default 2:1
            sl_distance=actual_sl_dist,
            vix=regime.vix,
            correlation=signal.get("correlation", 0.0),
            sector_margin_pct=signal.get("sector_margin_pct", 0.0),
            total_positions=total_positions,
            free_margin_pct=free_margin_pct,
            lot_size=signal.get("lot_size", 50),
            params=self.config.sizing,
            vix_rules=self.config.vix,
        )

        # Log the full chain
        self.logger.info(
            "Sizing chain for %s: approved=%s lots=%d",
            instrument, result.approved, result.final_lots,
        )
        for step in result.chain:
            self.logger.debug(
                "  Step %d (%s): %.2f → %.2f (%s)",
                step.step, step.name, step.input_size, step.output_size, step.reason,
            )

        if not result.approved:
            self.logger.info("Signal REJECTED by sizing: %s — %s", instrument, result.reject_reason)
            self._record_evaluation({
                **trace_base,
                "result": "rejected_sizing",
                "reason": result.reject_reason,
                "final_lots": result.final_lots,
                "sizing_chain": self._serialize_chain_for_telemetry(result.chain),
                "free_margin_pct": free_margin_pct,
                "total_positions": total_positions,
            })
            return

        # --- 6. Enqueue ORDER_REQUEST ---
        enqueued = self._enqueue_order(signal, result, regime)
        self._record_evaluation({
            **trace_base,
            "result": "enqueued" if enqueued else "duplicate",
            "reason": "order request enqueued" if enqueued else "duplicate idempotency key",
            "final_lots": result.final_lots,
            "sizing_chain": self._serialize_chain_for_telemetry(result.chain),
            "free_margin_pct": free_margin_pct,
            "total_positions": total_positions,
        })

    def _regime_allows_signal(
        self,
        regime: RegimeSnapshot,
        direction: str,
        strategy_type: str,
    ) -> Tuple[bool, str]:
        """
        Regime = Master Filter (Elder's Triple Screen).
        - Bearish regime: block all bullish directional signals
        - Congestion: allow credit spreads (volatility) but block directional
        - HALT_SHORT_VEGA flag: block credit spreads
        """
        # Hard block: VIX-driven halts
        if "HALT_SHORT_VEGA" in regime.vix_flags:
            # Block all premium-selling strategies
            credit_strategies = {
                StrategyType.BULL_PUT_SPREAD.value,
                StrategyType.BEAR_CALL_SPREAD.value,
                StrategyType.IRON_CONDOR.value,
                StrategyType.JADE_LIZARD.value,
                StrategyType.SHORT_STRANGLE.value,
                StrategyType.SHORT_STRADDLE.value,
                StrategyType.RISK_REVERSAL.value,
            }
            if strategy_type in credit_strategies:
                self.logger.warning("HALT_SHORT_VEGA: blocking %s", strategy_type)
                return False, "HALT_SHORT_VEGA blocks premium-selling strategy"

        direction_u = str(direction or "").strip().upper()
        strategy_u = str(strategy_type or "").strip().upper()

        bullish_bias = {
            StrategyType.DITM_CALL.value,
            StrategyType.BULL_PUT_SPREAD.value,
        }
        bearish_bias = {
            StrategyType.DITM_PUT.value,
            StrategyType.BEAR_CALL_SPREAD.value,
        }
        naked_premium = {
            StrategyType.JADE_LIZARD.value,
            StrategyType.SHORT_STRANGLE.value,
            StrategyType.SHORT_STRADDLE.value,
        }

        if regime.phase == RegimePhase.BLOWOFF:
            return False, "Blowoff regime blocks fresh entries"

        # Bearish regime blocks bullish-bias setups.
        if regime.phase == RegimePhase.BEARISH and strategy_u in bullish_bias:
            return False, "Bearish regime blocks bullish-bias setup"

        # Bullish regime blocks bearish-bias setups.
        if regime.phase == RegimePhase.BULLISH and strategy_u in bearish_bias:
            return False, "Bullish regime blocks bearish-bias setup"

        # Congestion: prioritize credit spreads over directional
        if regime.phase == RegimePhase.CONGESTION and direction_u == "BUY":
            pure_directional = {StrategyType.DITM_CALL.value, StrategyType.DITM_PUT.value}
            if strategy_u in pure_directional:
                return False, "Congestion regime blocks pure directional BUY setup"

        # Naked premium collection only in congestion/sweet-spot and never in defined-risk-only mode.
        if strategy_u in naked_premium:
            vix_flags = set(str(flag).strip().upper() for flag in regime.vix_flags)
            if regime.phase != RegimePhase.CONGESTION:
                return False, f"{strategy_u} allowed only in CONGESTION regime"
            if "PREMIUMS_TOO_LOW" in vix_flags:
                return False, f"{strategy_u} blocked: premiums too low"
            if "DEFINED_RISK_ONLY" in vix_flags:
                return False, f"{strategy_u} blocked: DEFINED_RISK_ONLY regime"
            if direction_u != "SELL":
                return False, f"{strategy_u} supports SELL entries only"

        return True, "Regime allows signal"

    def _validate_signal(
        self,
        instrument: str,
        strategy_type: str,
        direction: str,
        signal: Dict[str, Any],
    ) -> Tuple[bool, str]:
        instrument_u = str(instrument or "").strip().upper()
        strategy_u = str(strategy_type or "").strip().upper()
        direction_u = str(direction or "").strip().upper()

        if not instrument_u:
            return False, "Missing instrument"
        if not strategy_u:
            return False, "Missing strategy_type"
        if instrument_u in {"INDIAVIX", "VIX"}:
            return False, f"{instrument_u} is context-only and not tradable"

        if strategy_u == StrategyType.DITM_CALL.value and direction_u != "BUY":
            return False, "DITM_CALL supports BUY entries only"
        if strategy_u == StrategyType.DITM_PUT.value and direction_u != "BUY":
            return False, "DITM_PUT supports BUY entries only"

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
        legs = signal.get("legs") or []
        if strategy_u in leg_required and not legs:
            return False, f"{strategy_u} requires legs in signal payload"

        return True, "Signal payload valid"

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
        match = re.match(r"^([A-Z]+)\d{2}[A-Z]{3}\d{2}(?:\d+(?:CE|PE)|FUT)?$", compact)
        return match.group(1) if match else compact

    def _position_gate_allows_signal(
        self,
        pos_snapshot: PositionSnapshot,
        instrument: str,
        strategy_type: str,
        direction: str,
    ) -> Tuple[bool, str]:
        sig_underlying = self._base_underlying(instrument)
        if not sig_underlying:
            return True, "No underlying context"

        same_underlying_count = 0
        same_side_count = 0
        same_strategy_side_count = 0
        strategy_prefix = f"TOMIC_{str(strategy_type or '').strip().upper()}_{sig_underlying}"
        direction_u = str(direction or "").strip().upper()

        for pos in pos_snapshot.positions.values():
            pos_underlying = self._base_underlying(pos.instrument)
            if pos_underlying != sig_underlying:
                continue
            same_underlying_count += 1
            if str(pos.direction or "").strip().upper() == direction_u:
                same_side_count += 1
            if (
                str(pos.direction or "").strip().upper() == direction_u
                and str(pos.strategy_tag or "").strip().upper().startswith(strategy_prefix)
            ):
                same_strategy_side_count += 1

        if same_underlying_count >= int(self.config.sizing.max_per_underlying or 1):
            return False, f"Max positions per underlying reached ({same_underlying_count}/{self.config.sizing.max_per_underlying})"

        if not self._allow_pyramiding and same_side_count > 0:
            return False, f"Open same-side position exists for {sig_underlying}"

        if same_strategy_side_count > 0:
            return False, f"Existing {strategy_type} {direction_u} position already active for {sig_underlying}"

        return True, "Position gate allows signal"

    def _get_free_margin(self) -> float:
        """Get current free margin percentage. Calls fetcher if available."""
        if self._margin_fetcher:
            try:
                return self._margin_fetcher()
            except Exception as e:
                self.logger.warning("Margin fetch failed: %s, using 1.0 (100%%)", e)
                return 1.0
        return 1.0  # assume 100% available if no fetcher

    @staticmethod
    def _format_float(value: Any, digits: int = 2) -> str:
        try:
            num = float(value)
            return f"{num:.{digits}f}"
        except (TypeError, ValueError):
            return "NA"

    def _build_entry_reason(
        self,
        signal: Dict[str, Any],
        sizing: SizingResult,
        regime: RegimeSnapshot,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Compose a human-readable and structured reason for why an entry was taken.
        """
        router_reason = str(signal.get("router_reason", "") or "").strip()
        signal_reason = str(signal.get("reason", "") or "").strip()
        pattern = str(signal.get("pattern", "") or "").strip()
        strategy_type = str(signal.get("strategy_type", "") or "").strip().upper()
        signal_direction = str(signal.get("signal_direction", "") or signal.get("direction", "")).strip().upper()

        chain_tail = sizing.chain[-1].reason if sizing.chain else ""
        score_bits: List[str] = []
        if signal.get("signal_score") is not None:
            score_bits.append(f"sniper={self._format_float(signal.get('signal_score'), 2)}")
        if signal.get("signal_strength") is not None:
            score_bits.append(f"vol={self._format_float(signal.get('signal_strength'), 2)}")
        if signal.get("rs_score") is not None:
            score_bits.append(f"rs={self._format_float(signal.get('rs_score'), 1)}")

        parts: List[str] = []
        if router_reason:
            parts.append(f"Router: {router_reason}")
        if signal_reason:
            parts.append(f"Signal: {signal_reason}")
        if pattern:
            parts.append(f"Pattern: {pattern}")
        if score_bits:
            parts.append(f"Scores: {', '.join(score_bits)}")
        parts.append(
            f"Regime: {regime.phase.value} (score={regime.score}, vix={self._format_float(regime.vix, 1)})"
        )
        if chain_tail:
            parts.append(f"Sizing: {chain_tail}")
        parts.append(f"Action: {strategy_type} {signal_direction} qty={sizing.final_lots}")

        reason_text = " | ".join(part for part in parts if part).strip()
        if not reason_text:
            reason_text = "Entry approved by router + regime + sizing gates"

        reason_meta: Dict[str, Any] = {
            "router_reason": router_reason,
            "router_action": str(signal.get("router_action", "") or ""),
            "router_source": str(signal.get("router_source", "") or ""),
            "router_priority_score": self._safe_float_for_json(signal.get("router_priority_score")),
            "signal_reason": signal_reason,
            "pattern": pattern,
            "signal_score": self._safe_float_for_json(signal.get("signal_score")),
            "signal_strength": self._safe_float_for_json(signal.get("signal_strength")),
            "rs_score": self._safe_float_for_json(signal.get("rs_score")),
            "signal_direction": signal_direction,
            "strategy_type": strategy_type,
            "regime_phase": regime.phase.value,
            "regime_score": regime.score,
            "regime_vix": self._safe_float_for_json(regime.vix),
            "sizing_final_lots": int(sizing.final_lots),
            "sizing_chain": [s.to_dict() for s in sizing.chain],
        }
        return reason_text, reason_meta

    def _enqueue_order(
        self,
        signal: Dict[str, Any],
        sizing: SizingResult,
        regime: RegimeSnapshot,
    ) -> bool:
        """Build and enqueue ORDER_REQUEST to command table."""
        instrument = signal.get("instrument", "")
        strategy_type = signal.get("strategy_type", "")

        # Build strategy tag
        strategy_tag = f"TOMIC_{strategy_type}_{instrument}"

        # Build idempotency key
        correlation_id = signal.get("correlation_id", "")
        idem_key = f"{strategy_tag}:entry:{int(time.time())}"
        entry_reason, entry_reason_meta = self._build_entry_reason(signal, sizing, regime)

        # Create order request event
        event = OrderRequestEvent(
            correlation_id=correlation_id,
            strategy_id=strategy_tag,
            idempotency_key=idem_key,
            instrument=instrument,
            strategy_type=strategy_type,
            direction=signal.get("direction", ""),
            quantity=sizing.final_lots,
            legs=signal.get("legs", []),
            signal_direction=signal.get("signal_direction", ""),
            option_type=signal.get("option_type", ""),
            entry_price=float(signal.get("entry_price", 0.0) or 0.0),
            stop_price=float(signal.get("stop_price", 0.0) or 0.0),
            target_price=float(signal.get("target_price", 0.0) or 0.0),
            sizing_chain=[s.to_dict() for s in sizing.chain],
            regime_snapshot={
                "phase": regime.phase.value,
                "score": regime.score,
                "vix": regime.vix,
                "version": regime.version,
            },
            strategy_tag=strategy_tag,
            entry_reason=entry_reason,
            entry_reason_meta=entry_reason_meta,
        )

        # Enqueue to command table
        payload = event.model_dump(mode="json")
        cmd_id = self._command_store.enqueue(
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            idempotency_key=event.idempotency_key,
            event_type=EventType.ORDER_REQUEST.value,
            source_agent="risk_agent",
            payload=payload,
        )

        if cmd_id:
            self._total_trades += 1
            self.logger.info(
                "ORDER_REQUEST enqueued: cmd=%s %s %s qty=%d",
                cmd_id, instrument, strategy_type, sizing.final_lots,
            )
            return True
        else:
            self.logger.warning(
                "Duplicate ORDER_REQUEST skipped (idempotency): %s", idem_key,
            )
            return False

    # -------------------------------------------------------------------
    # Black Swan Hedge
    # -------------------------------------------------------------------

    def compute_hedge_budget(self) -> float:
        """Calculate current Black Swan hedge budget."""
        return compute_black_swan_budget(
            total_trades=self._total_trades,
            monthly_profit_estimate=self._monthly_profit_estimate,
            params=self.config.black_swan,
        )
