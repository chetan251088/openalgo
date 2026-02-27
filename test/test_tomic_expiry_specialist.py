"""Tests for ExpirySpecialist."""
import pytest
from datetime import date, datetime
from unittest.mock import MagicMock
from tomic.agents.expiry_specialist import (
    ExpirySpecialist, is_expiry_day, is_after_gamma_entry_time,
)
from tomic.config import TomicConfig


def test_nifty_expiry_thursday():
    # 2026-02-26 is a Thursday
    assert is_expiry_day("NIFTY", date(2026, 2, 26)) is True
    assert is_expiry_day("NIFTY", date(2026, 2, 27)) is False  # Friday


def test_banknifty_expiry_wednesday():
    # 2026-02-25 is a Wednesday
    assert is_expiry_day("BANKNIFTY", date(2026, 2, 25)) is True
    assert is_expiry_day("BANKNIFTY", date(2026, 2, 26)) is False  # Thursday


def test_sensex_expiry_friday():
    # 2026-02-27 is a Friday
    assert is_expiry_day("SENSEX", date(2026, 2, 27)) is True
    assert is_expiry_day("SENSEX", date(2026, 2, 26)) is False


def test_after_gamma_entry_time():
    dt = datetime(2026, 2, 26, 14, 1, 0)
    assert is_after_gamma_entry_time(dt, "14:00") is True
    dt_before = datetime(2026, 2, 26, 13, 59, 0)
    assert is_after_gamma_entry_time(dt_before, "14:00") is False


def test_gamma_signal_blocked_before_entry_time():
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    dt = datetime(2026, 2, 26, 13, 0, 0)  # before 14:00
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])
    assert len(signals) == 0


def test_gamma_signal_generated_after_entry_time():
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    # Thursday 14:01 → NIFTY expiry day, after gamma entry time
    dt = datetime(2026, 2, 26, 14, 1, 0)
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])
    assert len(signals) == 1
    assert signals[0].signal_dict["strategy_type"] == "GAMMA_CAPTURE"
    assert signals[0].signal_dict["instrument"] == "NIFTY"


def test_gamma_signal_not_generated_twice():
    """Once generated for the day, don't regenerate."""
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    dt = datetime(2026, 2, 26, 14, 1, 0)
    spec.get_gamma_signals(now=dt, instruments=["NIFTY"])  # first call
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])  # second call
    assert len(signals) == 0  # already generated today


def test_reset_for_new_day_clears_state():
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    dt = datetime(2026, 2, 26, 14, 1, 0)
    spec.get_gamma_signals(now=dt, instruments=["NIFTY"])
    spec.reset_for_new_day()
    # After reset, should generate again
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])
    assert len(signals) == 1
