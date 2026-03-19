"""
Market Pulse scoring engine.
Computes 5 category scores + decision from market data.
All thresholds imported from market_pulse_config.py.
"""

import logging
import math
from typing import Any

from services.market_pulse_config import (
    BREADTH,
    CATEGORY_WEIGHTS,
    DECISION_THRESHOLDS,
    MACRO,
    MOMENTUM,
    TREND,
    VOLATILITY,
)

logger = logging.getLogger(__name__)


def _clamp(value: float, low: float = 0, high: float = 100) -> int:
    """Clamp value to [low, high] and round (conventional rounding, not banker's)."""
    return int(max(low, min(high, math.floor(value + 0.5))))


def _safe(val, default=0):
    """Return val if not None, else default."""
    return val if val is not None else default


# ── Volatility Score ────────────────────────────────────────────

def score_volatility(
    vix_current: float | None,
    vix_slope_5d: float | None,
    vix_percentile: float | None,
    pcr: float | None,
) -> tuple[int, list[dict]]:
    """Score volatility 0-100 with rules fired."""
    rules = []
    sub_scores = []
    V = VOLATILITY

    # VIX Level
    vix = _safe(vix_current, 15)
    if vix < V["vix_complacency_threshold"]:
        s = 60
        rules.append({"rule": "VIX complacency", "detail": f"VIX {vix:.1f} < {V['vix_complacency_threshold']}", "impact": "penalty"})
    elif V["vix_optimal_low"] <= vix <= V["vix_optimal_high"]:
        s = 80 + (V["vix_optimal_high"] - vix) / (V["vix_optimal_high"] - V["vix_optimal_low"]) * 20
        rules.append({"rule": "VIX in optimal range", "detail": f"VIX {vix:.1f} in [{V['vix_optimal_low']}-{V['vix_optimal_high']}]", "impact": "positive"})
    elif vix <= V["vix_elevated_high"]:
        s = 50 + (V["vix_elevated_high"] - vix) / (V["vix_elevated_high"] - V["vix_optimal_high"]) * 30
        rules.append({"rule": "VIX elevated", "detail": f"VIX {vix:.1f} in [{V['vix_optimal_high']}-{V['vix_elevated_high']}]", "impact": "neutral"})
    elif vix >= V["vix_spike_threshold"]:
        s = max(0, 30 - (vix - V["vix_spike_threshold"]) * 3)
        rules.append({"rule": "VIX spike", "detail": f"VIX {vix:.1f} >= {V['vix_spike_threshold']}", "impact": "negative"})
    else:
        s = 40
    sub_scores.append(("vix_level", _clamp(s), V["vix_level_weight"]))

    # VIX Slope
    slope = _safe(vix_slope_5d, 0)
    if slope < -1:
        s = 80 + V["slope_falling_bonus"]
        rules.append({"rule": "VIX falling", "detail": f"5d slope {slope:.2f}%", "impact": "positive"})
    elif slope > 2:
        s = max(0, 50 + V["slope_rising_penalty"])
        rules.append({"rule": "VIX rising sharply", "detail": f"5d slope {slope:.2f}%", "impact": "negative"})
    else:
        s = 65
    sub_scores.append(("vix_slope", _clamp(s), V["vix_slope_weight"]))

    # VIX Percentile
    pct = _safe(vix_percentile, 50)
    if pct < V["percentile_low_threshold"]:
        s = 80 + (V["percentile_low_threshold"] - pct) / V["percentile_low_threshold"] * 20
        rules.append({"rule": "VIX low percentile", "detail": f"{pct:.0f}th percentile (1Y)", "impact": "positive"})
    elif pct > V["percentile_high_threshold"]:
        s = max(0, 30 - (pct - V["percentile_high_threshold"]))
        rules.append({"rule": "VIX high percentile", "detail": f"{pct:.0f}th percentile (1Y)", "impact": "negative"})
    else:
        s = 50 + (V["percentile_high_threshold"] - pct) / (V["percentile_high_threshold"] - V["percentile_low_threshold"]) * 30
    sub_scores.append(("vix_percentile", _clamp(s), V["vix_percentile_weight"]))

    # PCR
    pcr_val = _safe(pcr, 1.0)
    if V["pcr_healthy_low"] <= pcr_val <= V["pcr_healthy_high"]:
        s = 70 + (1 - abs(pcr_val - 1.05) / 0.5) * 20
        rules.append({"rule": "PCR healthy range", "detail": f"PCR {pcr_val:.2f}", "impact": "positive"})
    elif pcr_val > V["pcr_extreme_fear"]:
        s = 50
        rules.append({"rule": "PCR extreme fear", "detail": f"PCR {pcr_val:.2f} > {V['pcr_extreme_fear']}", "impact": "neutral"})
    elif pcr_val < V["pcr_extreme_greed"]:
        s = 40
        rules.append({"rule": "PCR extreme greed", "detail": f"PCR {pcr_val:.2f} < {V['pcr_extreme_greed']}", "impact": "negative"})
    else:
        s = 60
    sub_scores.append(("pcr", _clamp(s), V["pcr_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Momentum Score ──────────────────────────────────────────────

def score_momentum(
    sectors_above_20d: int,
    total_sectors: int,
    leadership_spread: float,
    higher_highs_pct: float,
    rotation_diversity: int,
) -> tuple[int, list[dict]]:
    """Score momentum 0-100."""
    rules = []
    sub_scores = []
    M = MOMENTUM

    # Sector participation
    if sectors_above_20d >= total_sectors:
        s = 100
    elif sectors_above_20d >= M["sectors_strong"]:
        s = 75
    elif sectors_above_20d >= M["sectors_moderate_low"]:
        s = 50
    else:
        s = 20
    rules.append({"rule": "Sector participation", "detail": f"{sectors_above_20d}/{total_sectors} sectors above 20d MA", "impact": "positive" if s >= 70 else "negative" if s < 40 else "neutral"})
    sub_scores.append(("participation", _clamp(s), M["sector_participation_weight"]))

    # Leadership spread
    spread = _safe(leadership_spread, 3)
    if M["spread_healthy_low"] <= spread <= M["spread_healthy_high"]:
        s = 80
        rules.append({"rule": "Healthy leadership spread", "detail": f"Top3-Bot3 spread {spread:.1f}%", "impact": "positive"})
    elif spread > M["spread_concentrated"]:
        s = 40
        rules.append({"rule": "Concentrated leadership", "detail": f"Spread {spread:.1f}% > {M['spread_concentrated']}%", "impact": "negative"})
    else:
        s = 60
    sub_scores.append(("spread", _clamp(s), M["leadership_spread_weight"]))

    # Higher highs
    hh = _safe(higher_highs_pct, 20)
    if hh > M["highs_strong"]:
        s = 90
    elif hh > M["highs_moderate"]:
        s = 65
    elif hh > M["highs_weak"]:
        s = 40
    else:
        s = 20
    rules.append({"rule": "Higher highs participation", "detail": f"{hh:.0f}% of Nifty 50 at 20d highs", "impact": "positive" if s >= 65 else "negative"})
    sub_scores.append(("higher_highs", _clamp(s), M["higher_highs_weight"]))

    # Rotation health
    if rotation_diversity >= 4:
        s = 85
        rules.append({"rule": "Healthy sector rotation", "detail": f"{rotation_diversity} sectors leading", "impact": "positive"})
    elif rotation_diversity >= 2:
        s = 55
    else:
        s = 25
        rules.append({"rule": "Narrow leadership", "detail": f"Only {rotation_diversity} sector(s) leading", "impact": "negative"})
    sub_scores.append(("rotation", _clamp(s), M["rotation_health_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Trend Score ─────────────────────────────────────────────────

def score_trend(
    nifty_ltp: float | None,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
    banknifty_ltp: float | None,
    banknifty_sma50: float | None,
    rsi: float | None,
    slope_50d: float | None,
    slope_200d: float | None,
) -> tuple[int, list[dict]]:
    """Score trend 0-100."""
    rules = []
    sub_scores = []
    T = TREND

    # Nifty vs MAs
    ltp = _safe(nifty_ltp)
    above_count = 0
    s = 10
    if sma_200 and ltp > sma_200:
        s += 30
        above_count += 1
    if sma_50 and ltp > sma_50:
        s += 25
        above_count += 1
    if sma_20 and ltp > sma_20:
        s += 20
        above_count += 1

    if above_count == 3:
        rules.append({"rule": "Nifty above all MAs", "detail": f"Above 20/50/200 DMA", "impact": "positive"})
    elif above_count == 0:
        rules.append({"rule": "Nifty below all MAs", "detail": "Below 20/50/200 DMA", "impact": "negative"})
    else:
        rules.append({"rule": f"Nifty above {above_count}/3 MAs", "detail": f"{above_count} MAs bullish", "impact": "neutral"})
    sub_scores.append(("nifty_ma", _clamp(s), T["nifty_ma_weight"]))

    # BankNifty vs 50d
    bn_ltp = _safe(banknifty_ltp)
    bn_sma = _safe(banknifty_sma50)
    if bn_ltp and bn_sma and bn_ltp > bn_sma:
        s = 80
        rules.append({"rule": "BankNifty above 50d MA", "detail": "Risk-on signal from financials", "impact": "positive"})
    else:
        s = 35
        rules.append({"rule": "BankNifty below 50d MA", "detail": "Financials weak", "impact": "negative"})
    sub_scores.append(("banknifty", _clamp(s), T["banknifty_weight"]))

    # RSI
    rsi_val = _safe(rsi, 50)
    if T["rsi_strong_low"] <= rsi_val <= T["rsi_strong_high"]:
        s = 85
        rules.append({"rule": "RSI in strong zone", "detail": f"RSI {rsi_val:.1f}", "impact": "positive"})
    elif rsi_val > T["rsi_overbought"]:
        s = 50
        rules.append({"rule": "RSI overbought", "detail": f"RSI {rsi_val:.1f} > {T['rsi_overbought']}", "impact": "neutral"})
    elif rsi_val < T["rsi_oversold"]:
        s = 30
        rules.append({"rule": "RSI oversold", "detail": f"RSI {rsi_val:.1f} < {T['rsi_oversold']}", "impact": "negative"})
    elif rsi_val >= 40:
        s = 60
    else:
        s = 40
    sub_scores.append(("rsi", _clamp(s), T["rsi_weight"]))

    # MA Slopes
    s50 = _safe(slope_50d, 0)
    s200 = _safe(slope_200d, 0)
    if s50 > 0 and s200 > 0:
        s = 90
        rules.append({"rule": "Both MAs rising", "detail": f"50d slope {s50:.2f}%, 200d slope {s200:.2f}%", "impact": "positive"})
    elif s50 < 0 and s200 < 0:
        s = 15
        rules.append({"rule": "Both MAs falling", "detail": f"50d slope {s50:.2f}%, 200d slope {s200:.2f}%", "impact": "negative"})
    else:
        s = 50
        rules.append({"rule": "Mixed MA slopes", "detail": f"50d: {s50:.2f}%, 200d: {s200:.2f}%", "impact": "neutral"})
    sub_scores.append(("ma_slope", _clamp(s), T["ma_slope_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Breadth Score ───────────────────────────────────────────────

def score_breadth(
    ad_ratio: float | None,
    pct_above_50d: float | None,
    pct_above_200d: float | None,
    highs_52w: int | None,
    lows_52w: int | None,
) -> tuple[int, list[dict]]:
    """Score breadth 0-100."""
    rules = []
    sub_scores = []
    B = BREADTH

    # A/D Ratio
    ad = _safe(ad_ratio, 1.0)
    if ad >= B["ad_strong"]:
        s = 95
    elif ad >= B["ad_healthy_low"]:
        s = 70
    elif ad >= B["ad_neutral_low"]:
        s = 50
    elif ad >= B["ad_weak"]:
        s = 30
    else:
        s = 15
    rules.append({"rule": "Advance/Decline ratio", "detail": f"A/D {ad:.2f}", "impact": "positive" if s >= 70 else "negative" if s < 40 else "neutral"})
    sub_scores.append(("ad_ratio", _clamp(s), B["ad_ratio_weight"]))

    # % above 50d
    pct50 = _safe(pct_above_50d, 50)
    if pct50 > 70:
        s = 90
    elif pct50 > 50:
        s = 65
    elif pct50 > 30:
        s = 40
    else:
        s = 15
    rules.append({"rule": "% above 50d MA", "detail": f"{pct50:.0f}% of Nifty 50", "impact": "positive" if s >= 65 else "negative" if s < 40 else "neutral"})
    sub_scores.append(("above_50d", _clamp(s), B["above_50d_weight"]))

    # % above 200d
    pct200 = _safe(pct_above_200d, 60)
    if pct200 > 80:
        s = 90
    elif pct200 > 60:
        s = 70
    elif pct200 > 40:
        s = 45
    else:
        s = 25
    rules.append({"rule": "% above 200d MA", "detail": f"{pct200:.0f}% of Nifty 50", "impact": "positive" if s >= 70 else "negative" if s < 45 else "neutral"})
    sub_scores.append(("above_200d", _clamp(s), B["above_200d_weight"]))

    # New highs vs lows
    h = _safe(highs_52w, 0)
    l = _safe(lows_52w, 0)
    ratio = h / max(l, 1)
    if ratio > 3:
        s = 90
    elif h > l:
        s = 65
    else:
        s = 20
    rules.append({"rule": "52w highs vs lows", "detail": f"Highs: {h}, Lows: {l}", "impact": "positive" if s >= 65 else "negative"})
    sub_scores.append(("highs_lows", _clamp(s), B["highs_lows_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Macro Score ─────────────────────────────────────────────────

def score_macro(
    usdinr_slope_5d: float | None,
    usdinr_slope_20d: float | None,
    vix_current: float | None,
    event_hours_away: float | None,
    event_type: str | None,
    nifty_usdinr_corr: float | None,
) -> tuple[int, list[dict]]:
    """Score macro/liquidity 0-100."""
    rules = []
    sub_scores = []
    MC = MACRO

    # USDINR Trend
    slope5 = _safe(usdinr_slope_5d, 0)
    slope20 = _safe(usdinr_slope_20d, 0)
    if slope5 <= 0 and slope20 <= 0:
        s = 85
        rules.append({"rule": "USDINR stable/falling", "detail": f"5d: {slope5:.2f}%, 20d: {slope20:.2f}%", "impact": "positive"})
    elif slope5 > 0.5:
        s = 20
        rules.append({"rule": "USDINR spiking", "detail": f"5d slope {slope5:.2f}%", "impact": "negative"})
    else:
        s = 55
        rules.append({"rule": "USDINR rising slowly", "detail": f"5d: {slope5:.2f}%", "impact": "neutral"})
    sub_scores.append(("usdinr", _clamp(s), MC["usdinr_weight"]))

    # RBI Stance Proxy
    vix = _safe(vix_current, 15)
    if vix < 14 and slope5 <= 0:
        s = 85
        rules.append({"rule": "RBI stance: dovish proxy", "detail": f"Low VIX ({vix:.1f}) + stable INR", "impact": "positive"})
    elif vix > 20 and slope5 > 0.3:
        s = 30
        rules.append({"rule": "RBI stance: hawkish proxy", "detail": f"High VIX ({vix:.1f}) + weak INR", "impact": "negative"})
    else:
        s = 60
        rules.append({"rule": "RBI stance: neutral proxy", "detail": f"VIX {vix:.1f}, INR slope {slope5:.2f}%", "impact": "neutral"})
    sub_scores.append(("rbi", _clamp(s), MC["rbi_stance_weight"]))

    # Event Risk
    hours = _safe(event_hours_away, 999)
    etype = event_type or "none"
    if hours > MC["event_major_hours"]:
        s = 90
        rules.append({"rule": "No imminent events", "detail": f"Next event in {hours:.0f}h", "impact": "positive"})
    elif etype == "major":
        s = 30
        rules.append({"rule": "Major event imminent", "detail": f"{etype} event in {hours:.0f}h", "impact": "negative"})
    else:
        s = 65
        rules.append({"rule": "Minor event approaching", "detail": f"{etype} event in {hours:.0f}h", "impact": "neutral"})
    sub_scores.append(("event", _clamp(s), MC["event_risk_weight"]))

    # Global risk proxy — Nifty-USDINR correlation
    # IMPORTANT: None means data unavailable → neutral score (50), NOT the
    # positive "decoupled" branch. Using _safe(x, 0) here would silently
    # boost macro by falling into the "decoupled" path.
    if nifty_usdinr_corr is None:
        s = 50
        rules.append({"rule": "Global risk proxy unavailable", "detail": "Insufficient data for Nifty-USDINR correlation", "impact": "neutral"})
    elif abs(nifty_usdinr_corr) > 0.6:
        s = 30
        rules.append({"rule": "Nifty-USDINR correlated (risk-off)", "detail": f"Correlation {nifty_usdinr_corr:.2f}", "impact": "negative"})
    else:
        s = 75
        rules.append({"rule": "Nifty-USDINR decoupled", "detail": f"Correlation {nifty_usdinr_corr:.2f}", "impact": "positive"})
    sub_scores.append(("global", _clamp(s), MC["global_risk_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Market Quality Score ────────────────────────────────────────

def compute_market_quality(scores: dict[str, int]) -> int:
    """Compute weighted market quality score."""
    total = 0
    for category, weight in CATEGORY_WEIGHTS.items():
        total += scores.get(category, 50) * weight
    return _clamp(total)


def get_decision(score: int) -> str:
    """Return YES / CAUTION / NO based on score."""
    if score >= DECISION_THRESHOLDS["yes_min"]:
        return "YES"
    elif score >= DECISION_THRESHOLDS["caution_min"]:
        return "CAUTION"
    return "NO"


def classify_regime(
    nifty_ltp: float | None,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
    slope_50d: float | None,
    vix_current: float | None,
) -> str:
    """Classify market regime: uptrend / downtrend / chop."""
    ltp = _safe(nifty_ltp)
    above = sum(1 for ma in [sma_20, sma_50, sma_200] if ma and ltp > ma)
    slope = _safe(slope_50d, 0)
    vix = _safe(vix_current, 15)

    if above >= 3 and slope > 0:
        return "uptrend"
    elif above == 0 and slope < 0:
        return "downtrend"
    else:
        return "chop"
