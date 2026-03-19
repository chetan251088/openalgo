"""
Execution Window Score — tracks breakout quality over multiple sessions.
Uses DuckDB for persistent state.
"""

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from services.market_pulse_config import EXECUTION_DAY, EXECUTION_SWING

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "market_pulse.duckdb"
)


def _get_connection():
    """Get DuckDB connection."""
    import duckdb
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = duckdb.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS breakout_events (
            symbol VARCHAR,
            breakout_date DATE,
            breakout_price DOUBLE,
            day1_close DOUBLE,
            day2_close DOUBLE,
            day3_close DOUBLE,
            held BOOLEAN DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def track_breakouts(constituent_data: dict[str, dict]) -> None:
    """Detect and track new breakouts from constituent OHLCV data."""
    today = date.today()
    conn = _get_connection()

    try:
        for symbol, data in constituent_data.items():
            hist = data.get("history")
            if hist is None or len(hist) < 25:
                continue

            closes = hist["close"]
            highs_20d = closes.iloc[-(EXECUTION_SWING["breakout_period"] + 1):-1].max()
            current = closes.iloc[-1]

            # New breakout: current close > 20d high
            if current > highs_20d:
                existing = conn.execute(
                    "SELECT 1 FROM breakout_events WHERE symbol = ? AND breakout_date = ?",
                    [symbol, today.isoformat()]
                ).fetchone()

                if not existing:
                    conn.execute(
                        "INSERT INTO breakout_events (symbol, breakout_date, breakout_price) VALUES (?, ?, ?)",
                        [symbol, today.isoformat(), float(current)]
                    )

            # Update follow-through for recent breakouts
            recent = conn.execute(
                "SELECT symbol, breakout_date, breakout_price FROM breakout_events "
                "WHERE breakout_date >= ? AND held IS NULL",
                [(today - timedelta(days=5)).isoformat()]
            ).fetchall()

            for sym, bdate, bprice in recent:
                if sym != symbol:
                    continue
                days_since = (today - bdate).days if isinstance(bdate, date) else 0
                if days_since >= 1 and len(closes) > 1:
                    conn.execute(
                        f"UPDATE breakout_events SET day{min(days_since, 3)}_close = ? WHERE symbol = ? AND breakout_date = ?",
                        [float(current), sym, bdate]
                    )
                if days_since >= 3:
                    held = current >= bprice * 0.98  # within 2% of breakout price
                    conn.execute(
                        "UPDATE breakout_events SET held = ? WHERE symbol = ? AND breakout_date = ?",
                        [held, sym, bdate]
                    )

        conn.commit()
    finally:
        conn.close()


def compute_execution_window_swing() -> tuple[int, dict[str, Any]]:
    """Compute swing mode execution window score from DuckDB state."""
    conn = _get_connection()
    try:
        lookback = date.today() - timedelta(days=EXECUTION_SWING["lookback_days"])
        rows = conn.execute(
            "SELECT * FROM breakout_events WHERE breakout_date >= ?",
            [lookback.isoformat()]
        ).fetchdf()

        if rows.empty:
            return 50, {"note": "No breakout events tracked yet", "breakouts": 0}

        total = len(rows)
        held = rows["held"].sum() if "held" in rows.columns else 0
        failed = total - held if held else 0

        # Breakout hold rate
        hold_rate = (held / max(total, 1)) * 100

        # Follow-through (avg gain day1-3)
        gains = []
        for _, row in rows.iterrows():
            bp = row.get("breakout_price", 0)
            if bp > 0:
                for col in ["day1_close", "day2_close", "day3_close"]:
                    val = row.get(col)
                    if val and val > 0:
                        gains.append((val - bp) / bp * 100)
        avg_followthrough = sum(gains) / max(len(gains), 1) if gains else 0

        # Failure rate
        failure_rate = (failed / max(total, 1)) * 100

        # Score components
        E = EXECUTION_SWING
        hold_score = min(100, hold_rate * 1.2)
        ft_score = min(100, max(0, 50 + avg_followthrough * 20))
        fail_score = max(0, 100 - failure_rate * 1.5)
        pullback_score = 60  # placeholder — would need intraday data

        score = int(
            hold_score * E["breakout_hold_weight"]
            + ft_score * E["follow_through_weight"]
            + fail_score * E["failure_rate_weight"]
            + pullback_score * E["pullback_buying_weight"]
        )

        details = {
            "breakouts": total,
            "held": int(held),
            "hold_rate": round(hold_rate, 1),
            "avg_followthrough_pct": round(avg_followthrough, 2),
            "failure_rate": round(failure_rate, 1),
        }

        return max(0, min(100, score)), details
    finally:
        conn.close()


def compute_execution_window_day(market_data: dict) -> tuple[int, dict[str, Any]]:
    """Compute day trading mode execution window additions."""
    details = {}
    scores = []
    E = EXECUTION_DAY

    # Trend consistency: closing in upper/lower 25% of daily range
    nifty_hist = market_data.get("nifty_history")
    if nifty_hist is not None and len(nifty_hist) >= 5:
        recent = nifty_hist.tail(5)
        conviction_days = 0
        for _, row in recent.iterrows():
            rng = row["high"] - row["low"]
            if rng > 0:
                pos = (row["close"] - row["low"]) / rng
                if pos >= 0.75 or pos <= 0.25:
                    conviction_days += 1
        trend_consistency = (conviction_days / 5) * 100
        scores.append(("trend_consistency", trend_consistency, E["trend_consistency_weight"]))
        details["trend_consistency"] = round(trend_consistency, 1)

    # Gap fill rate
    if nifty_hist is not None and len(nifty_hist) >= 6:
        recent = nifty_hist.tail(E["gap_lookback_days"] + 1)
        gaps_held = 0
        total_gaps = 0
        for i in range(1, len(recent)):
            prev_close = recent.iloc[i - 1]["close"]
            curr_open = recent.iloc[i]["open"]
            curr_close = recent.iloc[i]["close"]
            gap = (curr_open - prev_close) / prev_close * 100
            if abs(gap) > 0.2:  # significant gap
                total_gaps += 1
                if gap > 0 and curr_close >= curr_open:  # gap up held
                    gaps_held += 1
                elif gap < 0 and curr_close <= curr_open:  # gap down held
                    gaps_held += 1
        gap_rate = (gaps_held / max(total_gaps, 1)) * 100 if total_gaps > 0 else 50
        scores.append(("gap_fill", gap_rate, E["gap_fill_weight"]))
        details["gap_hold_rate"] = round(gap_rate, 1)

    # Sector follow-through
    sector_hists = market_data.get("sector_histories", {})
    if len(sector_hists) >= 6:
        yesterday_returns = {}
        today_returns = {}
        for key, hist in sector_hists.items():
            if len(hist) >= 3:
                yesterday_returns[key] = (hist["close"].iloc[-2] - hist["close"].iloc[-3]) / hist["close"].iloc[-3] * 100
                today_returns[key] = (hist["close"].iloc[-1] - hist["close"].iloc[-2]) / hist["close"].iloc[-2] * 100

        if yesterday_returns and today_returns:
            yesterday_leaders = sorted(yesterday_returns, key=yesterday_returns.get, reverse=True)[:3]
            still_leading = sum(1 for s in yesterday_leaders if today_returns.get(s, 0) > 0)
            ft_score = (still_leading / 3) * 100
            scores.append(("sector_ft", ft_score, E["sector_followthrough_weight"]))
            details["sector_followthrough"] = round(ft_score, 1)

    # VIX-Price divergence
    vix_ind = market_data.get("vix_indicators", {})
    nifty_ind = market_data.get("nifty_indicators", {})
    vix_slope = vix_ind.get("slope_5d")
    nifty_hist_df = market_data.get("nifty_history")
    if vix_slope is not None and nifty_hist_df is not None and len(nifty_hist_df) >= 5:
        nifty_5d_return = (nifty_hist_df["close"].iloc[-1] - nifty_hist_df["close"].iloc[-5]) / nifty_hist_df["close"].iloc[-5] * 100
        if vix_slope < 0 and nifty_5d_return > 0:
            div_score = 90  # healthy
        elif vix_slope > 1 and nifty_5d_return > 0:
            div_score = 30  # trap risk
        else:
            div_score = 55
        scores.append(("vix_div", div_score, E["vix_divergence_weight"]))
        details["vix_price_divergence"] = div_score

    if not scores:
        return 50, {"note": "Insufficient data for day trading signals"}

    total = sum(s * w for _, s, w in scores)
    weight_sum = sum(w for _, _, w in scores)
    normalized = total / weight_sum if weight_sum > 0 else 50

    return max(0, min(100, int(normalized))), details
