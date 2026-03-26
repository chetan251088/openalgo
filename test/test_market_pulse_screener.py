"""Tests for Market Pulse screener."""

import re

import pytest
from services.market_pulse_screener import (
    generate_fno_ideas,
    select_fno_strategy,
    screen_equities,
)


def _extract_strike_numbers(text: str) -> list[int]:
    return [int(match) for match in re.findall(r"(\d+)(?:CE|PE)", text)]


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
        assert "bull" in strategy["type"].lower() or "credit" in strategy["type"].lower()
        assert strategy["bias"] == "bullish"

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

    def test_high_vix_downtrend_keeps_bearish_bias(self):
        """High VIX downtrend should prefer defined-risk bearish structures."""
        strategy = select_fno_strategy(vix=25, regime="downtrend")
        assert "bear" in strategy["type"].lower() or "credit" in strategy["type"].lower()
        assert strategy["bias"] == "bearish"

    def test_generate_fno_ideas_uses_correct_option_family_for_bear_put_spread(self):
        """Bear put spread must contain only put strikes and two legs."""
        ideas = generate_fno_ideas(
            regime="downtrend",
            vix=17,
            nifty_ltp=22782,
            banknifty_ltp=52042,
        )
        nifty_idea = next(idea for idea in ideas if idea["instrument"] == "NIFTY")
        assert nifty_idea["strategy"] == "Bear Put Spread"
        assert "PE /" in nifty_idea["strikes"]
        assert "CE" not in nifty_idea["strikes"]

    def test_day_mode_fno_ideas_follow_live_directional_bias(self):
        """Day mode should prefer live directional bias over the swing backdrop."""
        ideas = generate_fno_ideas(
            regime="downtrend",
            vix=18,
            nifty_ltp=22782,
            banknifty_ltp=52042,
            mode="day",
            directional_bias="LONG",
            bias_confidence=88,
        )
        nifty_idea = next(idea for idea in ideas if idea["instrument"] == "NIFTY")
        assert nifty_idea["bias"] == "bullish"
        assert "call" in nifty_idea["strategy"].lower() or "credit" in nifty_idea["strategy"].lower()

    def test_swing_iron_condor_keeps_short_legs_outside_spot(self):
        """Swing condor shorts must sit on either side of spot, not deep ITM."""
        ideas = generate_fno_ideas(
            regime="chop",
            vix=25.2,
            nifty_ltp=22912,
            banknifty_ltp=52363,
            mode="swing",
        )
        nifty_idea = next(idea for idea in ideas if idea["instrument"] == "NIFTY")
        assert nifty_idea["strategy"] == "Iron Condor"
        assert nifty_idea["strikes"] == "Buy 21800PE / Sell 22000PE / Sell 23800CE / Buy 24000CE"

    def test_day_iron_condor_is_tighter_than_swing_but_still_otm(self):
        """Day condor should be closer to spot than swing condor, but still OTM."""
        day_ideas = generate_fno_ideas(
            regime="chop",
            vix=25.2,
            nifty_ltp=22912,
            banknifty_ltp=52363,
            mode="day",
        )
        swing_ideas = generate_fno_ideas(
            regime="chop",
            vix=25.2,
            nifty_ltp=22912,
            banknifty_ltp=52363,
            mode="swing",
        )
        day_nifty = next(idea for idea in day_ideas if idea["instrument"] == "NIFTY")
        swing_nifty = next(idea for idea in swing_ideas if idea["instrument"] == "NIFTY")

        assert day_nifty["strikes"] == "Buy 22500PE / Sell 22600PE / Sell 23200CE / Buy 23300CE"
        day_strikes = _extract_strike_numbers(day_nifty["strikes"])
        swing_strikes = _extract_strike_numbers(swing_nifty["strikes"])
        assert day_strikes[1] < 22912 < day_strikes[2]
        assert swing_strikes[1] < 22912 < swing_strikes[2]
        assert (22912 - day_strikes[1]) < (22912 - swing_strikes[1])

    def test_call_credit_spread_short_call_stays_otm(self):
        """Bearish credit spreads must short calls above spot, not random deep ITM strikes."""
        ideas = generate_fno_ideas(
            regime="downtrend",
            vix=25.2,
            nifty_ltp=22912,
            banknifty_ltp=52363,
            mode="swing",
        )
        nifty_idea = next(idea for idea in ideas if idea["instrument"] == "NIFTY")
        assert nifty_idea["strategy"] == "Call Credit Spread"
        assert nifty_idea["strikes"] == "Sell 23500CE / Buy 23700CE"

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
        closes = pd.Series([100.0] * 45 + [99.0, 98.2, 97.5, 96.8, 96.0])
        highs = closes + 0.5
        lows = closes - 0.5
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows})

        constituent_data = {
            'TCS': {'history': hist, 'sector': 'IT'},
        }
        nifty_hist = pd.DataFrame({
            'close': pd.Series([100.0] * 45 + [100.3, 100.4, 100.5, 100.6, 100.7]),
            'high': pd.Series([100.5] * 50),
            'low': pd.Series([99.5] * 50),
        })

        ideas = screen_equities(constituent_data, "downtrend", nifty_hist)

        # In downtrend, should now be able to emit short setups
        assert isinstance(ideas, list)
        assert ideas
        assert ideas[0]["signal"] == "SELL"
        assert ideas[0]["entry"] is not None
        assert ideas[0]["target"] is not None
        assert ideas[0]["risk_reward"] is not None
        assert ideas[0]["risk_reward"] >= 1.25

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

    def test_day_mode_can_issue_buy_signal_from_live_tape_even_if_recent_structure_is_weak(self):
        """Day mode should not stay stuck on stale 5d weakness during a strong session rally."""
        import pandas as pd

        closes = pd.Series([120.0] * 45 + [116.0, 113.0, 111.0, 109.0, 108.0])
        highs = closes + 1.0
        lows = closes - 1.0
        volumes = pd.Series([100000] * 50)
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows, 'volume': volumes})
        constituent_data = {
            'HDFCBANK': {'history': hist, 'sector': 'BANK'},
        }
        nifty_hist = pd.DataFrame({
            'close': pd.Series([24000.0] * 45 + [23600.0, 23300.0, 23100.0, 22950.0, 22850.0]),
            'high': pd.Series([24050.0] * 50),
            'low': pd.Series([23950.0] * 50),
        })

        ideas = screen_equities(
            constituent_data,
            "uptrend",
            nifty_hist,
            mode="day",
            live_quotes={
                "HDFCBANK": {
                    "ltp": 113.5,
                    "change_pct": 3.4,
                }
            },
            benchmark_change_pct=1.9,
            intraday_context={
                "HDFCBANK": {
                    "above_vwap": True,
                    "below_vwap": False,
                    "rvol": 1.72,
                    "vwap_distance_pct": 0.84,
                }
            },
        )

        assert ideas
        assert ideas[0]["signal"] == "BUY"
        assert ideas[0]["rvol"] == 1.72
        assert ideas[0]["rs_label"] == "+1.50% vs NIFTY"
        assert "VWAP" in (ideas[0]["liquidity_note"] or "")

    def test_day_mode_skips_buy_without_intraday_confirmation(self):
        """Day mode should not issue actionable buys without VWAP/RVOL confirmation."""
        import pandas as pd

        closes = pd.Series([120.0] * 45 + [116.0, 113.0, 111.0, 109.0, 108.0])
        highs = closes + 1.0
        lows = closes - 1.0
        volumes = pd.Series([100000] * 50)
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows, 'volume': volumes})
        constituent_data = {
            'HDFCBANK': {'history': hist, 'sector': 'BANK'},
        }
        nifty_hist = pd.DataFrame({
            'close': pd.Series([24000.0] * 45 + [23600.0, 23300.0, 23100.0, 22950.0, 22850.0]),
            'high': pd.Series([24050.0] * 50),
            'low': pd.Series([23950.0] * 50),
        })

        ideas = screen_equities(
            constituent_data,
            "uptrend",
            nifty_hist,
            mode="day",
            live_quotes={
                "HDFCBANK": {
                    "ltp": 113.5,
                    "change_pct": 3.4,
                    "volume": 180000,
                }
            },
            benchmark_change_pct=1.9,
        )

        assert ideas == []

    def test_day_mode_idea_fields_fall_back_to_quote_tape_when_intraday_context_partial(self):
        """Day ideas should still expose VWAP/RVOL-style fields when bar context is partial."""
        import pandas as pd

        closes = pd.Series([120.0] * 45 + [116.0, 113.0, 111.0, 109.0, 108.0])
        highs = closes + 1.0
        lows = closes - 1.0
        volumes = pd.Series([100000] * 50)
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows, 'volume': volumes})
        constituent_data = {
            'HDFCBANK': {'history': hist, 'sector': 'BANK'},
        }
        nifty_hist = pd.DataFrame({
            'close': pd.Series([24000.0] * 45 + [23600.0, 23300.0, 23100.0, 22950.0, 22850.0]),
            'high': pd.Series([24050.0] * 50),
            'low': pd.Series([23950.0] * 50),
        })

        ideas = screen_equities(
            constituent_data,
            "uptrend",
            nifty_hist,
            mode="day",
            live_quotes={
                "HDFCBANK": {
                    "ltp": 113.5,
                    "change_pct": 3.4,
                    "volume": 180000,
                    "average_price": 112.4,
                }
            },
            benchmark_change_pct=1.9,
            intraday_context={
                "HDFCBANK": {
                    "above_vwap": True,
                    "below_vwap": False,
                }
            },
        )

        assert ideas
        assert ideas[0]["signal"] == "BUY"
        assert ideas[0]["rvol"] == 1.8
        assert ideas[0]["vwap_distance_pct"] == 0.98
        assert "RVOL 1.80x" in (ideas[0]["liquidity_note"] or "")
        assert "VWAP +0.98%" in (ideas[0]["liquidity_note"] or "")

    def test_swing_equity_ideas_include_rs_and_volume_context(self):
        """Swing ideas should surface RS and volume-vs-average context even without delivery data."""
        import pandas as pd

        closes = pd.Series([100.0] * 45 + [101.0, 102.0, 103.0, 104.0, 108.0])
        highs = closes + 1.0
        lows = closes - 1.0
        volumes = pd.Series([100000] * 40 + [120000, 125000, 130000, 140000, 220000, 240000, 250000, 260000, 280000, 320000])
        hist = pd.DataFrame({'close': closes, 'high': highs, 'low': lows, 'volume': volumes})
        constituent_data = {
            'RELIANCE': {
                'history': hist,
                'sector': 'ENERGY',
                'delivery_pct': 50.52,
                'avg_delivery_pct_10d': 44.10,
                'delivery_vs_10d_avg': 1.15,
            },
        }
        nifty_hist = pd.DataFrame({
            'close': pd.Series([22000.0] * 45 + [22050.0, 22100.0, 22150.0, 22200.0, 22250.0]),
            'high': pd.Series([22100.0] * 50),
            'low': pd.Series([21900.0] * 50),
        })

        ideas = screen_equities(
            constituent_data,
            "uptrend",
            nifty_hist,
            mode="swing",
        )

        assert ideas
        idea = ideas[0]
        assert idea["rs_label"].endswith("vs NIFTY")
        assert idea["volume_vs_10d_avg"] is not None
        assert "10D avg" in (idea["liquidity_note"] or "")
        assert "Delivery 50.5%" in (idea["liquidity_note"] or "")
        assert "1.15x 10D deliv" in (idea["liquidity_note"] or "")

    def test_generate_fno_ideas_rationale_includes_tape_and_oi_context(self):
        """F&O ideas should expose tape confirmation and option-writer levels where available."""
        ideas = generate_fno_ideas(
            regime="uptrend",
            vix=18.4,
            nifty_ltp=22912,
            banknifty_ltp=None,
            mode="day",
            directional_bias="LONG",
            bias_confidence=82,
            options_context={
                "NIFTY": {
                    "call_wall": {"strike": 23000},
                    "put_wall": {"strike": 22800},
                    "max_pain": 22900,
                }
            },
            market_levels={
                "NIFTY": {
                    "state": "above_pdh",
                }
            },
            intraday_context={
                "NIFTY": {
                    "vwap_distance_pct": 0.62,
                    "rvol": 1.58,
                }
            },
        )

        assert ideas
        rationale = ideas[0]["rationale"]
        assert "Day bias long 82/100" in rationale
        assert "above VWAP 0.62%" in rationale
        assert "RVOL 1.58x" in rationale
        assert "trading above PDH" in rationale
        assert "put wall 22800" in rationale
        assert "call wall 23000" in rationale
