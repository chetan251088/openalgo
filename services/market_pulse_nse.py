"""
Fetch market breadth data from NSE India website.
Provides advance/decline ratios and 52-week highs/lows.
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
}

# Module-level session for cookie reuse
_session: requests.Session | None = None
_session_created_at: float = 0
_SESSION_MAX_AGE = 300  # refresh session every 5 min

# Circuit Breaker
_last_failure_time: float = 0
_NSE_COOLDOWN = 60  # seconds to wait before retrying if blocked

def _get_session() -> requests.Session | None:
    """Get or create an NSE session with cookies."""
    global _session, _session_created_at, _last_failure_time
    now = time.time()
    
    if now - _last_failure_time < _NSE_COOLDOWN:
        logger.debug("NSE fetch skipped: in cooldown")
        return None
        
    if _session is None or (now - _session_created_at) > _SESSION_MAX_AGE:
        _session = requests.Session()
        _session.headers.update(_NSE_HEADERS)
        # Hit homepage first to get cookies
        try:
            _session.get(_NSE_BASE, timeout=3) # Fail fast
        except Exception as e:
            logger.warning("NSE session init failed: %s", e)
            _last_failure_time = now
            return None
        _session_created_at = now
    return _session


def _nse_get(path: str) -> dict | None:
    """Make a GET request to NSE API endpoint."""
    global _last_failure_time, _session
    sess = _get_session()
    if sess is None:
        return None
        
    try:
        resp = sess.get(f"{_NSE_BASE}{path}", timeout=3) # Fail fast
        if resp.status_code == 200:
            return resp.json()
        logger.warning("NSE %s returned status %d", path, resp.status_code)
        # Only trigger cooldown for actual blocks/rate-limits/server errors, not 404 Not Found
        if resp.status_code in (401, 403, 429, 502, 503, 504):
            _last_failure_time = time.time()
            _session = None
    except Exception as e:
        logger.warning("NSE fetch failed for %s: %s", path, e)
        _last_failure_time = time.time()
        _session = None
    return None



def fetch_advance_decline() -> dict[str, Any]:
    """Fetch NSE advance/decline data.

    Returns: {"advances": int, "declines": int, "unchanged": int, "ad_ratio": float}
    """
    data = _nse_get("/api/market-data-pre-open?key=NIFTY%2050")
    if data is None:
        # Fallback: try market status endpoint
        data = _nse_get("/api/marketStatus")

    # Try the live market endpoint for A/D
    ad_data = _nse_get("/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O")
    if ad_data and "advance" in ad_data:
        advances = ad_data.get("advance", {}).get("advances", 0)
        declines = ad_data.get("advance", {}).get("declines", 0)
        unchanged = ad_data.get("advance", {}).get("unchanged", 0)
        ad_ratio = round(advances / max(declines, 1), 2)
        return {
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "ad_ratio": ad_ratio,
        }

    # Absolute fallback
    return {"advances": 0, "declines": 0, "unchanged": 0, "ad_ratio": 1.0, "error": "unavailable"}


def fetch_highs_lows() -> dict[str, Any]:
    """Fetch 52-week highs and lows from NSE.

    Returns: {"highs_52w": int, "lows_52w": int, "ratio": float}
    """
    data = _nse_get("/api/live-analysis-variations?index=gainers52w")
    highs = 0
    if data and "data" in data:
        highs = len(data["data"])

    data_lows = _nse_get("/api/live-analysis-variations?index=losers52w")
    lows = 0
    if data_lows and "data" in data_lows:
        lows = len(data_lows["data"])

    ratio = round(highs / max(lows, 1), 2)
    return {"highs_52w": highs, "lows_52w": lows, "ratio": ratio}


def fetch_market_breadth() -> dict[str, Any]:
    """Aggregate all NSE breadth data into a single dict."""
    ad = fetch_advance_decline()
    hl = fetch_highs_lows()
    return {
        "advance_decline": ad,
        "highs_lows": hl,
    }
