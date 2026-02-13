"""
TOMIC Regime Agent Tests
=========================
Test Ichimoku, Impulse System, congestion/blowoff detection,
VIX flags, regime scoring, AtomicRegimeState, and RegimeAgent tick.
"""

import pytest
import time
from tomic.agents.regime_agent import (
    AtomicRegimeState,
    RegimeSnapshot,
    compute_ichimoku,
    compute_impulse_system,
    compute_regime_score,
    compute_vix_flags,
    detect_blowoff,
    detect_congestion,
    score_to_phase,
    _compute_ema,
    _compute_atr,
)
from tomic.config import RegimePhase, TomicConfig, VIXRules


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vix_rules():
    return VIXRules()


@pytest.fixture
def state():
    return AtomicRegimeState()


def _bullish_ohlcv(n=60):
    """Generate rising OHLCV data."""
    closes = [100 + i * 0.5 for i in range(n)]
    highs = [c + 2 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1_000_000] * n
    return highs, lows, closes, volumes


def _bearish_ohlcv(n=60):
    """Generate declining OHLCV data."""
    closes = [200 - i * 0.5 for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 2 for c in closes]
    volumes = [1_000_000] * n
    return highs, lows, closes, volumes


def _flat_ohlcv(n=60):
    """Generate flat (congestion) OHLCV data."""
    closes = [100 + (i % 3) * 0.1 for i in range(n)]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    volumes = [500_000] * n
    return highs, lows, closes, volumes


# ---------------------------------------------------------------------------
# Ichimoku
# ---------------------------------------------------------------------------

class TestIchimoku:
    def test_bullish_above_cloud(self):
        highs, lows, closes, _ = _bullish_ohlcv(60)
        result = compute_ichimoku(highs, lows, closes)
        assert result["signal"] == "BULLISH"
        assert result["price"] > result["cloud_top"]

    def test_bearish_below_cloud(self):
        highs, lows, closes, _ = _bearish_ohlcv(60)
        result = compute_ichimoku(highs, lows, closes)
        assert result["signal"] == "BEARISH"
        assert result["price"] < result["cloud_bottom"]

    def test_insufficient_data_neutral(self):
        result = compute_ichimoku([1, 2], [0, 1], [0.5, 1.5])
        assert result["signal"] == "NEUTRAL"

    def test_tenkan_kijun_calculated(self):
        highs, lows, closes, _ = _bullish_ohlcv(60)
        result = compute_ichimoku(highs, lows, closes)
        assert result["tenkan"] > 0
        assert result["kijun"] > 0
        assert result["senkou_a"] > 0
        assert result["senkou_b"] > 0


# ---------------------------------------------------------------------------
# Impulse System
# ---------------------------------------------------------------------------

class TestImpulseSystem:
    def test_green_in_uptrend(self):
        _, _, closes, _ = _bullish_ohlcv(60)
        result = compute_impulse_system(closes)
        # Rising EMA in uptrend
        assert result["color"] in ("GREEN", "BLUE")  # MACD histogram may lag
        assert result["ema_rising"] is True

    def test_red_in_downtrend(self):
        _, _, closes, _ = _bearish_ohlcv(60)
        result = compute_impulse_system(closes)
        assert result["color"] in ("RED", "BLUE")  # MACD histogram may lag
        assert result["ema_rising"] is False

    def test_insufficient_data_blue(self):
        result = compute_impulse_system([1, 2, 3])
        assert result["color"] == "BLUE"

    def test_ema_computed(self):
        _, _, closes, _ = _bullish_ohlcv(60)
        result = compute_impulse_system(closes)
        assert result["ema"] > 0


# ---------------------------------------------------------------------------
# Congestion Detection
# ---------------------------------------------------------------------------

class TestCongestionDetection:
    def test_flat_market_detected(self):
        highs, lows, closes, _ = _flat_ohlcv(60)
        result = detect_congestion(highs, lows, closes)
        assert result is True

    def test_trending_not_congestion(self):
        highs, lows, closes, _ = _bullish_ohlcv(60)
        result = detect_congestion(highs, lows, closes)
        assert result is False

    def test_insufficient_data(self):
        assert detect_congestion([1], [0], [0.5]) is False


# ---------------------------------------------------------------------------
# Blowoff Detection
# ---------------------------------------------------------------------------

class TestBlowoffDetection:
    def test_extreme_move_detected(self):
        # Normal data then spike
        n = 60
        closes = [100.0] * (n - 1) + [200.0]  # massive spike
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        volumes = [100_000] * (n - 1) + [10_000_000]  # volume surge
        result = detect_blowoff(closes, highs, lows, volumes)
        assert result is True

    def test_normal_move_not_blowoff(self):
        highs, lows, closes, volumes = _bullish_ohlcv(60)
        result = detect_blowoff(closes, highs, lows, volumes)
        assert result is False

    def test_insufficient_data(self):
        assert detect_blowoff([1], [2], [0], [100]) is False


# ---------------------------------------------------------------------------
# VIX Flags
# ---------------------------------------------------------------------------

class TestVIXFlags:
    def test_normal_vix_no_flags(self, vix_rules):
        flags = compute_vix_flags(18.0, vix_rules)
        assert flags == []

    def test_low_vix_premiums_too_low(self, vix_rules):
        flags = compute_vix_flags(10.0, vix_rules)
        assert "PREMIUMS_TOO_LOW" in flags

    def test_high_vix_half_size(self, vix_rules):
        flags = compute_vix_flags(32.0, vix_rules)
        assert "HALF_SIZE" in flags
        assert "DEFINED_RISK_ONLY" in flags

    def test_extreme_vix_halt(self, vix_rules):
        flags = compute_vix_flags(45.0, vix_rules)
        assert "HALT_SHORT_VEGA" in flags
        assert "HALF_SIZE" in flags
        assert "DEFINED_RISK_ONLY" in flags


# ---------------------------------------------------------------------------
# Regime Score
# ---------------------------------------------------------------------------

class TestRegimeScore:
    def test_bullish_score(self, vix_rules):
        score = compute_regime_score("BULLISH", "GREEN", False, False, 18.0, vix_rules)
        assert score == 14  # 8 + 6

    def test_bearish_score(self, vix_rules):
        score = compute_regime_score("BEARISH", "RED", False, False, 18.0, vix_rules)
        assert score == -14  # -8 + -6

    def test_congestion_penalty(self, vix_rules):
        score = compute_regime_score("NEUTRAL", "BLUE", True, False, 18.0, vix_rules)
        assert score == -4  # 0 + 0 - 4

    def test_score_clamped_to_range(self, vix_rules):
        score = compute_regime_score("BULLISH", "GREEN", False, True, 18.0, vix_rules, pcr=1.5)
        assert -20 <= score <= 20

    def test_vix_below_12_caps_score(self, vix_rules):
        score = compute_regime_score("BULLISH", "GREEN", False, False, 10.0, vix_rules)
        assert abs(score) <= vix_rules.score_cap_below_12  # capped at ±5

    def test_pcr_bonus(self, vix_rules):
        score_no_pcr = compute_regime_score("BULLISH", "GREEN", False, False, 18.0, vix_rules)
        score_with_pcr = compute_regime_score("BULLISH", "GREEN", False, False, 18.0, vix_rules, pcr=1.5)
        assert score_with_pcr > score_no_pcr


# ---------------------------------------------------------------------------
# Score to Phase
# ---------------------------------------------------------------------------

class TestScoreToPhase:
    def test_high_score_bullish(self):
        assert score_to_phase(10, False, False) == RegimePhase.BULLISH

    def test_low_score_bearish(self):
        assert score_to_phase(-10, False, False) == RegimePhase.BEARISH

    def test_mid_score_congestion(self):
        assert score_to_phase(0, False, False) == RegimePhase.CONGESTION

    def test_congestion_flag(self):
        assert score_to_phase(10, True, False) == RegimePhase.CONGESTION

    def test_blowoff_flag(self):
        assert score_to_phase(10, False, True) == RegimePhase.BLOWOFF


# ---------------------------------------------------------------------------
# AtomicRegimeState
# ---------------------------------------------------------------------------

class TestAtomicRegimeState:
    def test_initial_version_zero(self, state):
        assert state.current_version == 0

    def test_update_increments_version(self, state):
        v = state.update(
            phase=RegimePhase.BULLISH, score=10, vix=18.0,
            vix_flags=[], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        assert v == 1

    def test_snapshot_matches_update(self, state):
        state.update(
            phase=RegimePhase.BEARISH, score=-10, vix=35.0,
            vix_flags=["HALF_SIZE"], ichimoku_signal="BEARISH",
            impulse_color="RED", congestion=False, blowoff=False,
        )
        snap = state.read_snapshot()
        assert snap.phase == RegimePhase.BEARISH
        assert snap.score == -10
        assert snap.vix == 35.0
        assert "HALF_SIZE" in snap.vix_flags
        assert snap.ichimoku_signal == "BEARISH"
        assert snap.impulse_color == "RED"

    def test_snapshot_is_copy(self, state):
        state.update(
            phase=RegimePhase.BULLISH, score=5, vix=18.0,
            vix_flags=["A"], ichimoku_signal="BULLISH",
            impulse_color="GREEN", congestion=False, blowoff=False,
        )
        snap = state.read_snapshot()
        snap.vix_flags.append("MUTATED")  # modify the snapshot
        snap2 = state.read_snapshot()
        assert "MUTATED" not in snap2.vix_flags

    def test_multiple_updates_monotonic(self, state):
        for i in range(5):
            v = state.update(
                phase=RegimePhase.CONGESTION, score=i, vix=20.0,
                vix_flags=[], ichimoku_signal="NEUTRAL",
                impulse_color="BLUE", congestion=True, blowoff=False,
            )
            assert v == i + 1


# ---------------------------------------------------------------------------
# EMA / ATR helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_ema_basic(self):
        values = [10, 11, 12, 13, 14, 15]
        ema = _compute_ema(values, 3)
        assert len(ema) == len(values)
        assert ema[-1] > ema[0]

    def test_ema_empty(self):
        assert _compute_ema([], 3) == []

    def test_atr_basic(self):
        highs = [12, 13, 14, 15, 16, 15, 14, 13, 14, 15, 16, 17, 18, 19, 20]
        lows = [10, 11, 12, 13, 14, 13, 12, 11, 12, 13, 14, 15, 16, 17, 18]
        closes = [11, 12, 13, 14, 15, 14, 13, 12, 13, 14, 15, 16, 17, 18, 19]
        atr = _compute_atr(highs, lows, closes, 14)
        assert atr > 0
