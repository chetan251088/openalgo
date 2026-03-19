"""
Market Pulse data aggregation service.
Fetches from broker APIs (via existing OpenAlgo services) + NSE website.
Caches for 30 seconds.
"""

import json
import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from services.market_pulse_config import (
    CACHE_TTL_SECONDS,
    EXECUTION_SWING,
    INDEX_SYMBOLS,
    SECTOR_INDICES,
    USDINR_SYMBOL,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ─────────────────────────────────────────────
_cache: dict[str, Any] = {}
_cache_ts: float = 0


def _is_cache_valid() -> bool:
    return (time.time() - _cache_ts) < CACHE_TTL_SECONDS and bool(_cache)


def _load_json(filename: str) -> dict:
    """Load a JSON file from the data/ directory."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", filename)
    with open(path, "r") as f:
        return json.load(f)


def _get_constituents() -> list[dict]:
    """Load Nifty 50 constituent list."""
    data = _load_json("nifty50_constituents.json")
    return data.get("constituents", [])


def _get_events() -> list[dict]:
    """Load market events calendar."""
    data = _load_json("market_events.json")
    return data.get("events", [])


# ── Broker Data Fetching ────────────────────────────────────────

def _fetch_quote(symbol: str, exchange: str) -> dict | None:
    """Fetch a single quote via the existing quotes service."""
    try:
        from database.auth_db import get_api_key_for_tradingview
        from services.quotes_service import get_quotes

        api_key = get_api_key_for_tradingview()
        if not api_key:
            logger.warning("No API key available for market pulse")
            return None

        success, data, _ = get_quotes(symbol=symbol, exchange=exchange, api_key=api_key)
        if success and data.get("status") == "success":
            return data.get("data", {})
    except Exception as e:
        logger.warning("Quote fetch failed for %s:%s - %s", symbol, exchange, e)
    return None


def _fetch_history(symbol: str, exchange: str, days: int = 200) -> pd.DataFrame | None:
    """Fetch historical OHLCV via existing history service."""
    try:
        from database.auth_db import get_api_key_for_tradingview
        from services.history_service import get_history

        api_key = get_api_key_for_tradingview()
        if not api_key:
            return None

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        success, data, _ = get_history(
            symbol=symbol,
            exchange=exchange,
            interval="D",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            api_key=api_key,
        )

        if success and data.get("status") == "success":
            candles = data.get("data", [])
            if candles:
                df = pd.DataFrame(candles)
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df
    except Exception as e:
        logger.warning("History fetch failed for %s:%s - %s", symbol, exchange, e)
    return None


def _fetch_option_chain_pcr() -> float | None:
    """Compute Nifty Put/Call Ratio from option chain OI."""
    try:
        from database.auth_db import get_api_key_for_tradingview
        from services.option_chain_service import get_option_chain

        api_key = get_api_key_for_tradingview()
        if not api_key:
            return None

        success, data, _ = get_option_chain(
            symbol="NIFTY", exchange="NFO", api_key=api_key
        )
        if success and data.get("status") == "success":
            chain = data.get("data", [])
            total_put_oi = sum(row.get("put_oi", 0) for row in chain)
            total_call_oi = sum(row.get("call_oi", 0) for row in chain)
            if total_call_oi > 0:
                return round(total_put_oi / total_call_oi, 3)
    except Exception as e:
        logger.warning("PCR fetch failed: %s", e)
    return None


# ── Technical Indicators ────────────────────────────────────────

def compute_sma(series: pd.Series, period: int) -> float | None:
    """Compute simple moving average."""
    if len(series) < period:
        return None
    return round(series.tail(period).mean(), 2)


def compute_rsi(series: pd.Series, period: int = 14) -> float | None:
    """Compute RSI."""
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.tail(period).mean()
    avg_loss = loss.tail(period).mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_slope(series: pd.Series, period: int = 5) -> float | None:
    """Compute slope as percentage change over period."""
    if len(series) < period:
        return None
    return round((series.iloc[-1] - series.iloc[-period]) / series.iloc[-period] * 100, 4)


def compute_percentile(current: float, series: pd.Series) -> float | None:
    """Compute percentile rank of current value in historical series."""
    if len(series) < 20:
        return None
    below = (series < current).sum()
    return round(below / len(series) * 100, 1)


# ── Main Aggregation ────────────────────────────────────────────

def fetch_market_data(mode: str = "swing") -> dict[str, Any]:
    """Fetch and aggregate all market data. Returns cached if fresh.

    Args:
        mode: "swing" or "day"

    Returns: dict with all data needed for scoring.
    """
    global _cache, _cache_ts

    if _is_cache_valid() and _cache.get("mode") == mode:
        return _cache

    result: dict[str, Any] = {"mode": mode, "errors": []}

    # 1. Index quotes (current prices)
    ticker = {}
    for key, info in INDEX_SYMBOLS.items():
        quote = _fetch_quote(info["symbol"], info["exchange"])
        if quote:
            ticker[key] = quote
        else:
            result["errors"].append(f"Quote unavailable: {key}")
    result["ticker"] = ticker

    # 2. USDINR
    usdinr_quote = _fetch_quote(USDINR_SYMBOL["symbol"], USDINR_SYMBOL["exchange"])
    if usdinr_quote:
        ticker["USDINR"] = usdinr_quote

    # 3. Sector indices
    sectors = {}
    for key, info in SECTOR_INDICES.items():
        quote = _fetch_quote(info["symbol"], info["exchange"])
        if quote:
            sectors[key] = quote
    result["sectors"] = sectors

    # 4. Historical data for Nifty (for MAs, RSI, slopes)
    nifty_hist = _fetch_history("NIFTY 50", "NSE", days=250)
    result["nifty_history"] = nifty_hist

    # 5. Historical data for BankNifty
    banknifty_hist = _fetch_history("NIFTY BANK", "NSE", days=100)
    result["banknifty_history"] = banknifty_hist

    # 6. India VIX history (for percentile and slope)
    vix_hist = _fetch_history("INDIA VIX", "NSE", days=260)
    result["vix_history"] = vix_hist

    # 7. USDINR history
    usdinr_hist = _fetch_history(USDINR_SYMBOL["symbol"], USDINR_SYMBOL["exchange"], days=50)
    result["usdinr_history"] = usdinr_hist

    # 8. Sector index histories (for MAs, to check above/below 20d)
    sector_histories = {}
    for key, info in SECTOR_INDICES.items():
        hist = _fetch_history(info["symbol"], info["exchange"], days=50)
        if hist is not None:
            sector_histories[key] = hist
    result["sector_histories"] = sector_histories

    # 9. Nifty 50 constituent histories (for breadth + execution window)
    # IMPORTANT: Must fetch 200+ days for % above 200d MA breadth scoring
    # and chop-regime 200d MA logic. The design specifies 50 symbols × 50 days
    # but that only covers execution window breakout tracking. Breadth scoring
    # (design line 117) and chop regime (design line 169) need 200d.
    # We fetch 250 days (extra buffer for market holidays).
    _CONSTITUENT_HISTORY_DAYS = 250  # enough for 200d MA + holiday buffer
    constituents = _get_constituents()
    constituent_data = {}
    for c in constituents:
        hist = _fetch_history(c["symbol"], c["exchange"], days=_CONSTITUENT_HISTORY_DAYS)
        if hist is not None:
            constituent_data[c["symbol"]] = {"history": hist, "sector": c["sector"]}
    result["constituent_data"] = constituent_data

    # 10. Nifty PCR
    pcr = _fetch_option_chain_pcr()
    result["pcr"] = pcr

    # 11. NSE breadth
    try:
        from services.market_pulse_nse import fetch_market_breadth
        breadth = fetch_market_breadth()
        result["nse_breadth"] = breadth
    except Exception as e:
        logger.warning("NSE breadth fetch failed: %s", e)
        result["errors"].append("NSE breadth unavailable")
        result["nse_breadth"] = None

    # 12. Events calendar
    result["events"] = _get_events()

    # 13. Computed indicators for Nifty
    if nifty_hist is not None and "close" in nifty_hist.columns:
        closes = nifty_hist["close"]
        result["nifty_indicators"] = {
            "sma_20": compute_sma(closes, 20),
            "sma_50": compute_sma(closes, 50),
            "sma_200": compute_sma(closes, 200),
            "rsi_14": compute_rsi(closes, 14),
            "slope_50d": compute_slope(
                pd.Series([compute_sma(closes.head(len(closes) - i), 50) for i in range(5)][::-1]),
                5,
            ) if len(closes) >= 55 else None,
            "slope_200d": compute_slope(
                pd.Series([compute_sma(closes.head(len(closes) - i), 200) for i in range(5)][::-1]),
                5,
            ) if len(closes) >= 205 else None,
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
        }
    else:
        result["nifty_indicators"] = {}

    # 14. BankNifty indicators
    if banknifty_hist is not None and "close" in banknifty_hist.columns:
        closes = banknifty_hist["close"]
        result["banknifty_indicators"] = {
            "sma_50": compute_sma(closes, 50),
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
        }
    else:
        result["banknifty_indicators"] = {}

    # 15. VIX indicators
    if vix_hist is not None and "close" in vix_hist.columns:
        closes = vix_hist["close"]
        current_vix = closes.iloc[-1] if len(closes) > 0 else None
        result["vix_indicators"] = {
            "current": current_vix,
            "slope_5d": compute_slope(closes, 5),
            "percentile_1y": compute_percentile(current_vix, closes) if current_vix else None,
        }
    else:
        result["vix_indicators"] = {}

    # 16. USDINR indicators
    if usdinr_hist is not None and "close" in usdinr_hist.columns:
        closes = usdinr_hist["close"]
        result["usdinr_indicators"] = {
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
            "slope_5d": compute_slope(closes, 5),
            "slope_20d": compute_slope(closes, 20),
        }
    else:
        result["usdinr_indicators"] = {}

    result["updated_at"] = time.time()
    _cache = result
    _cache_ts = time.time()

    return result
