"""Tests for DailyPlanAgent strategy selection logic."""
import pytest
from unittest.mock import MagicMock
from tomic.agents.daily_plan_agent import (
    DailyPlanAgent, DailyTradePlan,
    select_strategy_from_context,
)
from tomic.config import TomicConfig, RegimePhase, StrategyType


def make_market_context(vix=16.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL", nifty_ltp=25000.0):
    mc = MagicMock()
    mc.vix = vix
    mc.vix_regime = vix_regime
    mc.pcr = pcr
    mc.pcr_bias = pcr_bias
    mc.nifty_ltp = nifty_ltp
    return mc


def make_regime_snapshot(phase=RegimePhase.CONGESTION, score=0, vix=16.0):
    snap = MagicMock()
    snap.phase = phase
    snap.score = score
    snap.vix = vix
    return snap


def test_vix_too_low_returns_skip():
    ctx = make_market_context(vix=10.0, vix_regime="TOO_LOW")
    regime = make_regime_snapshot(phase=RegimePhase.CONGESTION)
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.SKIP


def test_vix_extreme_returns_skip():
    ctx = make_market_context(vix=40.0, vix_regime="EXTREME")
    regime = make_regime_snapshot(phase=RegimePhase.BULLISH)
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.SKIP


def test_normal_vix_bullish_regime_bull_put():
    ctx = make_market_context(vix=15.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL")
    regime = make_regime_snapshot(phase=RegimePhase.BULLISH)
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.BULL_PUT_SPREAD


def test_normal_vix_bearish_regime_bear_call():
    ctx = make_market_context(vix=15.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL")
    regime = make_regime_snapshot(phase=RegimePhase.BEARISH)
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.BEAR_CALL_SPREAD


def test_normal_vix_congestion_iron_condor():
    ctx = make_market_context(vix=15.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL")
    regime = make_regime_snapshot(phase=RegimePhase.CONGESTION)
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.IRON_CONDOR


def test_pcr_bullish_tilt_overrides_neutral_regime():
    """High PCR should tilt toward Bull Put in congestion regime."""
    ctx = make_market_context(vix=16.0, vix_regime="NORMAL", pcr=1.4, pcr_bias="BULLISH")
    regime = make_regime_snapshot(phase=RegimePhase.CONGESTION, score=0)
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.BULL_PUT_SPREAD


def test_plan_rationale_is_human_readable():
    config = TomicConfig()
    mc_agent = MagicMock()
    mc_agent.read_context.return_value = make_market_context(
        vix=16.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL", nifty_ltp=25000.0
    )
    regime_state = MagicMock()
    regime_state.read_snapshot.return_value = make_regime_snapshot(phase=RegimePhase.CONGESTION)

    agent = DailyPlanAgent(
        config=config,
        market_context_agent=mc_agent,
        regime_state=regime_state,
    )
    plan = agent.generate_plan("NIFTY", entry_mode="morning")
    assert plan is not None
    assert "VIX" in plan.rationale
    assert len(plan.rationale) > 20


def test_skip_returns_none_plan():
    config = TomicConfig()
    mc_agent = MagicMock()
    mc_agent.read_context.return_value = make_market_context(vix=10.0, vix_regime="TOO_LOW")
    regime_state = MagicMock()
    regime_state.read_snapshot.return_value = make_regime_snapshot(phase=RegimePhase.CONGESTION)

    agent = DailyPlanAgent(
        config=config,
        market_context_agent=mc_agent,
        regime_state=regime_state,
    )
    plan = agent.generate_plan("NIFTY", entry_mode="morning")
    assert plan is None
