from __future__ import annotations

import json
import os
import queue
import random
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _now() -> float:
    return time.time()


@dataclass
class LearningConfig:
    enabled: bool = True
    auto_apply: bool = True
    tune_interval_s: int = 60
    min_trades: int = 10
    exploration_rate: float = 0.15
    max_history: int = 5000
    db_path: str = "db/ai_scalper_ledger.db"


class LearningStore:
    def __init__(self, db_path: str, max_history: int) -> None:
        self.db_path = db_path
        self.max_history = max_history
        self.queue: "queue.Queue[dict]" = queue.Queue()
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
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                ts_entry REAL,
                ts_exit REAL,
                side TEXT,
                symbol TEXT,
                quantity INTEGER,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                reason TEXT,
                playbook TEXT,
                arm_id TEXT,
                params_json TEXT,
                features_json TEXT,
                mode TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades(ts_exit);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_playbook ON trades(playbook);")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bandit_stats (
                arm_id TEXT PRIMARY KEY,
                plays INTEGER,
                reward_sum REAL,
                reward_sq REAL,
                updated_at REAL
            )
            """
        )

    def stop(self) -> None:
        self.stop_event.set()

    def _worker(self) -> None:
        conn = self._connect()
        self._init_db(conn)
        while not self.stop_event.is_set():
            try:
                task = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                kind = task.get("type")
                if kind == "entry":
                    self._write_entry(conn, task)
                elif kind == "exit":
                    self._write_exit(conn, task)
                elif kind == "bandit":
                    self._write_bandit(conn, task)
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass

    def _write_entry(self, conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades
            (id, ts_entry, side, symbol, quantity, entry_price, playbook, arm_id, params_json, features_json, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                task.get("ts_entry"),
                task.get("side"),
                task.get("symbol"),
                task.get("quantity"),
                task.get("entry_price"),
                task.get("playbook"),
                task.get("arm_id"),
                task.get("params_json"),
                task.get("features_json"),
                task.get("mode"),
            ),
        )
        conn.commit()

    def _write_exit(self, conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
        conn.execute(
            """
            UPDATE trades
            SET ts_exit = ?, exit_price = ?, pnl = ?, reason = ?
            WHERE id = ?
            """,
            (
                task.get("ts_exit"),
                task.get("exit_price"),
                task.get("pnl"),
                task.get("reason"),
                task["id"],
            ),
        )
        conn.commit()
        self._prune_history(conn)

    def _write_bandit(self, conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO bandit_stats (arm_id, plays, reward_sum, reward_sq, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(arm_id) DO UPDATE SET
                plays = excluded.plays,
                reward_sum = excluded.reward_sum,
                reward_sq = excluded.reward_sq,
                updated_at = excluded.updated_at
            """,
            (
                task["arm_id"],
                task.get("plays"),
                task.get("reward_sum"),
                task.get("reward_sq"),
                task.get("updated_at"),
            ),
        )
        conn.commit()

    def _prune_history(self, conn: sqlite3.Connection) -> None:
        if not self.max_history:
            return
        count = conn.execute("SELECT COUNT(*) FROM trades WHERE ts_exit IS NOT NULL").fetchone()[0]
        if count <= self.max_history:
            return
        excess = count - self.max_history
        conn.execute(
            """
            DELETE FROM trades WHERE id IN (
                SELECT id FROM trades WHERE ts_exit IS NOT NULL ORDER BY ts_exit ASC LIMIT ?
            )
            """,
            (excess,),
        )
        conn.commit()

    def enqueue_entry(self, payload: Dict[str, Any]) -> None:
        self.queue.put({"type": "entry", **payload})

    def enqueue_exit(self, payload: Dict[str, Any]) -> None:
        self.queue.put({"type": "exit", **payload})

    def enqueue_bandit(self, payload: Dict[str, Any]) -> None:
        self.queue.put({"type": "bandit", **payload})

    def fetch_trades(self, limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._connect()
        self._init_db(conn)
        rows = conn.execute(
            """
            SELECT id, ts_entry, ts_exit, side, symbol, quantity, entry_price, exit_price,
                   pnl, reason, playbook, arm_id, params_json, features_json, mode
            FROM trades
            ORDER BY ts_entry DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        results = []
        for row in rows:
            params = json.loads(row[12]) if row[12] else None
            features = json.loads(row[13]) if row[13] else None
            results.append(
                {
                    "id": row[0],
                    "ts_entry": row[1],
                    "ts_exit": row[2],
                    "side": row[3],
                    "symbol": row[4],
                    "quantity": row[5],
                    "entry_price": row[6],
                    "exit_price": row[7],
                    "pnl": row[8],
                    "reason": row[9],
                    "playbook": row[10],
                    "arm_id": row[11],
                    "params": params,
                    "features": features,
                    "mode": row[14],
                }
            )
        return results

    def get_bandit_stats(self) -> Dict[str, Dict[str, float]]:
        conn = self._connect()
        self._init_db(conn)
        rows = conn.execute("SELECT arm_id, plays, reward_sum, reward_sq FROM bandit_stats").fetchall()
        conn.close()
        stats: Dict[str, Dict[str, float]] = {}
        for arm_id, plays, reward_sum, reward_sq in rows:
            stats[arm_id] = {
                "plays": plays or 0,
                "reward_sum": reward_sum or 0.0,
                "reward_sq": reward_sq or 0.0,
            }
        return stats

    def summary(self, limit: int = 500) -> Dict[str, Any]:
        trades = [t for t in self.fetch_trades(limit) if t.get("ts_exit")]
        total = len(trades)
        wins = [t for t in trades if (t.get("pnl") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl") or 0) < 0]
        win_rate = (len(wins) / total) * 100 if total else 0.0
        sum_wins = sum(t.get("pnl") or 0 for t in wins)
        sum_losses = sum(t.get("pnl") or 0 for t in losses)
        profit_factor = (sum_wins / abs(sum_losses)) if sum_losses else None
        avg_pnl = (sum(t.get("pnl") or 0 for t in trades) / total) if total else 0.0

        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in reversed(trades):
            equity += t.get("pnl") or 0
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown

        by_playbook: Dict[str, Dict[str, Any]] = {}
        by_side: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            pb = t.get("playbook") or "unknown"
            by_playbook.setdefault(pb, {"trades": 0, "pnl": 0.0})
            by_playbook[pb]["trades"] += 1
            by_playbook[pb]["pnl"] += t.get("pnl") or 0
            side = t.get("side") or "?"
            by_side.setdefault(side, {"trades": 0, "pnl": 0.0})
            by_side[side]["trades"] += 1
            by_side[side]["pnl"] += t.get("pnl") or 0

        return {
            "total": total,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "profit_factor": profit_factor,
            "max_drawdown": max_dd,
            "sum_pnl": sum(t.get("pnl") or 0 for t in trades),
            "by_playbook": by_playbook,
            "by_side": by_side,
        }


@dataclass
class BanditArm:
    arm_id: str
    params: Dict[str, Any]


class BanditTuner:
    def __init__(self, config: LearningConfig, tick_size: float) -> None:
        self.config = config
        self.tick_size = tick_size or 0.05
        self.stats: Dict[str, Dict[str, float]] = {}

    def load(self, store: LearningStore) -> None:
        self.stats = store.get_bandit_stats()

    def _arms_for_base(self, base: Dict[str, Any]) -> List[BanditArm]:
        def clamp(val, lo, hi):
            return max(lo, min(hi, val))

        momentum = int(base.get("momentum_ticks", 3))
        tp = float(base.get("tp_points", 5))
        sl = float(base.get("sl_points", 8))
        trail = float(base.get("trail_distance", 2))

        arms = []
        variants = [
            ("base", 0, 0, 0, 0),
            ("tight", +1, -1, -1, -0.5),
            ("loose", -1, +1, +1, +0.5),
            ("trend", +2, 0, +1, +1),
            ("def", +1, 0, +2, 0),
        ]
        for name, dm, dtp, dsl, dtrail in variants:
            params = {
                "momentum_ticks": clamp(momentum + dm, 2, 10),
                "tp_points": max(1.0, tp + dtp),
                "sl_points": max(2.0, sl + dsl),
                "trail_distance": max(self.tick_size, trail + dtrail),
            }
            arms.append(BanditArm(arm_id=name, params=params))
        return arms

    def choose_arm(self, base: Dict[str, Any]) -> BanditArm:
        arms = self._arms_for_base(base)
        explore = self.config.exploration_rate
        if explore > 0 and (random.random() < explore):
            return random.choice(arms)
        best = arms[0]
        best_score = -1e9
        for arm in arms:
            stat = self.stats.get(arm.arm_id, {"plays": 0, "reward_sum": 0.0})
            plays = stat.get("plays", 0) or 0
            reward_sum = stat.get("reward_sum", 0.0) or 0.0
            score = reward_sum / plays if plays else 0.0
            if score > best_score:
                best_score = score
                best = arm
        return best

    def update(self, arm_id: str, reward: float) -> Dict[str, Any]:
        stat = self.stats.get(arm_id, {"plays": 0, "reward_sum": 0.0, "reward_sq": 0.0})
        stat["plays"] = int(stat.get("plays", 0)) + 1
        stat["reward_sum"] = float(stat.get("reward_sum", 0.0)) + reward
        stat["reward_sq"] = float(stat.get("reward_sq", 0.0)) + reward * reward
        self.stats[arm_id] = stat
        return stat


class LearningOrchestrator:
    def __init__(self, config: LearningConfig, tick_size: float) -> None:
        self.config = config
        self.store = LearningStore(config.db_path, config.max_history)
        self.bandit = BanditTuner(config, tick_size)
        self.bandit.load(self.store)
        self.last_tune_ts = 0.0
        self.trades_since_tune = 0
        self.current_arm_id = "base"
        self.trade_arms: Dict[str, str] = {}

    def stop(self) -> None:
        self.store.stop()

    def record_entry(self, payload: Dict[str, Any]) -> str:
        if not self.config.enabled:
            return ""
        trade_id = str(uuid.uuid4())
        payload = dict(payload)
        payload["id"] = trade_id
        payload["ts_entry"] = payload.get("ts_entry") or _now()
        payload["arm_id"] = self.current_arm_id
        payload["params_json"] = json.dumps(payload.get("params") or {})
        payload["features_json"] = json.dumps(payload.get("features") or {})
        payload.pop("params", None)
        payload.pop("features", None)
        self.store.enqueue_entry(payload)
        self.trade_arms[trade_id] = self.current_arm_id
        return trade_id

    def record_exit(self, trade_id: str, payload: Dict[str, Any]) -> None:
        if not self.config.enabled or not trade_id:
            return
        payload = dict(payload)
        payload["id"] = trade_id
        payload["ts_exit"] = payload.get("ts_exit") or _now()
        self.store.enqueue_exit(payload)
        arm_id = self.trade_arms.pop(trade_id, "base")
        reward = float(payload.get("pnl") or 0.0)
        qty = payload.get("quantity")
        if qty:
            try:
                reward = reward / float(qty)
            except (TypeError, ValueError, ZeroDivisionError):
                reward = float(payload.get("pnl") or 0.0)
        stats = self.bandit.update(arm_id, reward)
        self.store.enqueue_bandit(
            {
                "arm_id": arm_id,
                "plays": stats["plays"],
                "reward_sum": stats["reward_sum"],
                "reward_sq": stats["reward_sq"],
                "updated_at": _now(),
            }
        )
        self.trades_since_tune += 1

    def maybe_tune(self, base_config: Dict[str, Any]) -> Optional[BanditArm]:
        if not self.config.enabled or not self.config.auto_apply:
            return None
        now = _now()
        if self.trades_since_tune < self.config.min_trades:
            return None
        if now - self.last_tune_ts < self.config.tune_interval_s:
            return None
        arm = self.bandit.choose_arm(base_config)
        self.current_arm_id = arm.arm_id
        self.last_tune_ts = now
        self.trades_since_tune = 0
        return arm

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "auto_apply": self.config.auto_apply,
            "current_arm": self.current_arm_id,
            "trades_since_tune": self.trades_since_tune,
        }


_store: Optional[LearningStore] = None
_store_lock = threading.Lock()


def get_learning_store(db_path: str = "db/ai_scalper_ledger.db", max_history: int = 5000) -> LearningStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = LearningStore(db_path, max_history)
        return _store
