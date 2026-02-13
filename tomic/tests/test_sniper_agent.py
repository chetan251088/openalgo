from __future__ import annotations

from tomic.agents.regime_agent import AtomicRegimeState
from tomic.agents.sniper_agent import SniperAgent
from tomic.config import RegimePhase


def _snapshot(phase: RegimePhase):
    state = AtomicRegimeState()
    state.update(
        phase=phase,
        score=0,
        vix=15.0,
        vix_flags=[],
        ichimoku_signal="NEUTRAL",
        impulse_color="BLUE",
        congestion=(phase == RegimePhase.CONGESTION),
        blowoff=(phase == RegimePhase.BLOWOFF),
    )
    return state.read_snapshot()


def test_bullish_allows_only_buy_direction() -> None:
    agent = SniperAgent.__new__(SniperAgent)
    snap = _snapshot(RegimePhase.BULLISH)
    assert SniperAgent._allowed_direction(agent, snap, "BUY") == "BUY"  # type: ignore[arg-type]
    assert SniperAgent._allowed_direction(agent, snap, "SELL") is None  # type: ignore[arg-type]


def test_bearish_allows_only_sell_direction() -> None:
    agent = SniperAgent.__new__(SniperAgent)
    snap = _snapshot(RegimePhase.BEARISH)
    assert SniperAgent._allowed_direction(agent, snap, "SELL") == "SELL"  # type: ignore[arg-type]
    assert SniperAgent._allowed_direction(agent, snap, "BUY") is None  # type: ignore[arg-type]


def test_congestion_and_blowoff_block_directional_entries() -> None:
    agent = SniperAgent.__new__(SniperAgent)
    congestion = _snapshot(RegimePhase.CONGESTION)
    blowoff = _snapshot(RegimePhase.BLOWOFF)
    assert SniperAgent._allowed_direction(agent, congestion, "BUY") is None  # type: ignore[arg-type]
    assert SniperAgent._allowed_direction(agent, blowoff, "SELL") is None  # type: ignore[arg-type]
