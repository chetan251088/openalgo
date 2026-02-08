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


class AutoTradeLogStore:
    def __init__(self, db_path: str = "db/ai_scalper_logs.db", max_history: int = 50000) -> None:
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
            CREATE TABLE IF NOT EXISTS auto_trade_logs (
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
                meta_json TEXT,
                conditions_json TEXT
            )
            """
        )
        self._ensure_column(conn, "auto_trade_logs", "conditions_json", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_trade_logs_ts ON auto_trade_logs(ts);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_trade_logs_mode ON auto_trade_logs(mode);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_trade_logs_symbol ON auto_trade_logs(symbol);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_trade_logs_underlying ON auto_trade_logs(underlying);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_trade_logs_trade_id ON auto_trade_logs(trade_id);")
        self._ensure_column(conn, "auto_trade_logs", "trade_id", "TEXT")
        self._ensure_column(conn, "auto_trade_logs", "hold_ms", "REAL")

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
            "source": event.get("source") or "local",
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
            "matched_conditions",
            "entry_conditions",
            "exit_conditions",
        }
        # Persist entry/exit conditions for analysis (what triggered the trade)
        conditions = event.get("matched_conditions") or event.get("entry_conditions") or event.get("exit_conditions")
        if conditions is not None:
            record["conditions_json"] = json.dumps(conditions) if not isinstance(conditions, str) else conditions
        else:
            record["conditions_json"] = None
        meta = {k: v for k, v in event.items() if k not in known}
        record["meta_json"] = json.dumps(meta) if meta else None
        return record

    def _write_batch(self, conn: sqlite3.Connection, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        rows = [self._normalize_event(e) for e in events]
        conn.executemany(
            """
            INSERT OR IGNORE INTO auto_trade_logs
            (event_id, trade_id, ts, ts_iso, event_type, source, mode, side, symbol, action,
             qty, price, tp_points, sl_points, pnl, reason, hold_ms, underlying, exchange, meta_json, conditions_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row.get("conditions_json"),
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
        count = conn.execute("SELECT COUNT(*) FROM auto_trade_logs").fetchone()[0]
        if count <= self.max_history:
            return
        excess = count - self.max_history
        conn.execute(
            """
            DELETE FROM auto_trade_logs WHERE id IN (
                SELECT id FROM auto_trade_logs ORDER BY ts ASC LIMIT ?
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
                # coalesce multiple queued batches
                while len(batch) < 200:
                    try:
                        extra = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    batch.extend(extra or [])
                self._write_batch(conn, batch)
            except Exception as exc:
                logger.debug("Auto trade log write failed: %s", exc)
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
        limit = max(1, min(int(limit or 200), 5000))
        query = f"""
            SELECT event_id, trade_id, ts, ts_iso, event_type, source, mode, side, symbol, action,
                   qty, price, tp_points, sl_points, pnl, reason, hold_ms, underlying, exchange, meta_json, conditions_json
            FROM auto_trade_logs
            {clause}
            ORDER BY ts DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        results = []
        for row in rows:
            meta = {}
            if row[19]:
                try:
                    meta = json.loads(row[19])
                except Exception:
                    meta = {}
            conditions = None
            if len(row) > 20 and row[20]:
                try:
                    conditions = json.loads(row[20])
                except Exception:
                    pass
            out = {
                "event_id": row[0],
                "tradeId": row[1],
                "ts": row[3] or _iso_from_epoch(row[2]),
                "type": row[4],
                "source": row[5],
                "mode": row[6],
                "side": row[7],
                "symbol": row[8],
                "action": row[9],
                "qty": row[10],
                "price": row[11],
                "tpPoints": row[12],
                "slPoints": row[13],
                "pnl": row[14],
                "reason": row[15],
                "holdMs": row[16],
                "underlying": row[17],
                "exchange": row[18],
                **meta,
            }
            if conditions is not None:
                out["matched_conditions"] = conditions
            results.append(out)
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
        limit = max(1, min(int(limit or 2000), 10000))
        query = f"""
            SELECT ts, pnl, reason, side, hold_ms
            FROM auto_trade_logs
            {clause}
            ORDER BY ts DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [
            {"ts": row[0], "pnl": row[1], "reason": row[2], "side": row[3], "hold_ms": row[4]}
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
        if not events:
            return {
                "summary": {
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0,
                    "total_pnl": 0,
                    "avg_pnl": 0,
                    "profit_factor": None,
                    "avg_hold_s": None,
                    "max_win": 0,
                    "max_loss": 0,
                    "max_drawdown": 0,
                },
                "equity": [],
                "distribution": [],
                "time_buckets": [],
                "reason_breakdown": [],
                "side_breakdown": [],
                "limit": limit,
            }

        pnls = [float(e.get("pnl") or 0) for e in events]
        total = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total if total else 0.0
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = -sum(p for p in pnls if p < 0)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
        max_win = max(pnls) if pnls else 0.0
        max_loss = min(pnls) if pnls else 0.0

        hold_vals = [float(e["hold_ms"]) for e in events if e.get("hold_ms") is not None]
        avg_hold_s = (sum(hold_vals) / len(hold_vals) / 1000.0) if hold_vals else None

        equity = []
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for e in sorted(events, key=lambda x: x["ts"] or 0):
            pnl = float(e.get("pnl") or 0)
            cum += pnl
            peak = max(peak, cum)
            dd = cum - peak
            max_dd = min(max_dd, dd)
            equity.append(
                {
                    "time": int(e["ts"]) if e.get("ts") else None,
                    "value": round(cum, 2),
                    "drawdown": round(dd, 2),
                }
            )

        bucket = float(bucket or 50.0) or 50.0
        buckets: Dict[float, int] = {}
        for p in pnls:
            key = bucket * (int(p // bucket))
            buckets[key] = buckets.get(key, 0) + 1
        distribution = [
            {"bucket": k, "count": v}
            for k, v in sorted(buckets.items(), key=lambda item: item[0])
        ]

        interval_min = max(1, int(interval_min or 5))
        time_map: Dict[int, Dict[str, float]] = {}
        for e in events:
            ts = e.get("ts")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts)
            minutes = dt.hour * 60 + dt.minute
            bucket_min = (minutes // interval_min) * interval_min
            slot = time_map.setdefault(bucket_min, {"count": 0, "pnl": 0.0, "wins": 0})
            pnl = float(e.get("pnl") or 0)
            slot["count"] += 1
            slot["pnl"] += pnl
            if pnl > 0:
                slot["wins"] += 1
        time_buckets = []
        for bucket_min, slot in sorted(time_map.items()):
            hh = bucket_min // 60
            mm = bucket_min % 60
            count = slot["count"]
            win_rate = (slot["wins"] / count * 100) if count else 0
            time_buckets.append(
                {
                    "bucket": f"{hh:02d}:{mm:02d}",
                    "count": count,
                    "pnl": round(slot["pnl"], 2),
                    "win_rate": round(win_rate, 1),
                    "avg_pnl": round(slot["pnl"] / count, 2) if count else 0,
                }
            )

        reason_map: Dict[str, Dict[str, float]] = {}
        for e in events:
            reason = e.get("reason") or "Unknown"
            slot = reason_map.setdefault(reason, {"count": 0, "pnl": 0.0})
            pnl = float(e.get("pnl") or 0)
            slot["count"] += 1
            slot["pnl"] += pnl
        reason_breakdown = [
            {"reason": r, "count": v["count"], "pnl": round(v["pnl"], 2)}
            for r, v in sorted(reason_map.items(), key=lambda item: -item[1]["count"])
        ]

        side_map: Dict[str, Dict[str, float]] = {}
        for e in events:
            s = e.get("side") or "--"
            slot = side_map.setdefault(s, {"count": 0, "pnl": 0.0, "wins": 0})
            pnl = float(e.get("pnl") or 0)
            slot["count"] += 1
            slot["pnl"] += pnl
            if pnl > 0:
                slot["wins"] += 1
        side_breakdown = []
        for s, v in side_map.items():
            count = v["count"]
            side_breakdown.append(
                {
                    "side": s,
                    "count": count,
                    "pnl": round(v["pnl"], 2),
                    "win_rate": round((v["wins"] / count * 100) if count else 0, 1),
                }
            )

        return {
            "summary": {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / total * 100) if total else 0, 1),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(avg_pnl, 2),
                "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
                "avg_hold_s": round(avg_hold_s, 2) if avg_hold_s is not None else None,
                "max_win": round(max_win, 2),
                "max_loss": round(max_loss, 2),
                "max_drawdown": round(abs(max_dd), 2),
            },
            "equity": equity,
            "distribution": distribution,
            "time_buckets": time_buckets,
            "reason_breakdown": reason_breakdown,
            "side_breakdown": side_breakdown,
            "limit": limit,
        }


_log_store: AutoTradeLogStore | None = None


def get_auto_trade_log_store() -> AutoTradeLogStore:
    global _log_store
    if _log_store is None:
        _log_store = AutoTradeLogStore()
    return _log_store
