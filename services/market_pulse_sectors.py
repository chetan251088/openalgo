"""
Market Pulse — Sector Performance & Rotation (Phase 2, Tier 2).

Fetches real-time sector index performance from Zerodha quotes,
computes relative strength vs Nifty, and generates heatmap data.

Uses SECTOR_INDICES from market_pulse_config.py.
"""

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────
_sector_cache: dict[str, Any] = {}
_sector_cache_ts: dict[str, float] = {}
_CACHE_TTL = max(30, int(os.getenv("MARKET_PULSE_SECTOR_CACHE_TTL", "1800")))  # 30 min


def _check_cache(key: str) -> Any | None:
    if key in _sector_cache:
        if (time.time() - _sector_cache_ts.get(key, 0)) < _CACHE_TTL:
            return _sector_cache[key]
    return None


def _set_cache(key: str, value: Any) -> None:
    _sector_cache[key] = value
    _sector_cache_ts[key] = time.time()


def _get_api_key() -> str | None:
    return os.getenv("APP_KEY") or os.getenv("OPENALGO_API_KEY")


# ── Sector Performance ───────────────────────────────────────────

def fetch_sector_performance() -> dict[str, Any]:
    """Fetch real-time performance for all sector indices.

    Returns: {
        sectors: [{name, today_pct, 5d_pct, rs_vs_nifty, ltp, prev_close, flow_hint}, ...],
        nifty_today_pct,
        timestamp
    }
    """
    cached = _check_cache("sector_perf")
    if cached is not None:
        return cached

    from services.market_pulse_config import SECTOR_INDICES
    from datetime import datetime

    result: dict[str, Any] = {
        "sectors": [],
        "nifty_today_pct": 0,
        "timestamp": datetime.now().isoformat(),
        "available": False,
    }

    try:
        api_key = _get_api_key()
        if not api_key:
            return result

        from services.quotes_service import get_multiquotes

        # Build symbols list: all sectors + NIFTY for relative strength
        symbols_to_fetch = [
            {"symbol": info["symbol"], "exchange": info["exchange"]}
            for info in SECTOR_INDICES.values()
        ]
        # Add NIFTY for baseline
        symbols_to_fetch.append({"symbol": "NIFTY", "exchange": "NSE_INDEX"})

        success, resp, _ = get_multiquotes(symbols_to_fetch, api_key=api_key)
        if not success:
            logger.warning("Sector multiquotes failed")
            return result

        # Build quote lookup
        quotes_map: dict[str, dict] = {}
        for item in resp.get("results", []):
            sym = item.get("symbol")
            if sym:
                quotes_map[sym] = item.get("data", item)

        # Get Nifty baseline
        nifty_quote = quotes_map.get("NIFTY", {})
        nifty_ltp = nifty_quote.get("ltp", 0)
        nifty_prev = nifty_quote.get("prev_close", 0)
        nifty_today_pct = 0
        if nifty_prev and nifty_prev > 0:
            nifty_today_pct = round(((nifty_ltp / nifty_prev) - 1) * 100, 2)

        result["nifty_today_pct"] = nifty_today_pct

        # Compute each sector
        sectors = []
        for name, info in SECTOR_INDICES.items():
            sym = info["symbol"]
            quote = quotes_map.get(sym, {})
            ltp = quote.get("ltp", 0)
            prev_close = quote.get("prev_close", 0)

            today_pct = 0
            if prev_close and prev_close > 0:
                today_pct = round(((ltp / prev_close) - 1) * 100, 2)

            # Relative strength vs Nifty (today only for real-time)
            rs_vs_nifty = round(today_pct - nifty_today_pct, 2)

            # Flow hint based on RS
            if rs_vs_nifty > 1.0:
                flow_hint = "Outperforming"
            elif rs_vs_nifty > 0:
                flow_hint = "In-line"
            elif rs_vs_nifty > -1.0:
                flow_hint = "Slightly Weak"
            else:
                flow_hint = "Underperforming"

            sectors.append({
                "name": name,
                "symbol": sym,
                "ltp": round(ltp, 2),
                "prev_close": round(prev_close, 2),
                "today_pct": today_pct,
                "rs_vs_nifty": rs_vs_nifty,
                "flow_hint": flow_hint,
            })

        # Sort by today's performance descending
        sectors.sort(key=lambda x: x["today_pct"], reverse=True)

        result["sectors"] = sectors
        result["available"] = True

    except Exception as e:
        logger.exception("Error fetching sector performance: %s", e)

    _set_cache("sector_perf", result)
    return result


# ── Sector Heatmap Data ──────────────────────────────────────────

def get_sector_heatmap() -> list[dict[str, Any]]:
    """Return sector data formatted for heatmap rendering.

    Each entry: {name, value (today_pct), color_intensity (-1 to +1)}
    """
    perf = fetch_sector_performance()
    sectors = perf.get("sectors", [])

    if not sectors:
        return []

    # Normalize to -1..+1 range for color intensity
    max_abs = max(abs(s["today_pct"]) for s in sectors) if sectors else 1
    max_abs = max(max_abs, 0.01)  # Avoid division by zero

    heatmap = []
    for s in sectors:
        intensity = s["today_pct"] / max_abs  # -1 to +1
        heatmap.append({
            "name": s["name"],
            "symbol": s["symbol"],
            "value": s["today_pct"],
            "rs": s["rs_vs_nifty"],
            "intensity": round(intensity, 3),
            "flow_hint": s["flow_hint"],
        })

    return heatmap


# ── Rotation Signals ──────────────────────────────────────────────

def get_rotation_signals() -> dict[str, Any]:
    """Identify sector rotation patterns.

    Returns: {
        leaders: top 3 outperforming sectors,
        laggards: bottom 3 underperforming sectors,
        rotation_signal: descriptive string
    }
    """
    perf = fetch_sector_performance()
    sectors = perf.get("sectors", [])

    if len(sectors) < 4:
        return {"leaders": [], "laggards": [], "rotation_signal": "Insufficient data"}

    leaders = sectors[:3]
    laggards = sectors[-3:]

    # Generate rotation signal
    leader_names = [s["name"] for s in leaders]
    laggard_names = [s["name"] for s in laggards]

    # Detect defensive vs risk-on rotation
    defensive = {"FMCG", "PHARMA", "IT", "CONSDUR"}
    cyclical = {"BANK", "METAL", "AUTO", "ENERGY", "REALTY", "PSUBANK"}

    leader_set = set(leader_names)
    laggard_set = set(laggard_names)

    if leader_set & cyclical and laggard_set & defensive:
        signal = "Risk-On: Cyclicals leading, Defensives lagging"
    elif leader_set & defensive and laggard_set & cyclical:
        signal = "Risk-Off: Defensives leading, Cyclicals lagging"
    elif all(s["today_pct"] > 0 for s in sectors):
        signal = "Broad Rally: All sectors positive"
    elif all(s["today_pct"] < 0 for s in sectors):
        signal = "Broad Sell-off: All sectors negative"
    else:
        signal = "Mixed: Selective participation"

    return {
        "leaders": [{"name": s["name"], "pct": s["today_pct"], "rs": s["rs_vs_nifty"]} for s in leaders],
        "laggards": [{"name": s["name"], "pct": s["today_pct"], "rs": s["rs_vs_nifty"]} for s in laggards],
        "rotation_signal": signal,
    }


# ── Unified Endpoint ─────────────────────────────────────────────

def fetch_sector_context() -> dict[str, Any]:
    """Single function that returns all sector data for the API."""
    cached = _check_cache("sector_full")
    if cached is not None:
        return cached

    from datetime import datetime

    perf = fetch_sector_performance()
    heatmap = get_sector_heatmap()
    rotation = get_rotation_signals()

    result = {
        "performance": perf,
        "heatmap": heatmap,
        "rotation": rotation,
        "timestamp": datetime.now().isoformat(),
    }

    _set_cache("sector_full", result)
    return result
