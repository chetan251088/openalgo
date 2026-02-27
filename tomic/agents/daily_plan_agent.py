"""
TOMIC Daily Plan Agent — Morning Trade Plan Generator
======================================================
Runs at 9:45 AM. Reads MarketContext + RegimeSnapshot.
Selects strategy type using the VIX/regime matrix.
Generates a DailyTradePlan with target deltas and expiry.

Plans are stored in memory and consumed by the StrategyEngine.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy selection matrix (pure function — testable)
# ---------------------------------------------------------------------------

def select_strategy_from_context(ctx, regime, params=None):
    """
    Map VIX regime + market regime to strategy type.

    Matrix:
      VIX TOO_LOW / EXTREME → SKIP
      PCR BULLISH + not strongly bearish → BULL_PUT_SPREAD
      PCR BEARISH + not strongly bullish → BEAR_CALL_SPREAD
      Regime BULLISH → BULL_PUT_SPREAD
      Regime BEARISH → BEAR_CALL_SPREAD
      Regime BLOWOFF → SKIP
      Regime CONGESTION (default) → IRON_CONDOR
    """
    from tomic.config import DailyPlanParams, RegimePhase, StrategyType
    if params is None:
        params = DailyPlanParams()

    if ctx.vix_regime in ("TOO_LOW", "EXTREME"):
        return StrategyType.SKIP

    phase = regime.phase

    # PCR tilt: strong PCR signal can override mild regime
    if ctx.pcr_bias == "BULLISH" and phase != RegimePhase.BEARISH:
        return StrategyType.BULL_PUT_SPREAD
    if ctx.pcr_bias == "BEARISH" and phase != RegimePhase.BULLISH:
        return StrategyType.BEAR_CALL_SPREAD

    if phase == RegimePhase.BULLISH:
        return StrategyType.BULL_PUT_SPREAD
    if phase == RegimePhase.BEARISH:
        return StrategyType.BEAR_CALL_SPREAD
    if phase == RegimePhase.BLOWOFF:
        return StrategyType.SKIP

    return StrategyType.IRON_CONDOR  # CONGESTION default


def _delta_targets_for_vix(vix_regime: str, params) -> tuple:
    """Return (short_delta, wing_delta) based on VIX level."""
    if vix_regime == "ELEVATED":
        return params.short_delta_elevated, params.wing_delta_elevated
    if vix_regime == "HIGH":
        return params.short_delta_high, params.wing_delta_high
    return params.short_delta_normal, params.wing_delta_normal


# ---------------------------------------------------------------------------
# DailyTradePlan
# ---------------------------------------------------------------------------

@dataclass
class DailyTradePlan:
    """A fully specified trade plan for one instrument on one day."""
    date: str                        # YYYY-MM-DD
    instrument: str
    strategy_type: Any               # StrategyType enum
    entry_mode: str                  # morning / continuous / event / expiry_gamma
    vix_at_plan: float
    regime_at_plan: str
    pcr_at_plan: float
    short_delta_target: float
    wing_delta_target: float
    lots: int = 1
    expiry_date: str = ""            # DDMMMyy e.g. "30JAN25"
    rationale: str = ""
    valid_until_hhmm: str = "14:00"
    is_active: bool = True
    reentry_count: int = 0           # how many re-entries used today
    created_at_mono: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "instrument": self.instrument,
            "strategy_type": self.strategy_type.value,
            "entry_mode": self.entry_mode,
            "vix_at_plan": self.vix_at_plan,
            "regime_at_plan": self.regime_at_plan,
            "pcr_at_plan": self.pcr_at_plan,
            "short_delta_target": self.short_delta_target,
            "wing_delta_target": self.wing_delta_target,
            "lots": self.lots,
            "expiry_date": self.expiry_date,
            "rationale": self.rationale,
            "valid_until_hhmm": self.valid_until_hhmm,
            "is_active": self.is_active,
            "reentry_count": self.reentry_count,
        }


# ---------------------------------------------------------------------------
# DailyPlanAgent
# ---------------------------------------------------------------------------

class DailyPlanAgent:
    """
    Generates DailyTradePlan objects for each instrument.
    Called at 9:45 AM by the runtime (morning mode).
    Also callable on-demand for continuous / event modes.
    """

    def __init__(self, config, market_context_agent, regime_state) -> None:
        self._config = config
        self._params = config.daily_plan
        self._mc = market_context_agent
        self._regime_state = regime_state
        self._lock = threading.Lock()
        self._plans: Dict[str, DailyTradePlan] = {}   # instrument → plan
        self._today: str = ""

    def generate_all_plans(self, entry_mode: str = "morning") -> List[DailyTradePlan]:
        """Generate plans for all configured instruments."""
        plans = []
        for instrument in self._params.instruments:
            plan = self.generate_plan(instrument, entry_mode=entry_mode)
            if plan is not None:
                plans.append(plan)
        return plans

    def generate_plan(
        self,
        instrument: str,
        entry_mode: str = "morning",
    ) -> Optional[DailyTradePlan]:
        """Generate a trade plan for one instrument."""
        from tomic.config import StrategyType
        ctx = self._mc.read_context()
        regime = self._regime_state.read_snapshot()
        today = date.today().isoformat()

        strategy = select_strategy_from_context(ctx, regime, self._params)
        if strategy == StrategyType.SKIP:
            logger.info(
                "DailyPlan: SKIP %s — VIX %.1f (%s), regime %s",
                instrument, ctx.vix, ctx.vix_regime, regime.phase.value,
            )
            return None

        short_delta, wing_delta = _delta_targets_for_vix(ctx.vix_regime, self._params)

        rationale = (
            f"VIX={ctx.vix:.1f} ({ctx.vix_regime}), "
            f"Regime={regime.phase.value} (score={regime.score}), "
            f"PCR={ctx.pcr:.2f} ({ctx.pcr_bias}) → {strategy.value}. "
            f"Short delta target: {short_delta:.2f}, wing: {wing_delta:.2f}."
        )

        plan = DailyTradePlan(
            date=today,
            instrument=instrument.upper(),
            strategy_type=strategy,
            entry_mode=entry_mode,
            vix_at_plan=ctx.vix,
            regime_at_plan=regime.phase.value,
            pcr_at_plan=ctx.pcr,
            short_delta_target=short_delta,
            wing_delta_target=wing_delta,
            lots=1,
            rationale=rationale,
            valid_until_hhmm=self._params.plan_valid_until_hhmm,
        )

        with self._lock:
            self._plans[instrument.upper()] = plan
            self._today = today

        logger.info("DailyPlan generated: %s %s — %s", today, instrument, rationale)
        return plan

    def get_active_plan(self, instrument: str) -> Optional[DailyTradePlan]:
        with self._lock:
            return self._plans.get(instrument.upper())

    def get_all_active_plans(self) -> List[DailyTradePlan]:
        with self._lock:
            return [p for p in self._plans.values() if p.is_active]

    def mark_plan_inactive(self, instrument: str) -> None:
        with self._lock:
            plan = self._plans.get(instrument.upper())
            if plan:
                plan.is_active = False

    def increment_reentry(self, instrument: str) -> bool:
        """Returns True if re-entry is allowed (count < max)."""
        max_reentries = self._config.position_manager.max_reentries_per_day
        with self._lock:
            plan = self._plans.get(instrument.upper())
            if plan is None:
                return False
            if plan.reentry_count >= max_reentries:
                return False
            plan.reentry_count += 1
            return True

    def reset_for_new_day(self) -> None:
        """Clear all plans at start of new trading day."""
        with self._lock:
            self._plans.clear()
            self._today = date.today().isoformat()

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "date": self._today,
                "plans": [p.to_dict() for p in self._plans.values()],
            }
