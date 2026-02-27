"""
TOMIC Strategy Engine — Unified Signal Generator
=================================================
Replaces ConflictRouter. Handles 4 entry modes:
  1. MORNING_PLAN  — 9:45 AM, based on DailyTradePlan
  2. CONTINUOUS    — every 15 min, re-evaluate if conditions shift
  3. EVENT_DRIVEN  — VIX spike / PCR extreme / S/R test
  4. EXPIRY_GAMMA  — after 14:00 on expiry days (see ExpirySpecialist)

Outputs RoutedSignal objects compatible with the existing RiskAgent interface.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tomic.conflict_router import RoutedSignal, SignalSource
from tomic.config import StrategyType, TomicConfig

logger = logging.getLogger(__name__)


@dataclass
class EntryTrigger:
    """An event-driven entry trigger."""
    reason: str
    instrument: Optional[str] = None   # None = all instruments

    @classmethod
    def from_vix_spike(
        cls,
        prev_vix: float,
        curr_vix: float,
        threshold_pct: float = 0.15,
    ) -> Optional["EntryTrigger"]:
        if prev_vix <= 0:
            return None
        change = (curr_vix - prev_vix) / prev_vix
        if abs(change) >= threshold_pct:
            return cls(reason="vix_spike")
        return None


class StrategyEngine:
    """
    Unified signal engine. Called by runtime._signal_loop().
    Returns a list of RoutedSignal objects for the RiskAgent.
    """

    def __init__(
        self,
        config: TomicConfig,
        daily_plan_agent,
        market_context_agent,
        regime_state,
    ) -> None:
        self._config = config
        self._daily_plan_agent = daily_plan_agent
        self._market_context_agent = market_context_agent
        self._regime_state = regime_state
        self._prev_vix: float = 0.0
        self._last_continuous_check: float = 0.0
        self._continuous_interval_s: float = 15 * 60  # 15 minutes

    def get_pending_signals(
        self,
        plans=None,
        ctx=None,
    ) -> List[RoutedSignal]:
        """
        Main entry point called by runtime._signal_loop().
        Returns all signals ready for the RiskAgent.
        """
        if ctx is None:
            ctx = self._market_context_agent.read_context()

        # Hard block: never trade in extreme VIX
        if ctx.vix_regime in ("TOO_LOW", "EXTREME"):
            return []

        if plans is None:
            plans = self._daily_plan_agent.get_all_active_plans()

        signals = []
        for plan in plans:
            signals.extend(self._signals_for_plan(plan))

        # Check event-driven triggers
        trigger = EntryTrigger.from_vix_spike(self._prev_vix, ctx.vix, threshold_pct=0.15)
        if trigger and ctx.vix_regime not in ("TOO_LOW", "EXTREME"):
            logger.info("Event trigger: %s — regenerating plans", trigger.reason)
            new_plans = self._daily_plan_agent.generate_all_plans(entry_mode="event_driven")
            for plan in new_plans:
                signals.extend(self._signals_for_plan(plan))

        self._prev_vix = ctx.vix
        return signals

    def _signals_for_plan(self, plan) -> List[RoutedSignal]:
        """Convert a DailyTradePlan into RoutedSignal(s) for the RiskAgent."""
        if plan.strategy_type == StrategyType.SKIP:
            return []
        if not plan.is_active:
            return []

        # Build the signal dict (compatible with RiskAgent.receive_signal interface)
        signal_dict: Dict[str, Any] = {
            "instrument": plan.instrument,
            "strategy_type": plan.strategy_type.value,
            "direction": "SELL",           # options selling: net short premium
            "short_delta_target": plan.short_delta_target,
            "wing_delta_target": plan.wing_delta_target,
            "lots": plan.lots,
            "expiry_date": plan.expiry_date,
            "entry_mode": plan.entry_mode,
            "rationale": plan.rationale,
            "vix_at_signal": plan.vix_at_plan,
            "signal_strength": self._compute_strength(plan),
            # Abstract legs — resolved by ExecutionAgent via LegResolver
            "legs": self._build_abstract_legs(plan.strategy_type),
        }

        return [RoutedSignal(
            source=SignalSource.VOLATILITY,
            signal_dict=signal_dict,
            priority_score=signal_dict["signal_strength"],
        )]

    def _compute_strength(self, plan) -> float:
        """Signal strength 0–100. Higher = more conviction."""
        strength = 50.0
        if plan.vix_at_plan >= 18:
            strength += 10.0   # more premium to collect
        if plan.regime_at_plan == "CONGESTION":
            strength += 10.0   # range-bound = ideal for premium selling
        if plan.reentry_count == 0:
            strength += 5.0    # fresh entry
        return min(100.0, strength)

    @staticmethod
    def _build_abstract_legs(strategy_type: StrategyType) -> List[Dict[str, Any]]:
        """Build abstract leg specs. LegResolver fills in real strikes."""
        if strategy_type == StrategyType.IRON_CONDOR:
            return [
                {"leg_type": "BUY_PUT",   "option_type": "PE", "direction": "BUY",  "offset": "wing_put"},
                {"leg_type": "SELL_PUT",  "option_type": "PE", "direction": "SELL", "offset": "short_put"},
                {"leg_type": "BUY_CALL",  "option_type": "CE", "direction": "BUY",  "offset": "wing_call"},
                {"leg_type": "SELL_CALL", "option_type": "CE", "direction": "SELL", "offset": "short_call"},
            ]
        if strategy_type == StrategyType.BULL_PUT_SPREAD:
            return [
                {"leg_type": "BUY_PUT",  "option_type": "PE", "direction": "BUY",  "offset": "wing_put"},
                {"leg_type": "SELL_PUT", "option_type": "PE", "direction": "SELL", "offset": "short_put"},
            ]
        if strategy_type == StrategyType.BEAR_CALL_SPREAD:
            return [
                {"leg_type": "BUY_CALL",  "option_type": "CE", "direction": "BUY",  "offset": "wing_call"},
                {"leg_type": "SELL_CALL", "option_type": "CE", "direction": "SELL", "offset": "short_call"},
            ]
        if strategy_type == StrategyType.GAMMA_CAPTURE:
            return [
                {"leg_type": "BUY_CALL", "option_type": "CE", "direction": "BUY", "offset": "otm_call"},
                {"leg_type": "BUY_PUT",  "option_type": "PE", "direction": "BUY", "offset": "otm_put"},
            ]
        return []
