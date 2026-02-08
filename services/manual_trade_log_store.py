from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from utils.logging import get_logger

logger = get_logger(__name__)


def _now_epoch() -> float:
    return time.time()


def _parse_ts(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
        try:
            cleaned = value.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).timestamp()
        except ValueError:
            return None
    return None


def _iso_from_epoch(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class ManualTradeLogStore:
    def __init__(self, db_path: str = "db/manual_trade_logs.db", max_history: int = 50000) -> None:
        self.db_path = db_path
        self.max_history = max_history
        self.queue: "queue.Queue[list[dict]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_trade_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                trade_id TEXT,
                ts REAL,
                ts_iso TEXT,
                event_type TEXT,
                source TEXT,
                mode TEXT,
                side TEXT,
                symbol TEXT,
                action TEXT,
                qty INTEGER,
                price REAL,
                tp_points REAL,
                sl_points REAL,
                pnl REAL,
                reason TEXT,
                hold_ms REAL,
                underlying TEXT,
                exchange TEXT,
                meta_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_trade_logs_ts ON manual_trade_logs(ts);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_trade_logs_mode ON manual_trade_logs(mode);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_trade_logs_symbol ON manual_trade_logs(symbol);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_trade_logs_underlying ON manual_trade_logs(underlying);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_trade_logs_trade_id ON manual_trade_logs(trade_id);")
        self._ensure_column(conn, "manual_trade_logs", "trade_id", "TEXT")
        self._ensure_column(conn, "manual_trade_logs", "hold_ms", "REAL")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column in cols:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def stop(self) -> None:
        self.stop_event.set()

    def enqueue(self, events: Dict[str, Any] | Iterable[Dict[str, Any]]) -> None:
        if isinstance(events, dict):
            payload = [events]
        else:
            payload = list(events)
        if not payload:
            return
        self.queue.put(payload)

    def _normalize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event = dict(event or {})
        ts_epoch = _parse_ts(event.get("ts")) or _parse_ts(event.get("timestamp")) or _now_epoch()
        ts_iso = event.get("ts") or _iso_from_epoch(ts_epoch)
        event_id = event.get("eventId") or event.get("event_id") or event.get("id") or str(uuid.uuid4())
        trade_id = event.get("tradeId") or event.get("trade_id")
        record = {
            "event_id": event_id,
            "trade_id": trade_id,
            "ts": ts_epoch,
            "ts_iso": ts_iso,
            "event_type": event.get("type") or event.get("event_type"),
            "source": event.get("source") or "manual",
            "mode": event.get("mode"),
            "side": event.get("side"),
            "symbol": event.get("symbol"),
            "action": event.get("action"),
            "qty": event.get("qty"),
            "price": event.get("price"),
            "tp_points": event.get("tpPoints") or event.get("tp_points"),
            "sl_points": event.get("slPoints") or event.get("sl_points"),
            "pnl": event.get("pnl"),
            "reason": event.get("reason"),
            "hold_ms": event.get("holdMs") or event.get("hold_ms"),
            "underlying": event.get("underlying"),
            "exchange": event.get("exchange") or event.get("underlying_exchange"),
        }
        known = {
            "eventId",
            "event_id",
            "tradeId",
            "trade_id",
            "id",
            "ts",
            "timestamp",
            "type",
            "event_type",
            "source",
            "mode",
            "side",
            "symbol",
            "action",
            "qty",
            "price",
            "tpPoints",
            "tp_points",
            "slPoints",
            "sl_points",
            "pnl",
            "reason",
            "holdMs",
            "hold_ms",
            "underlying",
            "exchange",
            "underlying_exchange",
        }
        meta = {k: v for k, v in event.items() if k not in known}
        record["meta_json"] = json.dumps(meta) if meta else None
        return record

    def _write_batch(self, conn: sqlite3.Connection, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        rows = [self._normalize_event(e) for e in events]
        conn.executemany(
            """
            INSERT OR IGNORE INTO manual_trade_logs
            (event_id, trade_id, ts, ts_iso, event_type, source, mode, side, symbol, action,
             qty, price, tp_points, sl_points, pnl, reason, hold_ms, underlying, exchange, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["event_id"],
                    row["trade_id"],
                    row["ts"],
                    row["ts_iso"],
                    row["event_type"],
                    row["source"],
                    row["mode"],
                    row["side"],
                    row["symbol"],
                    row["action"],
                    row["qty"],
                    row["price"],
                    row["tp_points"],
                    row["sl_points"],
                    row["pnl"],
                    row["reason"],
                    row["hold_ms"],
                    row["underlying"],
                    row["exchange"],
                    row["meta_json"],
                )
                for row in rows
            ],
        )
        conn.commit()
        self._prune_history(conn)

    def _build_filter_clause(
        self,
        mode: Optional[str] = None,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        underlying: Optional[str] = None,
        since: Optional[Any] = None,
        until: Optional[Any] = None,
        exit_only: bool = False,
    ) -> tuple[str, list[Any]]:
        params: list[Any] = []
        where: list[str] = []
        if mode:
            where.append("mode = ?")
            params.append(mode)
        if source:
            where.append("source = ?")
            params.append(source)
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if side:
            where.append("side = ?")
            params.append(side)
        if underlying:
            where.append("underlying = ?")
            params.append(underlying)
        ts_since = _parse_ts(since)
        ts_until = _parse_ts(until)
        if ts_since is not None:
            where.append("ts >= ?")
            params.append(ts_since)
        if ts_until is not None:
            where.append("ts <= ?")
            params.append(ts_until)
        if exit_only:
            where.append("event_type = 'EXIT'")
            where.append("pnl IS NOT NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return clause, params

    def _prune_history(self, conn: sqlite3.Connection) -> None:
        if not self.max_history:
            return
        count = conn.execute("SELECT COUNT(*) FROM manual_trade_logs").fetchone()[0]
        if count <= self.max_history:
            return
        excess = count - self.max_history
        conn.execute(
            """
            DELETE FROM manual_trade_logs WHERE id IN (
                SELECT id FROM manual_trade_logs ORDER BY ts ASC LIMIT ?
            )
            """,
            (excess,),
        )
        conn.commit()

    def _worker(self) -> None:
        conn = self._connect()
        self._init_db(conn)
        while not self.stop_event.is_set():
            try:
                batch = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if not batch:
                continue
            try:
                while len(batch) < 200:
                    try:
                        extra = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    batch.extend(extra or [])
                self._write_batch(conn, batch)
            except Exception as exc:
                logger.debug("Manual trade log write failed: %s", exc)
        try:
            conn.close()
        except Exception:
            pass

    def fetch(
        self,
        limit: int = 200,
        mode: Optional[str] = None,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        underlying: Optional[str] = None,
        since: Optional[Any] = None,
        until: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        self._init_db(conn)
        clause, params = self._build_filter_clause(
            mode=mode,
            source=source,
            symbol=symbol,
            side=side,
            underlying=underlying,
            since=since,
            until=until,
        )
        rows = conn.execute(
            f"""
            SELECT event_id, trade_id, ts, ts_iso, event_type, source, mode, side, symbol,
                   action, qty, price, tp_points, sl_points, pnl, reason, hold_ms, underlying,
                   exchange, meta_json
            FROM manual_trade_logs
            {clause}
            ORDER BY ts DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        conn.close()
        results = []
        for row in rows:
            results.append(
                {
                    "event_id": row[0],
                    "trade_id": row[1],
                    "ts": row[2],
                    "ts_iso": row[3],
                    "event_type": row[4],
                    "source": row[5],
                    "mode": row[6],
                    "side": row[7],
                    "symbol": row[8],
                    "action": row[9],
                    "qty": row[10],
                    "price": row[11],
                    "tp_points": row[12],
                    "sl_points": row[13],
                    "pnl": row[14],
                    "reason": row[15],
                    "hold_ms": row[16],
                    "underlying": row[17],
                    "exchange": row[18],
                    "meta": json.loads(row[19]) if row[19] else None,
                }
            )
        return results

    def fetch_exit_events(
        self,
        limit: int = 2000,
        mode: Optional[str] = None,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        underlying: Optional[str] = None,
        since: Optional[Any] = None,
        until: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._connect()
        self._init_db(conn)
        clause, params = self._build_filter_clause(
            mode=mode,
            source=source,
            symbol=symbol,
            side=side,
            underlying=underlying,
            since=since,
            until=until,
            exit_only=True,
        )
        rows = conn.execute(
            f"""
            SELECT ts, pnl, reason, side, symbol, hold_ms
            FROM manual_trade_logs
            {clause}
            ORDER BY ts DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        conn.close()
        return [
            {
                "ts": row[0],
                "pnl": row[1],
                "reason": row[2],
                "side": row[3],
                "symbol": row[4],
                "hold_ms": row[5],
            }
            for row in rows
        ]

    def analytics(
        self,
        limit: int = 2000,
        mode: Optional[str] = None,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        underlying: Optional[str] = None,
        since: Optional[Any] = None,
        until: Optional[Any] = None,
        bucket: float = 50.0,
        interval_min: int = 5,
    ) -> Dict[str, Any]:
        events = self.fetch_exit_events(
            limit=limit,
            mode=mode,
            source=source,
            symbol=symbol,
            side=side,
            underlying=underlying,
            since=since,
            until=until,
        )

        pnl_values = [float(e.get("pnl") or 0.0) for e in events]
        total_trades = len(pnl_values)
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        win_rate = (len(wins) / total_trades) * 100 if total_trades else 0.0
        sum_wins = sum(wins)
        sum_losses = sum(losses)
        profit_factor = (sum_wins / abs(sum_losses)) if sum_losses else None
        total_pnl = sum(pnl_values)
        avg_pnl = (total_pnl / total_trades) if total_trades else 0.0
        max_win = max(wins) if wins else 0.0
        max_loss = min(losses) if losses else 0.0

        # Equity curve + drawdown
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        equity_points = []
        for event in reversed(events):
            pnl = float(event.get("pnl") or 0.0)
            equity += pnl
            peak = max(peak, equity)
            drawdown = peak - equity
            max_dd = max(max_dd, drawdown)
            ts = event.get("ts")
            equity_points.append({"time": int(ts) if ts else None, "value": equity, "drawdown": drawdown})

        # Distribution buckets
        distribution = {}
        if bucket and bucket > 0:
            for pnl in pnl_values:
                key = bucket * (pnl // bucket)
                distribution[key] = distribution.get(key, 0) + 1
        distribution_list = [
            {"bucket": float(k), "count": int(v)}
            for k, v in sorted(distribution.items(), key=lambda x: x[0])
        ]

        # Time buckets
        time_buckets: Dict[str, Dict[str, Any]] = {}
        for event in events:
            ts = event.get("ts")
            if not ts:
                continue
            ts_min = int(ts // (interval_min * 60)) * (interval_min * 60)
            label = _iso_from_epoch(ts_min)
            bucket = time_buckets.setdefault(label, {"count": 0, "pnl": 0.0, "wins": 0})
            pnl = float(event.get("pnl") or 0.0)
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
        time_bucket_list = [
            {
                "bucket": label,
                "count": data["count"],
                "pnl": data["pnl"],
                "win_rate": (data["wins"] / data["count"]) * 100 if data["count"] else 0.0,
                "avg_pnl": (data["pnl"] / data["count"]) if data["count"] else 0.0,
            }
            for label, data in sorted(time_buckets.items(), key=lambda x: x[0])
        ]

        # Reason breakdown
        reason_breakdown: Dict[str, Dict[str, Any]] = {}
        for event in events:
            reason = event.get("reason") or "Unknown"
            bucket = reason_breakdown.setdefault(reason, {"count": 0, "pnl": 0.0})
            bucket["count"] += 1
            bucket["pnl"] += float(event.get("pnl") or 0.0)
        reason_list = [
            {"reason": key, "count": data["count"], "pnl": data["pnl"]}
            for key, data in sorted(reason_breakdown.items(), key=lambda x: x[1]["count"], reverse=True)
        ]

        # Side breakdown
        side_breakdown: Dict[str, Dict[str, Any]] = {}
        for event in events:
            side_key = event.get("side") or "?"
            bucket = side_breakdown.setdefault(side_key, {"count": 0, "pnl": 0.0, "wins": 0})
            pnl = float(event.get("pnl") or 0.0)
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
        side_list = [
            {
                "side": key,
                "count": data["count"],
                "pnl": data["pnl"],
                "win_rate": (data["wins"] / data["count"]) * 100 if data["count"] else 0.0,
            }
            for key, data in sorted(side_breakdown.items(), key=lambda x: x[1]["count"], reverse=True)
        ]

        avg_hold_s = None
        hold_samples = [e.get("hold_ms") for e in events if e.get("hold_ms")]
        if hold_samples:
            avg_hold_s = sum(hold_samples) / len(hold_samples) / 1000.0

        summary = {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "profit_factor": profit_factor,
            "avg_hold_s": avg_hold_s,
            "max_win": max_win,
            "max_loss": max_loss,
            "max_drawdown": max_dd,
        }

        return {
            "summary": summary,
            "equity": equity_points,
            "distribution": distribution_list,
            "time_buckets": time_bucket_list,
            "reason_breakdown": reason_list,
            "side_breakdown": side_list,
            "limit": limit,
        }


_manual_store: Optional[ManualTradeLogStore] = None
_manual_store_lock = threading.Lock()


def get_manual_trade_log_store(db_path: str = "db/manual_trade_logs.db") -> ManualTradeLogStore:
    global _manual_store
    with _manual_store_lock:
        if _manual_store is None:
            _manual_store = ManualTradeLogStore(db_path=db_path)
        return _manual_store
