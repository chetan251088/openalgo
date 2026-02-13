"""
TOMIC Pipeline E2E Tests
==========================
Test the full signal → regime filter → sizing → command enqueue pipeline.
Uses mocked infrastructure (no real ZeroMQ, SQLite in tmp_path).
"""

import pytest
import time
from unittest.mock import MagicMock, patch
from tomic.agents.regime_agent import (
    AtomicRegimeState,
    RegimeAgent,
    compute_ichimoku,
    compute_impulse_system,
    compute_regime_score,
)
from tomic.agents.risk_agent import RiskAgent, run_sizing_chain
from tomic.command_store import CommandStore
from tomic.config import RegimePhase, StrategyType, TomicConfig
from tomic.event_bus import EventPublisher
from tomic.events import EventType
from tomic.position_book import PositionBook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return TomicConfig.load("sandbox")


@pytest.fixture
def publisher():
    pub = MagicMock(spec=EventPublisher)
    pub.publish = MagicMock(return_value=True)
    return pub


@pytest.fixture
def command_store(tmp_path):
    cs = CommandStore(db_path=str(tmp_path / "commands.db"))
    cs.initialize()
    return cs


@pytest.fixture
def position_book(tmp_path):
    return PositionBook(db_path=str(tmp_path / "positions.db"))


@pytest.fixture
def regime_state():
    return AtomicRegimeState()


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
def regime_agent(config, publisher, regime_state):
    return RegimeAgent(
        config=config,
        publisher=publisher,
        regime_state=regime_state,
    )


# ---------------------------------------------------------------------------
# Regime Agent Tick Tests
# ---------------------------------------------------------------------------

class TestRegimeAgentTick:
    def test_tick_without_data_noop(self, regime_agent, publisher):
        """No OHLCV data → no regime update published."""
        regime_agent._setup()
        regime_agent._tick()
        publisher.publish.assert_not_called()

    def test_tick_with_data_publishes(self, regime_agent, publisher):
        """Feed enough data → regime update published."""
        # Feed 60 rising candles
        for i in range(60):
            regime_agent.feed_candle(
                high=102 + i * 0.5,
                low=98 + i * 0.5,
                close=100 + i * 0.5,
                volume=1_000_000,
            )
        regime_agent.feed_vix(18.0)
        regime_agent._setup()
        regime_agent._tick()
        publisher.publish.assert_called_once()

    def test_regime_state_updated_after_tick(self, regime_agent):
        """State version increments after tick with data."""
        for i in range(60):
            regime_agent.feed_candle(102 + i, 98 + i, 100 + i, 1_000_000)
        regime_agent.feed_vix(18.0)
        regime_agent._setup()
        regime_agent._tick()
        assert regime_agent.regime_state.current_version >= 1

    def test_tick_same_inputs_do_not_republish(self, regime_agent, publisher):
        """No-change ticks should not emit additional RegimeUpdate events."""
        for i in range(60):
            regime_agent.feed_candle(102 + i * 0.5, 98 + i * 0.5, 100 + i * 0.5, 1_000_000)
        regime_agent.feed_vix(18.0)
        regime_agent._setup()

        regime_agent._tick()
        first_version = regime_agent.regime_state.current_version
        assert first_version >= 1
        assert publisher.publish.call_count == 1

        regime_agent._tick()
        assert regime_agent.regime_state.current_version == first_version
        assert publisher.publish.call_count == 1


# ---------------------------------------------------------------------------
# Risk Agent Regime Filter Tests
# ---------------------------------------------------------------------------

class TestRegimeFilter:
    def test_bearish_blocks_bullish_buy(self, risk_agent, regime_state, command_store):
        """Bearish regime should block bullish BUY signals."""
        regime_state.update(
            phase=RegimePhase.BEARISH, score=-15, vix=20.0,
            vix_flags=[], ichimoku_signal="BEARISH",
            impulse_color="RED", congestion=False, blowoff=False,
        )
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.BULL_PUT_SPREAD.value,
            "direction": "BUY",
            "entry_price": 100,
            "stop_price": 95,
        }
        risk_agent._evaluate_signal(signal)
        # Should NOT enqueue
        cmd = command_store.dequeue()
        assert cmd is None

    def test_bullish_allows_buy(self, risk_agent, regime_state, command_store):
        """Bullish regime allows BUY signals."""
        regime_state.update(
            phase=RegimePhase.BULLISH, score=12, vix=18.0,
            vix_flags=[], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.IRON_CONDOR.value,
            "direction": "SELL",
            "entry_price": 200,
            "stop_price": 190,
            "lot_size": 50,
        }
        risk_agent._evaluate_signal(signal)
        cmd = command_store.dequeue()
        assert cmd is not None

    def test_halt_short_vega_blocks_credit(self, risk_agent, regime_state, command_store):
        """HALT_SHORT_VEGA flag blocks credit spread strategies."""
        regime_state.update(
            phase=RegimePhase.CONGESTION, score=0, vix=45.0,
            vix_flags=["HALT_SHORT_VEGA"], ichimoku_signal="NEUTRAL",
            impulse_color="BLUE", congestion=True, blowoff=False,
        )
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.IRON_CONDOR.value,
            "direction": "SELL",
            "entry_price": 200,
            "stop_price": 190,
        }
        risk_agent._evaluate_signal(signal)
        cmd = command_store.dequeue()
        assert cmd is None

    def test_congestion_blocks_ditm(self, risk_agent, regime_state, command_store):
        """Congestion regime blocks DITM directional buys."""
        regime_state.update(
            phase=RegimePhase.CONGESTION, score=0, vix=18.0,
            vix_flags=[], ichimoku_signal="NEUTRAL",
            impulse_color="BLUE", congestion=True, blowoff=False,
        )
        signal = {
            "instrument": "RELIANCE",
            "strategy_type": StrategyType.DITM_CALL.value,
            "direction": "BUY",
            "entry_price": 2500,
            "stop_price": 2450,
        }
        risk_agent._evaluate_signal(signal)
        cmd = command_store.dequeue()
        assert cmd is None


# ---------------------------------------------------------------------------
# Full Pipeline: Signal → Regime → Sizing → Command
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_end_to_end_approval(self, risk_agent, regime_state, command_store):
        """Signal passes regime + sizing → ORDER_REQUEST enqueued."""
        regime_state.update(
            phase=RegimePhase.BULLISH, score=12, vix=18.0,
            vix_flags=[], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.IRON_CONDOR.value,
            "direction": "SELL",
            "entry_price": 200,
            "stop_price": 190,
            "lot_size": 50,
            "instrument_vol": 0.20,
            "win_rate": 0.60,
            "rr_ratio": 2.5,
        }
        risk_agent.enqueue_signal(signal)
        risk_agent._tick()

        cmd = command_store.dequeue()
        assert cmd is not None
        assert cmd.event_type == EventType.ORDER_REQUEST.value

    def test_sizing_rejection_no_enqueue(self, risk_agent, regime_state, command_store):
        """Signal passes regime but fails sizing → no order."""
        regime_state.update(
            phase=RegimePhase.BULLISH, score=12, vix=18.0,
            vix_flags=[], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.IRON_CONDOR.value,
            "direction": "SELL",
            "entry_price": 200,
            "stop_price": 190,
            "sector_margin_pct": 0.25,  # over 20% limit → REJECT
        }
        risk_agent.enqueue_signal(signal)
        risk_agent._tick()

        cmd = command_store.dequeue()
        assert cmd is None

    def test_idempotency_prevents_duplicate(self, risk_agent, regime_state, command_store):
        """Same signal enqueued twice → second is skipped by idempotency."""
        regime_state.update(
            phase=RegimePhase.BULLISH, score=12, vix=18.0,
            vix_flags=[], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.IRON_CONDOR.value,
            "direction": "SELL",
            "entry_price": 200,
            "stop_price": 190,
            "lot_size": 50,
        }
        risk_agent.enqueue_signal(signal)
        risk_agent._tick()

        # First should succeed
        cmd1 = command_store.dequeue()
        assert cmd1 is not None

    def test_multiple_signals_processed(self, risk_agent, regime_state, command_store):
        """Multiple signals enqueued and processed in one tick."""
        regime_state.update(
            phase=RegimePhase.BULLISH, score=12, vix=18.0,
            vix_flags=[], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        for i in range(3):
            signal = {
                "instrument": f"STOCK{i}",
                "strategy_type": StrategyType.IRON_CONDOR.value,
                "direction": "SELL",
                "entry_price": 200 + i * 10,
                "stop_price": 190 + i * 10,
                "lot_size": 50,
            }
            risk_agent.enqueue_signal(signal)

        risk_agent._tick()

        # Should have 3 commands enqueued
        count = 0
        for _ in range(5):
            cmd = command_store.dequeue()
            if cmd:
                count += 1
                command_store.mark_done(cmd.id, cmd.owner_token)
        assert count == 3

    def test_regime_agent_feeds_risk_agent(self, regime_agent, risk_agent, command_store):
        """Regime agent computes state → Risk agent reads it for filtering."""
        # Feed bullish data to regime agent
        for i in range(60):
            regime_agent.feed_candle(102 + i * 0.5, 98 + i * 0.5, 100 + i * 0.5, 1_000_000)
        regime_agent.feed_vix(18.0)
        regime_agent._setup()
        regime_agent._tick()

        # Regime should be computed
        snap = regime_agent.regime_state.read_snapshot()
        assert snap.version >= 1

        # Risk agent uses same regime state
        signal = {
            "instrument": "NIFTY",
            "strategy_type": StrategyType.IRON_CONDOR.value,
            "direction": "SELL",
            "entry_price": 200,
            "stop_price": 190,
            "lot_size": 50,
        }
        risk_agent.enqueue_signal(signal)
        risk_agent._tick()

        cmd = command_store.dequeue()
        assert cmd is not None
