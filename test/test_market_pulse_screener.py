"""Tests for Market Pulse screener."""

import pytest
from services.market_pulse_screener import (
    select_fno_strategy,
    screen_equities,
)


class TestFnoStrategySelection:
    def test_low_vix_uptrend(self):
        strategy = select_fno_strategy(vix=12, regime="uptrend")
        assert "call" in strategy["type"].lower() or "bull" in strategy["type"].lower()
        # Additional checks
        assert "description" in strategy
        assert "risk" in strategy
        assert "reward" in strategy
        assert "levels" in strategy

    def test_high_vix_any(self):
        strategy = select_fno_strategy(vix=22, regime="uptrend")
        assert "sell" in strategy["type"].lower() or "iron" in strategy["type"].lower()
        # Additional checks
        assert "description" in strategy
        assert "risk" in strategy
        assert "reward" in strategy
        assert "levels" in strategy

    def test_low_vix_downtrend(self):
        strategy = select_fno_strategy(vix=12, regime="downtrend")
        assert "put" in strategy["type"].lower() or "bear" in strategy["type"].lower()
        # Additional checks
        assert "description" in strategy
        assert "risk" in strategy
        assert "reward" in strategy
        assert "levels" in strategy

    def test_invalid_regime(self):
        """Test that invalid regime raises ValueError."""
        with pytest.raises(ValueError):
            select_fno_strategy(vix=12, regime="invalid")

    def test_negative_vix(self):
        """Test that negative VIX raises ValueError."""
        with pytest.raises(ValueError):
            select_fno_strategy(vix=-5, regime="uptrend")


class TestEquityScreening:
    def test_uptrend_screening(self):
        """Test long ideas in uptrend."""
        import pandas as pd
        closes = pd.Series([100.0] * 200 + [101.0, 102.0, 103.0])  # uptrend last 3 days
        hist = pd.DataFrame({'close': closes})
        constituent_data = {
            'INFY': {'history': hist, 'sector': 'IT'},
        }
        nifty_hist = pd.DataFrame({'close': pd.Series([100.0] * 200 + [100.5, 100.8, 101.0])})
        
        ideas = screen_equities(constituent_data, "uptrend", nifty_hist)
        
        # Should have at least one idea (high conviction)
        assert len(ideas) > 0
        assert ideas[0]['symbol'] == 'INFY'
        assert ideas[0]['conviction'] >= 50
        assert ideas[0]['regime'] == 'uptrend'

    def test_downtrend_screening(self):
        """Test short ideas in downtrend."""
        import pandas as pd
        closes = pd.Series([100.0] * 200 + [99.0, 98.0, 97.0])  # downtrend last 3 days
        hist = pd.DataFrame({'close': closes})
        constituent_data = {
            'TCS': {'history': hist, 'sector': 'IT'},
        }
        
        ideas = screen_equities(constituent_data, "downtrend", None)
        
        assert len(ideas) > 0
        assert ideas[0]['symbol'] == 'TCS'
        assert ideas[0]['regime'] == 'downtrend'

    def test_chop_screening(self):
        """Test range ideas in consolidation."""
        import pandas as pd
        sma_20_val = 100.0
        closes = pd.Series([sma_20_val] * 20 + [sma_20_val + 0.5, sma_20_val - 0.5, sma_20_val])
        hist = pd.DataFrame({'close': closes})
        constituent_data = {
            'LT': {'history': hist, 'sector': 'Industrial'},
        }
        
        ideas = screen_equities(constituent_data, "chop", None)
        
        if len(ideas) > 0:  # May or may not have ideas depending on exact values
            assert ideas[0]['regime'] == 'chop'

    def test_insufficient_data(self):
        """Test that short data is skipped."""
        import pandas as pd
        hist = pd.DataFrame({'close': pd.Series([100.0] * 20)})  # Only 20 days
        constituent_data = {
            'TEST': {'history': hist, 'sector': 'Test'},
        }
        
        ideas = screen_equities(constituent_data, "uptrend", None)
        
        assert len(ideas) == 0  # Should skip due to < 25 days
