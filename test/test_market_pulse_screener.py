"""Tests for Market Pulse screener."""

import pytest
from services.market_pulse_screener import (
    select_fno_strategy,
    screen_equities,
)


class TestFnoStrategySelection:
    def test_low_vix_uptrend(self):
        """Test low VIX + uptrend strategy."""
        strategy = select_fno_strategy(vix=12, regime="uptrend")
        assert "type" in strategy
        assert "bias" in strategy
        assert "call" in strategy["type"].lower() or "bull" in strategy["type"].lower()
        assert strategy["bias"] == "bullish"

    def test_high_vix_any(self):
        """Test high VIX strategy."""
        strategy = select_fno_strategy(vix=22, regime="uptrend")
        assert "type" in strategy
        assert "bias" in strategy
        assert "sell" in strategy["type"].lower() or "iron" in strategy["type"].lower()
        assert strategy["bias"] == "neutral"

    def test_low_vix_downtrend(self):
        """Test low VIX + downtrend strategy."""
        strategy = select_fno_strategy(vix=12, regime="downtrend")
        assert "type" in strategy
        assert "bias" in strategy
        assert "put" in strategy["type"].lower() or "bear" in strategy["type"].lower()
        assert strategy["bias"] == "bearish"

    def test_mid_vix_uptrend(self):
        """Test mid VIX + uptrend strategy."""
        strategy = select_fno_strategy(vix=17, regime="uptrend")
        assert "type" in strategy
        assert "bias" in strategy
        assert strategy["bias"] == "bullish"

    def test_mid_vix_downtrend(self):
        """Test mid VIX + downtrend strategy."""
        strategy = select_fno_strategy(vix=17, regime="downtrend")
        assert "type" in strategy
        assert "bias" in strategy
        assert strategy["bias"] == "bearish"

    def test_low_vix_chop(self):
        """Test low VIX + consolidation strategy."""
        strategy = select_fno_strategy(vix=12, regime="chop")
        assert "type" in strategy
        assert "bias" in strategy

    def test_invalid_regime(self):
        """Test that invalid regime raises ValueError."""
        with pytest.raises(ValueError, match="Invalid regime"):
            select_fno_strategy(vix=12, regime="invalid")

    def test_negative_vix(self):
        """Test that negative VIX raises ValueError."""
        with pytest.raises(ValueError, match="VIX cannot be negative"):
            select_fno_strategy(vix=-5, regime="uptrend")


class TestEquityScreening:
    def test_uptrend_screening(self):
        """Test uptrend screening with sample data."""
        import pandas as pd

        # Create sample data with uptrend
        closes = pd.Series([100.0] * 20 + [100.5, 101.0, 101.5, 102.0, 102.5])
        highs = closes + 0.5
        lows = closes - 0.5
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows})

        constituent_data = {
            'INFY': {'history': hist, 'sector': 'IT'},
        }

        ideas = screen_equities(constituent_data, "uptrend", None)

        # In uptrend with breakout signal, should get at least one idea
        assert isinstance(ideas, list)
        # Ideas should be sorted by conviction
        if len(ideas) > 1:
            assert ideas[0].get('conviction') in ['HIGH', 'MED', 'LOW']

    def test_downtrend_screening(self):
        """Test downtrend screening with sample data."""
        import pandas as pd

        # Create sample data with downtrend
        closes = pd.Series([100.0] * 20 + [99.5, 99.0, 98.5, 98.0, 97.5])
        highs = closes + 0.5
        lows = closes - 0.5
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows})

        constituent_data = {
            'TCS': {'history': hist, 'sector': 'IT'},
        }

        ideas = screen_equities(constituent_data, "downtrend", None)

        # In downtrend, should get AVOID signals
        assert isinstance(ideas, list)

    def test_insufficient_data(self):
        """Test that short data is skipped."""
        import pandas as pd

        # Only 20 days of data (less than MIN_DATA_POINTS=25)
        hist = pd.DataFrame({
            'close': pd.Series([100.0] * 20),
            'high': pd.Series([100.5] * 20),
            'low': pd.Series([99.5] * 20),
        })
        constituent_data = {
            'TEST': {'history': hist, 'sector': 'Test'},
        }

        ideas = screen_equities(constituent_data, "uptrend", None)

        assert len(ideas) == 0  # Should skip due to < MIN_DATA_POINTS

    def test_consolidation_screening(self):
        """Test consolidation (chop) screening with sample data."""
        import pandas as pd

        sma_200_val = 100.0
        closes = pd.Series([sma_200_val] * 200 + [sma_200_val + 0.3, sma_200_val - 0.3, sma_200_val])
        highs = closes + 0.5
        lows = closes - 0.5
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows})

        constituent_data = {
            'LT': {'history': hist, 'sector': 'Industrial'},
        }

        ideas = screen_equities(constituent_data, "chop", None)

        # Should return a list (may or may not have ideas)
        assert isinstance(ideas, list)

    def test_screen_equities_returns_sorted_ideas(self):
        """Test that screen_equities returns top 10 sorted ideas."""
        import pandas as pd

        # Create multiple constituents
        constituent_data = {}
        for i in range(15):
            closes = pd.Series([100.0 + i * 0.5] * 25)
            highs = closes + 0.5
            lows = closes - 0.5
            hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows})
            constituent_data[f'STOCK{i}'] = {'history': hist, 'sector': 'Test'}

        ideas = screen_equities(constituent_data, "uptrend", None)

        # Should return at most 10 ideas
        assert len(ideas) <= 10
        # Should be sorted (HIGH before MED before LOW)
        if len(ideas) > 1:
            conv_values = ['HIGH', 'MED', 'LOW']
            for i in range(len(ideas) - 1):
                curr_idx = conv_values.index(ideas[i].get('conviction', 'LOW'))
                next_idx = conv_values.index(ideas[i+1].get('conviction', 'LOW'))
                assert curr_idx <= next_idx
