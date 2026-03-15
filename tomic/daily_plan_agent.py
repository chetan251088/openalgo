"""
DailyPlanAgent — Generates a DailyTradePlan at 9:45 AM each trading day.
Uses VIX regime, market regime, MiroFish bias, sector rotation, and PCR to select
the optimal options selling strategy for each enabled instrument.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .config import DailyPlanParams, MarketContextParams, IntelligenceParams, StrategyType

logger = logging.getLogger(__name__)


@dataclass
class InstrumentPlan:
    instrument: str
    strategy_type: str
    short_delta_target: float
    wing_delta_target: float
    lots: int
    rationale: str
    expiry_date: Optional[str] = None
    entry_mode: str = "morning_plan"
    is_active: bool = True
    reentry_count: int = 0


@dataclass
class DailyTradePlan:
    date: str
    plans: List[InstrumentPlan] = field(default_factory=list)
    market_snapshot: dict = field(default_factory=dict)
    generated_at: str = ""
    valid_until: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "plans": [
                {
                    "instrument": p.instrument,
                    "strategy_type": p.strategy_type,
                    "short_delta_target": p.short_delta_target,
                    "wing_delta_target": p.wing_delta_target,
                    "lots": p.lots,
                    "rationale": p.rationale,
                    "expiry_date": p.expiry_date,
                    "entry_mode": p.entry_mode,
                    "is_active": p.is_active,
                    "reentry_count": p.reentry_count,
                }
                for p in self.plans
            ],
            "market_snapshot": self.market_snapshot,
            "generated_at": self.generated_at,
            "valid_until": self.valid_until,
        }


# Strategy Selection Matrix: (vix_regime, market_regime, mirofish_bias) -> strategy
STRATEGY_MATRIX = {
    # VIX NORMAL (12-18)
    ("NORMAL", "BULLISH", "BULLISH"): (StrategyType.BULL_PUT_SPREAD, "Aligned bullish signals"),
    ("NORMAL", "BULLISH", "NEUTRAL"): (StrategyType.BULL_PUT_SPREAD, "Technical bullish, prediction neutral"),
    ("NORMAL", "BULLISH", "BEARISH"): (StrategyType.SKIP, "Conflicting signals: technical bullish but prediction bearish"),
    ("NORMAL", "BEARISH", "BEARISH"): (StrategyType.BEAR_CALL_SPREAD, "Aligned bearish signals"),
    ("NORMAL", "BEARISH", "NEUTRAL"): (StrategyType.BEAR_CALL_SPREAD, "Technical bearish, prediction neutral"),
    ("NORMAL", "BEARISH", "BULLISH"): (StrategyType.SKIP, "Conflicting signals: technical bearish but prediction bullish"),
    ("NORMAL", "CONGESTION", "BULLISH"): (StrategyType.BULL_PUT_SPREAD, "Rangebound with bullish tilt from prediction"),
    ("NORMAL", "CONGESTION", "BEARISH"): (StrategyType.BEAR_CALL_SPREAD, "Rangebound with bearish tilt from prediction"),
    ("NORMAL", "CONGESTION", "NEUTRAL"): (StrategyType.IRON_CONDOR, "Neutral range — classic Iron Condor"),
    # VIX ELEVATED (18-25)
    ("ELEVATED", "BULLISH", "BULLISH"): (StrategyType.BULL_PUT_SPREAD, "Elevated VIX bullish — wider deltas"),
    ("ELEVATED", "BULLISH", "NEUTRAL"): (StrategyType.BULL_PUT_SPREAD, "Elevated VIX bullish — wider deltas"),
    ("ELEVATED", "BULLISH", "BEARISH"): (StrategyType.IRON_CONDOR, "Elevated VIX conflicting — hedge both sides"),
    ("ELEVATED", "BEARISH", "BEARISH"): (StrategyType.BEAR_CALL_SPREAD, "Elevated VIX bearish — wider deltas"),
    ("ELEVATED", "BEARISH", "NEUTRAL"): (StrategyType.BEAR_CALL_SPREAD, "Elevated VIX bearish — wider deltas"),
    ("ELEVATED", "BEARISH", "BULLISH"): (StrategyType.IRON_CONDOR, "Elevated VIX conflicting — hedge both sides"),
    ("ELEVATED", "CONGESTION", "BULLISH"): (StrategyType.IRON_CONDOR, "Elevated rangebound — Iron Condor with tilt"),
    ("ELEVATED", "CONGESTION", "BEARISH"): (StrategyType.IRON_CONDOR, "Elevated rangebound — Iron Condor with tilt"),
    ("ELEVATED", "CONGESTION", "NEUTRAL"): (StrategyType.IRON_CONDOR, "Elevated rangebound — symmetric Iron Condor"),
    # VIX HIGH (25-35) — only Iron Condor, reduced size
    ("HIGH", "BULLISH", "BULLISH"): (StrategyType.IRON_CONDOR, "High VIX — Iron Condor only, reduced size"),
    ("HIGH", "BULLISH", "NEUTRAL"): (StrategyType.IRON_CONDOR, "High VIX — Iron Condor only, reduced size"),
    ("HIGH", "BULLISH", "BEARISH"): (StrategyType.SKIP, "High VIX with conflicting signals — sit out"),
    ("HIGH", "BEARISH", "BEARISH"): (StrategyType.IRON_CONDOR, "High VIX — Iron Condor only, reduced size"),
    ("HIGH", "BEARISH", "NEUTRAL"): (StrategyType.IRON_CONDOR, "High VIX — Iron Condor only, reduced size"),
    ("HIGH", "BEARISH", "BULLISH"): (StrategyType.SKIP, "High VIX with conflicting signals — sit out"),
    ("HIGH", "CONGESTION", "BULLISH"): (StrategyType.IRON_CONDOR, "High VIX range — Iron Condor, tight"),
    ("HIGH", "CONGESTION", "BEARISH"): (StrategyType.IRON_CONDOR, "High VIX range — Iron Condor, tight"),
    ("HIGH", "CONGESTION", "NEUTRAL"): (StrategyType.IRON_CONDOR, "High VIX range — Iron Condor, tight"),
}


SKIP_TIMEOUT_SECONDS = 7200  # 2 hours — if all instruments SKIP for this long, force fallback
SKIP_FALLBACK_STRATEGY = StrategyType.IRON_CONDOR
SKIP_FALLBACK_LOTS_REDUCTION = 0.5  # 50% of normal lot count


class DailyPlanAgent:
    """Generates the daily trade plan based on market context and intelligence."""

    def __init__(
        self,
        config: Optional[DailyPlanParams] = None,
        market_context_config: Optional[MarketContextParams] = None,
        intelligence_config: Optional[IntelligenceParams] = None,
        intelligence_service=None,
    ):
        self.config = config or DailyPlanParams()
        self.mc_config = market_context_config or MarketContextParams()
        self.intel_config = intelligence_config or IntelligenceParams()
        self.intelligence = intelligence_service
        self._today_plan: Optional[DailyTradePlan] = None
        self._skip_since: dict = {}  # instrument -> timestamp when SKIP started

    def generate_plan(self, market_context) -> DailyTradePlan:
        """Generate today's trade plan from the current market context.

        Args:
            market_context: AtomicMarketContext snapshot.
        """
        now = datetime.now()
        
        plans = []
        for instrument in self.config.instruments:
            plan = self._plan_for_instrument(instrument, market_context)
            if plan:
                plans.append(plan)

        # Margin pre-check: if total estimated margin > 85% of account, cull lowest-confidence trade
        # This reserves margin for event-driven scalps later in the day
        plans = self._apply_margin_cap(plans, market_context)

        daily_plan = DailyTradePlan(
            date=now.strftime("%Y-%m-%d"),
            plans=plans,
            market_snapshot={
                "vix": market_context.vix,
                "vix_regime": market_context.vix_regime,
                "pcr": market_context.pcr,
                "pcr_bias": market_context.pcr_bias,
                "nifty_ltp": market_context.nifty_ltp,
                "nifty_trend": market_context.nifty_trend,
                "mirofish_bias": market_context.mirofish_bias,
                "mirofish_confidence": market_context.mirofish_confidence,
            },
            generated_at=now.isoformat(),
            valid_until=f"{now.strftime('%Y-%m-%d')}T{self.config.plan_valid_until_hhmm}:00",
        )

        self._today_plan = daily_plan
        logger.info(
            "Daily plan generated: %d instrument(s), VIX=%.1f (%s), MiroFish=%s (%.0f%%)",
            len(plans), market_context.vix, market_context.vix_regime,
            market_context.mirofish_bias, (market_context.mirofish_confidence or 0) * 100,
        )
        return daily_plan

    def get_today_plan(self) -> Optional[DailyTradePlan]:
        return self._today_plan

    def _plan_for_instrument(self, instrument: str, ctx) -> Optional[InstrumentPlan]:
        """Generate a plan for a single instrument."""
        # Skip if VIX is too low or extreme
        if ctx.vix_regime in ("TOO_LOW", "EXTREME"):
            return InstrumentPlan(
                instrument=instrument,
                strategy_type=StrategyType.SKIP.value,
                short_delta_target=0, wing_delta_target=0, lots=0,
                rationale=f"VIX {ctx.vix:.1f} in {ctx.vix_regime} regime — no selling",
                is_active=False,
            )

        # Determine market regime from trend
        regime = self._classify_regime(ctx)
        
        # Get MiroFish bias (default to NEUTRAL if unavailable or low confidence)
        mf_bias = "NEUTRAL"
        if ctx.mirofish_bias and ctx.mirofish_confidence >= self.intel_config.mirofish_confidence_min:
            mf_bias = ctx.mirofish_bias

        # Look up strategy from matrix
        key = (ctx.vix_regime, regime, mf_bias)
        entry = STRATEGY_MATRIX.get(key)
        
        if not entry:
            # Fallback: use BLOWOFF or unknown regime -> SKIP
            return InstrumentPlan(
                instrument=instrument,
                strategy_type=StrategyType.SKIP.value,
                short_delta_target=0, wing_delta_target=0, lots=0,
                rationale=f"No matrix entry for ({ctx.vix_regime}, {regime}, {mf_bias})",
                is_active=False,
            )

        strategy_type, rationale = entry

        if strategy_type == StrategyType.SKIP:
            # Track how long this instrument has been in SKIP state
            now_ts = time.time()
            if instrument not in self._skip_since:
                self._skip_since[instrument] = now_ts

            skip_duration = now_ts - self._skip_since[instrument]

            if skip_duration > SKIP_TIMEOUT_SECONDS and instrument in ("NIFTY", "BANKNIFTY"):
                # SKIP timeout: conflicting signals for >2 hours on an index
                # Fall back to index-only Iron Condor with reduced size
                short_delta, wing_delta = self._get_deltas(ctx.vix_regime)
                fallback_lots = max(1, int(1 * SKIP_FALLBACK_LOTS_REDUCTION))
                logger.warning(
                    "SKIP timeout for %s (%.0f min). Falling back to %s with %d lots.",
                    instrument, skip_duration / 60,
                    SKIP_FALLBACK_STRATEGY.value, fallback_lots,
                )
                self._skip_since.pop(instrument, None)
                return InstrumentPlan(
                    instrument=instrument,
                    strategy_type=SKIP_FALLBACK_STRATEGY.value,
                    short_delta_target=short_delta,
                    wing_delta_target=wing_delta,
                    lots=fallback_lots,
                    rationale=f"SKIP timeout ({skip_duration/60:.0f}min): {rationale}. Forced fallback to {SKIP_FALLBACK_STRATEGY.value} index-only, reduced size.",
                )

            return InstrumentPlan(
                instrument=instrument,
                strategy_type=strategy_type.value,
                short_delta_target=0, wing_delta_target=0, lots=0,
                rationale=rationale,
                is_active=False,
            )

        # Non-SKIP strategy selected — clear skip timer for this instrument
        self._skip_since.pop(instrument, None)

        # Select deltas based on VIX regime
        short_delta, wing_delta = self._get_deltas(ctx.vix_regime)

        # Compute lots (base = 1, modified by rotation and confidence)
        lots = self._compute_lots(instrument, ctx)

        return InstrumentPlan(
            instrument=instrument,
            strategy_type=strategy_type.value,
            short_delta_target=short_delta,
            wing_delta_target=wing_delta,
            lots=lots,
            rationale=rationale,
        )

    def _classify_regime(self, ctx) -> str:
        """Classify market regime from trend and PCR."""
        trend = ctx.nifty_trend
        if trend == "ABOVE_20MA":
            return "BULLISH"
        elif trend == "BELOW_20MA":
            return "BEARISH"
        else:
            return "CONGESTION"

    def _get_deltas(self, vix_regime: str):
        """Return (short_delta, wing_delta) based on VIX regime."""
        if vix_regime == "NORMAL":
            return self.config.short_delta_normal, self.config.wing_delta_normal
        elif vix_regime == "ELEVATED":
            return self.config.short_delta_elevated, self.config.wing_delta_elevated
        elif vix_regime == "HIGH":
            return self.config.short_delta_high, self.config.wing_delta_high
        return self.config.short_delta_normal, self.config.wing_delta_normal

    def _compute_lots(self, instrument: str, ctx) -> int:
        """Compute lot count with intelligence-based adjustments."""
        base_lots = 1

        # Boost/penalize based on sector rotation (for stock options)
        if self.intelligence:
            intel = self.intelligence.get_intelligence()
            if intel and intel.rotation:
                if instrument in intel.rotation.leading_sectors:
                    base_lots = max(1, int(base_lots * self.intel_config.rotation_leading_boost))
                elif instrument in intel.rotation.lagging_sectors:
                    base_lots = max(1, int(base_lots * self.intel_config.rotation_lagging_penalty))

        # Reduce if VIX is HIGH
        if ctx.vix_regime == "HIGH":
            base_lots = max(1, base_lots // 2)

        # Reduce if MiroFish confidence is low
        if ctx.mirofish_confidence < 0.4:
            base_lots = max(1, base_lots - 1)

        return max(1, base_lots)

    def _apply_margin_cap(self, plans: List[InstrumentPlan], ctx) -> List[InstrumentPlan]:
        """If total estimated margin exceeds 85% of account, cull the lowest-confidence trade.
        
        This reserves margin for event-driven scalps later in the day.
        Being fully margined blocks you from taking sudden opportunities.
        """
        MAX_MARGIN_PCT = 0.85
        active_plans = [p for p in plans if p.is_active and p.strategy_type != StrategyType.SKIP.value]

        if len(active_plans) <= 1:
            return plans

        # Rough margin estimate: each active plan uses ~15-25% of capital for defined-risk spreads
        estimated_margin_per_plan = 0.18  # 18% average
        total_margin_pct = sum(estimated_margin_per_plan * p.lots for p in active_plans)

        if total_margin_pct <= MAX_MARGIN_PCT:
            return plans

        # Cull the lowest-confidence / highest-lots plan
        # Sort by a priority score: lower MiroFish confidence + higher lots = cull first
        active_plans.sort(key=lambda p: (
            ctx.mirofish_confidence if ctx.mirofish_bias else 0.5,
            -p.lots,
        ))

        culled = active_plans[0]
        culled.is_active = False
        culled.rationale = f"MARGIN CAP: culled to keep total margin < {MAX_MARGIN_PCT:.0%}. Original: {culled.rationale}"
        logger.info(
            "Margin cap: culled %s (%s, %d lots) to keep margin < %.0f%%",
            culled.instrument, culled.strategy_type, culled.lots, MAX_MARGIN_PCT * 100,
        )

        return plans
