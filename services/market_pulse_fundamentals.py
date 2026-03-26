"""
Market Pulse — Fundamental Quality Scoring (Phase 2, Tier 1+2).

Computes quality scores, key ratios, and shareholding patterns
for equity ideas using existing OpenAlgo services.

Data sources:
  - Zerodha multiquotes (LTP, prev_close, volume, OI)
  - Zerodha history (1Y daily for price-strength metrics)
  - OpenAlgo SymToken DB (sector, instrument info)
  - NSE shareholding API (promoter/FII/DII quarterly)
"""

import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────
_fund_cache: dict[str, Any] = {}
_fund_cache_ts: dict[str, float] = {}
_CACHE_TTL = max(60, int(os.getenv("MARKET_PULSE_FUND_CACHE_TTL", "3600")))  # 1 hr


def _check_cache(key: str) -> Any | None:
    if key in _fund_cache:
        if (time.time() - _fund_cache_ts.get(key, 0)) < _CACHE_TTL:
            return _fund_cache[key]
    return None


def _set_cache(key: str, value: Any) -> None:
    _fund_cache[key] = value
    _fund_cache_ts[key] = time.time()


def _get_api_key() -> str | None:
    return os.getenv("APP_KEY") or os.getenv("OPENALGO_API_KEY")


# ── Quote Fetching ───────────────────────────────────────────────

def _fetch_quotes_batch(symbols: list[dict[str, str]]) -> dict[str, dict]:
    """Fetch quotes for multiple symbols via existing multiquotes service."""
    try:
        from services.quotes_service import get_multiquotes
        api_key = _get_api_key()
        if not api_key:
            return {}

        success, resp, _ = get_multiquotes(symbols, api_key=api_key)
        if not success:
            return {}

        result = {}
        for item in resp.get("results", []):
            sym = item.get("symbol")
            if sym and "data" in item:
                result[sym] = item["data"]
            elif sym and "error" not in item:
                result[sym] = item
        return result
    except Exception as e:
        logger.warning("Multiquotes batch failed: %s", e)
        return {}


def _fetch_history_for_strength(symbol: str, exchange: str) -> dict[str, Any]:
    """Fetch 1Y daily history to compute price-strength metrics."""
    cache_key = f"hist:{symbol}:{exchange}"
    cached = _check_cache(cache_key)
    if cached is not None:
        return cached

    try:
        from services.history_service import get_history_with_auth
        api_key = _get_api_key()
        if not api_key:
            return {}

        end = date.today()
        start = end - timedelta(days=365)

        df = get_history_with_auth(
            symbol=symbol,
            exchange=exchange,
            interval="D",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )

        if df is None or df.empty:
            return {}

        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        volumes = df["volume"].tolist()

        if len(closes) < 20:
            return {}

        high_52w = max(highs)
        low_52w = min(lows)
        current = closes[-1]

        # 200-DMA
        dma_200 = sum(closes[-200:]) / min(len(closes), 200) if len(closes) >= 200 else sum(closes) / len(closes)
        # 50-DMA
        dma_50 = sum(closes[-50:]) / min(len(closes), 50) if len(closes) >= 50 else sum(closes) / len(closes)

        # Average volume (20-day)
        avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)

        # Relative volume
        today_vol = volumes[-1] if volumes else 0
        rvol = round(today_vol / max(avg_vol_20, 1), 2)

        # Returns
        ret_1m = ((current / closes[-22]) - 1) * 100 if len(closes) >= 22 else 0
        ret_3m = ((current / closes[-66]) - 1) * 100 if len(closes) >= 66 else 0
        ret_6m = ((current / closes[-132]) - 1) * 100 if len(closes) >= 132 else 0
        ret_1y = ((current / closes[0]) - 1) * 100

        result = {
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "vs_52w_high": round(((current / high_52w) - 1) * 100, 2),
            "vs_52w_low": round(((current / low_52w) - 1) * 100, 2),
            "vs_200dma": round(((current / dma_200) - 1) * 100, 2),
            "vs_50dma": round(((current / dma_50) - 1) * 100, 2),
            "dma_200": round(dma_200, 2),
            "dma_50": round(dma_50, 2),
            "rvol": rvol,
            "avg_vol_20": int(avg_vol_20),
            "ret_1m": round(ret_1m, 2),
            "ret_3m": round(ret_3m, 2),
            "ret_6m": round(ret_6m, 2),
            "ret_1y": round(ret_1y, 2),
        }

        _set_cache(cache_key, result)
        return result

    except Exception as e:
        logger.warning("History fetch failed for %s:%s - %s", symbol, exchange, e)
        return {}


# ── Shareholding Pattern (NSE) ───────────────────────────────────

def _fetch_shareholding(symbol: str) -> dict[str, Any] | None:
    """Fetch shareholding pattern from NSE API."""
    cache_key = f"shp:{symbol}"
    cached = _check_cache(cache_key)
    if cached is not None:
        return cached

    try:
        from services.market_pulse_nse import _nse_get
        data = _nse_get(f"/api/corporate-shareholding?index=equities&symbol={symbol}")

        if data is None:
            return None

        # Parse shareholding data
        result: dict[str, list[float]] = {
            "promoter": [],
            "fii": [],
            "dii": [],
            "public": [],
            "quarters": [],
        }

        shareholding_list = data if isinstance(data, list) else data.get("data", [])
        if not shareholding_list:
            return None

        for quarter in shareholding_list[-4:]:  # Last 4 quarters
            if isinstance(quarter, dict):
                result["quarters"].append(quarter.get("date", ""))
                for cat in quarter.get("shareholdings", []):
                    cat_name = str(cat.get("category", "")).lower()
                    pct = cat.get("percentage", 0)
                    if "promoter" in cat_name:
                        result["promoter"].append(round(pct, 2))
                    elif "fii" in cat_name or "fpi" in cat_name or "foreign" in cat_name:
                        result["fii"].append(round(pct, 2))
                    elif "dii" in cat_name or "mutual" in cat_name or "domestic" in cat_name:
                        result["dii"].append(round(pct, 2))
                    elif "public" in cat_name or "others" in cat_name:
                        result["public"].append(round(pct, 2))

        if any(result[k] for k in ["promoter", "fii", "dii"]):
            _set_cache(cache_key, result)
            return result

    except Exception as e:
        logger.warning("Shareholding fetch failed for %s: %s", symbol, e)

    return None


# ── Quality Score Computation ─────────────────────────────────────

def compute_quality_score(
    quote: dict[str, Any],
    price_strength: dict[str, Any],
) -> int:
    """Compute a 0-100 quality score from available data.

    Components:
      - PE Valuation (25%): Lower PE (within reason) scores higher
      - Volume Health (20%): Higher RVOL = more participation
      - Price Strength (25%): Near 52W high + above 200DMA = strong
      - Momentum (15%): 1M + 3M returns
      - Stability (15%): Distance from 52W low (higher = more stable)
    """
    score = 0.0

    # 1. PE Valuation Score (25 pts)
    pe = quote.get("pe", 0)
    if pe and pe > 0:
        if pe < 15:
            pe_score = 25  # Deep value
        elif pe < 25:
            pe_score = 22  # Reasonable
        elif pe < 40:
            pe_score = 15  # Growth premium
        elif pe < 60:
            pe_score = 8   # Expensive
        else:
            pe_score = 3   # Very expensive
    else:
        pe_score = 12  # Unknown — neutral
    score += pe_score

    # 2. Volume Health (20 pts)
    rvol = price_strength.get("rvol", 1.0)
    if rvol >= 2.0:
        vol_score = 20  # High participation
    elif rvol >= 1.2:
        vol_score = 16
    elif rvol >= 0.8:
        vol_score = 12  # Normal
    elif rvol >= 0.5:
        vol_score = 8   # Low interest
    else:
        vol_score = 4   # Very low
    score += vol_score

    # 3. Price Strength (25 pts)
    vs_high = price_strength.get("vs_52w_high", -50)
    vs_200dma = price_strength.get("vs_200dma", 0)

    # Near 52W high = strong
    if vs_high > -5:
        high_score = 15
    elif vs_high > -15:
        high_score = 12
    elif vs_high > -25:
        high_score = 8
    else:
        high_score = 4

    # Above 200DMA = bullish structure
    if vs_200dma > 5:
        dma_score = 10
    elif vs_200dma > 0:
        dma_score = 8
    elif vs_200dma > -5:
        dma_score = 5
    else:
        dma_score = 2

    score += high_score + dma_score

    # 4. Momentum (15 pts)
    ret_1m = price_strength.get("ret_1m", 0)
    ret_3m = price_strength.get("ret_3m", 0)
    # Moderate positive momentum is ideal (not overbought)
    if 0 < ret_1m < 10:
        mom_score = 8
    elif ret_1m >= 10:
        mom_score = 5  # May be overbought
    elif ret_1m > -5:
        mom_score = 6  # Slight pullback
    else:
        mom_score = 3  # Weak

    if ret_3m > 0:
        mom_score += 7 if ret_3m < 20 else 4
    else:
        mom_score += 2
    score += min(mom_score, 15)

    # 5. Stability (15 pts) — distance from 52W low
    vs_low = price_strength.get("vs_52w_low", 0)
    if vs_low > 40:
        stab_score = 15
    elif vs_low > 20:
        stab_score = 12
    elif vs_low > 10:
        stab_score = 8
    else:
        stab_score = 4
    score += stab_score

    return min(100, max(0, int(score)))


def _classify_market_cap(ltp: float, symbol: str) -> str:
    """Rough market cap tier classification."""
    # Without actual shares outstanding, use symbol heuristics
    large_caps = {
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "HCLTECH",
        "AXISBANK", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN",
        "ULTRACEMCO", "NESTLEIND", "BAJFINANCE", "WIPRO", "M&M",
        "NTPC", "POWERGRID", "TATAMOTORS", "TATASTEEL", "ONGC",
        "ADANIPORTS", "COALINDIA", "BAJAJFINSV", "HDFCLIFE", "TECHM",
        "DRREDDY", "DIVISLAB", "GRASIM", "CIPLA", "HEROMOTOCO",
        "APOLLOHOSP", "EICHERMOT", "JSWSTEEL", "BPCL",
        "BANKNIFTY", "NIFTY",
    }
    if symbol.upper() in large_caps:
        return "Large"
    if ltp > 1000:
        return "Mid"  # Rough heuristic
    return "Small"


# ── Batch Fundamentals for Equity Ideas ──────────────────────────

def batch_fundamentals(
    symbols: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Compute fundamentals for a batch of symbols.

    Args:
        symbols: [{"symbol": "RELIANCE", "exchange": "NSE"}, ...]

    Returns:
        {"RELIANCE": {"quality_score": 78, "pe": 28.5, ...}, ...}
    """
    cache_key = "batch:" + ",".join(sorted(s["symbol"] for s in symbols))
    cached = _check_cache(cache_key)
    if cached is not None:
        return cached

    if not symbols:
        return {}

    # 1. Fetch quotes in batch
    quotes_map = _fetch_quotes_batch(symbols)

    result: dict[str, dict[str, Any]] = {}

    for sym_info in symbols:
        symbol = sym_info["symbol"]
        exchange = sym_info.get("exchange", "NSE")

        try:
            quote = quotes_map.get(symbol, {})
            ltp = quote.get("ltp", 0)
            prev_close = quote.get("prev_close", 0)
            volume = quote.get("volume", 0)

            # Compute basic ratios from price data
            pe = 0
            pb = 0
            div_yield = 0

            # PE from Zerodha quote if available (some endpoints include it)
            if "pe" in quote:
                pe = quote["pe"]

            # Price strength from 1Y history
            price_strength = _fetch_history_for_strength(symbol, exchange)

            # Quality score
            quality = compute_quality_score(
                {"pe": pe, "ltp": ltp, "volume": volume},
                price_strength,
            )

            # Market cap tier
            cap_tier = _classify_market_cap(ltp, symbol)

            entry = {
                "quality_score": quality,
                "pe": round(pe, 2) if pe else None,
                "pb": round(pb, 2) if pb else None,
                "div_yield": round(div_yield, 2) if div_yield else None,
                "ltp": round(ltp, 2) if ltp else None,
                "prev_close": round(prev_close, 2) if prev_close else None,
                "volume": volume,
                "market_cap_tier": cap_tier,
                "price_strength": {
                    "vs_52w_high": price_strength.get("vs_52w_high"),
                    "vs_200dma": price_strength.get("vs_200dma"),
                    "vs_50dma": price_strength.get("vs_50dma"),
                    "rvol": price_strength.get("rvol"),
                    "ret_1m": price_strength.get("ret_1m"),
                    "ret_3m": price_strength.get("ret_3m"),
                    "ret_1y": price_strength.get("ret_1y"),
                },
            }

            # Shareholding (Tier 2 — triggers NSE rate limit if batched for too many symbols)
            if len(symbols) <= 3:
                try:
                    shp = _fetch_shareholding(symbol)
                    if shp:
                        entry["shareholding"] = shp
                except Exception:
                    pass

            result[symbol] = entry

        except Exception as e:
            logger.warning("Fundamentals failed for %s: %s", symbol, e)
            result[symbol] = {"quality_score": 50, "error": str(e)}

    _set_cache(cache_key, result)
    return result
