"""
Market Pulse — Position Sizing & Risk Context (Phase 8).

Provides:
  - Position sizing advisor based on regime + VIX + quality score
  - Exposure estimation
  - Risk context labels
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Risk Parameters ─────────────────────────────────────────────
_BASE_RISK_PCT = float(os.getenv("MARKET_PULSE_BASE_RISK_PCT", "2.0"))
_MAX_RISK_PCT = float(os.getenv("MARKET_PULSE_MAX_RISK_PCT", "5.0"))


def compute_position_sizing(
    quality_score: int = 50,
    execution_score: int = 50,
    vix_current: float | None = None,
    regime: str = "chop",
    directional_bias: str = "NEUTRAL",
    bias_confidence: int = 50,
    account_value: float | None = None,
) -> dict[str, Any]:
    """Compute recommended position sizing based on market context.

    Returns recommended risk % of capital per trade, conviction level,
    and position sizing guidance.
    """
    # Base risk: 2% of capital per trade
    risk_pct = _BASE_RISK_PCT

    # Quality multiplier: scale 0.5x to 1.5x based on quality score
    quality_mult = 0.5 + (quality_score / 100)
    risk_pct *= quality_mult

    # Execution multiplier: 0.7x to 1.2x
    exec_mult = 0.7 + (execution_score / 200)
    risk_pct *= exec_mult

    # VIX adjustment: reduce in high vol, increase slightly in low vol
    vix_mult = 1.0
    if vix_current is not None:
        if vix_current > 25:
            vix_mult = 0.5  # Cut size in half
        elif vix_current > 20:
            vix_mult = 0.7
        elif vix_current > 16:
            vix_mult = 0.9
        elif vix_current < 11:
            vix_mult = 0.8  # Complacency — slightly reduce
        else:
            vix_mult = 1.1  # Sweet spot
    risk_pct *= vix_mult

    # Regime adjustment
    if regime == "uptrend" and directional_bias == "LONG":
        risk_pct *= 1.2  # Aligned — increase
    elif regime == "downtrend" and directional_bias == "SHORT":
        risk_pct *= 1.1  # Aligned — slight increase
    elif regime == "chop":
        risk_pct *= 0.7  # Choppy — reduce
    elif (regime == "uptrend" and directional_bias == "SHORT") or \
         (regime == "downtrend" and directional_bias == "LONG"):
        risk_pct *= 0.5  # Counter-trend — halve

    # Bias confidence
    if bias_confidence < 40:
        risk_pct *= 0.7
    elif bias_confidence > 80:
        risk_pct *= 1.1

    # Cap at max
    risk_pct = min(risk_pct, _MAX_RISK_PCT)
    risk_pct = max(risk_pct, 0.25)
    risk_pct = round(risk_pct, 2)

    # Size label
    if risk_pct >= 3.5:
        size_label = "AGGRESSIVE"
        color = "green"
    elif risk_pct >= 2.0:
        size_label = "NORMAL"
        color = "cyan"
    elif risk_pct >= 1.0:
        size_label = "REDUCED"
        color = "amber"
    else:
        size_label = "MINIMAL"
        color = "red"

    # Position value if account provided
    position_value = None
    risk_amount = None
    if account_value:
        risk_amount = round(account_value * risk_pct / 100, 0)
        position_value = round(risk_amount / 0.02, 0)  # Assuming 2% stop loss

    result: dict[str, Any] = {
        "risk_per_trade_pct": risk_pct,
        "size_label": size_label,
        "color": color,
        "multipliers": {
            "quality": round(quality_mult, 2),
            "execution": round(exec_mult, 2),
            "vix": round(vix_mult, 2),
        },
        "context": _build_context_note(quality_score, vix_current, regime, directional_bias),
    }

    if risk_amount is not None:
        result["risk_amount"] = risk_amount
        result["suggested_position_value"] = position_value

    return result


def _build_context_note(
    quality: int,
    vix: float | None,
    regime: str,
    bias: str,
) -> str:
    """Build a plain-English risk context note."""
    parts = []

    if quality >= 80:
        parts.append("Favorable market quality supports larger positions")
    elif quality >= 60:
        parts.append("Moderate market quality — standard sizing")
    elif quality >= 40:
        parts.append("Below-average quality — reduce exposure")
    else:
        parts.append("Poor market quality — minimal positions only")

    if vix is not None:
        if vix > 22:
            parts.append(f"VIX at {vix:.1f} — elevated volatility demands smaller size")
        elif vix < 12:
            parts.append(f"VIX at {vix:.1f} — low vol may precede a spike")

    if regime == "chop":
        parts.append("Choppy conditions favor reduced position sizes")

    return ". ".join(parts) + "."


def compute_exposure_summary(
    open_positions: list[dict] | None = None,
    quality_score: int = 50,
) -> dict[str, Any]:
    """Summarize current exposure relative to market quality."""
    if not open_positions:
        return {
            "total_positions": 0,
            "estimated_exposure": 0,
            "risk_level": "none",
            "note": "No open positions detected",
        }

    total = len(open_positions)
    buy_count = sum(1 for p in open_positions if p.get("quantity", 0) > 0)
    sell_count = total - buy_count

    risk_level = "normal"
    if total > 5 and quality_score < 50:
        risk_level = "elevated"
    elif total > 3 and quality_score < 40:
        risk_level = "high"

    return {
        "total_positions": total,
        "long_positions": buy_count,
        "short_positions": sell_count,
        "risk_level": risk_level,
        "note": f"{total} open positions with quality score {quality_score}/100",
    }
