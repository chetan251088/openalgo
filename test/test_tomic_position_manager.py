"""Tests for PositionManager P&L thresholds."""
import pytest
from unittest.mock import MagicMock
from tomic.agents.position_manager import (
    PositionManager, PositionState, PositionAction,
    evaluate_position,
)
from tomic.config import TomicConfig


def test_profit_target_triggers_close():
    # Entered for credit of 100, now worth 50 (50% profit)
    action = evaluate_position(
        entry_credit=100.0,
        current_value=50.0,
        trail_stop_activated=False,
    )
    assert action == PositionAction.CLOSE_PROFIT


def test_stop_loss_triggers_close():
    # Credit 100, current value 210 (debit 210 > 2× credit)
    action = evaluate_position(
        entry_credit=100.0,
        current_value=210.0,
        trail_stop_activated=False,
    )
    assert action == PositionAction.CLOSE_LOSS


def test_trail_stop_activates_at_30pct():
    # At 30% profit, trail stop should activate
    action = evaluate_position(
        entry_credit=100.0,
        current_value=70.0,   # 30% profit (was 100, now costs 70)
        trail_stop_activated=False,
    )
    assert action == PositionAction.ACTIVATE_TRAIL


def test_hold_in_normal_range():
    # 15% profit — no action yet
    action = evaluate_position(
        entry_credit=100.0,
        current_value=85.0,
        trail_stop_activated=False,
    )
    assert action == PositionAction.HOLD


def test_trail_stop_closes_if_reversed():
    # Trail was activated at 30%, now reversed back above breakeven
    action = evaluate_position(
        entry_credit=100.0,
        current_value=105.0,  # now losing! trail stop triggers close
        trail_stop_activated=True,
    )
    assert action == PositionAction.CLOSE_TRAIL


def test_position_state_tracks_credits():
    state = PositionState(
        instrument="NIFTY",
        strategy_tag="TOMIC_IRON_CONDOR_NIFTY",
        entry_credit=120.0,
        lots=1,
    )
    assert state.pnl_pct(current_value=60.0) == pytest.approx(0.50)
    assert state.pnl_pct(current_value=240.0) == pytest.approx(-1.0)


def test_register_and_unregister_position():
    config = TomicConfig()
    position_book = MagicMock()
    manager = PositionManager(config=config, position_book=position_book)
    manager.register_position(
        strategy_tag="TOMIC_IC_NIFTY",
        instrument="NIFTY",
        entry_credit=200.0,
        lots=1,
    )
    states = manager.get_states()
    assert "TOMIC_IC_NIFTY" in states
    assert states["TOMIC_IC_NIFTY"]["entry_credit"] == 200.0

    manager.unregister_position("TOMIC_IC_NIFTY")
    states = manager.get_states()
    assert "TOMIC_IC_NIFTY" not in states
