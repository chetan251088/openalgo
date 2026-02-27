"""Tests for StrategyEngine — 4-mode signal generation."""
import pytest
from unittest.mock import MagicMock
from tomic.agents.strategy_engine import StrategyEngine, EntryTrigger
from tomic.config import TomicConfig, StrategyType


def make_plan(strategy=StrategyType.IRON_CONDOR, instrument="NIFTY", is_active=True, reentry_count=0):
    plan = MagicMock()
    plan.instrument = instrument
    plan.strategy_type = strategy
    plan.entry_mode = "morning"
    plan.vix_at_plan = 16.0
    plan.regime_at_plan = "CONGESTION"
    plan.pcr_at_plan = 1.0
    plan.short_delta_target = 0.25
    plan.wing_delta_target = 0.10
    plan.lots = 1
    plan.expiry_date = ""
    plan.rationale = "test rationale"
    plan.is_active = is_active
    plan.reentry_count = reentry_count
    return plan


def make_context(vix=16.0, vix_regime="NORMAL"):
    ctx = MagicMock()
    ctx.vix = vix
    ctx.vix_regime = vix_regime
    return ctx


def make_engine():
    config = TomicConfig()
    plan_agent = MagicMock()
    mc_agent = MagicMock()
    regime_state = MagicMock()
    return StrategyEngine(
        config=config,
        daily_plan_agent=plan_agent,
        market_context_agent=mc_agent,
        regime_state=regime_state,
    )


def test_morning_trigger_generates_signal():
    engine = make_engine()
    plan = make_plan()

    signals = engine._signals_for_plan(plan)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_dict["instrument"] == "NIFTY"
    assert sig.signal_dict["strategy_type"] == StrategyType.IRON_CONDOR.value


def test_skip_plan_generates_no_signal():
    engine = make_engine()
    plan = make_plan(strategy=StrategyType.SKIP)
    signals = engine._signals_for_plan(plan)
    assert len(signals) == 0


def test_inactive_plan_generates_no_signal():
    engine = make_engine()
    plan = make_plan(is_active=False)
    signals = engine._signals_for_plan(plan)
    assert len(signals) == 0


def test_vix_extreme_blocks_all_signals():
    engine = make_engine()
    ctx = make_context(vix=40.0, vix_regime="EXTREME")
    engine._market_context_agent.read_context.return_value = ctx
    plan = make_plan()
    signals = engine.get_pending_signals(plans=[plan], ctx=ctx)
    assert len(signals) == 0


def test_event_trigger_on_vix_spike():
    trigger = EntryTrigger.from_vix_spike(prev_vix=15.0, curr_vix=22.0, threshold_pct=0.15)
    assert trigger is not None
    assert trigger.reason == "vix_spike"


def test_no_trigger_on_small_vix_move():
    trigger = EntryTrigger.from_vix_spike(prev_vix=15.0, curr_vix=16.0, threshold_pct=0.15)
    assert trigger is None
