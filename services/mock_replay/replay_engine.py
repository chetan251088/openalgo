"""
Replay engine: load 1m OHLC from Historify and yield synthetic LTP ticks.

Supports regime transforms (choppy, high_vol, low_vol, momentum, vix-style)
for after-hours testing under different market conditions.
"""

from __future__ import annotations

import time
from typing import Any, Generator, Optional

import pandas as pd
import numpy as np


# Ticks per 1m candle (interpolate open -> close). More = smoother replay.
TICKS_PER_CANDLE = 6

# Replay speed: 1.0 = real time (1 min data in 1 min), 5.0 = 5x, 0 = as fast as possible
DEFAULT_SPEED = 1.0

# Regime identifiers (user chooses on UI)
REGIME_NONE = "none"
REGIME_CHOPPY = "choppy"
REGIME_HIGH_VOL = "high_vol"
REGIME_LOW_VOL = "low_vol"
REGIME_HIGH_MOMENTUM = "high_momentum"
REGIME_LOW_MOMENTUM = "low_momentum"
REGIME_HIGH_VIX = "high_vix"
REGIME_LOW_VIX = "low_vix"

REGIMES = [
    REGIME_NONE,
    REGIME_CHOPPY,
    REGIME_HIGH_VOL,
    REGIME_LOW_VOL,
    REGIME_HIGH_MOMENTUM,
    REGIME_LOW_MOMENTUM,
    REGIME_HIGH_VIX,
    REGIME_LOW_VIX,
]


def _apply_regime(df: pd.DataFrame, regime: str) -> pd.DataFrame:
    """Transform 1m OHLC by regime. Returns new DataFrame with open, high, low, close, timestamp."""
    if not regime or regime == REGIME_NONE or df.empty:
        return df.copy()
    df = df.copy()
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l_ = df["low"].astype(float)
    c = df["close"].astype(float)
    if regime == REGIME_CHOPPY:
        # Mean-revert: blend close toward previous close, add small noise
        prev = c.shift(1).fillna(o)
        blend = 0.6 * c + 0.4 * prev
        noise = np.random.uniform(-0.5, 0.5, len(df))
        c = blend + noise
        df["close"] = np.clip(c, l_.values, h.values)
        df["high"] = np.maximum(h, df["close"])
        df["low"] = np.minimum(l_, df["close"])
    elif regime == REGIME_HIGH_VOL:
        # Scale range (high-low) by 1.5
        mid = (h + l_) / 2
        rng = (h - l_) * 1.5
        df["high"] = mid + rng / 2
        df["low"] = mid - rng / 2
        df["open"] = o
        df["close"] = np.clip(c, df["low"].values, df["high"].values)
    elif regime == REGIME_LOW_VOL:
        # Shrink range by 0.5
        mid = (h + l_) / 2
        rng = (h - l_) * 0.5
        df["high"] = mid + rng / 2
        df["low"] = mid - rng / 2
        df["open"] = o
        df["close"] = np.clip(c, df["low"].values, df["high"].values)
    elif regime == REGIME_HIGH_MOMENTUM:
        # Emphasize trend: scale (close - open) by 1.4
        move = (c - o) * 1.4
        df["close"] = o + move
        df["high"] = np.maximum(h, np.maximum(o, df["close"]))
        df["low"] = np.minimum(l_, np.minimum(o, df["close"]))
    elif regime == REGIME_LOW_MOMENTUM:
        # Smooth: close closer to open
        df["close"] = o + (c - o) * 0.4
        df["high"] = (h + df["close"]) / 2
        df["low"] = (l_ + df["close"]) / 2
    elif regime == REGIME_HIGH_VIX:
        # Like high vol + slightly more range
        mid = (h + l_) / 2
        rng = (h - l_) * 1.6
        df["high"] = mid + rng / 2
        df["low"] = mid - rng / 2
        df["close"] = np.clip(c, df["low"].values, df["high"].values)
    elif regime == REGIME_LOW_VIX:
        # Like low vol
        mid = (h + l_) / 2
        rng = (h - l_) * 0.5
        df["high"] = mid + rng / 2
        df["low"] = mid - rng / 2
        df["close"] = np.clip(c, df["low"].values, df["high"].values)
    return df


def _interpolate_ticks(
    ts_sec: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    n: int,
) -> list[tuple[int, float]]:
    """Yield (timestamp_ms, ltp) for n ticks within one 1m bar. Simple linear open->close."""
    if n <= 0:
        n = 1
    out: list[tuple[int, float]] = []
    for i in range(n):
        t = (i + 1) / (n + 1)  # 0..1 within bar
        # Linear from open to close (optionally could do open->high->low->close)
        ltp = open_ + (close - open_) * t
        # Clamp to high/low of the bar
        ltp = max(low, min(high, ltp))
        # Timestamp: spread within the minute (epoch sec * 1000 + fraction in ms)
        ts_ms = ts_sec * 1000 + int((i + 1) * (60_000 / (n + 1)))
        out.append((ts_ms, round(ltp, 2)))
    return out


def replay_ticks(
    symbol: str,
    exchange: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    speed: float = DEFAULT_SPEED,
    ticks_per_candle: int = TICKS_PER_CANDLE,
    regime: str = REGIME_NONE,
) -> Generator[dict[str, Any], None, None]:
    """
    Load 1m OHLC from Historify and yield market_data-style tick dicts.

    regime: one of REGIMES (none, choppy, high_vol, low_vol, high_momentum, low_momentum, high_vix, low_vix).
    Yields dicts with: type, symbol, exchange, data.ltp, data.timestamp (ms).
    """
    from database.historify_db import get_ohlcv

    db_sym, db_exch = _normalize_symbol(symbol, exchange)
    df = get_ohlcv(
        symbol=db_sym,
        exchange=db_exch,
        interval="1m",
        start_timestamp=start_ts,
        end_timestamp=end_ts,
    )
    if df is None or df.empty:
        return

    # Ensure we have numeric columns
    for col in ("open", "high", "low", "close", "timestamp"):
        if col not in df.columns:
            return
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = _apply_regime(df, regime or REGIME_NONE)

    first_ts_sec = int(df["timestamp"].iloc[0]) if len(df) else 0
    replay_start_wall = time.time()
    for _, row in df.iterrows():
        ts_sec = int(row["timestamp"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        ticks = _interpolate_ticks(ts_sec, open_, high, low, close, ticks_per_candle)
        for ts_ms, ltp in ticks:
            if speed > 0:
                # Replay pacing: replay time (ts_ms) should occur at wall time replay_start_wall + (ts - first)/speed
                replay_elapsed_sec = (ts_ms / 1000.0 - first_ts_sec) / speed
                sleep_until = replay_start_wall + replay_elapsed_sec
                now = time.time()
                if sleep_until > now:
                    time.sleep(sleep_until - now)

            yield {
                "type": "market_data",
                "symbol": symbol,
                "exchange": exchange,
                "mode": 1,
                "data": {"ltp": ltp, "timestamp": ts_ms},
                "broker": "historify_replay",
            }


def _normalize_symbol(symbol: str, exchange: str) -> tuple[str, str]:
    """Map common names to Historify catalog style."""
    s = (symbol or "").strip().upper()
    e = (exchange or "").strip().upper()
    if s in ("NIFTY", "NIFTY 50", "NIFTY50", "NIFTY-INDEX"):
        return ("NIFTY", e or "NSE_INDEX")
    if s in ("SENSEX", "BSE SENSEX"):
        return ("SENSEX", e or "BSE_INDEX")
    if s in ("BANKNIFTY", "BANK NIFTY"):
        return ("BANK NIFTY", e or "NSE_INDEX")
    return (s or symbol, e or "NSE_INDEX")


def get_replay_range(symbol: str, exchange: str) -> Optional[dict[str, Any]]:
    """Return first_timestamp, last_timestamp, record_count for 1m data, or None."""
    from database.historify_db import get_data_range

    db_sym, db_exch = _normalize_symbol(symbol, exchange)
    return get_data_range(db_sym, db_exch, "1m")
