"""Tests for Market Pulse execution tracking."""

from datetime import date, timedelta

import pandas as pd

import services.market_pulse_execution as market_pulse_execution


def test_track_breakouts_converts_numpy_bool_for_duckdb(tmp_path, monkeypatch):
    """Held status should be written as a native bool, not numpy.bool_."""
    db_path = tmp_path / "market_pulse.duckdb"
    monkeypatch.setattr(market_pulse_execution, "_DB_PATH", str(db_path))

    conn = market_pulse_execution._get_connection()
    try:
        conn.execute(
            "INSERT INTO breakout_events (symbol, breakout_date, breakout_price) VALUES (?, ?, ?)",
            ["TEST", (date.today() - timedelta(days=3)).isoformat(), 100.0],
        )
        conn.commit()
    finally:
        conn.close()

    closes = pd.Series([100.0] * 24 + [103.0])
    highs = closes + 1.0
    lows = closes - 1.0
    hist = pd.DataFrame({"close": closes, "high": highs, "low": lows})

    market_pulse_execution.track_breakouts(
        {"TEST": {"history": hist, "sector": "Test"}}
    )

    conn = market_pulse_execution._get_connection()
    try:
        held = conn.execute(
            "SELECT held FROM breakout_events WHERE symbol = ?",
            ["TEST"],
        ).fetchone()[0]
    finally:
        conn.close()

    assert isinstance(held, bool)
    assert held is True
