"""
Market Pulse — Intraday Trading Context Service.

Computes scalper/day-trader context from intraday OHLCV bars:
  - Opening Range (first 15 min)
  - Initial Balance (first 60 min)
  - VWAP with ±1σ, ±2σ bands
  - Average Daily Range (ADR) + % consumed today
  - Session Phase classification
  - Developing High/Low with touch counts
"""

import logging
import math
import os
import time
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Cache ───────────────────────────────────────────────────────
_intraday_ctx_cache: dict[str, dict[str, Any]] = {}
_intraday_ctx_ts: dict[str, float] = {}
_INTRADAY_CTX_TTL = max(5, int(os.getenv("MARKET_PULSE_INTRADAY_CTX_TTL", "15")))

# ── Session Phase definitions (IST) ────────────────────────────
SESSION_PHASES = [
    {"name": "pre_open",        "label": "Pre-Open",        "start": "09:00", "end": "09:15"},
    {"name": "opening_drive",   "label": "Opening Drive",   "start": "09:15", "end": "09:45"},
    {"name": "morning_range",   "label": "Morning Range",   "start": "09:45", "end": "11:00"},
    {"name": "lunch_chop",      "label": "Lunch Chop",      "start": "11:00", "end": "13:30"},
    {"name": "afternoon_trend", "label": "Afternoon Trend", "start": "13:30", "end": "14:45"},
    {"name": "closing_session", "label": "Close",           "start": "14:45", "end": "15:30"},
]


def _time_to_minutes(t: str) -> int:
    """Convert HH:MM to minutes from midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def get_session_phase(now: datetime | None = None) -> dict[str, Any]:
    """Classify current time into a session phase."""
    if now is None:
        now = datetime.now()
    current_minutes = now.hour * 60 + now.minute

    for phase in SESSION_PHASES:
        start = _time_to_minutes(phase["start"])
        end = _time_to_minutes(phase["end"])
        if start <= current_minutes < end:
            elapsed = current_minutes - start
            total = end - start
            return {
                "phase": phase["name"],
                "label": phase["label"],
                "start": phase["start"],
                "end": phase["end"],
                "progress_pct": round((elapsed / total) * 100, 1) if total > 0 else 0,
                "minutes_remaining": total - elapsed,
            }

    # Outside market hours
    market_open = _time_to_minutes("09:15")
    market_close = _time_to_minutes("15:30")
    if current_minutes < market_open:
        return {
            "phase": "pre_market",
            "label": "Pre-Market",
            "start": None,
            "end": "09:15",
            "progress_pct": 0,
            "minutes_remaining": market_open - current_minutes,
        }
    return {
        "phase": "post_market",
        "label": "Post-Market",
        "start": "15:30",
        "end": None,
        "progress_pct": 100,
        "minutes_remaining": 0,
    }


def compute_opening_range(
    intraday_bars: pd.DataFrame,
    or_minutes: int = 15,
) -> dict[str, Any] | None:
    """Compute Opening Range from first N minutes of intraday bars.

    Args:
        intraday_bars: DataFrame with columns [timestamp, open, high, low, close]
                       where timestamp is in IST.
        or_minutes: Duration of OR in minutes (default 15).
    """
    if intraday_bars is None or intraday_bars.empty:
        return None

    df = intraday_bars.copy()
    if "timestamp" not in df.columns:
        return None

    # Parse timestamps
    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["_dt"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    else:
        df["_dt"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["_dt"]).sort_values("_dt")

    if df.empty:
        return None

    # Get today's bars only
    today = df["_dt"].iloc[-1].date()
    today_bars = df[df["_dt"].dt.date == today]
    if today_bars.empty:
        return None

    # Market opens at 09:15 IST
    session_start = pd.Timestamp(f"{today} 09:15:00")
    or_end = session_start + pd.Timedelta(minutes=or_minutes)

    or_bars = today_bars[(today_bars["_dt"] >= session_start) & (today_bars["_dt"] < or_end)]
    if or_bars.empty:
        return None

    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    or_range = or_high - or_low

    # Current price relative to OR
    current = float(today_bars["close"].iloc[-1])
    state = "inside"
    if current > or_high:
        state = "above"
    elif current < or_low:
        state = "below"

    return {
        "or_high": round(or_high, 2),
        "or_low": round(or_low, 2),
        "or_range": round(or_range, 2),
        "or_range_pct": round((or_range / or_low) * 100, 3) if or_low > 0 else 0,
        "current_vs_or": state,
        "minutes": or_minutes,
        "complete": len(or_bars) >= (or_minutes // 5),  # True if OR period is fully formed
    }


def compute_initial_balance(
    intraday_bars: pd.DataFrame,
    ib_minutes: int = 60,
) -> dict[str, Any] | None:
    """Compute Initial Balance (first 60 minutes)."""
    if intraday_bars is None or intraday_bars.empty:
        return None

    df = intraday_bars.copy()
    if "timestamp" not in df.columns:
        return None

    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["_dt"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    else:
        df["_dt"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["_dt"]).sort_values("_dt")

    today = df["_dt"].iloc[-1].date()
    today_bars = df[df["_dt"].dt.date == today]
    if today_bars.empty:
        return None

    session_start = pd.Timestamp(f"{today} 09:15:00")
    ib_end = session_start + pd.Timedelta(minutes=ib_minutes)

    ib_bars = today_bars[(today_bars["_dt"] >= session_start) & (today_bars["_dt"] < ib_end)]
    if ib_bars.empty:
        return None

    ib_high = float(ib_bars["high"].max())
    ib_low = float(ib_bars["low"].min())
    ib_range = ib_high - ib_low

    current = float(today_bars["close"].iloc[-1])
    state = "inside"
    if current > ib_high:
        state = "above"
    elif current < ib_low:
        state = "below"

    return {
        "ib_high": round(ib_high, 2),
        "ib_low": round(ib_low, 2),
        "ib_range": round(ib_range, 2),
        "ib_range_pct": round((ib_range / ib_low) * 100, 3) if ib_low > 0 else 0,
        "current_vs_ib": state,
        "minutes": ib_minutes,
        "complete": len(ib_bars) >= (ib_minutes // 5),
    }


def compute_vwap_bands(intraday_bars: pd.DataFrame) -> dict[str, Any] | None:
    """Compute session VWAP with ±1σ, ±2σ bands from intraday bars."""
    if intraday_bars is None or intraday_bars.empty:
        return None

    df = intraday_bars.copy()
    if "timestamp" not in df.columns:
        return None

    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["_dt"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    else:
        df["_dt"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["_dt"]).sort_values("_dt")

    today = df["_dt"].iloc[-1].date()
    today_bars = df[df["_dt"].dt.date == today].copy()
    if today_bars.empty or "volume" not in today_bars.columns:
        return None

    # Typical price
    today_bars["typical"] = (
        today_bars["high"].astype(float)
        + today_bars["low"].astype(float)
        + today_bars["close"].astype(float)
    ) / 3

    volumes = today_bars["volume"].astype(float).values
    typicals = today_bars["typical"].values
    total_volume = volumes.sum()

    if total_volume <= 0:
        return None

    vwap = float(np.sum(typicals * volumes) / total_volume)

    # Standard deviation bands
    squared_diff = (typicals - vwap) ** 2
    variance = float(np.sum(squared_diff * volumes) / total_volume)
    std_dev = math.sqrt(variance) if variance > 0 else 0

    current = float(today_bars["close"].iloc[-1])
    distance_pct = round(((current - vwap) / vwap) * 100, 3) if vwap > 0 else 0

    return {
        "vwap": round(vwap, 2),
        "upper_1": round(vwap + std_dev, 2),
        "lower_1": round(vwap - std_dev, 2),
        "upper_2": round(vwap + 2 * std_dev, 2),
        "lower_2": round(vwap - 2 * std_dev, 2),
        "std_dev": round(std_dev, 2),
        "current": round(current, 2),
        "distance_pct": distance_pct,
        "zone": _classify_vwap_zone(current, vwap, std_dev),
    }


def _classify_vwap_zone(current: float, vwap: float, std: float) -> str:
    """Classify where price sits relative to VWAP bands."""
    if std <= 0:
        return "at_vwap"
    diff = current - vwap
    if abs(diff) < std * 0.25:
        return "at_vwap"
    if diff > 2 * std:
        return "extended_above"
    if diff > std:
        return "above_1sd"
    if diff > 0:
        return "above_vwap"
    if diff < -2 * std:
        return "extended_below"
    if diff < -std:
        return "below_1sd"
    return "below_vwap"


def compute_adr(
    daily_history: pd.DataFrame,
    lookback: int = 20,
    current_high: float | None = None,
    current_low: float | None = None,
) -> dict[str, Any] | None:
    """Compute Average Daily Range and today's consumed percentage.

    Args:
        daily_history: DataFrame with columns [high, low, close]
        lookback: Number of days for ADR calculation
        current_high: Today's intraday high (from live data)
        current_low: Today's intraday low (from live data)
    """
    if daily_history is None or daily_history.empty:
        return None

    if "high" not in daily_history.columns or "low" not in daily_history.columns:
        return None

    df = daily_history.copy()
    df["_range"] = df["high"].astype(float) - df["low"].astype(float)

    # Use last N *completed* days (exclude today if partial)
    completed_ranges = df["_range"].iloc[-lookback - 1:-1] if len(df) > lookback else df["_range"].iloc[:-1]

    if completed_ranges.empty:
        return None

    adr = float(completed_ranges.mean())
    adr_median = float(completed_ranges.median())

    # Today's consumed range
    consumed_pct = None
    today_range = None
    if current_high is not None and current_low is not None:
        today_range = current_high - current_low
        consumed_pct = round((today_range / adr) * 100, 1) if adr > 0 else 0

    # Context
    exhaustion = False
    if consumed_pct is not None and consumed_pct >= 80:
        exhaustion = True

    return {
        "adr": round(adr, 2),
        "adr_median": round(adr_median, 2),
        "adr_pct": round((adr / float(df["close"].iloc[-1])) * 100, 3)
        if len(df) > 0 and float(df["close"].iloc[-1]) > 0
        else None,
        "lookback_days": lookback,
        "today_range": round(today_range, 2) if today_range is not None else None,
        "consumed_pct": consumed_pct,
        "exhaustion_warning": exhaustion,
    }


def compute_developing_high_low(intraday_bars: pd.DataFrame) -> dict[str, Any] | None:
    """Track today's running high/low with touch counts."""
    if intraday_bars is None or intraday_bars.empty:
        return None

    df = intraday_bars.copy()
    if "timestamp" not in df.columns:
        return None

    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["_dt"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    else:
        df["_dt"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["_dt"]).sort_values("_dt")

    today = df["_dt"].iloc[-1].date()
    today_bars = df[df["_dt"].dt.date == today].copy()
    if today_bars.empty:
        return None

    highs = today_bars["high"].astype(float).values
    lows = today_bars["low"].astype(float).values

    dev_high = float(highs.max())
    dev_low = float(lows.min())

    # Count touches (bars where high is within 0.05% of developing high)
    high_tolerance = dev_high * 0.0005
    low_tolerance = dev_low * 0.0005
    high_touches = int(np.sum(np.abs(highs - dev_high) <= high_tolerance))
    low_touches = int(np.sum(np.abs(lows - dev_low) <= low_tolerance))

    current = float(today_bars["close"].iloc[-1])
    range_position = 50.0
    total_range = dev_high - dev_low
    if total_range > 0:
        range_position = round(((current - dev_low) / total_range) * 100, 1)

    return {
        "dev_high": round(dev_high, 2),
        "dev_low": round(dev_low, 2),
        "dev_range": round(total_range, 2),
        "high_touches": high_touches,
        "low_touches": low_touches,
        "current": round(current, 2),
        "range_position_pct": range_position,
    }


def compute_intraday_context(
    symbol: str,
    intraday_bars: pd.DataFrame | None,
    daily_history: pd.DataFrame | None,
    current_high: float | None = None,
    current_low: float | None = None,
) -> dict[str, Any]:
    """Compute full intraday trading context for a symbol.

    Returns a dict with: session_phase, opening_range, initial_balance,
    vwap_bands, adr, developing_high_low.
    """
    # Check cache
    cache_key = symbol
    now = time.time()
    if (
        cache_key in _intraday_ctx_cache
        and (now - _intraday_ctx_ts.get(cache_key, 0)) < _INTRADAY_CTX_TTL
    ):
        return _intraday_ctx_cache[cache_key]

    result: dict[str, Any] = {
        "symbol": symbol,
        "session_phase": get_session_phase(),
        "opening_range": compute_opening_range(intraday_bars),
        "initial_balance": compute_initial_balance(intraday_bars),
        "vwap_bands": compute_vwap_bands(intraday_bars),
        "adr": compute_adr(daily_history, current_high=current_high, current_low=current_low),
        "developing_high_low": compute_developing_high_low(intraday_bars),
        "computed_at": datetime.now().isoformat(),
    }

    _intraday_ctx_cache[cache_key] = result
    _intraday_ctx_ts[cache_key] = now
    return result
