"""
Market Pulse — Institutional Flow Intelligence (Phase 2, Tier 1).

Fetches FII/DII daily cash-market flows, F&O participant-wise OI,
streak counters, flow strength, and 45-day heatmap from NSE APIs.

Data sources:
  - NSE /api/fiidiiTradeReact  (daily FII/DII cash flows)
  - NSE /api/reports           (F&O participant-wise OI)
"""

import logging
import os
import time
import threading
from collections import deque
from datetime import datetime, date
from typing import Any

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────
_inst_cache: dict[str, Any] = {}
_inst_cache_ts: dict[str, float] = {}
_CACHE_TTL = max(15, int(os.getenv("MARKET_PULSE_INST_CACHE_TTL", "1800")))  # 30 min

# 45-day ring buffer for heatmap (persists in-memory across requests)
_daily_flow_history: deque[dict[str, Any]] = deque(maxlen=45)
_history_initialized = False


def _is_market_hours() -> bool:
    """Check if we're in IST market hours (9:00 - 16:00)."""
    now = datetime.now()
    return 9 <= now.hour < 16


def _cache_ttl() -> int:
    """Shorter TTL during market hours for fresher data."""
    return _CACHE_TTL if not _is_market_hours() else min(_CACHE_TTL, 300)


def _check_cache(key: str) -> Any | None:
    """Return cached value if still fresh, else None."""
    if key in _inst_cache:
        age = time.time() - _inst_cache_ts.get(key, 0)
        if age < _cache_ttl():
            return _inst_cache[key]
    return None


def _set_cache(key: str, value: Any) -> None:
    _inst_cache[key] = value
    _inst_cache_ts[key] = time.time()


# ── NSE Session (reuse from market_pulse_nse) ────────────────────

def _nse_get(path: str) -> dict | None:
    """Fetch JSON from NSE API, reusing the shared session."""
    try:
        from services.market_pulse_nse import _nse_get as nse_get
        return nse_get(path)
    except ImportError:
        logger.warning("market_pulse_nse not available, using direct request")
    # Inline fallback
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/market-data/live-equity-market",
    }
    try:
        sess = requests.Session()
        sess.headers.update(headers)
        sess.get("https://www.nseindia.com", timeout=10)
        resp = sess.get(f"https://www.nseindia.com{path}", timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("NSE %s returned %d", path, resp.status_code)
    except Exception as e:
        logger.warning("NSE fetch failed for %s: %s", path, e)
    return None


# ── FII/DII Daily Cash Flows ─────────────────────────────────────

def fetch_fii_dii_daily() -> dict[str, Any]:
    """Fetch today's FII/DII net buy/sell from NSE TRDREQ.

    Returns: {
        date, fii_net, dii_net,
        fii_buy, fii_sell, dii_buy, dii_sell,
        fii_5d, dii_5d,
        fii_streak, dii_streak,
        flow_strength
    }
    """
    cached = _check_cache("fii_dii_daily")
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "date": date.today().isoformat(),
        "fii_net": 0, "dii_net": 0,
        "fii_buy": 0, "fii_sell": 0,
        "dii_buy": 0, "dii_sell": 0,
        "fii_5d": [], "dii_5d": [],
        "fii_streak": {"direction": "neutral", "days": 0, "cumulative": 0},
        "dii_streak": {"direction": "neutral", "days": 0, "cumulative": 0},
        "flow_strength": 0,
        "available": False,
    }

    try:
        data = _nse_get("/api/fiidiiTradeReact")
        if data is None:
            logger.warning("FII/DII TRDREQ endpoint returned None")
            _set_cache("fii_dii_daily", result)
            return result

        # NSE returns a list — each item has category, date, buyValue, sellValue
        fii_entry = None
        dii_entry = None

        items = data if isinstance(data, list) else data.get("data", data)
        if isinstance(items, list):
            for item in items:
                cat = str(item.get("category", "")).upper()
                if "FII" in cat or "FPI" in cat:
                    fii_entry = item
                elif "DII" in cat:
                    dii_entry = item
        elif isinstance(items, dict):
            # Some NSE responses nest differently
            fii_entry = items.get("fii") or items.get("FII")
            dii_entry = items.get("dii") or items.get("DII")

        if fii_entry:
            fii_buy = _parse_crore(fii_entry.get("buyValue", 0))
            fii_sell = _parse_crore(fii_entry.get("sellValue", 0))
            result["fii_buy"] = round(fii_buy, 2)
            result["fii_sell"] = round(fii_sell, 2)
            result["fii_net"] = round(fii_buy - fii_sell, 2)
            if fii_entry.get("date"):
                result["date"] = str(fii_entry["date"])

        if dii_entry:
            dii_buy = _parse_crore(dii_entry.get("buyValue", 0))
            dii_sell = _parse_crore(dii_entry.get("sellValue", 0))
            result["dii_buy"] = round(dii_buy, 2)
            result["dii_sell"] = round(dii_sell, 2)
            result["dii_net"] = round(dii_buy - dii_sell, 2)

        result["available"] = True

        # Update ring buffer for heatmap
        _update_flow_history(result["date"], result["fii_net"], result["dii_net"])

        # Compute streak from history
        result["fii_streak"] = _compute_streak("fii")
        result["dii_streak"] = _compute_streak("dii")

        # Compute flow strength
        result["flow_strength"] = _compute_flow_strength(result["fii_net"], result["dii_net"])

        # Build 5-day arrays from history
        history = list(_daily_flow_history)
        result["fii_5d"] = [h["fii_net"] for h in history[-5:]]
        result["dii_5d"] = [h["dii_net"] for h in history[-5:]]

    except Exception as e:
        logger.exception("Error fetching FII/DII daily: %s", e)

    _set_cache("fii_dii_daily", result)
    return result


def _parse_crore(value: Any) -> float:
    """Parse a value that might be string with commas or a number."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("(", "-").replace(")", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _update_flow_history(dt: str, fii_net: float, dii_net: float) -> None:
    """Add today's flow to the ring buffer, deduplicating by date."""
    global _daily_flow_history
    # Check if today already exists
    for item in _daily_flow_history:
        if item["date"] == dt:
            item["fii_net"] = fii_net
            item["dii_net"] = dii_net
            return
    _daily_flow_history.append({"date": dt, "fii_net": fii_net, "dii_net": dii_net})


def _compute_streak(entity: str) -> dict[str, Any]:
    """Compute consecutive buying/selling streak from flow history."""
    history = list(_daily_flow_history)
    if not history:
        return {"direction": "neutral", "days": 0, "cumulative": 0}

    key = f"{entity}_net"
    # Walk backwards
    direction = None
    days = 0
    cumulative = 0.0

    for item in reversed(history):
        net = item.get(key, 0)
        if net == 0:
            break
        current_dir = "buy" if net > 0 else "sell"
        if direction is None:
            direction = current_dir
        if current_dir != direction:
            break
        days += 1
        cumulative += net

    return {
        "direction": direction or "neutral",
        "days": days,
        "cumulative": round(cumulative, 2),
    }


def _compute_flow_strength(fii_net: float, dii_net: float) -> float:
    """FII_NET / (|FII_NET| + |DII_NET|) × 100. Range: -100 to +100."""
    total = abs(fii_net) + abs(dii_net)
    if total == 0:
        return 0.0
    return round((fii_net / total) * 100, 1)


# ── F&O Participant-wise OI ──────────────────────────────────────

def fetch_participant_oi() -> dict[str, Any]:
    """Fetch FII/DII participant-wise open interest from NSE F&O reports.

    Returns: {
        fii_index_futures: {long, short, ls_ratio},
        fii_stock_futures: {long, short, ls_ratio},
        dii_index_futures: {long, short, ls_ratio},
        sentiment, sentiment_score
    }
    """
    cached = _check_cache("participant_oi")
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "fii_index_futures": {"long": 0, "short": 0, "ls_ratio": 0},
        "fii_stock_futures": {"long": 0, "short": 0, "ls_ratio": 0},
        "dii_index_futures": {"long": 0, "short": 0, "ls_ratio": 0},
        "sentiment": "Unavailable",
        "sentiment_score": 50,
        "available": False,
    }

    try:
        # NSE participant-wise OI endpoint
        data = _nse_get("/api/reports?archives=%5B%7B%22name%22%3A%22F%26O%20-%20Pair%20wise%20Open%20Interest%22%7D%5D")

        if data is None:
            # Try alternative endpoint
            data = _nse_get("/api/reports?archives=%5B%7B%22name%22%3A%22FO%20Participant%20wise%20Open%20Interest%22%7D%5D")

        if data is None:
            # Try the direct participant OI endpoint
            data = _nse_get("/api/participant-wise-open-interest")

        if data is None:
            logger.warning("All participant OI endpoints returned None")
            _set_cache("participant_oi", result)
            return result

        # Parse the response — structure varies by endpoint
        parsed = _parse_participant_oi(data)
        if parsed:
            result.update(parsed)
            result["available"] = True

            # Compute sentiment from FII futures L/S ratio
            fii_ls = result["fii_index_futures"]["ls_ratio"]
            result["sentiment"], result["sentiment_score"] = _compute_fno_sentiment(fii_ls)

    except Exception as e:
        logger.exception("Error fetching participant OI: %s", e)

    _set_cache("participant_oi", result)
    return result


def _parse_participant_oi(data: Any) -> dict[str, Any] | None:
    """Parse NSE participant OI data into our unified format."""
    try:
        # The data can come as list or dict with nested structure
        rows = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("data", [])
            if not rows and "aaData" in data:
                rows = data["aaData"]

        if not rows:
            return None

        result = {}
        for row in rows:
            if isinstance(row, dict):
                client_type = str(row.get("clientType", row.get("Client_Type", ""))).upper().strip()
                # Map client types
                if "FII" in client_type or "FPI" in client_type:
                    prefix = "fii"
                elif "DII" in client_type:
                    prefix = "dii"
                else:
                    continue

                # Index Futures
                fut_long = _parse_crore(row.get("futIdxLong", row.get("Future_Index_Long", 0)))
                fut_short = _parse_crore(row.get("futIdxShort", row.get("Future_Index_Short", 0)))

                result[f"{prefix}_index_futures"] = {
                    "long": int(fut_long),
                    "short": int(fut_short),
                    "ls_ratio": round(fut_long / max(fut_short, 1), 2),
                }

                # Stock Futures
                stk_long = _parse_crore(row.get("futStkLong", row.get("Future_Stock_Long", 0)))
                stk_short = _parse_crore(row.get("futStkShort", row.get("Future_Stock_Short", 0)))

                result[f"{prefix}_stock_futures"] = {
                    "long": int(stk_long),
                    "short": int(stk_short),
                    "ls_ratio": round(stk_long / max(stk_short, 1), 2),
                }

            elif isinstance(row, list) and len(row) >= 10:
                # Array format: [client_type, fut_idx_long, fut_idx_short, ...]
                client_type = str(row[0]).upper().strip()
                if "FII" in client_type or "FPI" in client_type:
                    prefix = "fii"
                elif "DII" in client_type:
                    prefix = "dii"
                else:
                    continue

                fut_long = _parse_crore(row[1])
                fut_short = _parse_crore(row[2])
                result[f"{prefix}_index_futures"] = {
                    "long": int(fut_long),
                    "short": int(fut_short),
                    "ls_ratio": round(fut_long / max(fut_short, 1), 2),
                }

        return result if result else None

    except Exception as e:
        logger.warning("Failed to parse participant OI: %s", e)
        return None


def _compute_fno_sentiment(fii_ls_ratio: float) -> tuple[str, int]:
    """Compute sentiment label and score from FII futures Long/Short ratio.

    Ratio > 1.3  → Highly Bullish (80+)
    Ratio > 1.0  → Mildly Bullish (60-79)
    Ratio ~ 1.0  → Neutral (40-59)
    Ratio > 0.7  → Mildly Bearish (20-39)
    Ratio < 0.7  → Highly Bearish (0-19)
    """
    if fii_ls_ratio >= 1.3:
        return "Highly Bullish", min(95, int(50 + (fii_ls_ratio - 1.0) * 100))
    elif fii_ls_ratio >= 1.0:
        return "Mildly Bullish", int(60 + (fii_ls_ratio - 1.0) * 60)
    elif fii_ls_ratio >= 0.85:
        return "Neutral", int(40 + (fii_ls_ratio - 0.85) * 130)
    elif fii_ls_ratio >= 0.7:
        return "Mildly Bearish", int(20 + (fii_ls_ratio - 0.7) * 130)
    else:
        return "Highly Bearish", max(5, int(fii_ls_ratio * 28))


# ── Heatmap Data ─────────────────────────────────────────────────

def get_flow_heatmap() -> list[dict[str, Any]]:
    """Return 45-day FII/DII daily flow history for heatmap rendering."""
    return list(_daily_flow_history)


# ── Unified Endpoint ─────────────────────────────────────────────

def fetch_institutional_context() -> dict[str, Any]:
    """Single function that returns all institutional data for the API."""
    cached = _check_cache("institutional_full")
    if cached is not None:
        return cached

    fii_dii = fetch_fii_dii_daily()
    fno = fetch_participant_oi()
    heatmap = get_flow_heatmap()

    result = {
        "fii_dii": fii_dii,
        "fno_participant": fno,
        "heatmap_45d": heatmap,
        "timestamp": datetime.now().isoformat(),
    }

    _set_cache("institutional_full", result)
    return result
