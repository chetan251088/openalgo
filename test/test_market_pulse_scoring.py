"""Tests for Market Pulse scoring engine."""

import pytest
from services.market_pulse_scoring import (
    score_volatility,
    score_momentum,
    score_trend,
    score_breadth,
    score_macro,
    compute_market_quality,
    get_decision,
)


class TestVolatilityScoring:
    def test_optimal_vix_range(self):
        """VIX in 11-16 should score high."""
        score, rules = score_volatility(
            vix_current=13.5, vix_slope_5d=-0.5, vix_percentile=40, pcr=1.0
        )
        assert 70 <= score <= 100, f"Optimal VIX should score high, got {score}"

    def test_spike_vix(self):
        """VIX above 25 should score low."""
        score, rules = score_volatility(
            vix_current=28, vix_slope_5d=3.0, vix_percentile=90, pcr=1.5
        )
        assert score < 40, f"Spike VIX should score low, got {score}"

    def test_complacency_penalty(self):
        """VIX below 10 should get complacency penalty."""
        score, rules = score_volatility(
            vix_current=9, vix_slope_5d=0, vix_percentile=5, pcr=0.8
        )
        assert score < 80, f"Complacent VIX should be penalized, got {score}"

    def test_missing_data_graceful(self):
        """Should handle None values gracefully."""
        score, rules = score_volatility(
            vix_current=None, vix_slope_5d=None, vix_percentile=None, pcr=None
        )
        assert 0 <= score <= 100


class TestTrendScoring:
    def test_strong_uptrend(self):
        """All MAs bullish should score high."""
        score, rules = score_trend(
            nifty_ltp=24500, sma_20=24200, sma_50=23800, sma_200=22500,
            banknifty_ltp=52000, banknifty_sma50=51000,
            rsi=58, slope_50d=0.5, slope_200d=0.2,
        )
        assert score >= 75, f"Strong uptrend should score high, got {score}"

    def test_downtrend(self):
        """Below all MAs should score low."""
        score, rules = score_trend(
            nifty_ltp=21000, sma_20=22000, sma_50=23000, sma_200=24000,
            banknifty_ltp=48000, banknifty_sma50=50000,
            rsi=35, slope_50d=-0.5, slope_200d=-0.2,
        )
        assert score < 40, f"Downtrend should score low, got {score}"


class TestBreadthScoring:
    def test_broad_participation(self):
        """Strong breadth should score high."""
        score, rules = score_breadth(
            ad_ratio=2.5, pct_above_50d=75, pct_above_200d=85,
            highs_52w=80, lows_52w=10,
        )
        assert score >= 75

    def test_narrow_breadth(self):
        """Weak breadth should score low."""
        score, rules = score_breadth(
            ad_ratio=0.4, pct_above_50d=25, pct_above_200d=35,
            highs_52w=5, lows_52w=50,
        )
        assert score < 40


class TestDecisionLogic:
    def test_yes_decision(self):
        assert get_decision(85) == "YES"

    def test_caution_decision(self):
        assert get_decision(70) == "CAUTION"

    def test_no_decision(self):
        assert get_decision(45) == "NO"

    def test_boundary_80(self):
        assert get_decision(80) == "YES"

    def test_boundary_60(self):
        assert get_decision(60) == "CAUTION"

    def test_boundary_59(self):
        assert get_decision(59) == "NO"


class TestMarketQuality:
    def test_weighted_average(self):
        """Market quality should be weighted average of 5 scores."""
        scores = {
            "volatility": 80,
            "momentum": 70,
            "trend": 90,
            "breadth": 60,
            "macro": 50,
        }
        quality = compute_market_quality(scores)
        # 80*0.25 + 70*0.25 + 90*0.20 + 60*0.20 + 50*0.10
        # = 20 + 17.5 + 18 + 12 + 5 = 72.5
        assert quality == 73  # rounded
