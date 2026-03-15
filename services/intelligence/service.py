"""
Intelligence Service - Central aggregation hub.
Combines MiroFish predictions, Sector Rotation data, and OpenScreener fundamentals
into a single MarketIntelligence object consumed by TOMIC and the Scalping engine.

Safety features:
- Kill switch: single toggle disables all intelligence gates (falls back to pure technical)
- Eventlet-safe: lock held only during pointer swap, not during HTTP calls
- Per-source error isolation: one source failing doesn't block the others
"""

import os
import time
import logging
from typing import Optional

from .models import MarketIntelligence, SectorRotation
from .mirofish_client import MiroFishClient
from .rotation_client import RotationClient
from .screener_service import ScreenerService
from .gate_snapshot import TradeGateSnapshot, compute_snapshot
from .decision_logger import DecisionLogger

logger = logging.getLogger(__name__)

# Use eventlet-safe semaphore if available (Flask-SocketIO + eventlet in production),
# fall back to threading.Lock for development.
try:
    from eventlet.semaphore import Semaphore as _Lock
    logger.info("Using eventlet.semaphore.Semaphore for intelligence lock")
except ImportError:
    from threading import Lock as _Lock
    logger.info("Using threading.Lock for intelligence lock (dev mode)")


class IntelligenceService:
    """Singleton aggregator for all intelligence sources.
    
    KILL SWITCH: Set intelligence_kill_switch=True to disable all intelligence
    gates and fall back to pure technical trading. This is the emergency toggle
    for production — if MiroFish is flip-flopping during a black swan, disable
    it in one click from the Command Center.
    """

    def __init__(self):
        self.mirofish = MiroFishClient(
            base_url=os.getenv("MIROFISH_URL", "http://localhost:5003"),
            cache_ttl=int(os.getenv("INTELLIGENCE_REFRESH_INTERVAL", "900")),
        )
        self.rotation = RotationClient(
            base_url=os.getenv("SECTOR_ROTATION_URL", "http://localhost:8000"),
            cache_ttl=300,
        )
        self.screener = ScreenerService()

        self._intelligence: Optional[MarketIntelligence] = None
        self._swap_lock = _Lock()  # held ONLY during pointer swap — never during HTTP calls
        self._last_refresh: float = 0.0

        # Kill switch: when True, get_intelligence() returns None → all gates pass → pure technical
        self.kill_switch: bool = False
        self._kill_switch_reason: str = ""
        self._kill_switch_activated_at: float = 0.0

        # Precomputed gate snapshot (refreshed every 60s by background daemon)
        self._gate_snapshot: TradeGateSnapshot = TradeGateSnapshot()
        self._snapshot_daemon_running: bool = False

        # Decision attribution logger
        self.decision_logger = DecisionLogger()

    def activate_kill_switch(self, reason: str = "Manual activation") -> None:
        """Disable all intelligence gates immediately. One-click emergency toggle."""
        self.kill_switch = True
        self._kill_switch_reason = reason
        self._kill_switch_activated_at = time.time()
        logger.critical("INTELLIGENCE KILL SWITCH ACTIVATED: %s", reason)

    def deactivate_kill_switch(self) -> None:
        """Re-enable intelligence gates."""
        self.kill_switch = False
        self._kill_switch_reason = ""
        self._kill_switch_activated_at = 0.0
        logger.info("Intelligence kill switch deactivated")

    def refresh(
        self,
        news: list = None,
        market_data: dict = None,
        requirement: str = "Predict NIFTY direction for the next trading session",
        symbols: list = None,
    ) -> MarketIntelligence:
        """Refresh all three intelligence sources.

        HTTP calls happen OUTSIDE the lock. Lock is only acquired for the
        final pointer swap (~microseconds), avoiding eventlet deadlocks.
        """
        mf_signal = None
        rot_signal = None
        fund_signal = None
        errors = []

        # 1. MiroFish prediction (up to 10s for quick, 45s for full)
        try:
            mf_signal = self.mirofish.get_prediction(
                news=news,
                market_data=market_data,
                requirement=requirement,
                quick_mode=True,
            )
        except Exception as e:
            logger.warning("MiroFish refresh failed: %s", e)
            errors.append(f"mirofish: {e}")
            mf_signal = self.mirofish.get_cached()

        # 2. Sector Rotation (up to 15s)
        try:
            rot_signal = self.rotation.get_rotation(benchmark="NIFTY", tail=8)
        except Exception as e:
            logger.warning("Rotation refresh failed: %s", e)
            errors.append(f"rotation: {e}")
            rot_signal = self.rotation.get_cached()

        # 3. OpenScreener fundamentals (reads from cache file, <1s)
        if symbols:
            try:
                self.screener.fetch_fundamentals(symbols)
                fund_signal = self.screener.get_cached()
            except Exception as e:
                logger.warning("Screener refresh failed: %s", e)
                errors.append(f"screener: {e}")
                fund_signal = self.screener.get_cached()
        else:
            fund_signal = self.screener.get_cached()

        # Pointer swap — this is the ONLY section under lock
        new_intelligence = MarketIntelligence(
            mirofish=mf_signal,
            rotation=rot_signal,
            fundamentals=fund_signal,
            timestamp=time.time(),
            staleness_seconds=0.0,
        )

        with self._swap_lock:
            self._intelligence = new_intelligence
            self._last_refresh = time.time()

        if errors:
            logger.info("Intelligence refresh completed with errors: %s", errors)
        else:
            logger.info("Intelligence refresh completed successfully")

        return new_intelligence

    def get_intelligence(self) -> Optional[MarketIntelligence]:
        """Return the latest cached intelligence.
        
        Returns None if kill switch is active — all downstream gates will
        receive None and pass through (fail-open to pure technical trading).
        """
        if self.kill_switch:
            return None

        if self._intelligence is None:
            return None

        now = time.time()
        self._intelligence.staleness_seconds = round(now - self._intelligence.timestamp, 1)
        return self._intelligence

    def get_rotation_for_symbol(self, symbol: str) -> Optional[SectorRotation]:
        """Get RRG data for a specific stock by mapping it to its sector."""
        if self.kill_switch:
            return None
        if self._intelligence is None or self._intelligence.rotation is None:
            return None

        sector_sym = self.rotation.get_sector_for_stock(symbol)
        if not sector_sym:
            return None

        for sr in self._intelligence.rotation.sectors:
            if sr.symbol == sector_sym:
                return sr
        return None

    def is_fundamentally_cleared(self, symbol: str) -> bool:
        """Quick check if a symbol passes the fundamental gate.
        When kill switch is active, all symbols are cleared (fail-open).
        """
        if self.kill_switch:
            return True
        return self.screener.is_symbol_cleared(symbol)

    # --- Gate Snapshot (precomputed, read by tick loop) ---

    def get_gate_snapshot(self) -> TradeGateSnapshot:
        """Return the current precomputed gate snapshot.
        This is what the scalping tick loop reads — O(1) dict lookup, no computation.
        """
        return self._gate_snapshot

    def start_snapshot_daemon(self, interval_s: int = 60, symbols: list = None) -> None:
        """Start a background thread that recomputes the gate snapshot every interval_s.
        
        This runs intelligence evaluation OFF the tick path. The tick loop only reads
        the snapshot — it never calls MiroFish, Rotation, or Screener.
        """
        if self._snapshot_daemon_running:
            return

        import threading

        def _daemon():
            self._snapshot_daemon_running = True
            logger.info("Gate snapshot daemon started (interval=%ds)", interval_s)
            while self._snapshot_daemon_running:
                try:
                    self._gate_snapshot = compute_snapshot(
                        intelligence_service=self,
                        symbols=symbols,
                    )
                    logger.debug(
                        "Gate snapshot refreshed: %d gates, bias=%s, conf=%.0f%%",
                        len(self._gate_snapshot.gates),
                        self._gate_snapshot.daily_bias,
                        self._gate_snapshot.daily_confidence * 100,
                    )
                except Exception as e:
                    logger.error("Gate snapshot computation failed: %s", e)
                time.sleep(interval_s)

        t = threading.Thread(target=_daemon, daemon=True, name="gate-snapshot-daemon")
        t.start()

    def stop_snapshot_daemon(self) -> None:
        self._snapshot_daemon_running = False

    def get_source_health(self) -> dict:
        """Return connectivity status for each intelligence source."""
        return {
            "kill_switch": {
                "active": self.kill_switch,
                "reason": self._kill_switch_reason,
                "activated_at": self._kill_switch_activated_at,
            },
            "mirofish": {
                "available": self.mirofish.is_available(),
                "cached": self.mirofish.get_cached() is not None,
                "stale": self.mirofish.get_cached().stale if self.mirofish.get_cached() else None,
                "circuit_breaker": self.mirofish.get_circuit_breaker_status(),
            },
            "rotation": {
                "available": self.rotation.is_available(),
                "cached": self.rotation.get_cached() is not None,
                "stale": self.rotation.get_cached().stale if self.rotation.get_cached() else None,
            },
            "screener": {
                "cached": self.screener.get_cached() is not None,
                "stale": self.screener.get_cached().stale if self.screener.get_cached() else None,
                "symbol_count": len(self.screener._cache),
            },
            "last_refresh": self._last_refresh,
            "seconds_since_refresh": round(time.time() - self._last_refresh, 1) if self._last_refresh else None,
            "gate_snapshot": {
                "gate_count": len(self._gate_snapshot.gates),
                "daily_bias": self._gate_snapshot.daily_bias,
                "daily_confidence": round(self._gate_snapshot.daily_confidence, 3),
                "computed_at": self._gate_snapshot.computed_at,
                "age_seconds": round(time.time() - self._gate_snapshot.computed_at, 1) if self._gate_snapshot.computed_at else None,
                "daemon_running": self._snapshot_daemon_running,
            },
        }
