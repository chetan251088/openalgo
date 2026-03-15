"""
StrategyEngine — Replaces the old ConflictRouter.
Handles 4 entry modes and routes signals to the RiskAgent.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

from .config import StrategyType, EntryMode, StrategyEngineParams

logger = logging.getLogger(__name__)


@dataclass
class RoutedSignal:
    """Signal output from StrategyEngine, compatible with RiskAgent.enqueue_signal()."""
    instrument: str
    strategy_type: str
    direction: str  # "SELL" for premium selling, "BUY" for gamma capture
    entry_mode: str
    short_delta: float = 0.0
    wing_delta: float = 0.0
    lots: int = 1
    rationale: str = ""
    confidence: float = 0.5
    timestamp: float = 0.0
    source: str = "strategy_engine"

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "strategy_type": self.strategy_type,
            "direction": self.direction,
            "entry_mode": self.entry_mode,
            "short_delta": self.short_delta,
            "wing_delta": self.wing_delta,
            "lots": self.lots,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "source": self.source,
        }


class StrategyEngine:
    """Routes market context and intelligence into concrete trading signals."""

    def __init__(self, config: Optional[StrategyEngineParams] = None):
        self.config = config or StrategyEngineParams()
        self._last_signal_time: dict = {}  # instrument -> timestamp
        self._signals_today: dict = {}  # instrument -> count

    def process_morning_plan(self, daily_plan) -> List[RoutedSignal]:
        """Convert a DailyTradePlan into RoutedSignals for each active instrument."""
        signals = []
        for plan in daily_plan.plans:
            if not plan.is_active:
                continue
            if plan.strategy_type == StrategyType.SKIP.value:
                continue

            signal = RoutedSignal(
                instrument=plan.instrument,
                strategy_type=plan.strategy_type,
                direction="SELL",
                entry_mode=EntryMode.MORNING_PLAN.value,
                short_delta=plan.short_delta_target,
                wing_delta=plan.wing_delta_target,
                lots=plan.lots,
                rationale=plan.rationale,
                confidence=0.7,
                timestamp=time.time(),
            )
            signals.append(signal)
            logger.info("Morning plan signal: %s %s %d lots", plan.instrument, plan.strategy_type, plan.lots)

        return signals

    def check_continuous_entry(self, market_context, position_book=None) -> List[RoutedSignal]:
        """Check for continuous (15-min interval) re-entry opportunities.
        Only fires if no active position exists for the instrument.
        """
        signals = []
        now = time.time()

        for instrument in ("NIFTY", "BANKNIFTY"):
            last = self._last_signal_time.get(instrument, 0)
            if now - last < self.config.continuous_interval_seconds:
                continue

            # Skip if position already exists
            if position_book and position_book.has_active_position(instrument):
                continue

            count = self._signals_today.get(instrument, 0)
            if count >= self.config.max_signals_per_instrument_per_day:
                continue

            self._last_signal_time[instrument] = now

        return signals

    def check_event_driven(self, market_context, prev_vix: float = 0) -> List[RoutedSignal]:
        """Check for VIX spike events that warrant immediate position adjustment."""
        signals = []
        vix = market_context.vix
        
        if prev_vix > 0 and vix > 0:
            vix_change_pct = ((vix - prev_vix) / prev_vix) * 100
            
            # VIX spike > 15% → tighten existing positions (handled by PositionManager)
            # VIX drop > 10% → potential new entry opportunity
            if vix_change_pct < -10 and market_context.vix_regime in ("NORMAL", "ELEVATED"):
                logger.info("VIX drop %.1f%% detected — potential entry opportunity", vix_change_pct)

        return signals

    def check_expiry_gamma(self, market_context, expiry_specialist=None) -> List[RoutedSignal]:
        """Check if expiry gamma capture conditions are met (after 14:00 on expiry day)."""
        if not expiry_specialist:
            return []
        return expiry_specialist.check_gamma_entry(market_context)

    def reset_daily(self) -> None:
        """Reset daily counters at start of new trading day."""
        self._signals_today.clear()
        self._last_signal_time.clear()
        logger.info("StrategyEngine daily counters reset")
