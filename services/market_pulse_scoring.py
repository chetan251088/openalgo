"""
Market Pulse scoring engine.
Computes 5 category scores + decision from market data.
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


def _round_half_up(value: float) -> int:
    """Round halves away from zero so 72.5 becomes 73."""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _clamp(value: float, low: float = 0, high: float = 100) -> int:
    """Clamp value to [low, high] and round half up."""
    return _round_half_up(max(low, min(high, value)))


def _safe(val, default=0):
    """Return val if not None, else default."""
    return val if val is not None else default


def score_volatility(
    vix_current: float | None,
    vix_slope_5d: float | None,
    vix_percentile: float | None,
    pcr: float | None,
) -> tuple[int, list[dict]]:
    """Score volatility 0-100 with rules fired."""
    rules = []
    sub_scores = []
    thresholds = VOLATILITY

    vix = _safe(vix_current, 15)
    if vix < thresholds["vix_complacency_threshold"]:
        score = 60
        rules.append(
            {
                "rule": "VIX complacency",
                "detail": f"VIX {vix:.1f} < {thresholds['vix_complacency_threshold']}",
                "impact": "negative",
            }
        )
    elif thresholds["vix_optimal_low"] <= vix <= thresholds["vix_optimal_high"]:
        score = 80 + (
            (thresholds["vix_optimal_high"] - vix)
            / (thresholds["vix_optimal_high"] - thresholds["vix_optimal_low"])
        ) * 20
        rules.append(
            {
                "rule": "VIX in optimal range",
                "detail": (
                    f"VIX {vix:.1f} in "
                    f"[{thresholds['vix_optimal_low']}-{thresholds['vix_optimal_high']}]"
                ),
                "impact": "positive",
            }
        )
    elif vix <= thresholds["vix_elevated_high"]:
        score = 50 + (
            (thresholds["vix_elevated_high"] - vix)
            / (thresholds["vix_elevated_high"] - thresholds["vix_optimal_high"])
        ) * 30
        rules.append(
            {
                "rule": "VIX elevated",
                "detail": (
                    f"VIX {vix:.1f} in "
                    f"[{thresholds['vix_optimal_high']}-{thresholds['vix_elevated_high']}]"
                ),
                "impact": "neutral",
            }
        )
    elif vix >= thresholds["vix_spike_threshold"]:
        score = max(0, 30 - (vix - thresholds["vix_spike_threshold"]) * 3)
        rules.append(
            {
                "rule": "VIX spike",
                "detail": f"VIX {vix:.1f} >= {thresholds['vix_spike_threshold']}",
                "impact": "negative",
            }
        )
    else:
        score = 40
    sub_scores.append(("vix_level", _clamp(score), thresholds["vix_level_weight"]))

    slope = _safe(vix_slope_5d, 0)
    if slope < -1:
        score = 80 + thresholds["slope_falling_bonus"]
        rules.append(
            {
                "rule": "VIX falling",
                "detail": f"5d slope {slope:.2f}%",
                "impact": "positive",
            }
        )
    elif slope > 2:
        score = max(0, 50 + thresholds["slope_rising_penalty"])
        rules.append(
            {
                "rule": "VIX rising sharply",
                "detail": f"5d slope {slope:.2f}%",
                "impact": "negative",
            }
        )
    else:
        score = 65
    sub_scores.append(("vix_slope", _clamp(score), thresholds["vix_slope_weight"]))

    percentile = _safe(vix_percentile, 50)
    if percentile < thresholds["percentile_low_threshold"]:
        score = 80 + (
            (thresholds["percentile_low_threshold"] - percentile)
            / thresholds["percentile_low_threshold"]
        ) * 20
        rules.append(
            {
                "rule": "VIX low percentile",
                "detail": f"{percentile:.0f}th percentile (1Y)",
                "impact": "positive",
            }
        )
    elif percentile > thresholds["percentile_high_threshold"]:
        score = max(0, 30 - (percentile - thresholds["percentile_high_threshold"]))
        rules.append(
            {
                "rule": "VIX high percentile",
                "detail": f"{percentile:.0f}th percentile (1Y)",
                "impact": "negative",
            }
        )
    else:
        score = 50 + (
            (thresholds["percentile_high_threshold"] - percentile)
            / (
                thresholds["percentile_high_threshold"]
                - thresholds["percentile_low_threshold"]
            )
        ) * 30
    sub_scores.append(
        ("vix_percentile", _clamp(score), thresholds["vix_percentile_weight"])
    )

    pcr_value = _safe(pcr, 1.0)
    if thresholds["pcr_healthy_low"] <= pcr_value <= thresholds["pcr_healthy_high"]:
        score = 70 + (1 - abs(pcr_value - 1.05) / 0.5) * 20
        rules.append(
            {
                "rule": "PCR healthy range",
                "detail": f"PCR {pcr_value:.2f}",
                "impact": "positive",
            }
        )
    elif pcr_value > thresholds["pcr_extreme_fear"]:
        score = 50
        rules.append(
            {
                "rule": "PCR extreme fear",
                "detail": f"PCR {pcr_value:.2f} > {thresholds['pcr_extreme_fear']}",
                "impact": "neutral",
            }
        )
    elif pcr_value < thresholds["pcr_extreme_greed"]:
        score = 40
        rules.append(
            {
                "rule": "PCR extreme greed",
                "detail": f"PCR {pcr_value:.2f} < {thresholds['pcr_extreme_greed']}",
                "impact": "negative",
            }
        )
    else:
        score = 60
    sub_scores.append(("pcr", _clamp(score), thresholds["pcr_weight"]))

    total = sum(item_score * weight for _, item_score, weight in sub_scores)
    return _clamp(total), rules


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
    thresholds = MOMENTUM

    if sectors_above_20d >= total_sectors:
        score = 100
    elif sectors_above_20d >= thresholds["sectors_strong"]:
        score = 75
    elif sectors_above_20d >= thresholds["sectors_moderate_low"]:
        score = 50
    else:
        score = 20
    rules.append(
        {
            "rule": "Sector participation",
            "detail": f"{sectors_above_20d}/{total_sectors} sectors above 20d MA",
            "impact": "positive" if score >= 70 else "negative" if score < 40 else "neutral",
        }
    )
    sub_scores.append(
        ("participation", _clamp(score), thresholds["sector_participation_weight"])
    )

    spread = _safe(leadership_spread, 3)
    if thresholds["spread_healthy_low"] <= spread <= thresholds["spread_healthy_high"]:
        score = 80
        rules.append(
            {
                "rule": "Healthy leadership spread",
                "detail": f"Top3-Bot3 spread {spread:.1f}%",
                "impact": "positive",
            }
        )
    elif spread > thresholds["spread_concentrated"]:
        score = 40
        rules.append(
            {
                "rule": "Concentrated leadership",
                "detail": f"Spread {spread:.1f}% > {thresholds['spread_concentrated']}%",
                "impact": "negative",
            }
        )
    else:
        score = 60
    sub_scores.append(("spread", _clamp(score), thresholds["leadership_spread_weight"]))

    higher_highs = _safe(higher_highs_pct, 20)
    if higher_highs > thresholds["highs_strong"]:
        score = 90
    elif higher_highs > thresholds["highs_moderate"]:
        score = 65
    elif higher_highs > thresholds["highs_weak"]:
        score = 40
    else:
        score = 20
    rules.append(
        {
            "rule": "Higher highs participation",
            "detail": f"{higher_highs:.0f}% of Nifty 50 at 20d highs",
            "impact": "positive" if score >= 65 else "negative",
        }
    )
    sub_scores.append(
        ("higher_highs", _clamp(score), thresholds["higher_highs_weight"])
    )

    if rotation_diversity >= 4:
        score = 85
        rules.append(
            {
                "rule": "Healthy sector rotation",
                "detail": f"{rotation_diversity} sectors leading",
                "impact": "positive",
            }
        )
    elif rotation_diversity >= 2:
        score = 55
    else:
        score = 25
        rules.append(
            {
                "rule": "Narrow leadership",
                "detail": f"Only {rotation_diversity} sector(s) leading",
                "impact": "negative",
            }
        )
    sub_scores.append(("rotation", _clamp(score), thresholds["rotation_health_weight"]))

    total = sum(item_score * weight for _, item_score, weight in sub_scores)
    return _clamp(total), rules


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
    thresholds = TREND

    ltp = _safe(nifty_ltp)
    above_count = 0
    score = 10
    if sma_200 and ltp > sma_200:
        score += 30
        above_count += 1
    if sma_50 and ltp > sma_50:
        score += 25
        above_count += 1
    if sma_20 and ltp > sma_20:
        score += 20
        above_count += 1

    if above_count == 3:
        rules.append(
            {
                "rule": "Nifty above all MAs",
                "detail": "Above 20/50/200 DMA",
                "impact": "positive",
            }
        )
    elif above_count == 0:
        rules.append(
            {
                "rule": "Nifty below all MAs",
                "detail": "Below 20/50/200 DMA",
                "impact": "negative",
            }
        )
    else:
        rules.append(
            {
                "rule": f"Nifty above {above_count}/3 MAs",
                "detail": f"{above_count} MAs bullish",
                "impact": "neutral",
            }
        )
    sub_scores.append(("nifty_ma", _clamp(score), thresholds["nifty_ma_weight"]))

    banknifty = _safe(banknifty_ltp)
    banknifty_sma = _safe(banknifty_sma50)
    if banknifty and banknifty_sma and banknifty > banknifty_sma:
        score = 80
        rules.append(
            {
                "rule": "BankNifty above 50d MA",
                "detail": "Risk-on signal from financials",
                "impact": "positive",
            }
        )
    else:
        score = 35
        rules.append(
            {
                "rule": "BankNifty below 50d MA",
                "detail": "Financials weak",
                "impact": "negative",
            }
        )
    sub_scores.append(("banknifty", _clamp(score), thresholds["banknifty_weight"]))

    rsi_value = _safe(rsi, 50)
    if thresholds["rsi_strong_low"] <= rsi_value <= thresholds["rsi_strong_high"]:
        score = 85
        rules.append(
            {
                "rule": "RSI in strong zone",
                "detail": f"RSI {rsi_value:.1f}",
                "impact": "positive",
            }
        )
    elif rsi_value > thresholds["rsi_overbought"]:
        score = 50
        rules.append(
            {
                "rule": "RSI overbought",
                "detail": f"RSI {rsi_value:.1f} > {thresholds['rsi_overbought']}",
                "impact": "neutral",
            }
        )
    elif rsi_value < thresholds["rsi_oversold"]:
        score = 30
        rules.append(
            {
                "rule": "RSI oversold",
                "detail": f"RSI {rsi_value:.1f} < {thresholds['rsi_oversold']}",
                "impact": "negative",
            }
        )
    elif rsi_value >= 40:
        score = 60
    else:
        score = 40
    sub_scores.append(("rsi", _clamp(score), thresholds["rsi_weight"]))

    slope50 = _safe(slope_50d, 0)
    slope200 = _safe(slope_200d, 0)
    if slope50 > 0 and slope200 > 0:
        score = 90
        rules.append(
            {
                "rule": "Both MAs rising",
                "detail": f"50d slope {slope50:.2f}%, 200d slope {slope200:.2f}%",
                "impact": "positive",
            }
        )
    elif slope50 < 0 and slope200 < 0:
        score = 15
        rules.append(
            {
                "rule": "Both MAs falling",
                "detail": f"50d slope {slope50:.2f}%, 200d slope {slope200:.2f}%",
                "impact": "negative",
            }
        )
    else:
        score = 50
        rules.append(
            {
                "rule": "Mixed MA slopes",
                "detail": f"50d: {slope50:.2f}%, 200d: {slope200:.2f}%",
                "impact": "neutral",
            }
        )
    sub_scores.append(("ma_slope", _clamp(score), thresholds["ma_slope_weight"]))

    total = sum(item_score * weight for _, item_score, weight in sub_scores)
    return _clamp(total), rules


def score_breadth(
    ad_ratio: float | None,
    pct_above_50d: float | None,
    pct_above_200d: float | None,
    highs_52w: int | None,
    lows_52w: int | None,
    *,
    ad_advances: int | None = None,
    ad_declines: int | None = None,
    ad_unchanged: int | None = None,
    above_50d_count: int | None = None,
    above_50d_total: int | None = None,
    above_200d_count: int | None = None,
    above_200d_total: int | None = None,
    highs_total: int | None = None,
    breadth_label: str = "Nifty 50",
) -> tuple[int, list[dict]]:
    """Score breadth 0-100."""
    rules = []
    sub_scores = []
    thresholds = BREADTH

    if ad_ratio is None:
        score = 50
        ad_detail = f"{breadth_label} A/D unavailable"
        impact = "neutral"
    else:
        ad_ratio_value = ad_ratio
        if ad_ratio_value >= thresholds["ad_strong"]:
            score = 95
        elif ad_ratio_value >= thresholds["ad_healthy_low"]:
            score = 70
        elif ad_ratio_value >= thresholds["ad_neutral_low"]:
            score = 50
        elif ad_ratio_value >= thresholds["ad_weak"]:
            score = 30
        else:
            score = 15
        ad_detail = f"{breadth_label} A/D {ad_ratio_value:.2f}"
        if ad_advances is not None and ad_declines is not None:
            counts_text = f"{ad_advances} up, {ad_declines} down"
            if ad_unchanged is not None:
                counts_text += f", {ad_unchanged} flat"
            ad_detail = f"{ad_detail} ({counts_text})"
        impact = "positive" if score >= 70 else "negative" if score < 40 else "neutral"
    rules.append(
        {
            "rule": "Advance/Decline ratio",
            "detail": ad_detail,
            "impact": impact,
        }
    )
    sub_scores.append(("ad_ratio", _clamp(score), thresholds["ad_ratio_weight"]))

    if pct_above_50d is None:
        score = 50
        detail = f"{breadth_label} 50d breadth unavailable"
        impact = "neutral"
    else:
        pct50 = pct_above_50d
        if pct50 > 70:
            score = 90
        elif pct50 > 50:
            score = 65
        elif pct50 > 30:
            score = 40
        else:
            score = 15
        detail = f"{pct50:.0f}% of {breadth_label}"
        if above_50d_count is not None and above_50d_total is not None:
            detail = f"{detail} ({above_50d_count}/{above_50d_total})"
        impact = "positive" if score >= 65 else "negative" if score < 40 else "neutral"
    rules.append(
        {
            "rule": "% above 50d MA",
            "detail": detail,
            "impact": impact,
        }
    )
    sub_scores.append(("above_50d", _clamp(score), thresholds["above_50d_weight"]))

    if pct_above_200d is None:
        score = 50
        detail = f"{breadth_label} 200d breadth unavailable"
        impact = "neutral"
    else:
        pct200 = pct_above_200d
        if pct200 > 80:
            score = 90
        elif pct200 > 60:
            score = 70
        elif pct200 > 40:
            score = 45
        else:
            score = 25
        detail = f"{pct200:.0f}% of {breadth_label}"
        if above_200d_count is not None and above_200d_total is not None:
            detail = f"{detail} ({above_200d_count}/{above_200d_total})"
        impact = "positive" if score >= 70 else "negative" if score < 45 else "neutral"
    rules.append(
        {
            "rule": "% above 200d MA",
            "detail": detail,
            "impact": impact,
        }
    )
    sub_scores.append(("above_200d", _clamp(score), thresholds["above_200d_weight"]))

    if highs_52w is None or lows_52w is None:
        score = 50
        detail = f"{breadth_label} 52w highs/lows unavailable"
        impact = "neutral"
    else:
        highs = highs_52w
        lows = lows_52w
        ratio = highs / max(lows, 1)
        if ratio > 3:
            score = 90
        elif highs > lows:
            score = 65
        else:
            score = 20
        detail = f"Highs: {highs}, Lows: {lows} in {breadth_label}"
        if highs_total is not None:
            detail = f"{detail} ({highs_total} qualified)"
        impact = "positive" if score >= 65 else "negative"
    rules.append(
        {
            "rule": "52w highs vs lows",
            "detail": detail,
            "impact": impact,
        }
    )
    sub_scores.append(("highs_lows", _clamp(score), thresholds["highs_lows_weight"]))

    total = sum(item_score * weight for _, item_score, weight in sub_scores)
    return _clamp(total), rules


def score_macro(
    usdinr_slope_5d: float | None,
    usdinr_slope_20d: float | None,
    vix_current: float | None,
    event_hours_away: float | None,
    event_type: str | None,
    nifty_usdinr_corr: float | None,
    institutional_flows: dict | None = None,
) -> tuple[int, list[dict]]:
    """Score macro/liquidity 0-100."""
    rules = []
    sub_scores = []
    thresholds = MACRO

    slope5 = _safe(usdinr_slope_5d, 0)
    slope20 = _safe(usdinr_slope_20d, 0)
    if slope5 <= 0 and slope20 <= 0:
        score = 85
        rules.append(
            {
                "rule": "USDINR stable/falling",
                "detail": f"5d: {slope5:.2f}%, 20d: {slope20:.2f}%",
                "impact": "positive",
            }
        )
    elif slope5 > 0.5:
        score = 20
        rules.append(
            {
                "rule": "USDINR spiking",
                "detail": f"5d slope {slope5:.2f}%",
                "impact": "negative",
            }
        )
    else:
        score = 55
        rules.append(
            {
                "rule": "USDINR rising slowly",
                "detail": f"5d: {slope5:.2f}%",
                "impact": "neutral",
            }
        )
    sub_scores.append(("usdinr", _clamp(score), thresholds["usdinr_weight"]))

    vix = _safe(vix_current, 15)
    if vix < 14 and slope5 <= 0:
        score = 85
        rules.append(
            {
                "rule": "RBI stance: dovish proxy",
                "detail": f"Low VIX ({vix:.1f}) + stable INR",
                "impact": "positive",
            }
        )
    elif vix > 20 and slope5 > 0.3:
        score = 30
        rules.append(
            {
                "rule": "RBI stance: hawkish proxy",
                "detail": f"High VIX ({vix:.1f}) + weak INR",
                "impact": "negative",
            }
        )
    else:
        score = 60
        rules.append(
            {
                "rule": "RBI stance: neutral proxy",
                "detail": f"VIX {vix:.1f}, INR slope {slope5:.2f}%",
                "impact": "neutral",
            }
        )
    sub_scores.append(("rbi", _clamp(score), thresholds["rbi_stance_weight"]))

    hours = _safe(event_hours_away, 999)
    event_kind = event_type or "none"
    if hours > thresholds["event_major_hours"]:
        score = 90
        rules.append(
            {
                "rule": "No imminent events",
                "detail": f"Next event in {hours:.0f}h",
                "impact": "positive",
            }
        )
    elif event_kind == "major":
        score = 30
        rules.append(
            {
                "rule": "Major event imminent",
                "detail": f"{event_kind} event in {hours:.0f}h",
                "impact": "negative",
            }
        )
    else:
        score = 65
        rules.append(
            {
                "rule": "Minor event approaching",
                "detail": f"{event_kind} event in {hours:.0f}h",
                "impact": "neutral",
            }
        )
    sub_scores.append(("event", _clamp(score), thresholds["event_risk_weight"]))

    if nifty_usdinr_corr is None:
        score = 50
        rules.append(
            {
                "rule": "Global risk proxy unavailable",
                "detail": "Insufficient data for Nifty-USDINR correlation",
                "impact": "neutral",
            }
        )
    elif abs(nifty_usdinr_corr) > 0.6:
        score = 30
        rules.append(
            {
                "rule": "Nifty-USDINR correlated (risk-off)",
                "detail": f"Correlation {nifty_usdinr_corr:.2f}",
                "impact": "negative",
            }
        )
    else:
        score = 75
        rules.append(
            {
                "rule": "Nifty-USDINR decoupled",
                "detail": f"Correlation {nifty_usdinr_corr:.2f}",
                "impact": "positive",
            }
        )
    sub_scores.append(("global", _clamp(score), thresholds["global_risk_weight"]))

    flow_snapshot = (institutional_flows or {}).get("latest") or {}
    flow_trend = (institutional_flows or {}).get("five_day") or {}
    flow_freshness = (institutional_flows or {}).get("freshness") or {}
    headline_bias = flow_snapshot.get("headline_bias", "neutral")
    latest_fii = _safe(flow_snapshot.get("fii_net"), 0)
    latest_dii = _safe(flow_snapshot.get("dii_net"), 0)
    fii_5d = _safe(flow_trend.get("fii_net"), 0)
    if flow_freshness.get("is_stale"):
        score = 50
        lag_days = flow_freshness.get("lag_business_days", 0)
        latest_date = flow_freshness.get("latest_trading_date", "unknown")
        rules.append(
            {
                "rule": "Institutional flows stale",
                "detail": f"{lag_days} business day lag, latest {latest_date}",
                "impact": "neutral",
            }
        )
    elif not flow_snapshot:
        score = 50
        rules.append(
            {
                "rule": "Institutional flows unavailable",
                "detail": "FII/DII feed unavailable",
                "impact": "neutral",
            }
        )
    elif headline_bias == "bullish":
        score = 80
        rules.append(
            {
                "rule": "Institutional flows supportive",
                "detail": f"FII {latest_fii:.0f} Cr, DII {latest_dii:.0f} Cr, 5d FII {fii_5d:.0f} Cr",
                "impact": "positive",
            }
        )
    elif headline_bias == "bearish":
        score = 30
        rules.append(
            {
                "rule": "Institutional flows defensive",
                "detail": f"FII {latest_fii:.0f} Cr, DII {latest_dii:.0f} Cr, 5d FII {fii_5d:.0f} Cr",
                "impact": "negative",
            }
        )
    else:
        score = 55
        rules.append(
            {
                "rule": "Institutional flows mixed",
                "detail": f"FII {latest_fii:.0f} Cr, DII {latest_dii:.0f} Cr",
                "impact": "neutral",
            }
        )
    sub_scores.append(
        ("institutional", _clamp(score), thresholds["institutional_weight"])
    )

    total = sum(item_score * weight for _, item_score, weight in sub_scores)
    return _clamp(total), rules


def assess_intraday_tape(
    nifty_change_pct: float | None,
    banknifty_change_pct: float | None,
    vix_change_pct: float | None,
    sector_returns_1d: list[float | None] | None,
    advances: int | None,
    declines: int | None,
    unchanged: int | None,
) -> dict[str, Any]:
    """Assess live session tape so day mode can diverge from the swing backdrop."""
    bull_score = 0.0
    bear_score = 0.0
    rules: list[dict[str, str]] = []

    sector_values = [float(value) for value in (sector_returns_1d or []) if value is not None]
    positive_sectors = sum(1 for value in sector_values if value > 0)
    total_sectors = len(sector_values)
    sector_positive_ratio = positive_sectors / total_sectors if total_sectors else None

    total_constituents = sum(
        value for value in [advances or 0, declines or 0, unchanged or 0]
    )
    advance_ratio = (
        (advances or 0) / total_constituents if total_constituents else None
    )

    if nifty_change_pct is not None:
        if nifty_change_pct >= 1.0:
            bull_score += 2.5
            rules.append(
                {
                    "rule": "Nifty impulse",
                    "detail": f"Nifty {nifty_change_pct:+.2f}%",
                    "impact": "positive",
                }
            )
        elif nifty_change_pct <= -1.0:
            bear_score += 2.5
            rules.append(
                {
                    "rule": "Nifty impulse",
                    "detail": f"Nifty {nifty_change_pct:+.2f}%",
                    "impact": "negative",
                }
            )

    if banknifty_change_pct is not None:
        if banknifty_change_pct >= 1.0:
            bull_score += 2.0
            rules.append(
                {
                    "rule": "Bank leadership",
                    "detail": f"Bank Nifty {banknifty_change_pct:+.2f}%",
                    "impact": "positive",
                }
            )
        elif banknifty_change_pct <= -1.0:
            bear_score += 2.0
            rules.append(
                {
                    "rule": "Bank drag",
                    "detail": f"Bank Nifty {banknifty_change_pct:+.2f}%",
                    "impact": "negative",
                }
            )

    if vix_change_pct is not None:
        if vix_change_pct <= -3.0:
            bull_score += 1.75
            rules.append(
                {
                    "rule": "Volatility crush",
                    "detail": f"India VIX {vix_change_pct:+.2f}%",
                    "impact": "positive",
                }
            )
        elif vix_change_pct >= 3.0:
            bear_score += 1.75
            rules.append(
                {
                    "rule": "Volatility expansion",
                    "detail": f"India VIX {vix_change_pct:+.2f}%",
                    "impact": "negative",
                }
            )

    if sector_positive_ratio is not None:
        if sector_positive_ratio >= 0.75:
            bull_score += 2.5
            rules.append(
                {
                    "rule": "Sector breadth thrust",
                    "detail": f"{positive_sectors}/{total_sectors} sectors green",
                    "impact": "positive",
                }
            )
        elif sector_positive_ratio <= 0.25:
            bear_score += 2.5
            rules.append(
                {
                    "rule": "Sector breadth unwind",
                    "detail": f"{positive_sectors}/{total_sectors} sectors green",
                    "impact": "negative",
                }
            )
        else:
            rules.append(
                {
                    "rule": "Sector breadth mixed",
                    "detail": f"{positive_sectors}/{total_sectors} sectors green",
                    "impact": "neutral",
                }
            )

    if advance_ratio is not None:
        adv = advances or 0
        dec = declines or 0
        flat = unchanged or 0
        if advance_ratio >= 0.75:
            bull_score += 2.5
            rules.append(
                {
                    "rule": "Constituent participation",
                    "detail": f"{adv} up, {dec} down, {flat} flat",
                    "impact": "positive",
                }
            )
        elif advance_ratio <= 0.25:
            bear_score += 2.5
            rules.append(
                {
                    "rule": "Constituent participation",
                    "detail": f"{adv} up, {dec} down, {flat} flat",
                    "impact": "negative",
                }
            )
        else:
            rules.append(
                {
                    "rule": "Constituent participation",
                    "detail": f"{adv} up, {dec} down, {flat} flat",
                    "impact": "neutral",
                }
            )

    delta = bull_score - bear_score
    if delta >= 2.5:
        bias = "LONG"
    elif delta <= -2.5:
        bias = "SHORT"
    else:
        bias = "NEUTRAL"

    confidence = _clamp(40 + abs(delta) * 8)
    score = _clamp(50 + delta * 10)

    if bias == "LONG" and confidence >= 70:
        headline = "Bullish intraday thrust"
        impact = "positive"
    elif bias == "SHORT" and confidence >= 70:
        headline = "Bearish intraday unwind"
        impact = "negative"
    else:
        headline = "Intraday tape mixed"
        impact = "neutral"

    rules.insert(
        0,
        {
            "rule": "Session tape",
            "detail": headline,
            "impact": impact,
        },
    )

    return {
        "bias": bias,
        "confidence": confidence,
        "score": score,
        "rules": rules,
        "sector_positive_ratio": round((sector_positive_ratio or 0) * 100, 1)
        if sector_positive_ratio is not None
        else None,
        "advance_ratio": round((advance_ratio or 0) * 100, 1)
        if advance_ratio is not None
        else None,
    }


def apply_intraday_tape_overrides(
    *,
    mode: str,
    trend_score: int,
    trend_rules: list[dict],
    momentum_score: int,
    momentum_rules: list[dict],
    breadth_score: int,
    breadth_rules: list[dict],
    tape: dict[str, Any] | None,
) -> dict[str, Any]:
    """Blend live tape into day-mode scores without disturbing swing logic."""
    if mode != "day" or not tape:
        return {
            "trend_score": trend_score,
            "trend_rules": trend_rules,
            "momentum_score": momentum_score,
            "momentum_rules": momentum_rules,
            "breadth_score": breadth_score,
            "breadth_rules": breadth_rules,
        }

    tape_bias = tape.get("bias", "NEUTRAL")
    tape_confidence = int(tape.get("confidence", 0))
    tape_score = int(tape.get("score", 50))
    tape_rules = list(tape.get("rules", []))

    if tape_bias == "NEUTRAL" or tape_confidence < 60:
        return {
            "trend_score": trend_score,
            "trend_rules": trend_rules,
            "momentum_score": momentum_score,
            "momentum_rules": momentum_rules,
            "breadth_score": breadth_score,
            "breadth_rules": breadth_rules,
        }

    base_weight = min(0.55, 0.25 + max(tape_confidence - 60, 0) / 100)

    adjusted_trend = _clamp(trend_score * (1 - base_weight) + tape_score * base_weight)
    adjusted_momentum = _clamp(
        momentum_score * (1 - min(0.65, base_weight + 0.05))
        + tape_score * min(0.65, base_weight + 0.05)
    )
    adjusted_breadth = _clamp(
        breadth_score * (1 - min(0.7, base_weight + 0.1))
        + tape_score * min(0.7, base_weight + 0.1)
    )

    summary_rule = {
        "rule": "Intraday tape override",
        "detail": (
            f"{tape_bias} bias {tape_confidence}/100 — "
            "day mode weights live tape over slower structure"
        ),
        "impact": "positive" if tape_bias == "LONG" else "negative",
    }

    return {
        "trend_score": adjusted_trend,
        "trend_rules": [summary_rule, *trend_rules, *tape_rules[:3]],
        "momentum_score": adjusted_momentum,
        "momentum_rules": [summary_rule, *momentum_rules, *tape_rules[:4]],
        "breadth_score": adjusted_breadth,
        "breadth_rules": [summary_rule, *breadth_rules, *tape_rules[:5]],
    }


def compute_market_quality(scores: dict[str, int]) -> int:
    """Compute weighted market quality score."""
    total = 0.0
    for category, weight in CATEGORY_WEIGHTS.items():
        total += scores.get(category, 50) * weight
    return _clamp(total)


def get_decision(score: int) -> str:
    """Return YES / CAUTION / NO based on score."""
    if score >= DECISION_THRESHOLDS["yes_min"]:
        return "YES"
    if score >= DECISION_THRESHOLDS["caution_min"]:
        return "CAUTION"
    return "NO"


def compute_directional_bias(
    regime: str,
    trend_score: int,
    momentum_score: int,
    breadth_score: int,
    vix_current: float | None,
    institutional_flows: dict | None = None,
    *,
    mode: str = "swing",
    intraday_tape: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Estimate whether current conditions favor long, short, or neutral exposure."""
    bull_score = 0.0
    bear_score = 0.0
    rules: list[dict] = []
    regime_weight = 3.0 if mode != "day" else 1.25

    if regime == "uptrend":
        bull_score += regime_weight
        rules.append(
            {
                "rule": "Regime alignment",
                "detail": "Trend regime favors long continuation",
                "impact": "positive",
            }
        )
    elif regime == "downtrend":
        bear_score += regime_weight
        rules.append(
            {
                "rule": "Regime alignment",
                "detail": "Trend regime favors short setups",
                "impact": "negative",
            }
        )
    else:
        rules.append(
            {
                "rule": "Regime alignment",
                "detail": "Choppy regime lowers directional conviction",
                "impact": "neutral",
            }
        )

    if trend_score >= 65:
        bull_score += 2
        rules.append(
            {
                "rule": "Trend score supportive",
                "detail": f"Trend {trend_score}/100",
                "impact": "positive",
            }
        )
    elif trend_score <= 40:
        bear_score += 2
        rules.append(
            {
                "rule": "Trend score weak",
                "detail": f"Trend {trend_score}/100",
                "impact": "negative",
            }
        )

    if momentum_score >= 60:
        bull_score += 1.5
        rules.append(
            {
                "rule": "Momentum supports follow-through",
                "detail": f"Momentum {momentum_score}/100",
                "impact": "positive",
            }
        )
    elif momentum_score <= 40:
        bear_score += 1.5
        rules.append(
            {
                "rule": "Momentum deterioration",
                "detail": f"Momentum {momentum_score}/100",
                "impact": "negative",
            }
        )

    if breadth_score >= 60:
        bull_score += 1.5
        rules.append(
            {
                "rule": "Breadth expansion",
                "detail": f"Breadth {breadth_score}/100",
                "impact": "positive",
            }
        )
    elif breadth_score <= 40:
        bear_score += 1.5
        rules.append(
            {
                "rule": "Breadth deterioration",
                "detail": f"Breadth {breadth_score}/100",
                "impact": "negative",
            }
        )

    if mode == "day" and intraday_tape:
        tape_bias = intraday_tape.get("bias", "NEUTRAL")
        tape_confidence = int(intraday_tape.get("confidence", 0))
        tape_weight = 0.0
        if tape_confidence >= 80:
            tape_weight = 4.0
        elif tape_confidence >= 70:
            tape_weight = 3.0
        elif tape_confidence >= 60:
            tape_weight = 1.75

        if tape_bias == "LONG" and tape_weight:
            bull_score += tape_weight
        elif tape_bias == "SHORT" and tape_weight:
            bear_score += tape_weight

        rules.append(
            {
                "rule": "Session tape",
                "detail": (
                    f"{tape_bias} {tape_confidence}/100 — "
                    "day mode allows live tape to override the backdrop"
                ),
                "impact": (
                    "positive"
                    if tape_bias == "LONG"
                    else "negative"
                    if tape_bias == "SHORT"
                    else "neutral"
                ),
            }
        )

    latest_flows = (institutional_flows or {}).get("latest") or {}
    flow_freshness = (institutional_flows or {}).get("freshness") or {}
    if flow_freshness.get("is_stale"):
        rules.append(
            {
                "rule": "Institutional positioning",
                "detail": (
                    f"Flow feed stale by {flow_freshness.get('lag_business_days', 0)} "
                    "business day(s)"
                ),
                "impact": "neutral",
            }
        )
    elif latest_flows:
        headline_bias = latest_flows.get("headline_bias")
        derivatives_bias = latest_flows.get("derivatives_bias")
        if headline_bias == "bullish":
            bull_score += 1.5
        elif headline_bias == "bearish":
            bear_score += 1.5

        if derivatives_bias == "bullish":
            bull_score += 1
        elif derivatives_bias == "bearish":
            bear_score += 1

        rules.append(
            {
                "rule": "Institutional positioning",
                "detail": (
                    f"Cash {latest_flows.get('cash_bias', 'neutral')} / "
                    f"Derivatives {derivatives_bias or 'neutral'}"
                ),
                "impact": (
                    "positive"
                    if headline_bias == "bullish"
                    else "negative"
                    if headline_bias == "bearish"
                    else "neutral"
                ),
            }
        )

    vix = _safe(vix_current, 15)
    if vix >= 26:
        rules.append(
            {
                "rule": "High VIX",
                "detail": f"VIX {vix:.1f} favors defined-risk execution",
                "impact": "neutral",
            }
        )

    delta = bull_score - bear_score
    abs_delta = abs(delta)
    if delta >= 1.5:
        bias = "LONG"
        confidence = _clamp(55 + abs_delta * 8)
    elif delta <= -1.5:
        bias = "SHORT"
        confidence = _clamp(55 + abs_delta * 8)
    else:
        bias = "NEUTRAL"
        confidence = _clamp(35 + abs_delta * 8)

    return {
        "bias": bias,
        "confidence": confidence,
        "rules": rules,
    }


def get_trade_decision(
    market_quality_score: int,
    execution_score: int,
    directional_bias: str,
    bias_confidence: int,
) -> str:
    """Turn quality, execution, and side conviction into a tradability decision."""
    base_decision = get_decision(market_quality_score)
    if execution_score < 35:
        return "NO"
    if directional_bias == "NEUTRAL":
        return base_decision
    if bias_confidence >= 80 and execution_score >= 70:
        return "YES"
    if bias_confidence >= 65 and execution_score >= 50 and base_decision == "NO":
        return "CAUTION"
    return base_decision


def resolve_execution_regime(
    *,
    mode: str,
    structural_regime: str,
    directional_bias: str,
    bias_confidence: int,
) -> str:
    """Choose the regime that should drive actual trade setup generation."""
    if mode != "day":
        return structural_regime
    if directional_bias == "LONG" and bias_confidence >= 65:
        return "uptrend"
    if directional_bias == "SHORT" and bias_confidence >= 65:
        return "downtrend"
    if directional_bias == "NEUTRAL" and bias_confidence >= 55:
        return "chop"
    return structural_regime


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
    above = sum(1 for moving_average in [sma_20, sma_50, sma_200] if moving_average and ltp > moving_average)
    slope = _safe(slope_50d, 0)
    _ = _safe(vix_current, 15)

    if above >= 3 and slope > 0:
        return "uptrend"
    if above == 0 and slope < 0:
        return "downtrend"
    return "chop"
