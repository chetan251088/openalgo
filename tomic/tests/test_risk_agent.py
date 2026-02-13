"""
TOMIC Risk Agent Tests
========================
Test 8-step sizing chain, regime filtering, Black Swan hedge,
and RiskAgent signal evaluation.
"""

import pytest
import logging
from unittest.mock import MagicMock, patch
from tomic.agents.risk_agent import (
    RiskAgent,
    SizingResult,
    SizingStep,
    compute_black_swan_budget,
    round_to_lots,
    run_sizing_chain,
    step_1_volatility_target,
    step_2_half_kelly,
    step_3_two_pct_rule,
    step_4_vix_overlay,
    step_5_idm,
    step_6_sector_heat,
    step_7_position_cap,
    step_8_margin_reserve,
)
from tomic.agents.regime_agent import AtomicRegimeState
from tomic.config import (
    BlackSwanParams,
    RegimePhase,
    SizingParams,
    StrategyType,
    TomicConfig,
    VIXRules,
)
from tomic.position_book import Position, PositionBook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def params():
    return SizingParams()


@pytest.fixture
def vix_rules():
    return VIXRules()


# ---------------------------------------------------------------------------
# Step 1: Volatility Target
# ---------------------------------------------------------------------------

class TestStep1VolatilityTarget:
    def test_normal_calculation(self):
        size, reason = step_1_volatility_target(1_000_000, 0.20, 0.25)
        assert size == pytest.approx(800_000)  # 200k / 0.25

    def test_zero_vol_rejects(self):
        size, reason = step_1_volatility_target(1_000_000, 0.20, 0.0)
        assert size == 0
        assert "REJECT" in reason

    def test_higher_vol_smaller_size(self):
        s1, _ = step_1_volatility_target(1_000_000, 0.20, 0.20)
        s2, _ = step_1_volatility_target(1_000_000, 0.20, 0.40)
        assert s2 < s1


# ---------------------------------------------------------------------------
# Step 2: Half-Kelly Cap
# ---------------------------------------------------------------------------

class TestStep2HalfKelly:
    def test_positive_kelly(self):
        size, _ = step_2_half_kelly(1_000_000, 0.55, 2.0)
        assert size > 0

    def test_negative_kelly_rejects(self):
        size, reason = step_2_half_kelly(1_000_000, 0.30, 1.0)
        assert size == 0
        assert "REJECT" in reason

    def test_zero_rr_rejects(self):
        size, reason = step_2_half_kelly(1_000_000, 0.55, 0.0)
        assert size == 0
        assert "REJECT" in reason

    def test_higher_win_rate_larger(self):
        s1, _ = step_2_half_kelly(1_000_000, 0.50, 2.0)
        s2, _ = step_2_half_kelly(1_000_000, 0.65, 2.0)
        assert s2 > s1


# ---------------------------------------------------------------------------
# Step 3: 2% Rule
# ---------------------------------------------------------------------------

class TestStep3TwoPctRule:
    def test_normal_calculation(self):
        size, _ = step_3_two_pct_rule(1_000_000, 50.0)
        assert size == pytest.approx(400)  # 20000 / 50

    def test_zero_sl_rejects(self):
        size, reason = step_3_two_pct_rule(1_000_000, 0.0)
        assert size == 0
        assert "REJECT" in reason

    def test_larger_sl_smaller_size(self):
        s1, _ = step_3_two_pct_rule(1_000_000, 50)
        s2, _ = step_3_two_pct_rule(1_000_000, 100)
        assert s2 < s1


# ---------------------------------------------------------------------------
# Step 4: VIX Overlay
# ---------------------------------------------------------------------------

class TestStep4VIXOverlay:
    def test_normal_vix_no_change(self):
        size, _ = step_4_vix_overlay(100, 18.0)
        assert size == 100

    def test_high_vix_halved(self):
        size, _ = step_4_vix_overlay(100, 35.0)
        assert size == 50

    def test_custom_threshold(self):
        size, _ = step_4_vix_overlay(100, 22.0, half_size_above=20.0)
        assert size == 50


# ---------------------------------------------------------------------------
# Step 5: IDM
# ---------------------------------------------------------------------------

class TestStep5IDM:
    def test_low_correlation_no_change(self):
        size, _ = step_5_idm(100, 0.3)
        assert size == 100

    def test_high_correlation_reduced(self):
        size, _ = step_5_idm(100, 0.8)
        assert size == pytest.approx(70)

    def test_exactly_at_threshold(self):
        size, _ = step_5_idm(100, 0.7)
        assert size == 100  # not strictly above


# ---------------------------------------------------------------------------
# Step 6: Sector Heat
# ---------------------------------------------------------------------------

class TestStep6SectorHeat:
    def test_within_limit(self):
        size, _ = step_6_sector_heat(100, 0.15)
        assert size == 100

    def test_over_limit_rejects(self):
        size, reason = step_6_sector_heat(100, 0.25)
        assert size == 0
        assert "REJECT" in reason


# ---------------------------------------------------------------------------
# Step 7: Position Cap
# ---------------------------------------------------------------------------

class TestStep7PositionCap:
    def test_below_cap(self):
        size, _ = step_7_position_cap(100, 5)
        assert size == 100

    def test_at_cap_rejects(self):
        size, reason = step_7_position_cap(100, 10)
        assert size == 0
        assert "REJECT" in reason

    def test_above_cap_rejects(self):
        size, reason = step_7_position_cap(100, 15)
        assert size == 0
        assert "REJECT" in reason


# ---------------------------------------------------------------------------
# Step 8: Margin Reserve
# ---------------------------------------------------------------------------

class TestStep8MarginReserve:
    def test_sufficient_margin(self):
        size, _ = step_8_margin_reserve(100, 0.50)
        assert size == 100

    def test_insufficient_margin_rejects(self):
        size, reason = step_8_margin_reserve(100, 0.20)
        assert size == 0
        assert "REJECT" in reason

    def test_exactly_at_reserve(self):
        size, _ = step_8_margin_reserve(100, 0.25)
        assert size == 100  # not strictly below


# ---------------------------------------------------------------------------
# Lot Rounding
# ---------------------------------------------------------------------------

class TestRoundToLots:
    def test_round_down(self):
        assert round_to_lots(175, 50) == 150

    def test_exact_lot(self):
        assert round_to_lots(200, 50) == 200

    def test_below_one_lot(self):
        assert round_to_lots(30, 50) == 0

    def test_zero_size(self):
        assert round_to_lots(0, 50) == 0

    def test_custom_lot_size(self):
        assert round_to_lots(175, 25) == 175  # 7 * 25 = 175


# ---------------------------------------------------------------------------
# Full Chain
# ---------------------------------------------------------------------------

class TestFullSizingChain:
    def test_approved_chain(self, params, vix_rules):
        result = run_sizing_chain(
            capital=1_000_000,
            instrument_vol=0.25,
            win_rate=0.55,
            reward_risk_ratio=2.0,
            sl_distance=50.0,
            vix=18.0,
            correlation=0.3,
            sector_margin_pct=0.10,
            total_positions=3,
            free_margin_pct=0.60,
            lot_size=50,
            params=params,
            vix_rules=vix_rules,
        )
        assert result.approved is True
        assert result.final_lots > 0
        assert len(result.chain) == 8

    def test_rejected_by_position_cap(self, params, vix_rules):
        result = run_sizing_chain(
            capital=1_000_000,
            instrument_vol=0.25,
            win_rate=0.55,
            reward_risk_ratio=2.0,
            sl_distance=50.0,
            vix=18.0,
            correlation=0.3,
            sector_margin_pct=0.10,
            total_positions=10,  # at cap
            free_margin_pct=0.60,
            params=params,
            vix_rules=vix_rules,
        )
        assert result.approved is False
        assert "position" in result.reject_reason.lower()

    def test_rejected_by_margin(self, params, vix_rules):
        result = run_sizing_chain(
            capital=1_000_000,
            instrument_vol=0.25,
            win_rate=0.55,
            reward_risk_ratio=2.0,
            sl_distance=50.0,
            vix=18.0,
            correlation=0.3,
            sector_margin_pct=0.10,
            total_positions=3,
            free_margin_pct=0.10,  # below reserve
            params=params,
            vix_rules=vix_rules,
        )
        assert result.approved is False

    def test_chain_each_step_reduces(self, params, vix_rules):
        result = run_sizing_chain(
            capital=1_000_000,
            instrument_vol=0.25,
            win_rate=0.55,
            reward_risk_ratio=2.0,
            sl_distance=50.0,
            vix=35.0,          # triggers VIX overlay
            correlation=0.8,    # triggers IDM reduction
            sector_margin_pct=0.10,
            total_positions=3,
            free_margin_pct=0.60,
            params=params,
            vix_rules=vix_rules,
        )
        # Verify monotonic decrease across relevant steps
        for step in result.chain:
            assert step.output_size <= step.input_size or step.input_size == float("inf")

    def test_chain_serializable(self, params, vix_rules):
        result = run_sizing_chain(
            capital=1_000_000,
            instrument_vol=0.25,
            win_rate=0.55,
            reward_risk_ratio=2.0,
            sl_distance=50.0,
            vix=18.0,
            correlation=0.3,
            sector_margin_pct=0.10,
            total_positions=3,
            free_margin_pct=0.60,
            params=params,
            vix_rules=vix_rules,
        )
        d = result.to_dict()
        assert "approved" in d
        assert "chain" in d
        assert len(d["chain"]) == 8


# ---------------------------------------------------------------------------
# Black Swan Hedge
# ---------------------------------------------------------------------------

class TestBlackSwanHedge:
    def test_bootstrap_budget(self):
        budget = compute_black_swan_budget(10, 0)
        assert budget == 5000.0

    def test_pct_budget_after_30_trades(self):
        budget = compute_black_swan_budget(50, 100_000)
        # midpoint of 1-2%: 1.5% of 100k = 1500, but min is 5000
        assert budget >= 5000.0

    def test_pct_budget_large_profit(self):
        budget = compute_black_swan_budget(50, 1_000_000)
        # 1.5% of 1M = 15000
        assert budget == pytest.approx(15000.0)

    def test_custom_params(self):
        p = BlackSwanParams(bootstrap_budget_inr=10000.0, min_trades_for_pct=20)
        budget = compute_black_swan_budget(5, 0, p)
        assert budget == 10000.0


class TestSignalValidation:
    def test_blocks_context_only_underlyings(self):
        agent = RiskAgent.__new__(RiskAgent)
        ok, reason = RiskAgent._validate_signal(  # type: ignore[arg-type]
            agent,
            instrument="INDIAVIX",
            strategy_type=StrategyType.DITM_CALL.value,
            direction="BUY",
            signal={},
        )
        assert ok is False
        assert "context-only" in reason

    def test_blocks_invalid_ditm_direction(self):
        agent = RiskAgent.__new__(RiskAgent)
        ok, reason = RiskAgent._validate_signal(  # type: ignore[arg-type]
            agent,
            instrument="NIFTY",
            strategy_type=StrategyType.DITM_CALL.value,
            direction="SELL",
            signal={},
        )
        assert ok is False
        assert "BUY entries only" in reason

    def test_blocks_invalid_ditm_put_direction(self):
        agent = RiskAgent.__new__(RiskAgent)
        ok, reason = RiskAgent._validate_signal(  # type: ignore[arg-type]
            agent,
            instrument="NIFTY",
            strategy_type=StrategyType.DITM_PUT.value,
            direction="SELL",
            signal={},
        )
        assert ok is False
        assert "BUY entries only" in reason

    def test_blocks_legged_strategy_without_legs(self):
        agent = RiskAgent.__new__(RiskAgent)
        ok, reason = RiskAgent._validate_signal(  # type: ignore[arg-type]
            agent,
            instrument="NIFTY",
            strategy_type=StrategyType.IRON_CONDOR.value,
            direction="SELL",
            signal={"legs": []},
        )
        assert ok is False
        assert "requires legs" in reason


class TestRegimeDirectionalAlignment:
    def _agent(self) -> RiskAgent:
        agent = RiskAgent.__new__(RiskAgent)
        agent.logger = logging.getLogger("test.risk_regime_alignment")
        return agent

    def test_bullish_blocks_bearish_bias_ditm_put(self):
        regime_state = AtomicRegimeState()
        regime_state.update(
            phase=RegimePhase.BULLISH,
            score=10,
            vix=16.0,
            vix_flags=[],
            ichimoku_signal="BULLISH",
            impulse_color="GREEN",
            congestion=False,
            blowoff=False,
        )
        allowed, reason = RiskAgent._regime_allows_signal(  # type: ignore[arg-type]
            self._agent(),
            regime_state.read_snapshot(),
            direction="BUY",
            strategy_type=StrategyType.DITM_PUT.value,
        )
        assert allowed is False
        assert "Bullish regime blocks bearish-bias setup" in reason

    def test_bearish_blocks_bullish_bias_ditm_call(self):
        regime_state = AtomicRegimeState()
        regime_state.update(
            phase=RegimePhase.BEARISH,
            score=-10,
            vix=18.0,
            vix_flags=[],
            ichimoku_signal="BEARISH",
            impulse_color="RED",
            congestion=False,
            blowoff=False,
        )
        allowed, reason = RiskAgent._regime_allows_signal(  # type: ignore[arg-type]
            self._agent(),
            regime_state.read_snapshot(),
            direction="BUY",
            strategy_type=StrategyType.DITM_CALL.value,
        )
        assert allowed is False
        assert "Bearish regime blocks bullish-bias setup" in reason

    def test_short_strangle_allowed_only_in_congestion(self):
        regime_state = AtomicRegimeState()
        regime_state.update(
            phase=RegimePhase.BULLISH,
            score=8,
            vix=18.0,
            vix_flags=[],
            ichimoku_signal="BULLISH",
            impulse_color="GREEN",
            congestion=False,
            blowoff=False,
        )
        allowed, reason = RiskAgent._regime_allows_signal(  # type: ignore[arg-type]
            self._agent(),
            regime_state.read_snapshot(),
            direction="SELL",
            strategy_type=StrategyType.SHORT_STRANGLE.value,
        )
        assert allowed is False
        assert "allowed only in CONGESTION" in reason

    def test_short_straddle_blocked_when_defined_risk_only(self):
        regime_state = AtomicRegimeState()
        regime_state.update(
            phase=RegimePhase.CONGESTION,
            score=0,
            vix=29.0,
            vix_flags=["DEFINED_RISK_ONLY"],
            ichimoku_signal="NEUTRAL",
            impulse_color="BLUE",
            congestion=True,
            blowoff=False,
        )
        allowed, reason = RiskAgent._regime_allows_signal(  # type: ignore[arg-type]
            self._agent(),
            regime_state.read_snapshot(),
            direction="SELL",
            strategy_type=StrategyType.SHORT_STRADDLE.value,
        )
        assert allowed is False
        assert "DEFINED_RISK_ONLY" in reason


class TestPositionGate:
    def test_blocks_same_side_position_when_pyramiding_disabled(self, tmp_path):
        pb = PositionBook(db_path=str(tmp_path / "risk_gate_positions.db"))
        pb.update_position(
            Position(
                instrument="NIFTY26FEB2623000CE",
                strategy_id="TOMIC_DITM_CALL_NIFTY",
                strategy_tag="TOMIC_DITM_CALL_NIFTY",
                direction="BUY",
                quantity=50,
                avg_price=120.0,
            )
        )

        agent = RiskAgent.__new__(RiskAgent)
        agent.config = TomicConfig.load("sandbox")
        agent._allow_pyramiding = False

        allowed, reason = RiskAgent._position_gate_allows_signal(  # type: ignore[arg-type]
            agent,
            pos_snapshot=pb.read_snapshot(),
            instrument="NIFTY",
            strategy_type=StrategyType.DITM_CALL.value,
            direction="BUY",
        )
        assert allowed is False
        assert ("Open same-side position exists" in reason) or ("Max positions per underlying reached" in reason)


class TestOrderReasonPayload:
    def test_enqueue_order_carries_entry_reason_and_meta(self):
        class _Store:
            def __init__(self):
                self.kwargs = {}

            def enqueue(self, **kwargs):
                self.kwargs = kwargs
                return 42

        agent = RiskAgent.__new__(RiskAgent)
        agent.logger = logging.getLogger("test.risk_order_reason")
        agent._command_store = _Store()
        agent._total_trades = 0

        regime_state = AtomicRegimeState()
        regime_state.update(
            phase=RegimePhase.BEARISH,
            score=-8,
            vix=18.4,
            vix_flags=[],
            ichimoku_signal="BEARISH",
            impulse_color="RED",
            congestion=False,
            blowoff=False,
        )
        regime = regime_state.read_snapshot()

        signal = {
            "instrument": "BANKNIFTY",
            "strategy_type": StrategyType.DITM_PUT.value,
            "direction": "BUY",
            "signal_direction": "SELL",
            "entry_price": 101.25,
            "stop_price": 94.0,
            "reason": "IV_LOW, BEARISH -> DITM Put",
            "router_reason": "bearish: volatility leads",
            "router_action": "ACCEPT",
            "router_source": "VOLATILITY",
            "router_priority_score": 88.5,
            "signal_strength": 5.75,
        }
        sizing = SizingResult(
            approved=True,
            final_lots=60,
            chain=[SizingStep(step=8, name="margin_reserve", input_size=120.0, output_size=120.0, reason="free_margin=84.0% OK")],
        )

        enqueued = RiskAgent._enqueue_order(agent, signal, sizing, regime)  # type: ignore[arg-type]
        assert enqueued is True
        payload = agent._command_store.kwargs.get("payload", {})

        assert isinstance(payload.get("entry_reason"), str) and payload.get("entry_reason")
        assert "Router: bearish: volatility leads" in payload["entry_reason"]
        assert "Signal: IV_LOW, BEARISH -> DITM Put" in payload["entry_reason"]
        assert "Regime: BEARISH" in payload["entry_reason"]
        assert "Action: DITM_PUT SELL qty=60" in payload["entry_reason"]
        assert payload["entry_reason_meta"]["router_reason"] == "bearish: volatility leads"
        assert payload["entry_reason_meta"]["strategy_type"] == StrategyType.DITM_PUT.value
