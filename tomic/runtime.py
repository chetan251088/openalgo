"""
TOMIC Runtime Bootstrap
=======================
Assembles core TOMIC components behind a single runtime interface used by
the Flask blueprint (`blueprints/tomic.py`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import nullcontext, suppress
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tomic.agents.execution_agent import ExecutionAgent
from tomic.agents.journaling_agent import JournalingAgent
from tomic.agents.regime_agent import AtomicRegimeState, RegimeAgent
from tomic.agents.risk_agent import RiskAgent
from tomic.agents.sniper_agent import SniperAgent
from tomic.agents.volatility_agent import VolatilityAgent
from tomic.circuit_breakers import CircuitBreakerEngine
from tomic.command_store import CommandStore
from tomic.config import TomicConfig
from tomic.conflict_router import ConflictRouter
from tomic.events import AlertEvent, AlertLevel
from tomic.event_bus import EventPublisher
from tomic.freshness import FreshnessTracker
from tomic.market_bridge import TomicMarketBridge
from tomic.position_book import PositionBook
from tomic.supervisor import Supervisor
from tomic.ws_data_manager import WSDataManager

logger = logging.getLogger(__name__)


class TomicRuntime:
    """
    Runtime coordinator for TOMIC components.

    Public API expected by blueprint:
      - start()
      - stop()
      - kill_switch(reason)
      - resume()
      - get_status()

    Public attributes expected by blueprint:
      - position_book
      - journaling_agent
      - circuit_breakers
      - freshness_tracker
      - ws_data_manager
    """

    def __init__(self, config: Optional[TomicConfig] = None, zmq_port: Optional[int] = None):
        self.config = self._with_endpoint_fallbacks(config or TomicConfig.load())
        self._zmq_port = self._resolve_zmq_port(zmq_port)
        self._capital = self._resolve_capital()
        self._lock = threading.Lock()
        self._quality_lock = threading.Lock()
        self._signal_control_lock = threading.Lock()
        self._started = False
        self._last_signal_quality: Dict[str, Any] = {}

        self._signal_loop_enabled = self._resolve_bool_env("TOMIC_SIGNAL_LOOP_ENABLED", default=True)
        self._signal_loop_interval_s = self._resolve_float_env("TOMIC_SIGNAL_LOOP_INTERVAL_S", default=5.0)
        self._signal_enqueue_cooldown_s = self._resolve_float_env("TOMIC_SIGNAL_ENQUEUE_COOLDOWN_S", default=20.0)
        self._startup_enqueue_grace_s = self._resolve_non_negative_float_env(
            "TOMIC_STARTUP_ENQUEUE_GRACE_S",
            default=20.0,
        )
        self._require_live_tick_after_start = self._resolve_bool_env(
            "TOMIC_REQUIRE_LIVE_TICK_AFTER_START",
            default=True,
        )
        self._reject_pending_on_start = self._resolve_bool_env(
            "TOMIC_REJECT_PENDING_ON_START",
            default=True,
        )
        self._reject_alert_threshold = int(self._resolve_float_env("TOMIC_SIGNAL_REJECT_ALERT_THRESHOLD", default=5.0))
        self._feed_stale_alert_after_s = self._resolve_float_env("TOMIC_FEED_STALE_ALERT_AFTER_S", default=15.0)
        self._alert_cooldown_s = self._resolve_float_env("TOMIC_ALERT_COOLDOWN_S", default=60.0)
        self._telegram_alerts_enabled = self._resolve_bool_env("TOMIC_TELEGRAM_ALERTS", default=True)
        self._enforce_market_hours = self._resolve_bool_env("TOMIC_ENFORCE_MARKET_HOURS", default=True)
        self._allow_offhours_scan = self._resolve_bool_env("TOMIC_ALLOW_OFFHOURS_SCAN", default=False)
        self._market_tz_name = self._resolve_text_env("TOMIC_MARKET_TZ", default="Asia/Kolkata")
        self._market_tz = self._resolve_timezone(self._market_tz_name)
        self._market_open_hh, self._market_open_mm, self._market_open_label = self._resolve_hhmm_env(
            "TOMIC_MARKET_OPEN",
            "09:15",
        )
        self._market_close_hh, self._market_close_mm, self._market_close_label = self._resolve_hhmm_env(
            "TOMIC_MARKET_CLOSE",
            "15:30",
        )
        self._warm_start_enabled = self._resolve_bool_env("TOMIC_WARM_START_ENABLED", default=True)
        self._warm_start_interval = self._resolve_text_env("TOMIC_WARM_START_INTERVAL", default="1m")
        self._warm_start_lookback_days = self._resolve_int_env("TOMIC_WARM_START_LOOKBACK_DAYS", default=3)
        self._warm_start_max_bars = self._resolve_int_env("TOMIC_WARM_START_MAX_BARS", default=180)
        self._warm_start_source = self._resolve_text_env("TOMIC_WARM_START_SOURCE", default="auto").lower()
        self._warm_start_status: Dict[str, Any] = {
            "enabled": self._warm_start_enabled,
            "status": "idle",
            "message": "",
            "loaded_symbols": 0,
            "attempted_symbols": 0,
            "regime_bars_seeded": 0,
            "sniper_bars_seeded": 0,
            "vol_price_bars_seeded": 0,
            "sources": {},
            "errors": {},
            "lookback_days": self._warm_start_lookback_days,
            "interval": self._warm_start_interval,
            "max_bars": self._warm_start_max_bars,
        }

        self._signal_loop_thread: Optional[threading.Thread] = None
        self._signal_loop_stop = threading.Event()
        self._signal_cycle_count = 0
        self._last_signal_cycle_wall = 0.0
        self._last_signal_cycle_error = ""
        self._last_signal_enqueue_count = 0
        self._last_signal_dedupe_skips = 0

        self._signal_recent_enqueues: Dict[str, float] = {}
        self._reject_streaks: Dict[str, int] = {}
        self._last_dead_letter_count = 0
        self._runtime_started_wall = 0.0
        self._runtime_started_mono = 0.0
        self._feed_disconnected_alert_active = False
        self._feed_stale_alert_active = False
        self._kill_switch_alert_active = False
        self._alert_last_sent: Dict[str, float] = {}

        # Core stateful stores/services
        self._publisher = EventPublisher(port=self._zmq_port)
        self.command_store = CommandStore(
            lease_timeout=self.config.supervisor.lease_timeout,
        )
        self.command_store.initialize()

        self.position_book = PositionBook()
        self.freshness_tracker = FreshnessTracker(self.config.freshness)
        self.circuit_breakers = CircuitBreakerEngine(
            thresholds=self.config.circuit_breakers,
            capital=self._capital,
        )
        self.ws_data_manager = WSDataManager(
            config=self.config,
            freshness_tracker=self.freshness_tracker,
        )

        # Agent graph
        self.regime_state = AtomicRegimeState()
        self.regime_agent = RegimeAgent(
            config=self.config,
            publisher=self._publisher,
            regime_state=self.regime_state,
        )
        self.sniper_agent = SniperAgent(
            config=self.config,
            regime_state=self.regime_state,
        )
        self.volatility_agent = VolatilityAgent(
            config=self.config,
            regime_state=self.regime_state,
        )
        self.conflict_router = ConflictRouter(
            config=self.config,
            regime_state=self.regime_state,
        )
        self.market_bridge = TomicMarketBridge(
            config=self.config,
            ws_data_manager=self.ws_data_manager,
            freshness_tracker=self.freshness_tracker,
            regime_agent=self.regime_agent,
            sniper_agent=self.sniper_agent,
            volatility_agent=self.volatility_agent,
        )
        self.risk_agent = RiskAgent(
            config=self.config,
            publisher=self._publisher,
            command_store=self.command_store,
            position_book=self.position_book,
            regime_state=self.regime_state,
            capital=self._capital,
        )
        self.execution_agent = ExecutionAgent(
            config=self.config,
            publisher=self._publisher,
            command_store=self.command_store,
            position_book=self.position_book,
            freshness_tracker=self.freshness_tracker,
            circuit_breakers=self.circuit_breakers,
            ws_data_manager=self.ws_data_manager,
        )
        self.journaling_agent = JournalingAgent(
            config=self.config,
            publisher=self._publisher,
            command_store=self.command_store,
            position_book=self.position_book,
        )

        self.supervisor = Supervisor(
            config=self.config,
            command_store=self.command_store,
            position_book=self.position_book,
            circuit_breakers=self.circuit_breakers,
            publisher=self._publisher,
            kill_callback=self._make_kill_callback(),
        )
        self.supervisor.register_agent("regime_agent", self.regime_agent)
        self.supervisor.register_agent("risk_agent", self.risk_agent)
        self.supervisor.register_agent("execution", self.execution_agent)
        self.supervisor.register_agent("journaling", self.journaling_agent)

    def start(self) -> None:
        """Start TOMIC runtime components."""
        with self._lock:
            if self._started:
                return

            started_stoppers: List[Callable[[], None]] = []
            try:
                self._runtime_started_wall = time.time()
                self._runtime_started_mono = time.monotonic()
                self._signal_recent_enqueues.clear()
                if self._reject_pending_on_start:
                    rejected = self.command_store.reject_all_pending(
                        reason="Runtime start reset: dropping stale queued commands"
                    )
                    if rejected > 0:
                        logger.warning("TOMIC start reset rejected %d stale queued commands", rejected)

                self._publisher.start()
                started_stoppers.append(self._publisher.stop)

                self._prime_market_state_from_history()

                self.regime_agent.start()
                started_stoppers.append(self.regime_agent.stop)

                self.risk_agent.start()
                started_stoppers.append(self.risk_agent.stop)

                self.execution_agent.start()
                started_stoppers.append(self.execution_agent.stop)

                self.journaling_agent.start()
                started_stoppers.append(self.journaling_agent.stop)

                # Start WS manager only when at least one WS endpoint is configured.
                if self.config.endpoints.feed_primary_ws or self.config.endpoints.feed_fallback_ws:
                    self.market_bridge.start()
                    started_stoppers.append(self.market_bridge.stop)

                    self.ws_data_manager.start()
                    started_stoppers.append(self.ws_data_manager.stop)
                else:
                    logger.info("TOMIC market data not started (no feed endpoint configured)")

                self.supervisor.start(zmq_port=self._zmq_port)
                started_stoppers.append(self.supervisor.stop)

                self._started = True
                if self._signal_loop_enabled:
                    self._start_signal_loop()
                    started_stoppers.append(self._stop_signal_loop)
                else:
                    logger.info("TOMIC signal loop disabled (TOMIC_SIGNAL_LOOP_ENABLED=false)")

                self._last_dead_letter_count = self.command_store.count_dead_letters()
                logger.info("TOMIC runtime started (zmq_port=%d)", self._zmq_port)
            except Exception:
                for stop_fn in reversed(started_stoppers):
                    with suppress(Exception):
                        stop_fn()
                self._started = False
                raise

    def stop(self) -> None:
        """Stop TOMIC runtime components."""
        with self._lock:
            if not self._started:
                return

            with suppress(Exception):
                self._stop_signal_loop()
            with suppress(Exception):
                self.supervisor.stop()
            with suppress(Exception):
                self.market_bridge.stop()
            with suppress(Exception):
                self.ws_data_manager.stop()
            with suppress(Exception):
                self.journaling_agent.stop()
            with suppress(Exception):
                self.execution_agent.stop()
            with suppress(Exception):
                self.risk_agent.stop()
            with suppress(Exception):
                self.regime_agent.stop()
            with suppress(Exception):
                self._publisher.stop()

            self._started = False
            logger.info("TOMIC runtime stopped")

    def kill_switch(self, reason: str) -> None:
        """Trigger supervisor kill switch."""
        self.supervisor.kill_switch(reason)

    def resume(self) -> None:
        """Resume from paused/kill-switch state."""
        if not self._started:
            raise RuntimeError("TOMIC runtime is not running")
        self.supervisor.resume()

    def get_status(self) -> dict:
        """Expose supervisor status payload plus feed/bridge diagnostics."""
        status = self.supervisor.get_status()
        status["ws_data"] = self.ws_data_manager.get_status() if self.ws_data_manager else {}
        status["market_bridge"] = self.market_bridge.get_status() if self.market_bridge else {}
        status["warm_start"] = dict(self._warm_start_status)
        status["signal_loop"] = {
            "enabled": self._signal_loop_enabled,
            "running": bool(self._signal_loop_thread and self._signal_loop_thread.is_alive()),
            "interval_s": self._signal_loop_interval_s,
            "enqueue_cooldown_s": self._signal_enqueue_cooldown_s,
            "cycles": self._signal_cycle_count,
            "last_cycle_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_signal_cycle_wall))
                if self._last_signal_cycle_wall > 0 else ""
            ),
            "last_cycle_age_s": (
                round(max(0.0, time.time() - self._last_signal_cycle_wall), 2)
                if self._last_signal_cycle_wall > 0 else -1.0
            ),
            "last_enqueued": self._last_signal_enqueue_count,
            "last_dedupe_skips": self._last_signal_dedupe_skips,
            "last_error": self._last_signal_cycle_error,
        }
        with self._quality_lock:
            cached = self._last_signal_quality.copy() if self._last_signal_quality else {}
        if cached:
            status["signal_quality_cached_at"] = cached.get("generated_at")
            status["signal_quality_cached_age_s"] = round(
                max(0.0, time.time() - float(cached.get("timestamp_epoch", time.time()))), 2
            )
        return status

    def get_signal_quality(self, run_scan: bool = True) -> Dict[str, Any]:
        """
        Produce a quality snapshot from live WS-fed agent state.

        When run_scan=True:
          - runs Sniper + Volatility scans
          - routes through ConflictRouter
          - returns scored summary with top candidates.

        When run_scan=False:
          - returns the latest cached snapshot if available.
        """
        if not run_scan:
            with self._quality_lock:
                if self._last_signal_quality:
                    cached = self._last_signal_quality.copy()
                    cached["cached"] = True
                    cached["cached_age_s"] = round(
                        max(0.0, time.time() - float(cached.get("timestamp_epoch", time.time()))), 2
                    )
                    return cached
            return {
                "generated_at": "",
                "timestamp_epoch": 0.0,
                "runtime_started": self._started,
                "cached": True,
                "cached_age_s": -1.0,
                "message": "No cached signal-quality snapshot yet. Run with run_scan=true first.",
                "signals": {
                    "sniper_count": 0,
                    "volatility_count": 0,
                    "routed_count": 0,
                    "decision_breakdown": {"ACCEPT": 0, "REJECT": 0, "DEFER": 0, "MERGE": 0},
                },
            }

        snapshot = self._compute_signal_quality_snapshot(enqueue_signals=False, source="api")
        with self._quality_lock:
            self._last_signal_quality = snapshot.copy()
        return snapshot

    def _start_signal_loop(self) -> None:
        with self._signal_control_lock:
            if self._signal_loop_thread and self._signal_loop_thread.is_alive():
                return
            self._signal_loop_stop.clear()
            self._signal_loop_thread = threading.Thread(
                target=self._signal_loop,
                daemon=True,
                name="tomic-signal-loop",
            )
            self._signal_loop_thread.start()
            logger.info(
                "TOMIC signal loop started (interval=%.1fs, cooldown=%.1fs)",
                self._signal_loop_interval_s,
                self._signal_enqueue_cooldown_s,
            )

    def _stop_signal_loop(self) -> None:
        with self._signal_control_lock:
            self._signal_loop_stop.set()
            if self._signal_loop_thread:
                self._signal_loop_thread.join(timeout=max(5.0, self._signal_loop_interval_s + 1.0))
            self._signal_loop_thread = None
            logger.info("TOMIC signal loop stopped")

    def _signal_loop(self) -> None:
        while not self._signal_loop_stop.is_set():
            cycle_started = time.monotonic()
            try:
                snapshot = self._compute_signal_quality_snapshot(
                    enqueue_signals=True,
                    source="loop",
                )
                with self._quality_lock:
                    self._last_signal_quality = snapshot.copy()
                self._last_signal_cycle_error = ""
            except Exception as exc:
                self._last_signal_cycle_error = str(exc)
                logger.error("TOMIC signal loop cycle failed: %s", exc, exc_info=True)
                self._emit_alert(
                    AlertLevel.RISK,
                    f"TOMIC signal loop error: {exc}",
                    dedupe_key="signal_loop_error",
                )

            try:
                self._run_operational_alert_checks()
            except Exception as exc:
                logger.debug("TOMIC operational alert checks failed: %s", exc)

            self._signal_cycle_count += 1
            self._last_signal_cycle_wall = time.time()

            elapsed = time.monotonic() - cycle_started
            wait_s = max(0.1, self._signal_loop_interval_s - elapsed)
            self._signal_loop_stop.wait(wait_s)

    def _compute_signal_quality_snapshot(self, enqueue_signals: bool, source: str) -> Dict[str, Any]:
        bridge_lock = self.market_bridge.lock if self.market_bridge else nullcontext()
        with bridge_lock:
            market_open, market_reason, market_meta = self._market_session_state()
            regime = self.regime_state.read_snapshot()
            scan_enabled = bool(market_open or self._allow_offhours_scan)
            sniper_signals = list(self.sniper_agent.scan()) if scan_enabled else []
            vol_signals = list(self.volatility_agent.scan()) if scan_enabled else []

            position_snapshot = self.position_book.read_snapshot()
            self.conflict_router.update_position_count(position_snapshot.total_positions)
            routed_signals = self.conflict_router.route(sniper_signals, vol_signals) if scan_enabled else []
            decisions = list(self.conflict_router.decisions) if scan_enabled else []
            router_diag = self.conflict_router.diagnostics(limit=30) if self.conflict_router else {}
            risk_diag = (
                self.risk_agent.get_telemetry_summary(limit=30)
                if hasattr(self.risk_agent, "get_telemetry_summary")
                else {}
            )
            ws_status = self.ws_data_manager.get_status() if self.ws_data_manager else {}
            bridge_status = self.market_bridge.get_status() if self.market_bridge else {}

            startup_guard_reasons: List[str] = []
            startup_elapsed_s = (
                max(0.0, time.monotonic() - self._runtime_started_mono)
                if self._runtime_started_mono > 0
                else 0.0
            )
            last_tick_wall = float(bridge_status.get("last_tick_wall", 0.0) or 0.0)
            live_tick_since_start = (
                last_tick_wall > 0 and self._runtime_started_wall > 0 and last_tick_wall >= self._runtime_started_wall
            )
            if enqueue_signals:
                if self._startup_enqueue_grace_s > 0 and startup_elapsed_s < self._startup_enqueue_grace_s:
                    startup_guard_reasons.append(
                        f"Startup guard active ({startup_elapsed_s:.1f}/{self._startup_enqueue_grace_s:.0f}s)"
                    )
                if self._require_live_tick_after_start and not live_tick_since_start:
                    startup_guard_reasons.append("Waiting for first live tick after Start")
            if enqueue_signals and not market_open and market_reason:
                startup_guard_reasons.append(market_reason)

            enqueue_result = {
                "enqueued_count": 0,
                "dedupe_skipped_count": 0,
                "enqueued_keys": [],
                "dedupe_keys": [],
            }
            if enqueue_signals and not startup_guard_reasons:
                enqueue_result = self._enqueue_routed_signals(routed_signals, source)
            self._update_reject_streaks(decisions)

            sniper_avg = (
                sum(float(s.signal_score) for s in sniper_signals) / len(sniper_signals)
                if sniper_signals else 0.0
            )
            vol_avg = (
                sum(float(s.signal_strength) for s in vol_signals) / len(vol_signals)
                if vol_signals else 0.0
            )
            routed_avg = (
                sum(float(s.priority_score) for s in routed_signals) / len(routed_signals)
                if routed_signals else 0.0
            )

            decision_breakdown = {"ACCEPT": 0, "REJECT": 0, "DEFER": 0, "MERGE": 0}
            for decision in decisions:
                key = str(decision.action.value).upper()
                decision_breakdown[key] = decision_breakdown.get(key, 0) + 1

            accept_rate = (
                (decision_breakdown.get("ACCEPT", 0) / len(decisions)) * 100.0
                if decisions else 0.0
            )

            ws_connected = bool(ws_status.get("connected", False))
            ws_authenticated = bool(ws_status.get("authenticated", False))
            ws_requires_auth = bool(ws_status.get("api_key_configured", False))

            no_action_reasons: List[str] = list(startup_guard_reasons)
            if not scan_enabled:
                no_action_reasons.append("Signal scan paused: market session closed")
            if not market_open and market_reason and market_reason not in no_action_reasons:
                no_action_reasons.append(market_reason)
            if not ws_connected:
                no_action_reasons.append("WS feed disconnected")
            elif ws_requires_auth and not ws_authenticated:
                auth_status = str(ws_status.get("last_auth_status", "")).strip().lower()
                auth_msg = str(ws_status.get("last_auth_message", "")).strip()
                if auth_msg:
                    no_action_reasons.append(f"WS auth failed: {auth_msg}")
                elif auth_status == "pending":
                    no_action_reasons.append("WS auth pending: awaiting proxy response")
                else:
                    no_action_reasons.append("WS authenticated=false while API key is configured")

            if int(bridge_status.get("subscriptions", 0) or 0) <= 0:
                no_action_reasons.append("Market bridge has zero subscriptions")

            sniper_cache = getattr(self.sniper_agent, "_ohlcv_cache", {})
            sniper_series = list(sniper_cache.values()) if isinstance(sniper_cache, dict) else []
            if len(sniper_series) == 0:
                no_action_reasons.append("Sniper has no candle inputs yet")
            else:
                max_sniper_bars = max(len((row or {}).get("C", [])) for row in sniper_series)
                if max_sniper_bars < 30:
                    no_action_reasons.append(
                        f"Sniper warmup in progress ({max_sniper_bars}/30 bars)"
                    )

            vol_price_cache = getattr(self.volatility_agent, "_price_cache", {})
            vol_iv_cache = getattr(self.volatility_agent, "_iv_cache", {})
            if len(vol_price_cache) == 0:
                no_action_reasons.append("Volatility has no underlying price candles yet")
            if len(vol_iv_cache) == 0:
                option_symbols = bridge_status.get("option_symbols", [])
                option_mode = str(bridge_status.get("option_symbol_mode", "manual")).strip().lower()
                option_last_error = str(bridge_status.get("auto_option_last_error", "")).strip()
                if not option_symbols:
                    if option_mode == "auto":
                        no_action_reasons.append(
                            "Volatility has no IV inputs (auto option discovery has not subscribed symbols yet)"
                        )
                        if option_last_error:
                            no_action_reasons.append(
                                f"Auto option discovery: {option_last_error}"
                            )
                    else:
                        no_action_reasons.append(
                            "Volatility has no IV inputs (TOMIC_FEED_OPTION_SYMBOLS is empty)"
                        )
                elif int(bridge_status.get("option_ticks", 0) or 0) <= 0:
                    no_action_reasons.append(
                        "Volatility has no IV inputs (no option ticks received yet)"
                    )
                elif int(bridge_status.get("vol_iv_updates", 0) or 0) <= 0:
                    no_action_reasons.append(
                        "Option ticks received but IV field missing in feed payload"
                    )

            if scan_enabled and len(sniper_signals) == 0:
                no_action_reasons.append("Sniper produced no pattern signals this cycle")
            if scan_enabled and len(vol_signals) == 0:
                no_action_reasons.append("Volatility produced no options strategy signals this cycle")

            blocking_reasons = router_diag.get("blocking_reasons", {}) if isinstance(router_diag, dict) else {}
            for reason, count in list(blocking_reasons.items())[:5]:
                no_action_reasons.append(f"Router {count}x block: {reason}")

            risk_recent = risk_diag.get("recent_evaluations", []) if isinstance(risk_diag, dict) else []
            if isinstance(risk_recent, list) and risk_recent:
                latest_risk = risk_recent[0]
                latest_result = str(latest_risk.get("result", "")).strip()
                latest_reason = str(latest_risk.get("reason", "")).strip()
                if latest_result and latest_result != "enqueued":
                    no_action_reasons.append(
                        f"Latest risk result={latest_result}" + (f" ({latest_reason})" if latest_reason else "")
                    )

            now_epoch = time.time()
            snapshot: Dict[str, Any] = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch)),
                "timestamp_epoch": now_epoch,
                "runtime_started": self._started,
                "source": source,
                "startup_guard": {
                    "grace_s": self._startup_enqueue_grace_s,
                    "elapsed_s": round(startup_elapsed_s, 2),
                    "require_live_tick_after_start": self._require_live_tick_after_start,
                    "live_tick_since_start": bool(live_tick_since_start),
                    "runtime_started_at": (
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._runtime_started_wall))
                        if self._runtime_started_wall > 0
                        else ""
                    ),
                },
                "market_session": market_meta,
                "regime": {
                    "phase": regime.phase.value,
                    "score": regime.score,
                    "vix": regime.vix,
                    "vix_flags": list(regime.vix_flags),
                    "version": regime.version,
                },
                "feed": {
                    "ws": ws_status,
                    "bridge": bridge_status,
                },
                "coverage": {
                    "sniper_instruments": len(getattr(self.sniper_agent, "_ohlcv_cache", {})),
                    "vol_price_underlyings": len(getattr(self.volatility_agent, "_price_cache", {})),
                    "vol_iv_underlyings": len(getattr(self.volatility_agent, "_iv_cache", {})),
                    "open_positions": position_snapshot.total_positions,
                },
                "router": router_diag,
                "risk": risk_diag,
                "agent_inputs": {
                    "sniper_readiness": [
                        {
                            "instrument": inst,
                            "bars": len((data or {}).get("C", [])),
                            "ready_30": len((data or {}).get("C", [])) >= 30,
                            "zones_cached": len(getattr(self.sniper_agent, "_sd_zones", {}).get(inst, [])),
                        }
                        for inst, data in list(getattr(self.sniper_agent, "_ohlcv_cache", {}).items())[:10]
                    ],
                    "volatility_readiness": [
                        {
                            "underlying": key,
                            "price_bars": len(getattr(self.volatility_agent, "_price_cache", {}).get(key, [])),
                            "has_iv": key in getattr(self.volatility_agent, "_iv_cache", {}),
                            "hv_ready_31": len(getattr(self.volatility_agent, "_price_cache", {}).get(key, [])) >= 31,
                        }
                        for key in list(
                            set(getattr(self.volatility_agent, "_price_cache", {}).keys())
                            | set(getattr(self.volatility_agent, "_iv_cache", {}).keys())
                        )[:10]
                    ],
                    "volatility_snapshots": [
                        {
                            "underlying": snap.underlying,
                            "iv": round(float(snap.iv), 4),
                            "hv": round(float(snap.hv), 4),
                            "iv_rank": round(float(snap.iv_rank), 2),
                            "iv_hv_ratio": round(float(snap.iv_hv_ratio), 3),
                            "vol_regime": snap.vol_regime.value,
                            "skew_state": snap.skew_state.value,
                            "term_structure": snap.term_structure.value,
                        }
                        for snap in list(getattr(self.volatility_agent, "_snapshots", {}).values())[:10]
                    ],
                },
                "diagnostics": {
                    "no_action_reasons": no_action_reasons[:12],
                },
                "signals": {
                    "sniper_count": len(sniper_signals),
                    "volatility_count": len(vol_signals),
                    "routed_count": len(routed_signals),
                    "sniper_avg_score": round(sniper_avg, 2),
                    "volatility_avg_strength": round(vol_avg, 2),
                    "routed_avg_priority": round(routed_avg, 2),
                    "routed_accept_rate_pct": round(accept_rate, 2),
                    "decision_breakdown": decision_breakdown,
                    "enqueued_count": int(enqueue_result["enqueued_count"]),
                    "dedupe_skipped_count": int(enqueue_result["dedupe_skipped_count"]),
                    "enqueued_keys": list(enqueue_result["enqueued_keys"]),
                    "dedupe_keys": list(enqueue_result["dedupe_keys"]),
                    "top_sniper": [
                        {
                            "instrument": sig.instrument,
                            "direction": sig.direction,
                            "pattern": sig.pattern.value,
                            "entry_price": sig.entry_price,
                            "stop_price": sig.stop_price,
                            "score": round(float(sig.signal_score), 2),
                        }
                        for sig in sorted(sniper_signals, key=lambda x: x.signal_score, reverse=True)[:5]
                    ],
                    "top_volatility": [
                        {
                            "underlying": sig.underlying,
                            "strategy_type": sig.strategy_type.value,
                            "direction": sig.direction,
                            "strength": round(float(sig.signal_strength), 2),
                            "reason": sig.reason,
                        }
                        for sig in sorted(vol_signals, key=lambda x: x.signal_strength, reverse=True)[:5]
                    ],
                    "top_routed": [
                        {
                            "source": routed.source.value,
                            "instrument": routed.signal_dict.get("instrument", ""),
                            "strategy_type": routed.signal_dict.get("strategy_type", ""),
                            "direction": routed.signal_dict.get("direction", ""),
                            "priority_score": round(float(routed.priority_score), 2),
                        }
                        for routed in routed_signals[:5]
                    ],
                    "router_decisions": [
                        {
                            "source": str(getattr(decision.source, "value", decision.source)),
                            "instrument": str(getattr(decision, "instrument", "")),
                            "strategy_type": str(getattr(decision, "strategy_type", "")),
                            "action": str(getattr(decision.action, "value", decision.action)),
                            "reason": str(getattr(decision, "reason", "")),
                            "priority_score": round(float(getattr(decision, "priority_score", 0.0) or 0.0), 2),
                        }
                        for decision in decisions[:20]
                    ],
                },
            }

        self._last_signal_enqueue_count = int(enqueue_result["enqueued_count"])
        self._last_signal_dedupe_skips = int(enqueue_result["dedupe_skipped_count"])
        return snapshot

    def _enqueue_routed_signals(self, routed_signals: List[Any], source: str) -> Dict[str, Any]:
        now_mono = time.monotonic()
        enqueued_keys: List[str] = []
        dedupe_keys: List[str] = []

        stale_keys = [
            key for key, ts in self._signal_recent_enqueues.items()
            if now_mono - ts > (self._signal_enqueue_cooldown_s * 25.0)
        ]
        for key in stale_keys:
            self._signal_recent_enqueues.pop(key, None)

        for idx, routed in enumerate(routed_signals):
            signal = dict(routed.signal_dict or {})
            instrument = str(signal.get("instrument", "")).strip().upper()
            strategy_type = str(signal.get("strategy_type", "")).strip().upper()
            direction = str(signal.get("direction", "")).strip().upper()
            if not instrument or not strategy_type:
                continue

            key = f"{instrument}:{strategy_type}:{direction or 'NA'}"
            last = self._signal_recent_enqueues.get(key, 0.0)
            if now_mono - last < self._signal_enqueue_cooldown_s:
                dedupe_keys.append(key)
                continue

            signal.setdefault("correlation_id", f"{source}:{instrument}:{int(time.time() * 1000)}:{idx}")
            route_decision = getattr(routed, "route_decision", None)
            if route_decision is not None:
                signal.setdefault("router_reason", str(getattr(route_decision, "reason", "") or ""))
                signal.setdefault(
                    "router_action",
                    str(getattr(getattr(route_decision, "action", None), "value", "") or ""),
                )
                signal.setdefault(
                    "router_source",
                    str(getattr(getattr(route_decision, "source", None), "value", "") or ""),
                )
            signal.setdefault(
                "router_priority_score",
                float(getattr(routed, "priority_score", 0.0) or 0.0),
            )
            self.risk_agent.enqueue_signal(signal)
            self._signal_recent_enqueues[key] = now_mono
            enqueued_keys.append(key)

        return {
            "enqueued_count": len(enqueued_keys),
            "dedupe_skipped_count": len(dedupe_keys),
            "enqueued_keys": enqueued_keys,
            "dedupe_keys": dedupe_keys,
        }

    def _update_reject_streaks(self, decisions: List[Any]) -> None:
        if self._reject_alert_threshold <= 0:
            return

        touched_instruments = set()
        for decision in decisions:
            instrument = str(getattr(decision, "instrument", "")).upper()
            if instrument:
                touched_instruments.add(instrument)

            action = str(getattr(decision.action, "value", decision.action)).upper()
            if action != "REJECT":
                continue

            reason = str(getattr(decision, "reason", "")).strip() or "unknown"
            reject_key = f"{instrument}|{reason}"
            count = self._reject_streaks.get(reject_key, 0) + 1
            self._reject_streaks[reject_key] = count

            if count in {self._reject_alert_threshold, self._reject_alert_threshold * 2}:
                self._emit_alert(
                    AlertLevel.RISK,
                    f"Repeated signal rejects ({count}) for {instrument or 'UNKNOWN'}: {reason}",
                    dedupe_key=f"reject:{reject_key}:{count}",
                    cooldown_s=self._alert_cooldown_s,
                )

        for instrument in touched_instruments:
            has_reject = any(
                str(getattr(decision, "instrument", "")).upper() == instrument
                and str(getattr(decision.action, "value", decision.action)).upper() == "REJECT"
                for decision in decisions
            )
            if has_reject:
                continue
            prefix = f"{instrument}|"
            stale_keys = [key for key in self._reject_streaks if key.startswith(prefix)]
            for key in stale_keys:
                self._reject_streaks.pop(key, None)

    def _run_operational_alert_checks(self) -> None:
        if not self._started:
            return

        ws_status = self.ws_data_manager.get_status() if self.ws_data_manager else {}
        bridge_status = self.market_bridge.get_status() if self.market_bridge else {}
        supervisor_status = self.supervisor.get_status() if self.supervisor else {}

        subscriptions = int(bridge_status.get("subscriptions", 0) or 0)
        ws_connected = bool(ws_status.get("connected", False))
        ws_authenticated = bool(ws_status.get("authenticated", False))
        ws_healthy = ws_connected and (ws_authenticated or not ws_status.get("api_key_configured", False))

        if subscriptions > 0 and not ws_healthy:
            if not self._feed_disconnected_alert_active:
                self._feed_disconnected_alert_active = True
                self._emit_alert(
                    AlertLevel.RISK,
                    "Market data feed disconnected or not authenticated",
                    dedupe_key="feed_disconnected",
                )
        elif self._feed_disconnected_alert_active:
            self._feed_disconnected_alert_active = False
            self._emit_alert(
                AlertLevel.INFO,
                "Market data feed reconnected",
                dedupe_key="feed_reconnected",
            )

        last_tick_age = float(bridge_status.get("last_tick_age_s", -1.0) or -1.0)
        is_stale = subscriptions > 0 and ws_healthy and last_tick_age >= self._feed_stale_alert_after_s
        if is_stale and not self._feed_stale_alert_active:
            self._feed_stale_alert_active = True
            self._emit_alert(
                AlertLevel.RISK,
                f"Market data stale (last tick age={last_tick_age:.1f}s)",
                dedupe_key="feed_stale",
            )
        elif not is_stale and self._feed_stale_alert_active:
            self._feed_stale_alert_active = False
            self._emit_alert(
                AlertLevel.INFO,
                "Market data staleness recovered",
                dedupe_key="feed_stale_recovered",
            )

        dead_letters = self.command_store.count_dead_letters()
        if dead_letters > self._last_dead_letter_count:
            self._emit_alert(
                AlertLevel.CRITICAL,
                f"Dead-letter commands increased: {dead_letters}",
                dedupe_key=f"dead_letters:{dead_letters}",
                cooldown_s=5.0,
            )
        self._last_dead_letter_count = dead_letters

        killed = bool(supervisor_status.get("killed", False))
        if killed and not self._kill_switch_alert_active:
            self._kill_switch_alert_active = True
            self._emit_alert(
                AlertLevel.CRITICAL,
                "Supervisor kill switch is active",
                dedupe_key="kill_switch_active",
                cooldown_s=5.0,
            )
        elif not killed and self._kill_switch_alert_active:
            self._kill_switch_alert_active = False
            self._emit_alert(
                AlertLevel.INFO,
                "Supervisor kill switch cleared",
                dedupe_key="kill_switch_cleared",
                cooldown_s=5.0,
            )

    def _emit_alert(
        self,
        level: AlertLevel,
        message: str,
        dedupe_key: Optional[str] = None,
        cooldown_s: Optional[float] = None,
    ) -> None:
        key = dedupe_key or f"{level.value}:{message}"
        now = time.monotonic()
        max_age = self._alert_cooldown_s if cooldown_s is None else max(0.0, cooldown_s)
        last = self._alert_last_sent.get(key, 0.0)
        if max_age > 0 and now - last < max_age:
            return
        self._alert_last_sent[key] = now

        tag = f"[{level.value}]"
        if level == AlertLevel.CRITICAL:
            logger.critical("%s %s", tag, message)
        elif level == AlertLevel.RISK:
            logger.warning("%s %s", tag, message)
        else:
            logger.info("%s %s", tag, message)

        with suppress(Exception):
            self._publisher.publish(
                AlertEvent(
                    source_agent="runtime",
                    alert_level=level,
                    message=f"{tag} {message}",
                )
            )

        if self._telegram_alerts_enabled and level in (AlertLevel.CRITICAL, AlertLevel.RISK):
            with suppress(Exception):
                from services.telegram_alert_service import telegram_alert_service

                telegram_alert_service.send_broadcast_alert(f"TOMIC {tag}\n{message}")

    def _make_kill_callback(self) -> Callable[[], None]:
        """Build best-effort cancel-all callback used by kill switch."""

        def _cancel_all_orders() -> None:
            base = (self.config.endpoints.execution_rest or "").rstrip("/")
            if not base:
                logger.warning("Kill callback skipped: execution_rest not configured")
                return

            url = f"{base}/api/v1/cancelallorder"
            headers = {}
            if self.config.endpoints.execution_api_key:
                headers["Authorization"] = f"Bearer {self.config.endpoints.execution_api_key}"

            response = requests.post(url, headers=headers, timeout=5.0)
            response.raise_for_status()
            logger.warning("Kill callback: cancelallorder executed")

        return _cancel_all_orders

    @staticmethod
    def _resolve_bool_env(name: str, default: bool) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _resolve_int_env(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        if value <= 0:
            return default
        return value

    @staticmethod
    def _resolve_text_env(name: str, default: str) -> str:
        raw = os.getenv(name, "").strip()
        return raw or default

    @staticmethod
    def _resolve_float_env(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        if value <= 0:
            return default
        return value

    @staticmethod
    def _resolve_non_negative_float_env(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        if value < 0:
            return default
        return value

    @staticmethod
    def _resolve_timezone(name: str):
        tz_name = str(name or "").strip()
        if not tz_name:
            return timezone(timedelta(hours=5, minutes=30))
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning("TOMIC_MARKET_TZ '%s' not found; falling back to IST fixed offset", tz_name)
            return timezone(timedelta(hours=5, minutes=30))

    @staticmethod
    def _parse_hhmm(value: str, fallback: str) -> tuple[int, int, str]:
        raw = str(value or "").strip()
        if not raw:
            raw = fallback
        try:
            hour_str, minute_str = raw.split(":", 1)
            hour = int(hour_str)
            minute = int(minute_str)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute, f"{hour:02d}:{minute:02d}"
        except Exception:
            pass
        fh, fm = (fallback.split(":", 1) + ["00"])[:2]
        hour = int(fh)
        minute = int(fm)
        return hour, minute, f"{hour:02d}:{minute:02d}"

    def _resolve_hhmm_env(self, name: str, default: str) -> tuple[int, int, str]:
        return self._parse_hhmm(self._resolve_text_env(name, default), default)

    def _market_session_state(self) -> tuple[bool, str, Dict[str, Any]]:
        now_local = datetime.now(self._market_tz)
        weekday = int(now_local.weekday())
        market_open = now_local.replace(
            hour=self._market_open_hh,
            minute=self._market_open_mm,
            second=0,
            microsecond=0,
        )
        market_close = now_local.replace(
            hour=self._market_close_hh,
            minute=self._market_close_mm,
            second=0,
            microsecond=0,
        )
        if market_close >= market_open:
            in_hours = market_open <= now_local <= market_close
        else:
            in_hours = now_local >= market_open or now_local <= market_close

        gate_open = True
        reason = ""
        if self._enforce_market_hours:
            if weekday >= 5:
                gate_open = False
                reason = f"Outside market hours (weekend: {now_local.strftime('%A')})"
            elif not in_hours:
                gate_open = False
                reason = (
                    "Outside market hours "
                    f"({self._market_open_label}-{self._market_close_label} {self._market_tz_name})"
                )

        meta = {
            "enforced": bool(self._enforce_market_hours),
            "open": bool(gate_open),
            "reason": reason,
            "offhours_scan_override": bool(self._allow_offhours_scan),
            "tz": self._market_tz_name,
            "now": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": now_local.strftime("%A"),
            "window": f"{self._market_open_label}-{self._market_close_label}",
        }
        return gate_open, reason, meta

    @staticmethod
    def _resolve_capital() -> float:
        raw = os.getenv("TOMIC_CAPITAL", "1000000").strip()
        try:
            return float(raw)
        except ValueError:
            return 1_000_000.0

    @staticmethod
    def _resolve_zmq_port(override: Optional[int]) -> int:
        if override is not None:
            return int(override)

        configured = os.getenv("TOMIC_ZMQ_PORT", "").strip()
        if configured:
            try:
                return int(configured)
            except ValueError:
                pass

        # Safe default for multi-instance setup:
        # 5000/5001/5002 Flask ports map to 5560/5561/5562.
        try:
            flask_port = int(os.getenv("FLASK_PORT", "5000"))
        except ValueError:
            flask_port = 5000
        return 5560 + max(0, flask_port - 5000)

    def _prime_market_state_from_history(self) -> None:
        base_status: Dict[str, Any] = {
            "enabled": self._warm_start_enabled,
            "status": "disabled" if not self._warm_start_enabled else "running",
            "message": "",
            "loaded_symbols": 0,
            "attempted_symbols": 0,
            "regime_bars_seeded": 0,
            "sniper_bars_seeded": 0,
            "vol_price_bars_seeded": 0,
            "sources": {},
            "errors": {},
            "lookback_days": self._warm_start_lookback_days,
            "interval": self._warm_start_interval,
            "max_bars": self._warm_start_max_bars,
        }

        if not self._warm_start_enabled:
            self._warm_start_status = base_status
            return

        try:
            underlyings = list(getattr(self.market_bridge, "_underlyings", []))
            sniper_symbols = list(getattr(self.market_bridge, "_sniper_symbols", []))
            vix_symbol = getattr(self.market_bridge, "_vix_symbol", None)
            benchmark_key = str(getattr(self.market_bridge, "_benchmark_key", ""))
            vix_key = str(getattr(self.market_bridge, "_vix_key", ""))

            requested_specs: Dict[str, Any] = {}
            for spec in [*underlyings, *sniper_symbols]:
                requested_specs[spec.key] = spec
            if vix_symbol is not None:
                requested_specs[vix_symbol.key] = vix_symbol

            end_date = date.today()
            start_date = end_date - timedelta(days=max(1, self._warm_start_lookback_days))

            history_by_key: Dict[str, List[Dict[str, float]]] = {}
            for key, spec in requested_specs.items():
                base_status["attempted_symbols"] += 1
                candles, source, error = self._fetch_history_candles(
                    symbol=str(spec.symbol),
                    exchange=str(spec.exchange),
                    start_date=start_date,
                    end_date=end_date,
                )
                if candles:
                    history_by_key[key] = candles[-self._warm_start_max_bars:]
                    base_status["loaded_symbols"] += 1
                    if source:
                        base_status["sources"][key] = source
                elif error:
                    base_status["errors"][key] = error

            for spec in underlyings:
                rows = history_by_key.get(spec.key, [])
                for row in rows:
                    high = float(row["high"])
                    low = float(row["low"])
                    close = float(row["close"])
                    volume = float(row["volume"])
                    self.volatility_agent.feed_price(str(spec.symbol), close)
                    base_status["vol_price_bars_seeded"] += 1
                    if spec.key == benchmark_key:
                        self.regime_agent.feed_candle(high=high, low=low, close=close, volume=volume)
                        base_status["regime_bars_seeded"] += 1
                        self.sniper_agent.feed_benchmark(close)

                if spec.key == vix_key and rows:
                    self.regime_agent.feed_vix(float(rows[-1]["close"]))

            if vix_symbol is not None and vix_symbol.key != "":
                vix_rows = history_by_key.get(vix_symbol.key, [])
                if vix_rows:
                    self.regime_agent.feed_vix(float(vix_rows[-1]["close"]))

            for spec in sniper_symbols:
                rows = history_by_key.get(spec.key, [])
                for row in rows:
                    self.sniper_agent.feed_candle(
                        instrument=str(spec.symbol),
                        open_=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                    base_status["sniper_bars_seeded"] += 1

            base_status["status"] = "ok" if base_status["loaded_symbols"] > 0 else "empty"
            if base_status["loaded_symbols"] > 0:
                base_status["message"] = (
                    f"Warm-start loaded {base_status['loaded_symbols']}/{base_status['attempted_symbols']} symbols"
                )
                logger.info(
                    "TOMIC warm-start complete: loaded=%d/%d, regime_bars=%d, sniper_bars=%d, vol_bars=%d",
                    base_status["loaded_symbols"],
                    base_status["attempted_symbols"],
                    base_status["regime_bars_seeded"],
                    base_status["sniper_bars_seeded"],
                    base_status["vol_price_bars_seeded"],
                )
            else:
                base_status["message"] = "Warm-start history unavailable; runtime will warm from live ticks"
                logger.warning("TOMIC warm-start: no historical candles loaded")
        except Exception as exc:
            base_status["status"] = "error"
            base_status["message"] = str(exc)
            logger.warning("TOMIC warm-start failed: %s", exc)

        self._warm_start_status = base_status

    def _fetch_history_candles(
        self,
        symbol: str,
        exchange: str,
        start_date: date,
        end_date: date,
    ) -> tuple[List[Dict[str, float]], str, str]:
        from services.history_service import get_history

        source_pref = self._warm_start_source
        if source_pref == "auto":
            sources = ["db", "api"]
        elif source_pref in {"db", "api"}:
            sources = [source_pref]
        else:
            sources = ["api"]

        errors: List[str] = []
        for source in sources:
            kwargs: Dict[str, Any] = {
                "symbol": symbol,
                "exchange": exchange,
                "interval": self._warm_start_interval,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "source": source,
            }
            if source == "api":
                api_key = (self.config.endpoints.execution_api_key or "").strip()
                if not api_key:
                    errors.append("api: execution_api_key missing")
                    continue
                kwargs["api_key"] = api_key

            success, response, status_code = get_history(**kwargs)
            if not success:
                message = response.get("message", "unknown") if isinstance(response, dict) else str(response)
                errors.append(f"{source}:{status_code}:{message}")
                continue

            rows = response.get("data", []) if isinstance(response, dict) else []
            candles = self._normalize_history_rows(rows)
            if candles:
                return candles, source, ""
            errors.append(f"{source}:{status_code}:empty")

        return [], "", "; ".join(errors)

    @staticmethod
    def _normalize_history_rows(rows: Any) -> List[Dict[str, float]]:
        if not isinstance(rows, list):
            return []

        candles: List[Dict[str, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            open_ = TomicRuntime._to_float(row.get("open"))
            high = TomicRuntime._to_float(row.get("high"))
            low = TomicRuntime._to_float(row.get("low"))
            close = TomicRuntime._to_float(row.get("close"))
            if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
                continue

            ts = TomicRuntime._history_row_timestamp(row)
            if ts <= 0:
                continue

            candles.append(
                {
                    "timestamp": ts,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": max(0.0, TomicRuntime._to_float(row.get("volume"))),
                }
            )

        candles.sort(key=lambda item: item["timestamp"])
        return candles

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _history_row_timestamp(row: Dict[str, Any]) -> float:
        for key in ("timestamp", "time"):
            value = row.get(key)
            if value is None:
                continue
            parsed = TomicRuntime._parse_any_timestamp(value)
            if parsed > 0:
                return parsed

        for key in ("datetime", "date"):
            text = str(row.get(key, "")).strip()
            if not text:
                continue
            parsed = TomicRuntime._parse_any_timestamp(text)
            if parsed > 0:
                return parsed
        return 0.0

    @staticmethod
    def _parse_any_timestamp(value: Any) -> float:
        numeric = TomicRuntime._to_float(value, default=-1.0)
        if numeric > 0:
            return numeric / 1000.0 if numeric > 1e12 else numeric

        text = str(value or "").strip()
        if not text:
            return 0.0

        if text.isdigit():
            raw = float(text)
            return raw / 1000.0 if raw > 1e12 else raw

        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
        return 0.0

    @staticmethod
    def _with_endpoint_fallbacks(config: TomicConfig) -> TomicConfig:
        endpoints = config.endpoints

        feed_primary_ws = (endpoints.feed_primary_ws or "").strip()
        if not feed_primary_ws:
            ws_url = os.getenv("WEBSOCKET_URL", "").strip()
            if ws_url:
                if ws_url.startswith("http://"):
                    feed_primary_ws = "ws://" + ws_url[len("http://"):]
                elif ws_url.startswith("https://"):
                    feed_primary_ws = "wss://" + ws_url[len("https://"):]
                else:
                    feed_primary_ws = ws_url
            else:
                ws_host = os.getenv("WEBSOCKET_HOST", "127.0.0.1").strip() or "127.0.0.1"
                ws_port = os.getenv("WEBSOCKET_PORT", "").strip()
                if not ws_port:
                    try:
                        flask_port = int(os.getenv("FLASK_PORT", "5000"))
                    except ValueError:
                        flask_port = 5000
                    ws_port = str(8765 + max(0, flask_port - 5000))
                feed_primary_ws = f"ws://{ws_host}:{ws_port}"

        feed_fallback_ws = (endpoints.feed_fallback_ws or "").strip()

        execution_rest = (endpoints.execution_rest or "").strip()
        if not execution_rest:
            host_server = os.getenv("HOST_SERVER", "").strip()
            if host_server:
                execution_rest = host_server
            else:
                flask_port = os.getenv("FLASK_PORT", "5000").strip() or "5000"
                execution_rest = f"http://127.0.0.1:{flask_port}"

        execution_api_key = (endpoints.execution_api_key or "").strip()
        if not execution_api_key:
            execution_api_key = (
                os.getenv("OPENALGO_API_KEY", "").strip()
                or os.getenv("API_KEY", "").strip()
            )

        analytics_rest = (endpoints.analytics_rest or "").strip() or execution_rest

        config.endpoints = replace(
            endpoints,
            feed_primary_ws=feed_primary_ws,
            feed_fallback_ws=feed_fallback_ws,
            execution_rest=execution_rest.rstrip("/"),
            execution_api_key=execution_api_key,
            analytics_rest=analytics_rest.rstrip("/"),
        )
        return config
