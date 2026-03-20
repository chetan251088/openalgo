"""
Rule-based equity and F&O screener.
Screens Nifty 50 constituents based on market regime.
"""

import logging
from typing import Any

import pandas as pd

from services.market_pulse_config import EXECUTION_SWING

logger = logging.getLogger(__name__)

# Screening thresholds
NEAR_HIGH_THRESHOLD = 0.98  # Within 2% of 20d high
NEAR_LOW_THRESHOLD = 1.02   # Within 2% of 20d low
CHOP_BAND_WIDTH = 0.05      # ±5% around 20d SMA
MIN_DATA_POINTS = 25        # Minimum history length required
CONVICTION_THRESHOLD = 50   # Minimum conviction to include in ideas


def screen_equities(
    constituent_data: dict[str, dict],
    regime: str,
    nifty_history: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Screen Nifty 50 stocks for trade ideas based on regime.

    Args:
        constituent_data: {symbol: {"history": DataFrame, "sector": str}}
        regime: "uptrend" | "downtrend" | "chop"
        nifty_history: Nifty 50 OHLCV for relative strength calc

    Returns: List of equity ideas sorted by conviction.
    """
    ideas = []

    nifty_return_5d = None
    if nifty_history is not None and len(nifty_history) >= 5:
        nc = nifty_history["close"]
        nifty_return_5d = (nc.iloc[-1] - nc.iloc[-5]) / nc.iloc[-5] * 100

    for symbol, data in constituent_data.items():
        hist = data.get("history")
        sector = data.get("sector", "")
        if hist is None or len(hist) < MIN_DATA_POINTS:
            continue

        closes = hist["close"]
        ltp = closes.iloc[-1]

        # Compute indicators
        sma_20 = closes.tail(20).mean()
        sma_50 = closes.tail(50).mean() if len(closes) >= 50 else None
        sma_200 = closes.tail(200).mean() if len(closes) >= 200 else None
        
        # Compute 20d high/low outside regime blocks for consistency
        high_20d = closes.iloc[-21:-1].max() if len(closes) >= 21 else closes.max()
        low_20d = closes.iloc[-21:-1].min() if len(closes) >= 21 else closes.min()

        # 5d return for relative strength
        stock_return_5d = (ltp - closes.iloc[-5]) / closes.iloc[-5] * 100 if len(closes) >= 5 else 0

        # Regime-specific rules
        conviction = 0
        details = []

        if regime == "uptrend":
            # Long setup: recent higher high, above SMA20/50
            if ltp > high_20d * NEAR_HIGH_THRESHOLD:  # near 20d high
                conviction += 40
                details.append("Near 20d high")
            if sma_50 and ltp > sma_50:
                conviction += 30
                details.append("Above 50d SMA")
            if sma_20 and ltp > sma_20:
                conviction += 20
                details.append("Above 20d SMA")
            # Relative strength: stock outperforming Nifty
            if nifty_return_5d is not None and stock_return_5d > nifty_return_5d:
                conviction += 10
                details.append("Outperforming Nifty")

        elif regime == "downtrend":
            # Short setup: recent lower low, below SMA20/50
            if ltp < low_20d * NEAR_LOW_THRESHOLD:
                conviction += 40
                details.append("Near 20d low")
            if sma_50 and ltp < sma_50:
                conviction += 30
                details.append("Below 50d SMA")
            if sma_20 and ltp < sma_20:
                conviction += 20
                details.append("Below 20d SMA")

        elif regime == "chop":
            # Range-trade setup: oscillating around SMA20
            if sma_20 and abs(ltp - sma_20) / sma_20 < CHOP_BAND_WIDTH:
                conviction = 50
                details.append("Near SMA 20 (range bound)")

        if conviction >= CONVICTION_THRESHOLD:
            ideas.append(
                {
                    "symbol": symbol,
                    "sector": sector,
                    "ltp": float(ltp),
                    "sma_20": float(sma_20) if sma_20 else None,
                    "sma_50": float(sma_50) if sma_50 else None,
                    "sma_200": float(sma_200) if sma_200 else None,
                    "conviction": conviction,
                    "details": ", ".join(details),
                    "regime": regime,
                }
            )

    # Sort by conviction descending
    return sorted(ideas, key=lambda x: x["conviction"], reverse=True)


def select_fno_strategy(vix: float, regime: str) -> dict[str, Any]:
    """Select F&O strategy based on VIX and market regime.

    Args:
        vix: Current VIX level
        regime: "uptrend" | "downtrend" | "chop"

    Returns: Strategy recommendation with type, description, and risk/reward.
    """
    if vix < 0:
        raise ValueError("VIX cannot be negative")
    if regime not in ("uptrend", "downtrend", "chop"):
        raise ValueError(f"Invalid regime '{regime}'. Must be 'uptrend', 'downtrend', or 'chop'.")
    
    if vix > 20:
        # High volatility: sell premium
        return {
            "type": "Iron Condor / Short Straddle",
            "description": "Sell premium in high VIX. Target mean reversion.",
            "risk": "Undefined",
            "reward": "Premium collected",
            "levels": {"profit_target": vix * 0.9, "stop_loss": vix * 1.2},
        }

    # Low VIX: directional bias
    if regime == "uptrend":
        return {
            "type": "Bull Call Spread",
            "description": "Long calls on strength. Limited risk.",
            "risk": "Debit paid",
            "reward": f"{(vix * 50):.0f} points",
            "levels": {"entry": "ATM", "target": "+2%", "stop": "-1%"},
        }
    elif regime == "downtrend":
        return {
            "type": "Bear Put Spread",
            "description": "Short puts on weakness. Defined risk.",
            "risk": "Spread width",
            "reward": "Premium collected",
            "levels": {"entry": "ATM", "target": "+2%", "stop": "-1%"},
        }
    else:  # chop
        return {
            "type": "Iron Butterfly",
            "description": "Sell straddle around ATM. High decay.",
            "risk": "Wing spread",
            "reward": "Premium collected",
            "levels": {"profit_target": vix * 0.8, "stop_loss": vix * 1.15},
        }
