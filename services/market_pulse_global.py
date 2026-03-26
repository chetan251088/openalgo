"""
Market Pulse — Global Correlation & Cross-Market Context (Phase 4).

Provides:
  - GIFT NIFTY / SGX NIFTY pre-market gap context
  - Nifty vs BankNifty relative strength (intraday)
  - Gold / Crude correlation context
  - Rolling correlations
"""

import logging
import os
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── Cache ───────────────────────────────────────────────────────
_global_cache: dict[str, Any] = {}
_global_cache_ts: float = 0
_GLOBAL_CACHE_TTL = max(30, int(os.getenv("MARKET_PULSE_GLOBAL_CACHE_TTL", "120")))


def _get_api_key() -> str | None:
    return os.getenv("APP_KEY") or os.getenv("OPENALGO_API_KEY")


def compute_nifty_banknifty_rs(
    nifty_quote: dict | None,
    banknifty_quote: dict | None,
) -> dict[str, Any] | None:
    """Compute intraday Nifty vs BankNifty relative strength."""
    if not nifty_quote or not banknifty_quote:
        return None

    nifty_change = nifty_quote.get("change_pct")
    bn_change = banknifty_quote.get("change_pct")

    if nifty_change is None or bn_change is None:
        return None

    nifty_change = float(nifty_change)
    bn_change = float(bn_change)
    spread = round(bn_change - nifty_change, 3)

    # Interpretation
    if spread > 1.0:
        interpretation = "banks_leading"
        note = "BankNifty outperforming — typically bullish rotation into financials"
    elif spread < -1.0:
        interpretation = "banks_lagging"
        note = "BankNifty underperforming — possible defensive rotation"
    elif abs(spread) < 0.3:
        interpretation = "broad_based"
        note = "Nifty and BankNifty moving in sync — broad-based move"
    else:
        interpretation = "slight_divergence"
        note = "Mild divergence — watch for rotation signal"

    return {
        "nifty_change_pct": round(nifty_change, 2),
        "banknifty_change_pct": round(bn_change, 2),
        "spread": spread,
        "interpretation": interpretation,
        "note": note,
    }


def compute_rolling_correlation(
    series_a: list | None,
    series_b: list | None,
    window: int = 20,
) -> float | None:
    """Compute rolling correlation between two return series."""
    if not series_a or not series_b:
        return None

    min_len = min(len(series_a), len(series_b), window)
    if min_len < 10:
        return None

    a = np.array(series_a[-min_len:], dtype=float)
    b = np.array(series_b[-min_len:], dtype=float)

    # Handle constant series
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0

    corr = float(np.corrcoef(a, b)[0, 1])
    if np.isnan(corr):
        return None
    return round(corr, 3)


def fetch_commodity_context(
    ticker: dict[str, dict] | None,
) -> dict[str, Any]:
    """Extract Gold & Crude context from available ticker data."""
    result: dict[str, Any] = {}

    # Gold (MCX GOLD or MCX GoldBees or any available proxy)
    gold_symbols = ["GOLDM", "GOLD", "GOLDBEES"]
    for sym in gold_symbols:
        quote = (ticker or {}).get(sym)
        if quote and quote.get("ltp"):
            result["gold"] = {
                "symbol": sym,
                "ltp": quote.get("ltp"),
                "change_pct": quote.get("change_pct"),
            }
            break

    # Crude (MCX CRUDEOIL)
    crude_symbols = ["CRUDEOILM", "CRUDEOIL"]
    for sym in crude_symbols:
        quote = (ticker or {}).get(sym)
        if quote and quote.get("ltp"):
            result["crude"] = {
                "symbol": sym,
                "ltp": quote.get("ltp"),
                "change_pct": quote.get("change_pct"),
            }
            break

    return result


def compute_gap_context(
    nifty_quote: dict | None,
    prev_close: float | None = None,
) -> dict[str, Any] | None:
    """Compute gap analysis from previous close to current open/LTP."""
    if not nifty_quote:
        return None

    ltp = nifty_quote.get("ltp")
    pc = prev_close or nifty_quote.get("prev_close")
    open_price = nifty_quote.get("open")

    if not ltp or not pc:
        return None

    gap_pct = round(((float(open_price or ltp) - float(pc)) / float(pc)) * 100, 2)

    gap_type = "flat"
    if abs(gap_pct) < 0.2:
        gap_type = "flat"
    elif gap_pct > 0.5:
        gap_type = "gap_up"
    elif gap_pct < -0.5:
        gap_type = "gap_down"
    else:
        gap_type = "small_gap_up" if gap_pct > 0 else "small_gap_down"

    gap_filled = False
    if open_price and pc and ltp:
        if gap_pct > 0 and float(ltp) <= float(pc):
            gap_filled = True
        elif gap_pct < 0 and float(ltp) >= float(pc):
            gap_filled = True

    return {
        "prev_close": round(float(pc), 2),
        "open": round(float(open_price), 2) if open_price else None,
        "current": round(float(ltp), 2),
        "gap_pct": gap_pct,
        "gap_type": gap_type,
        "gap_filled": gap_filled,
    }


def fetch_global_context(
    ticker: dict[str, dict] | None = None,
    nifty_history: Any = None,
) -> dict[str, Any]:
    """Build full global/cross-market context."""
    global _global_cache, _global_cache_ts

    now = time.time()
    if _global_cache and (now - _global_cache_ts) < _GLOBAL_CACHE_TTL:
        return _global_cache

    nifty_quote = (ticker or {}).get("NIFTY")
    bn_quote = (ticker or {}).get("BANKNIFTY")

    result: dict[str, Any] = {
        "nifty_banknifty_rs": compute_nifty_banknifty_rs(nifty_quote, bn_quote),
        "gap_context": compute_gap_context(nifty_quote),
        "commodities": fetch_commodity_context(ticker),
    }

    _global_cache = result
    _global_cache_ts = now
    return result
