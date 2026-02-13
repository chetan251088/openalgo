from __future__ import annotations

from tomic.agents.regime_agent import RegimeSnapshot
from tomic.agents.volatility_agent import (
    SkewState,
    TermStructure,
    VolRegime,
    VolSnapshot,
    select_strategy,
)
from tomic.config import RegimePhase, StrategyType, VIXRules, VolatilityParams


def _regime(phase: RegimePhase, vix: float, flags: list[str] | None = None) -> RegimeSnapshot:
    return RegimeSnapshot(
        version=1,
        phase=phase,
        score=8 if phase == RegimePhase.BULLISH else (-8 if phase == RegimePhase.BEARISH else 0),
        vix=vix,
        vix_flags=list(flags or []),
        ichimoku_signal="NEUTRAL",
        impulse_color="BLUE",
        congestion=(phase == RegimePhase.CONGESTION),
        blowoff=(phase == RegimePhase.BLOWOFF),
        timestamp_mono=0.0,
    )


def test_iv_high_congestion_prefers_iron_condor_with_legs() -> None:
    snap = VolSnapshot(
        underlying="NIFTY",
        iv=0.24,
        hv=0.12,
        iv_rank=72.0,
        iv_hv_ratio=2.0,
        vol_regime=VolRegime.IV_HIGH,
        skew_state=SkewState.NORMAL,
        term_structure=TermStructure.NORMAL,
    )
    signals = select_strategy(
        vol_snap=snap,
        regime=_regime(RegimePhase.CONGESTION, vix=19.0),
        params=VolatilityParams(),
        vix_rules=VIXRules(),
    )
    condors = [s for s in signals if s.strategy_type == StrategyType.IRON_CONDOR]
    assert condors, "Expected IRON_CONDOR in high-IV congestion"
    assert len(condors[0].legs) == 4
    assert condors[0].direction == "SELL"


def test_vix_too_low_blocks_credit_spreads() -> None:
    snap = VolSnapshot(
        underlying="NIFTY",
        iv=0.20,
        hv=0.10,
        iv_rank=80.0,
        iv_hv_ratio=2.0,
        vol_regime=VolRegime.IV_HIGH,
        skew_state=SkewState.NORMAL,
        term_structure=TermStructure.NORMAL,
    )
    signals = select_strategy(
        vol_snap=snap,
        regime=_regime(RegimePhase.BULLISH, vix=10.5, flags=["PREMIUMS_TOO_LOW"]),
        params=VolatilityParams(),
        vix_rules=VIXRules(),
    )
    assert all(
        s.strategy_type not in {StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD, StrategyType.IRON_CONDOR}
        for s in signals
    )


def test_term_structure_inverted_emits_calendar_with_near_far_legs() -> None:
    snap = VolSnapshot(
        underlying="BANKNIFTY",
        iv=0.18,
        hv=0.16,
        iv_rank=35.0,
        iv_hv_ratio=1.12,
        vol_regime=VolRegime.IV_NORMAL,
        skew_state=SkewState.NORMAL,
        term_structure=TermStructure.INVERTED,
    )
    signals = select_strategy(
        vol_snap=snap,
        regime=_regime(RegimePhase.BULLISH, vix=16.0),
        params=VolatilityParams(),
        vix_rules=VIXRules(),
    )
    calendars = [s for s in signals if s.strategy_type == StrategyType.CALENDAR_DIAGONAL]
    assert calendars
    assert len(calendars[0].legs) == 2
    assert calendars[0].legs[0].get("expiry_offset") == 0
    assert calendars[0].legs[1].get("expiry_offset") == 1


def test_steep_put_skew_emits_jade_lizard_when_enabled() -> None:
    snap = VolSnapshot(
        underlying="NIFTY",
        iv=0.23,
        hv=0.14,
        iv_rank=68.0,
        iv_hv_ratio=1.64,
        vol_regime=VolRegime.IV_HIGH,
        skew_state=SkewState.STEEP_PUT,
        skew_ratio=1.7,
        term_structure=TermStructure.NORMAL,
    )
    signals = select_strategy(
        vol_snap=snap,
        regime=_regime(RegimePhase.BULLISH, vix=18.0),
        params=VolatilityParams(),
        vix_rules=VIXRules(),
        feature_flags={"enable_jade_lizard": True, "allow_naked_premium": True},
    )
    jade = [s for s in signals if s.strategy_type == StrategyType.JADE_LIZARD]
    assert jade
    assert len(jade[0].legs) == 3
    assert jade[0].direction == "SELL"


def test_congestion_high_iv_emits_short_strangle_when_enabled() -> None:
    snap = VolSnapshot(
        underlying="BANKNIFTY",
        iv=0.26,
        hv=0.16,
        iv_rank=78.0,
        iv_hv_ratio=1.62,
        vol_regime=VolRegime.IV_HIGH,
        skew_state=SkewState.NORMAL,
        term_structure=TermStructure.NORMAL,
    )
    signals = select_strategy(
        vol_snap=snap,
        regime=_regime(RegimePhase.CONGESTION, vix=19.0),
        params=VolatilityParams(),
        vix_rules=VIXRules(),
        feature_flags={
            "enable_short_strangle": True,
            "enable_short_straddle": False,
            "allow_naked_premium": True,
            "naked_iv_rank_min": 65.0,
            "naked_iv_hv_min": 1.35,
        },
    )
    strangles = [s for s in signals if s.strategy_type == StrategyType.SHORT_STRANGLE]
    assert strangles
    assert len(strangles[0].legs) == 2
