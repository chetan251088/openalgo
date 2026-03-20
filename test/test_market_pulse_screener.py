"""Tests for Market Pulse screener."""

import pytest
from services.market_pulse_screener import (
    select_fno_strategy,
)


class TestFnoStrategySelection:
    def test_low_vix_uptrend(self):
        strategy = select_fno_strategy(vix=12, regime="uptrend")
        assert "call" in strategy["type"].lower() or "bull" in strategy["type"].lower()

    def test_high_vix_any(self):
        strategy = select_fno_strategy(vix=22, regime="uptrend")
        assert "sell" in strategy["type"].lower() or "iron" in strategy["type"].lower()

    def test_low_vix_downtrend(self):
        strategy = select_fno_strategy(vix=12, regime="downtrend")
        assert "put" in strategy["type"].lower() or "bear" in strategy["type"].lower()
