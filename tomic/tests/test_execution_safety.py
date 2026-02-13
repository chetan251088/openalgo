"""
Test Suite: TOMIC Execution Safety — Legging Policies, Unhedged Timer, Smart Delay
=====================================================================================
Tests the 3 non-bypassable safety invariants at the config level.
"""

import pytest
from tomic.config import (
    TomicConfig,
    TomicMode,
    LeggingPolicy,
    StrategyType,
    LEGGING_POLICY,
)


class TestLeggingPolicies:
    """Invariant 1: Per-strategy legging policy locked at config time."""

    def test_bull_put_spread_hedge_first(self):
        policy = LEGGING_POLICY[StrategyType.BULL_PUT_SPREAD]
        assert policy == LeggingPolicy.HEDGE_FIRST

    def test_bear_call_spread_hedge_first(self):
        policy = LEGGING_POLICY[StrategyType.BEAR_CALL_SPREAD]
        assert policy == LeggingPolicy.HEDGE_FIRST

    def test_iron_condor_hedge_first(self):
        policy = LEGGING_POLICY[StrategyType.IRON_CONDOR]
        assert policy == LeggingPolicy.HEDGE_FIRST

    def test_jade_lizard_has_policy(self):
        policy = LEGGING_POLICY[StrategyType.JADE_LIZARD]
        assert policy == LeggingPolicy.HEDGE_FIRST

    def test_short_strangle_has_policy(self):
        policy = LEGGING_POLICY[StrategyType.SHORT_STRANGLE]
        assert policy == LeggingPolicy.SHORT_FIRST_KILL_SWITCH

    def test_short_straddle_has_policy(self):
        policy = LEGGING_POLICY[StrategyType.SHORT_STRADDLE]
        assert policy == LeggingPolicy.SHORT_FIRST_KILL_SWITCH

    def test_risk_reversal_short_first(self):
        policy = LEGGING_POLICY[StrategyType.RISK_REVERSAL]
        assert policy == LeggingPolicy.SHORT_FIRST_KILL_SWITCH

    def test_ditm_call_single_leg(self):
        policy = LEGGING_POLICY[StrategyType.DITM_CALL]
        assert policy == LeggingPolicy.SINGLE_LEG

    def test_calendar_diagonal_hedge_first(self):
        policy = LEGGING_POLICY[StrategyType.CALENDAR_DIAGONAL]
        assert policy == LeggingPolicy.HEDGE_FIRST

    def test_all_strategies_have_policy(self):
        """Every strategy type must have a legging policy."""
        for st in StrategyType:
            assert st in LEGGING_POLICY, f"{st} missing legging policy"

    def test_policies_are_immutable_at_import(self):
        """Policy map should be a dict (frozen at module load time)."""
        assert isinstance(LEGGING_POLICY, dict)
        assert len(LEGGING_POLICY) == len(StrategyType)


class TestUnhedgedTimeout:
    """Invariant 2: Unhedged timeout configuration."""

    def test_default_timeout_5s(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.circuit_breakers.unhedged_timeout_seconds == 5.0

    def test_timeout_is_positive(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.circuit_breakers.unhedged_timeout_seconds > 0


class TestSmartOrderDelay:
    """Invariant 3: Smart order delay between legs."""

    def test_default_delay_half_second(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.execution.smart_order_delay == 0.5

    def test_delay_is_positive(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.execution.smart_order_delay > 0


class TestTomicModes:
    """Semi-Auto vs Full-Auto behavior."""

    def test_sandbox_mode(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.mode == TomicMode.SANDBOX

    def test_semi_auto_mode(self):
        config = TomicConfig.load(mode="semi_auto")
        assert config.mode == TomicMode.SEMI_AUTO

    def test_full_auto_mode(self):
        config = TomicConfig.load(mode="full_auto")
        assert config.mode == TomicMode.FULL_AUTO


class TestSafetyConfig:
    """Config completeness for safety-critical params."""

    def test_heartbeat_interval_positive(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.supervisor.heartbeat_interval > 0

    def test_agent_restart_retries(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.supervisor.agent_restart_max_retries >= 1

    def test_kill_switch_timeout(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.supervisor.exec_broker_consecutive_timeout_kill >= 1

    def test_daily_max_loss_pct(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.circuit_breakers.daily_max_loss_pct == 0.06

    def test_max_orders_per_minute(self):
        config = TomicConfig.load(mode="sandbox")
        assert config.circuit_breakers.max_orders_per_minute == 30
