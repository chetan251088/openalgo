"""
Mock trading database for replay/testing. All orders, positions, and trades
from the Mock Replay UI are stored here (no real broker).
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from utils.logging import get_logger

logger = get_logger(__name__)

MOCK_DB_PATH = os.getenv("MOCK_TRADING_DB_PATH", "db/mock_trading.db")


def _get_path() -> str:
    if os.path.isabs(MOCK_DB_PATH):
        return MOCK_DB_PATH
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, MOCK_DB_PATH)


@contextmanager
def _conn():
    p = _get_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("PRAGMA journal_mode=WAL;")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS mock_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                filled_price REAL,
                filled_qty INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_mock_orders_status ON mock_orders(status);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mock_orders_symbol ON mock_orders(symbol);")
        c.execute("""
            CREATE TABLE IF NOT EXISTS mock_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(symbol, exchange)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS mock_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                traded_at REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_mock_trades_order ON mock_trades(order_id);")


def place_mock_order(
    symbol: str,
    exchange: str,
    action: str,
    quantity: int,
    order_type: str = "MARKET",
    price: Optional[float] = None,
) -> Dict[str, Any]:
    """Place a mock order. Market orders are filled immediately at price or 0 (use LTP from client)."""
    order_id = str(uuid.uuid4())[:12].upper()
    now = time.time()
    with _conn() as c:
        c.execute(
            """INSERT INTO mock_orders (order_id, symbol, exchange, action, quantity, price, order_type, status, filled_price, filled_qty, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (order_id, symbol, exchange, action, quantity, price, order_type, "open", None, 0, now, now),
        )
    return {"order_id": order_id, "symbol": symbol, "exchange": exchange, "action": action, "quantity": quantity, "status": "open"}


def fill_mock_order(order_id: str, fill_price: float) -> bool:
    """Mark order complete and fill at price; update or create position; insert trade."""
    with _conn() as c:
        row = c.execute(
            "SELECT symbol, exchange, action, quantity FROM mock_orders WHERE order_id = ? AND status = 'open'",
            (order_id,),
        ).fetchone()
        if not row:
            return False
        symbol, exchange, action, qty = row
        now = time.time()
        c.execute(
            "UPDATE mock_orders SET status = 'complete', filled_price = ?, filled_qty = ?, updated_at = ? WHERE order_id = ?",
            (fill_price, qty, now, order_id),
        )
        c.execute(
            "INSERT INTO mock_trades (order_id, symbol, exchange, action, quantity, price, traded_at) VALUES (?,?,?,?,?,?,?)",
            (order_id, symbol, exchange, action, qty, fill_price, now),
        )
        pos = c.execute(
            "SELECT id, side, quantity, entry_price FROM mock_positions WHERE symbol = ? AND exchange = ?",
            (symbol, exchange),
        ).fetchone()
        if action == "BUY":
            if pos:
                old_side, old_qty, old_avg = pos[1], pos[2], pos[3]
                if old_side == "BUY":
                    new_qty = old_qty + qty
                    new_avg = (old_avg * old_qty + fill_price * qty) / new_qty
                    c.execute("UPDATE mock_positions SET quantity = ?, entry_price = ? WHERE symbol = ? AND exchange = ?", (new_qty, new_avg, symbol, exchange))
                else:
                    new_qty = old_qty - qty
                    if new_qty <= 0:
                        c.execute("DELETE FROM mock_positions WHERE symbol = ? AND exchange = ?", (symbol, exchange))
                    else:
                        c.execute("UPDATE mock_positions SET quantity = ? WHERE symbol = ? AND exchange = ?", (new_qty, symbol, exchange))
            else:
                c.execute(
                    "INSERT OR REPLACE INTO mock_positions (symbol, exchange, side, quantity, entry_price, created_at) VALUES (?,?,?,?,?,?)",
                    (symbol, exchange, "BUY", qty, fill_price, now),
                )
        else:
            if pos:
                old_side, old_qty, old_avg = pos[1], pos[2], pos[3]
                if old_side == "SELL":
                    new_qty = old_qty + qty
                    new_avg = (old_avg * old_qty + fill_price * qty) / new_qty
                    c.execute("UPDATE mock_positions SET quantity = ?, entry_price = ? WHERE symbol = ? AND exchange = ?", (new_qty, new_avg, symbol, exchange))
                else:
                    new_qty = old_qty - qty
                    if new_qty <= 0:
                        c.execute("DELETE FROM mock_positions WHERE symbol = ? AND exchange = ?", (symbol, exchange))
                    else:
                        c.execute("UPDATE mock_positions SET quantity = ? WHERE symbol = ? AND exchange = ?", (new_qty, symbol, exchange))
            else:
                c.execute(
                    "INSERT OR REPLACE INTO mock_positions (symbol, exchange, side, quantity, entry_price, created_at) VALUES (?,?,?,?,?,?)",
                    (symbol, exchange, "SELL", qty, fill_price, now),
                )
    return True


def get_mock_orders(limit: int = 100) -> List[Dict[str, Any]]:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT order_id, symbol, exchange, action, quantity, price, order_type, status, filled_price, filled_qty, created_at FROM mock_orders ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_mock_positions() -> List[Dict[str, Any]]:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT symbol, exchange, side, quantity, entry_price, created_at FROM mock_positions ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]


def get_mock_trades(limit: int = 200) -> List[Dict[str, Any]]:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT order_id, symbol, exchange, action, quantity, price, traded_at FROM mock_trades ORDER BY traded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def close_mock_position(symbol: str, exchange: str, close_price: float) -> Optional[Dict[str, Any]]:
    """Close position (create closing trade and remove position). Returns trade summary."""
    with _conn() as c:
        row = c.execute(
            "SELECT side, quantity, entry_price FROM mock_positions WHERE symbol = ? AND exchange = ?",
            (symbol, exchange),
        ).fetchone()
        if not row:
            return None
        side, qty, entry = row
        action = "SELL" if side == "BUY" else "BUY"
        order_id = str(uuid.uuid4())[:12].upper()
        now = time.time()
        c.execute("DELETE FROM mock_positions WHERE symbol = ? AND exchange = ?", (symbol, exchange))
        c.execute(
            "INSERT INTO mock_trades (order_id, symbol, exchange, action, quantity, price, traded_at) VALUES (?,?,?,?,?,?,?)",
            (order_id, symbol, exchange, action, qty, close_price, now),
        )
        pnl = (close_price - entry) * qty if side == "BUY" else (entry - close_price) * qty
        return {"symbol": symbol, "exchange": exchange, "quantity": qty, "entry_price": entry, "close_price": close_price, "pnl": round(pnl, 2)}


def cancel_mock_order(order_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE mock_orders SET status = 'cancelled', updated_at = ? WHERE order_id = ? AND status = 'open'", (time.time(), order_id))
        return cur.rowcount > 0
