"""Tests for Market Pulse scoring engine."""

from services.market_pulse_scoring import (
    apply_intraday_tape_overrides,
    assess_intraday_tape,
    compute_directional_bias,
    compute_market_quality,
    get_decision,
    get_trade_decision,
    score_breadth,
    score_macro,
    score_trend,
    score_volatility,
)


class TestVolatilityScoring:
    def test_optimal_vix_range(self):
        """VIX in 11-16 should score high."""
        score, rules = score_volatility(
            vix_current=13.5, vix_slope_5d=-0.5, vix_percentile=40, pcr=1.0
        )
        assert 70 <= score <= 100, f"Optimal VIX should score high, got {score}"
        assert rules

    def test_spike_vix(self):
        """VIX above 25 should score low."""
        score, _ = score_volatility(
            vix_current=28, vix_slope_5d=3.0, vix_percentile=90, pcr=1.5
        )
        assert score < 40, f"Spike VIX should score low, got {score}"

    def test_complacency_penalty(self):
        """VIX below 10 should get complacency penalty."""
        score, _ = score_volatility(
            vix_current=9, vix_slope_5d=0, vix_percentile=5, pcr=0.8
        )
        assert score < 80, f"Complacent VIX should be penalized, got {score}"

    def test_missing_data_graceful(self):
        """Should handle None values gracefully."""
        score, _ = score_volatility(
            vix_current=None, vix_slope_5d=None, vix_percentile=None, pcr=None
        )
        assert 0 <= score <= 100


class TestTrendScoring:
    def test_strong_uptrend(self):
        """All MAs bullish should score high."""
        score, _ = score_trend(
            nifty_ltp=24500,
            sma_20=24200,
            sma_50=23800,
            sma_200=22500,
            banknifty_ltp=52000,
            banknifty_sma50=51000,
            rsi=58,
            slope_50d=0.5,
            slope_200d=0.2,
        )
        assert score >= 75, f"Strong uptrend should score high, got {score}"

    def test_downtrend(self):
        """Below all MAs should score low."""
        score, _ = score_trend(
            nifty_ltp=21000,
            sma_20=22000,
            sma_50=23000,
            sma_200=24000,
            banknifty_ltp=48000,
            banknifty_sma50=50000,
            rsi=35,
            slope_50d=-0.5,
            slope_200d=-0.2,
        )
        assert score < 40, f"Downtrend should score low, got {score}"


class TestBreadthScoring:
    def test_broad_participation(self):
        """Strong breadth should score high."""
        score, _ = score_breadth(
            ad_ratio=2.5,
            pct_above_50d=75,
            pct_above_200d=85,
            highs_52w=80,
            lows_52w=10,
        )
        assert score >= 75

    def test_narrow_breadth(self):
        """Weak breadth should score low."""
        score, _ = score_breadth(
            ad_ratio=0.4,
            pct_above_50d=25,
            pct_above_200d=35,
            highs_52w=5,
            lows_52w=50,
        )
        assert score < 40

    def test_strong_ad_ratio_rule_is_positive(self):
        """A very strong A/D ratio must never be marked negative."""
        _, rules = score_breadth(
            ad_ratio=9.84,
            pct_above_50d=14,
            pct_above_200d=0,
            highs_52w=21,
            lows_52w=21,
        )
        ad_rule = next(rule for rule in rules if rule["rule"] == "Advance/Decline ratio")
        assert ad_rule["impact"] == "positive"


class TestMacroScoring:
    def test_missing_global_risk_proxy_is_neutral(self):
        """Missing correlation should not flow into the positive branch."""
        score, rules = score_macro(
            usdinr_slope_5d=0.1,
            usdinr_slope_20d=0.2,
            vix_current=14,
            event_hours_away=120,
            event_type=None,
            nifty_usdinr_corr=None,
        )
        assert 0 <= score <= 100
        assert any(rule["rule"] == "Global risk proxy unavailable" for rule in rules)

    def test_bearish_institutional_flows_pull_macro_score_down(self):
        """Defensive FII/DII positioning should count as a macro negative."""
        score, rules = score_macro(
            usdinr_slope_5d=0.1,
            usdinr_slope_20d=0.2,
            vix_current=22,
            event_hours_away=120,
            event_type=None,
            nifty_usdinr_corr=0.7,
            institutional_flows={
                "latest": {
                    "fii_net": -5500,
                    "dii_net": 5700,
                    "headline_bias": "bearish",
                },
                "five_day": {"fii_net": -25000},
            },
        )
        assert score < 60
        assert any(rule["rule"] == "Institutional flows defensive" for rule in rules)

    def test_stale_institutional_flows_become_neutral(self):
        """Stale flows should not contribute directional confidence."""
        score, rules = score_macro(
            usdinr_slope_5d=0.1,
            usdinr_slope_20d=0.2,
            vix_current=18,
            event_hours_away=120,
            event_type=None,
            nifty_usdinr_corr=0.2,
            institutional_flows={
                "freshness": {
                    "is_stale": True,
                    "lag_business_days": 2,
                    "latest_trading_date": "2026-03-20",
                },
                "latest": {
                    "fii_net": -5500,
                    "dii_net": 5700,
                    "headline_bias": "bearish",
                },
            },
        )
        assert 0 <= score <= 100
        assert any(rule["rule"] == "Institutional flows stale" for rule in rules)


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

    def test_trade_decision_can_promote_high_conviction_short_setup(self):
        assert get_trade_decision(37, 58, "SHORT", 79) == "CAUTION"

    def test_trade_decision_respects_neutral_bias(self):
        assert get_trade_decision(37, 58, "NEUTRAL", 42) == "NO"


class TestIntradayTape:
    def test_intraday_tape_detects_bullish_thrust(self):
        tape = assess_intraday_tape(
            nifty_change_pct=1.91,
            banknifty_change_pct=2.46,
            vix_change_pct=-6.92,
            sector_returns_1d=[1.2] * 11 + [-0.1],
            advances=47,
            declines=3,
            unchanged=0,
        )
        assert tape["bias"] == "LONG"
        assert tape["confidence"] >= 70
        assert tape["score"] >= 70

    def test_day_mode_tape_override_lifts_slow_scores(self):
        tape = {
            "bias": "LONG",
            "confidence": 86,
            "score": 88,
            "rules": [{"rule": "Session tape", "detail": "Bullish intraday thrust", "impact": "positive"}],
        }
        adjusted = apply_intraday_tape_overrides(
            mode="day",
            trend_score=28,
            trend_rules=[],
            momentum_score=32,
            momentum_rules=[],
            breadth_score=35,
            breadth_rules=[],
            tape=tape,
        )
        assert adjusted["trend_score"] > 28
        assert adjusted["momentum_score"] > 32
        assert adjusted["breadth_score"] > 35


class TestDirectionalBias:
    def test_directional_bias_detects_short_regime(self):
        bias = compute_directional_bias(
            regime="downtrend",
            trend_score=28,
            momentum_score=32,
            breadth_score=35,
            vix_current=24,
            institutional_flows={
                "latest": {
                    "headline_bias": "bearish",
                    "cash_bias": "bearish",
                    "derivatives_bias": "bearish",
                }
            },
        )
        assert bias["bias"] == "SHORT"
        assert bias["confidence"] >= 70

    def test_day_mode_can_flip_long_against_structural_downtrend_when_tape_is_strong(self):
        tape = {
            "bias": "LONG",
            "confidence": 90,
            "score": 92,
            "rules": [],
        }
        bias = compute_directional_bias(
            regime="downtrend",
            trend_score=42,
            momentum_score=60,
            breadth_score=72,
            vix_current=18,
            institutional_flows=None,
            mode="day",
            intraday_tape=tape,
        )
        assert bias["bias"] == "LONG"
        assert bias["confidence"] >= 65


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
        assert quality == 73
