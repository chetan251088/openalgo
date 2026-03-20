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
MIN_DATA_POINTS = 25        # Minimum history length required
ATR_PERIOD = 14             # ATR calculation period
SMA_20_PERIOD = 20          # 20-day moving average
SMA_50_PERIOD = 50          # 50-day moving average
SMA_200_PERIOD = 200        # 200-day moving average
HIGH_20D_LOOKBACK = 20      # 20-day high lookback
RS_OUTPERFORMANCE_THRESHOLD = 2.0  # RS % to consider strong
ATR_MULTIPLIER_SL = 1.5     # ATR multiplier for stop loss (uptrend)
ATR_MULTIPLIER_TARGET = 2.5  # ATR multiplier for target (uptrend)
WEAK_RS_THRESHOLD = -2.0    # Threshold for weak relative strength
CHOP_SMA_THRESHOLD = 0.01   # Within 1% of 20d SMA for consolidation
CHOP_RETURN_THRESHOLD = 2.0  # Low volatility for consolidation


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
        sma_20 = closes.tail(SMA_20_PERIOD).mean()
        sma_50 = closes.tail(SMA_50_PERIOD).mean() if len(closes) >= SMA_50_PERIOD else None
        sma_200 = closes.tail(SMA_200_PERIOD).mean() if len(closes) >= SMA_200_PERIOD else None
        high_20d = closes.iloc[-HIGH_20D_LOOKBACK-1:-1].max() if len(closes) >= HIGH_20D_LOOKBACK + 1 else closes.max()

        # 5d return for relative strength
        stock_return_5d = (ltp - closes.iloc[-5]) / closes.iloc[-5] * 100 if len(closes) >= 5 else 0
        rs_vs_nifty = stock_return_5d - (nifty_return_5d or 0)

        # ATR for stop loss
        if len(hist) >= ATR_PERIOD:
            tr = pd.concat([
                hist["high"] - hist["low"],
                (hist["high"] - hist["close"].shift(1)).abs(),
                (hist["low"] - hist["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = tr.tail(ATR_PERIOD).mean()
        else:
            atr = (hist["high"] - hist["low"]).tail(5).mean()

        idea = {
            "symbol": symbol,
            "sector": sector,
            "ltp": round(ltp, 2),
            "rs_vs_nifty": round(rs_vs_nifty, 2),
        }

        if regime == "uptrend":
            # BUY: breakouts + pullbacks to MA + strong RS
            if ltp > high_20d and rs_vs_nifty > 0:
                idea["signal"] = "BUY"
                idea["reason"] = "20d breakout + relative strength"
                idea["conviction"] = "HIGH" if rs_vs_nifty > RS_OUTPERFORMANCE_THRESHOLD else "MED"
                idea["entry"] = round(ltp, 2)
                idea["stop_loss"] = round(ltp - ATR_MULTIPLIER_SL * atr, 2)
                idea["target"] = round(ltp + ATR_MULTIPLIER_TARGET * atr, 2)
                ideas.append(idea)
            elif sma_20 and abs(ltp - sma_20) / sma_20 < CHOP_SMA_THRESHOLD and ltp > (sma_50 or 0):
                idea["signal"] = "BUY"
                idea["reason"] = "Pullback to 20d MA support"
                idea["conviction"] = "MED"
                idea["entry"] = round(ltp, 2)
                idea["stop_loss"] = round(sma_20 - atr, 2)
                idea["target"] = round(ltp + 2 * atr, 2)
                ideas.append(idea)

        elif regime == "downtrend":
            # AVOID: weak RS stocks below all MAs
            if rs_vs_nifty < WEAK_RS_THRESHOLD and ltp < sma_20:
                idea["signal"] = "AVOID"
                idea["reason"] = "Weak RS + below 20d MA"
                idea["conviction"] = "HIGH" if ltp < (sma_50 or ltp + 1) else "MED"
                idea["entry"] = None
                idea["stop_loss"] = None
                idea["target"] = None
                ideas.append(idea)

        else:  # chop
            if sma_200 and ltp > sma_200 and abs(stock_return_5d) < CHOP_RETURN_THRESHOLD:
                idea["signal"] = "HOLD"
                idea["reason"] = "Above 200d MA, low volatility"
                idea["conviction"] = "LOW"
                idea["entry"] = None
                idea["stop_loss"] = round(sma_200 * 0.98, 2)
                idea["target"] = None
                ideas.append(idea)

    # Sort by conviction then RS
    conv_order = {"HIGH": 0, "MED": 1, "LOW": 2}
    ideas.sort(key=lambda x: (conv_order.get(x.get("conviction", "LOW"), 3), -x.get("rs_vs_nifty", 0)))

    return ideas[:10]  # Top 10 ideas


def select_fno_strategy(vix: float, regime: str) -> dict[str, str]:
    """Select F&O strategy type based on VIX regime + trend.

    Args:
        vix: Current VIX level (must be >= 0)
        regime: "uptrend" | "downtrend" | "chop"

    Returns: Strategy recommendation with type and bias.

    Raises:
        ValueError: If VIX is negative or regime is invalid.
    """
    if vix < 0:
        raise ValueError("VIX cannot be negative")
    if regime not in ("uptrend", "downtrend", "chop"):
        raise ValueError(f"Invalid regime '{regime}'. Must be 'uptrend', 'downtrend', or 'chop'.")

    if vix < 15:
        if regime == "uptrend":
            return {"type": "Buy Calls / Bull Call Spreads", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Buy Puts / Bear Put Spreads", "bias": "bearish"}
        else:
            return {"type": "Bull Call Spreads", "bias": "neutral-bullish"}
    elif vix > 20:
        return {"type": "Sell Strangles / Iron Condors", "bias": "neutral"}
    else:
        if regime == "uptrend":
            return {"type": "Bull Call Spreads / Sell OTM Puts", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Bear Put Spreads / Sell OTM Calls", "bias": "bearish"}
        else:
            return {"type": "Iron Condors", "bias": "neutral"}


def generate_fno_ideas(
    regime: str,
    vix: float,
    nifty_ltp: float | None,
    banknifty_ltp: float | None,
) -> list[dict[str, Any]]:
    """Generate F&O trade ideas."""
    ideas = []
    strategy = select_fno_strategy(vix, regime)

    if nifty_ltp:
        atm_strike = round(nifty_ltp / 50) * 50
        if "call" in strategy["type"].lower() or "bull" in strategy["type"].lower():
            ideas.append({
                "instrument": "NIFTY",
                "strategy": strategy["type"].split("/")[0].strip(),
                "strikes": f"{atm_strike}CE",
                "bias": strategy["bias"],
                "rationale": f"VIX {vix:.1f} + {regime} regime",
            })
        if "put" in strategy["type"].lower() or "bear" in strategy["type"].lower():
            ideas.append({
                "instrument": "NIFTY",
                "strategy": strategy["type"].split("/")[0].strip(),
                "strikes": f"{atm_strike}PE",
                "bias": strategy["bias"],
                "rationale": f"VIX {vix:.1f} + {regime} regime",
            })
        if "sell" in strategy["type"].lower() or "iron" in strategy["type"].lower():
            ideas.append({
                "instrument": "NIFTY",
                "strategy": strategy["type"].split("/")[0].strip(),
                "strikes": f"{atm_strike - 200}PE / {atm_strike + 200}CE",
                "bias": strategy["bias"],
                "rationale": f"High VIX {vix:.1f} — premium selling",
            })

    if banknifty_ltp:
        atm_bn = round(banknifty_ltp / 100) * 100
        ideas.append({
            "instrument": "BANKNIFTY",
            "strategy": strategy["type"].split("/")[0].strip(),
            "strikes": f"{atm_bn}CE" if regime != "downtrend" else f"{atm_bn}PE",
            "bias": strategy["bias"],
            "rationale": "Sector leader — financials",
        })

    return ideas
