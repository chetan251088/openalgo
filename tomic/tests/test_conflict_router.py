"""
Test Suite: TOMIC Conflict Router
=================================
Validates regime master-filter behavior, source priority rules, and
allocation gates (sector heat, max positions).
"""

from __future__ import annotations

import pytest

from tomic.agents.regime_agent import AtomicRegimeState
from tomic.agents.sniper_agent import PatternType, SniperSignal
from tomic.agents.volatility_agent import VolSignal
from tomic.config import RegimePhase, StrategyType, TomicConfig
from tomic.conflict_router import ConflictRouter, ResolutionAction, SignalSource


@pytest.fixture
def config() -> TomicConfig:
    return TomicConfig.load("sandbox")


@pytest.fixture
def regime_state() -> AtomicRegimeState:
    return AtomicRegimeState()


@pytest.fixture
def router(config: TomicConfig, regime_state: AtomicRegimeState) -> ConflictRouter:
    return ConflictRouter(config=config, regime_state=regime_state)


def _set_phase(regime_state: AtomicRegimeState, phase: RegimePhase) -> None:
    regime_state.update(
        phase=phase,
        score=0,
        vix=18.0,
        vix_flags=[],
        ichimoku_signal="NEUTRAL",
        impulse_color="BLUE",
        congestion=phase == RegimePhase.CONGESTION,
        blowoff=phase == RegimePhase.BLOWOFF,
    )


def _sniper(instrument: str, direction: str, score: float = 70.0) -> SniperSignal:
    return SniperSignal(
        instrument=instrument,
        pattern=PatternType.VCP,
        direction=direction,
        entry_price=100.0,
        stop_price=95.0,
        rs_score=65.0,
        signal_score=score,
    )


def _vol(underlying: str, direction: str, strength: float = 80.0) -> VolSignal:
    return VolSignal(
        underlying=underlying,
        strategy_type=StrategyType.IRON_CONDOR,
        direction=direction,
        signal_strength=strength,
        reason="test",
    )


class TestConflictRouterRegimeFilter:
    def test_congestion_defers_sniper_only(self, router: ConflictRouter, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.CONGESTION)
        approved = router.route(sniper_signals=[_sniper("RELIANCE", "BUY")], vol_signals=[])

        assert approved == []
        assert len(router.decisions) == 1
        assert router.decisions[0].action == ResolutionAction.DEFER
        assert "congestion" in router.decisions[0].reason.lower()

    def test_congestion_prioritizes_volatility(self, router: ConflictRouter, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.CONGESTION)
        approved = router.route(
            sniper_signals=[_sniper("NIFTY", "BUY", score=90.0)],
            vol_signals=[_vol("NIFTY", "SELL", strength=60.0)],
        )

        assert len(approved) == 1
        assert approved[0].source == SignalSource.VOLATILITY
        assert approved[0].route_decision is not None
        assert approved[0].route_decision.action == ResolutionAction.ACCEPT

    def test_bearish_blocks_sniper_buy_when_alone(self, router: ConflictRouter, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.BEARISH)
        approved = router.route(sniper_signals=[_sniper("SBIN", "BUY")], vol_signals=[])

        assert approved == []
        assert len(router.decisions) == 1
        assert router.decisions[0].action == ResolutionAction.REJECT
        assert "blocked" in router.decisions[0].reason.lower()

    def test_bullish_prioritizes_sniper_on_conflict(self, router: ConflictRouter, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.BULLISH)
        approved = router.route(
            sniper_signals=[_sniper("NIFTY", "BUY", score=80.0)],
            vol_signals=[_vol("NIFTY", "SELL", strength=90.0)],
        )

        assert len(approved) == 1
        assert approved[0].source == SignalSource.SNIPER
        assert "bullish" in (approved[0].route_decision.reason if approved[0].route_decision else "").lower()

    def test_blowoff_defers_all(self, router: ConflictRouter, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.BLOWOFF)
        approved = router.route(
            sniper_signals=[_sniper("NIFTY", "BUY"), _sniper("SBIN", "SELL")],
            vol_signals=[_vol("NIFTY", "SELL"), _vol("SBIN", "SELL")],
        )

        assert approved == []
        assert all(d.action == ResolutionAction.DEFER for d in router.decisions)


class TestConflictRouterAllocationGates:
    def test_position_cap_blocks_entries(self, config: TomicConfig, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.BULLISH)
        router = ConflictRouter(config=config, regime_state=regime_state)
        router.update_position_count(config.sizing.max_positions)

        approved = router.route(sniper_signals=[_sniper("RELIANCE", "BUY")], vol_signals=[])

        assert approved == []
        assert len(router.decisions) == 1
        assert router.decisions[0].action == ResolutionAction.REJECT
        assert "position limit" in router.decisions[0].reason

    def test_sector_heat_blocks_entries(self, config: TomicConfig, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.BULLISH)
        router = ConflictRouter(config=config, regime_state=regime_state)
        router.update_sector_heat("INDEX", config.sizing.sector_heat_limit)

        approved = router.route(
            sniper_signals=[],
            vol_signals=[_vol("NIFTY", "SELL")],
        )

        assert approved == []
        assert len(router.decisions) == 1
        assert router.decisions[0].action == ResolutionAction.REJECT
        assert "sector heat" in router.decisions[0].reason

    def test_outputs_sorted_by_priority(self, router: ConflictRouter, regime_state: AtomicRegimeState):
        _set_phase(regime_state, RegimePhase.BULLISH)
        approved = router.route(
            sniper_signals=[
                _sniper("RELIANCE", "BUY", score=90.0),
                _sniper("SBIN", "BUY", score=50.0),
            ],
            vol_signals=[],
        )

        assert len(approved) == 2
        assert approved[0].priority_score >= approved[1].priority_score


class TestSniperSignalMapping:
    def test_sniper_sell_maps_to_ditm_put_buy(self):
        sig = _sniper("NIFTY", "SELL", score=75.0)
        payload = sig.to_signal_dict()

        assert payload["strategy_type"] == StrategyType.DITM_PUT.value
        assert payload["direction"] == "BUY"
        assert payload["option_type"] == "PE"
        assert payload["signal_direction"] == "SELL"
