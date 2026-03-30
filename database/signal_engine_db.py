"""
Signal Engine database — SQLite store for settings, signal log, and execution history.

Tables:
  se_settings   — key/value config (execute_mode, default_lots, etc.)
  se_signal_log — history of signals and executions
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

_DB_PATH = os.getenv("SIGNAL_ENGINE_DB_PATH", "db/signal_engine.db")


@contextmanager
def _conn():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(_DB_PATH) if os.path.dirname(_DB_PATH) else ".", exist_ok=True)
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS se_settings (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS se_signal_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    DEFAULT (datetime('now')),
                symbol      TEXT    NOT NULL,
                exchange    TEXT,
                dte         INTEGER,
                regime      TEXT,
                iv_rank     REAL,
                strategy    TEXT,
                favorable   INTEGER DEFAULT 0,
                executed    INTEGER DEFAULT 0,
                exec_mode   TEXT,
                lots        INTEGER DEFAULT 0,
                net_credit  REAL,
                legs_json   TEXT,
                notes       TEXT
            );
        """)
        # Insert defaults only if they don't exist
        defaults = [
            ("execute_mode", "OBSERVE"),   # OBSERVE | MANUAL | AUTO
            ("default_lots", "1"),
            ("max_lots", "3"),
            ("risk_pct", "1.0"),
            ("product", "NRML"),
            ("auto_user_id", ""),          # set automatically when user enables AUTO
        ]
        con.executemany(
            "INSERT OR IGNORE INTO se_settings (key, value) VALUES (?, ?)",
            defaults,
        )
    log.info("Signal Engine DB ready at %s", _DB_PATH)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    try:
        with _conn() as con:
            row = con.execute("SELECT value FROM se_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
    except Exception as exc:
        log.warning("get_setting(%s) failed: %s", key, exc)
        return default


def set_setting(key: str, value: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO se_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
            (key, value),
        )


def get_all_settings() -> dict[str, str]:
    try:
        with _conn() as con:
            rows = con.execute("SELECT key, value FROM se_settings").fetchall()
            return {r["key"]: r["value"] for r in rows}
    except Exception as exc:
        log.warning("get_all_settings failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Signal log helpers
# ---------------------------------------------------------------------------

def log_signal(
    symbol: str,
    exchange: str,
    dte: int,
    regime: str,
    iv_rank: float | None,
    strategy: str,
    favorable: bool,
    executed: bool = False,
    exec_mode: str = "",
    lots: int = 0,
    net_credit: float | None = None,
    legs: list[dict] | None = None,
    notes: str = "",
) -> int:
    """Insert a signal record, return its rowid."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO se_signal_log
               (symbol, exchange, dte, regime, iv_rank, strategy, favorable,
                executed, exec_mode, lots, net_credit, legs_json, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol, exchange, dte, regime,
                iv_rank, strategy,
                1 if favorable else 0,
                1 if executed else 0,
                exec_mode, lots, net_credit,
                json.dumps(legs or []),
                notes,
            ),
        )
        return cur.lastrowid


def update_signal_executed(row_id: int, lots: int, net_credit: float | None, notes: str = "") -> None:
    with _conn() as con:
        con.execute(
            "UPDATE se_signal_log SET executed=1, lots=?, net_credit=?, notes=? WHERE id=?",
            (lots, net_credit, notes, row_id),
        )


def get_recent_signals(limit: int = 20) -> list[dict[str, Any]]:
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM se_signal_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("get_recent_signals failed: %s", exc)
        return []


def has_open_position(symbol: str, within_hours: int = 12) -> bool:
    """True if we placed an order for this symbol in the last N hours."""
    try:
        with _conn() as con:
            row = con.execute(
                """SELECT id FROM se_signal_log
                   WHERE symbol=? AND executed=1
                     AND ts > datetime('now', ?)
                   LIMIT 1""",
                (symbol, f"-{within_hours} hours"),
            ).fetchone()
            return row is not None
    except Exception:
        return False
