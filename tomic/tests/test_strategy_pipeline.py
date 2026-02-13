"""
Test Suite: TOMIC Strategy Pipeline — E2E Strategy Construction & Execution
============================================================================
Tests Bull Put Spread + Iron Condor construction, DTE validation,
regime pre-filtering, strike selection, wing width rules, and full
pipeline integration with Risk Agent and Command Store.
"""

import pytest
import time
from unittest.mock import MagicMock

from tomic.config import (
    RegimePhase,
    StrategyType,
    TomicConfig,
    VolatilityParams,
)
from tomic.agents.regime_agent import AtomicRegimeState
from tomic.agents.risk_agent import RiskAgent, run_sizing_chain
from tomic.command_store import CommandStore
from tomic.position_book import PositionBook
from tomic.event_bus import EventPublisher
from tomic.events import EventType
from tomic.strategy_pipeline import (
    StrategyPipeline,
    StrategyTemplate,
    StrategyLeg,
    LegType,
    construct_bull_put_spread,
    construct_iron_condor,
    select_otm_strike,
    select_wing_strike,
    get_wing_width,
    validate_dte,
    _get_lot_size,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return TomicConfig.load("sandbox")


@pytest.fixture
def regime_state():
    return AtomicRegimeState()


@pytest.fixture
def command_store(tmp_path):
    cs = CommandStore(db_path=str(tmp_path / "commands.db"))
    cs.initialize()
    return cs


@pytest.fixture
def position_book(tmp_path):
    return PositionBook(db_path=str(tmp_path / "positions.db"))


@pytest.fixture
def publisher():
    pub = MagicMock(spec=EventPublisher)
    pub.publish = MagicMock(return_value=True)
    return pub


@pytest.fixture
def risk_agent(config, publisher, command_store, position_book, regime_state):
    return RiskAgent(
        config=config,
        publisher=publisher,
        command_store=command_store,
        position_book=position_book,
        regime_state=regime_state,
        capital=1_000_000,
    )


@pytest.fixture
def pipeline(config, regime_state, risk_agent):
    return StrategyPipeline(
        config=config,
        regime_state=regime_state,
        risk_agent=risk_agent,
    )


def _set_bullish(regime_state):
    regime_state.update(
        phase=RegimePhase.BULLISH, score=12, vix=18.0,
        vix_flags=[], ichimoku_signal="BULLISH",
        impulse_color="GREEN", congestion=False, blowoff=False,
    )


def _set_bearish(regime_state):
    regime_state.update(
        phase=RegimePhase.BEARISH, score=-15, vix=22.0,
        vix_flags=[], ichimoku_signal="BEARISH",
        impulse_color="RED", congestion=False, blowoff=False,
    )


def _set_congestion(regime_state):
    regime_state.update(
        phase=RegimePhase.CONGESTION, score=0, vix=16.0,
        vix_flags=[], ichimoku_signal="NEUTRAL",
        impulse_color="BLUE", congestion=True, blowoff=False,
    )


def _set_halt_vega(regime_state):
    regime_state.update(
        phase=RegimePhase.CONGESTION, score=-5, vix=45.0,
        vix_flags=["HALT_SHORT_VEGA"], ichimoku_signal="NEUTRAL",
        impulse_color="RED", congestion=False, blowoff=False,
    )


# ===================================================================
# Bull Put Spread Construction
# ===================================================================

class TestBullPutSpread:
    """Bull Put Spread construction and validation."""

    def test_basic_construction(self):
        strategy = construct_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_delta=0.20, short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy.strategy_type == StrategyType.BULL_PUT_SPREAD
        assert strategy.underlying == "NIFTY"
        assert strategy.expected_credit == 70  # 120 - 50
        assert strategy.max_loss == 130  # 200 - 70
        assert strategy.wing_width == 200
        assert len(strategy.legs) == 2

    def test_legs_ordered_hedge_first(self):
        """Hedge leg (BUY_PUT) should come before short leg (SELL_PUT)."""
        strategy = construct_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_delta=0.20, short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy.legs[0].leg_type == LegType.BUY_PUT
        assert strategy.legs[1].leg_type == LegType.SELL_PUT

    def test_leg_directions(self):
        strategy = construct_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_delta=0.20, short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy.legs[0].direction == "BUY"
        assert strategy.legs[1].direction == "SELL"

    def test_to_signal_dict(self):
        strategy = construct_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_delta=0.20, short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        signal = strategy.to_signal_dict(instrument_vol=0.22)
        assert signal["instrument"] == "NIFTY"
        assert signal["strategy_type"] == StrategyType.BULL_PUT_SPREAD.value
        assert signal["direction"] == "SELL"  # credit
        assert signal["lot_size"] == 50  # NIFTY lot
        assert len(signal["legs"]) == 2

    def test_dte_field(self):
        strategy = construct_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_delta=0.20, short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=40,
            short_premium=100, long_premium=30,
        )
        assert strategy.dte == 40


# ===================================================================
# Iron Condor Construction
# ===================================================================

class TestIronCondor:
    """Iron Condor construction and validation."""

    def test_basic_construction(self):
        strategy = construct_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-03-19", dte=35,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        assert strategy.strategy_type == StrategyType.IRON_CONDOR
        assert strategy.expected_credit == 130  # (120-50) + (100-40)
        assert strategy.max_loss == 70  # 200 - 130
        assert len(strategy.legs) == 4

    def test_four_legs_hedge_first(self):
        """Each side should have hedge (BUY) before short (SELL)."""
        strategy = construct_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-03-19", dte=35,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        # Put side
        assert strategy.legs[0].leg_type == LegType.BUY_PUT
        assert strategy.legs[1].leg_type == LegType.SELL_PUT
        # Call side
        assert strategy.legs[2].leg_type == LegType.BUY_CALL
        assert strategy.legs[3].leg_type == LegType.SELL_CALL

    def test_asymmetric_wings(self):
        """Wing widths can differ between put and call sides."""
        strategy = construct_iron_condor(
            underlying="BANKNIFTY", spot_price=50000,
            put_short_strike=49500, put_long_strike=49000,  # 500 wide
            call_short_strike=50500, call_long_strike=51200,  # 700 wide
            expiry="2026-03-19", dte=35,
            put_short_premium=300, put_long_premium=100,
            call_short_premium=250, call_long_premium=80,
        )
        # max loss = max(500, 700) - credit
        net_credit = (300 - 100) + (250 - 80)
        assert strategy.max_loss == 700 - net_credit

    def test_to_signal_dict(self):
        strategy = construct_iron_condor(
            underlying="BANKNIFTY", spot_price=50000,
            put_short_strike=49500, put_long_strike=49000,
            call_short_strike=50500, call_long_strike=51000,
            expiry="2026-03-19", dte=35,
            put_short_premium=300, put_long_premium=100,
            call_short_premium=250, call_long_premium=80,
        )
        signal = strategy.to_signal_dict()
        assert signal["lot_size"] == 15  # BANKNIFTY lot
        assert signal["direction"] == "SELL"


# ===================================================================
# Strike Selection
# ===================================================================

class TestStrikeSelection:
    """OTM strike selection with safety bias."""

    def test_put_otm_selects_below_spot(self):
        strikes = [23000, 23200, 23400, 23600, 23800, 24000, 24200]
        strike = select_otm_strike(24000, 0.20, strikes, "PUT")
        assert strike < 24000

    def test_call_otm_selects_above_spot(self):
        strikes = [23000, 23200, 23400, 23600, 23800, 24000, 24200, 24400]
        strike = select_otm_strike(24000, 0.20, strikes, "CALL")
        assert strike > 24000

    def test_empty_strikes_raises(self):
        with pytest.raises(ValueError):
            select_otm_strike(24000, 0.20, [], "PUT")

    def test_put_wing_below_short(self):
        wing = select_wing_strike(23800, 200, "PUT")
        assert wing == 23600

    def test_call_wing_above_short(self):
        wing = select_wing_strike(24200, 200, "CALL")
        assert wing == 24400

    def test_wing_snaps_to_available_strikes(self):
        strikes = [23000, 23200, 23500, 23700]
        wing = select_wing_strike(23800, 200, "PUT", strikes)
        # Target: 23600, nearest available: 23500 or 23700
        assert wing in (23500, 23700)


# ===================================================================
# Wing Width Rules
# ===================================================================

class TestWingWidth:
    """Per-underlying wing width from architecture docs."""

    def test_nifty_200(self):
        assert get_wing_width("NIFTY") == 200

    def test_banknifty_500(self):
        assert get_wing_width("BANKNIFTY") == 500

    def test_case_insensitive(self):
        assert get_wing_width("nifty") == 200
        assert get_wing_width("Nifty") == 200

    def test_other_underlying_zero(self):
        assert get_wing_width("RELIANCE") == 0


# ===================================================================
# DTE Validation
# ===================================================================

class TestDTEValidation:
    """Credit spreads: 30-45 DTE. Momentum: 5-10 DTE."""

    def test_credit_in_range(self):
        assert validate_dte(35, StrategyType.BULL_PUT_SPREAD) is True
        assert validate_dte(30, StrategyType.IRON_CONDOR) is True
        assert validate_dte(45, StrategyType.BEAR_CALL_SPREAD) is True

    def test_credit_out_of_range(self):
        assert validate_dte(10, StrategyType.BULL_PUT_SPREAD) is False
        assert validate_dte(60, StrategyType.IRON_CONDOR) is False

    def test_momentum_in_range(self):
        assert validate_dte(7, StrategyType.DITM_CALL) is True
        assert validate_dte(5, StrategyType.DITM_CALL) is True
        assert validate_dte(10, StrategyType.DITM_CALL) is True

    def test_momentum_out_of_range(self):
        assert validate_dte(35, StrategyType.DITM_CALL) is False


# ===================================================================
# Lot Sizes
# ===================================================================

class TestLotSizes:
    """Standard Indian options lot sizes."""

    def test_nifty(self):
        assert _get_lot_size("NIFTY") == 50

    def test_banknifty(self):
        assert _get_lot_size("BANKNIFTY") == 15

    def test_finnifty(self):
        assert _get_lot_size("FINNIFTY") == 25

    def test_default(self):
        assert _get_lot_size("RELIANCE") == 50


# ===================================================================
# Pipeline E2E — Full Regime → Risk → Command Flow
# ===================================================================

class TestPipelineE2E:
    """Full strategy pipeline through Regime → Risk → Command Table."""

    def test_bull_put_approved_bullish(self, pipeline, regime_state, command_store):
        """Bull Put Spread in bullish regime → approved → command enqueued."""
        _set_bullish(regime_state)

        strategy = pipeline.evaluate_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy is not None
        assert strategy.expected_credit == 70

        # Risk Agent should have received signal
        assert pipeline.pipeline_count == 1

    def test_bull_put_blocked_bearish(self, pipeline, regime_state, command_store):
        """Bull Put Spread in bearish regime → blocked before sizing."""
        _set_bearish(regime_state)

        strategy = pipeline.evaluate_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy is None
        assert pipeline.pipeline_count == 0

    def test_bull_put_blocked_halt_vega(self, pipeline, regime_state, command_store):
        """Bull Put Spread blocked when HALT_SHORT_VEGA."""
        _set_halt_vega(regime_state)

        strategy = pipeline.evaluate_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy is None

    def test_bull_put_bad_dte_rejected(self, pipeline, regime_state, command_store):
        """Bull Put Spread with DTE too short → rejected."""
        _set_bullish(regime_state)

        strategy = pipeline.evaluate_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_strike=23800, long_strike=23600,
            expiry="2026-02-20", dte=7,  # too short for credit spread
            short_premium=120, long_premium=50,
        )
        assert strategy is None

    def test_iron_condor_approved_congestion(self, pipeline, regime_state, command_store):
        """Iron Condor in congestion regime → approved (ideal regime)."""
        _set_congestion(regime_state)

        strategy = pipeline.evaluate_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-03-19", dte=35,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        assert strategy is not None
        assert strategy.expected_credit == 130

    def test_iron_condor_blocked_halt_vega(self, pipeline, regime_state, command_store):
        """Iron Condor blocked when VIX > 40 (HALT_SHORT_VEGA)."""
        _set_halt_vega(regime_state)

        strategy = pipeline.evaluate_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-03-19", dte=35,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        assert strategy is None

    def test_iron_condor_approved_bullish(self, pipeline, regime_state, command_store):
        """Iron Condor in bullish regime → also approved (neutral strategy)."""
        _set_bullish(regime_state)

        strategy = pipeline.evaluate_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-03-19", dte=35,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        assert strategy is not None

    def test_pipeline_end_to_end_command_enqueued(self, pipeline, regime_state, risk_agent, command_store):
        """Full E2E: approved strategy → tick → command in table."""
        _set_bullish(regime_state)

        strategy = pipeline.evaluate_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=120, long_premium=50,
        )
        assert strategy is not None

        # Process the signal in Risk Agent
        risk_agent._tick()

        # Command should be enqueued
        cmd = command_store.dequeue()
        assert cmd is not None
        assert cmd.event_type == "ORDER_REQUEST"
        assert "NIFTY" in cmd.payload.get("instrument", "")

    def test_no_credit_rejected(self, pipeline, regime_state, command_store):
        """Strategy with zero or negative credit → rejected."""
        _set_bullish(regime_state)

        strategy = pipeline.evaluate_bull_put_spread(
            underlying="NIFTY", spot_price=24000,
            short_strike=23800, long_strike=23600,
            expiry="2026-03-19", dte=35,
            short_premium=50, long_premium=80,  # debit, not credit
        )
        assert strategy is None

    def test_multiple_strategies_counted(self, pipeline, regime_state, command_store):
        """Pipeline count tracks number of signals submitted."""
        _set_congestion(regime_state)

        pipeline.evaluate_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-03-19", dte=35,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        pipeline.evaluate_bull_put_spread(
            underlying="BANKNIFTY", spot_price=50000,
            short_strike=49500, long_strike=49000,
            expiry="2026-03-19", dte=35,
            short_premium=300, long_premium=100,
        )
        assert pipeline.pipeline_count == 2

    def test_iron_condor_bad_dte(self, pipeline, regime_state, command_store):
        """Iron Condor with DTE too long → rejected."""
        _set_congestion(regime_state)

        strategy = pipeline.evaluate_iron_condor(
            underlying="NIFTY", spot_price=24000,
            put_short_strike=23800, put_long_strike=23600,
            call_short_strike=24200, call_long_strike=24400,
            expiry="2026-05-19", dte=90,
            put_short_premium=120, put_long_premium=50,
            call_short_premium=100, call_long_premium=40,
        )
        assert strategy is None
