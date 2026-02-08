from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils.logging import get_logger

from .advisor import build_advisor
from .config import AdvisorConfig, ModelTunerConfig
from .learning import get_learning_store
from .log_store import get_auto_trade_log_store
from database.auth_db import get_username_by_apikey
from database.telegram_db import get_telegram_user_by_username
from database.user_db import get_email_by_username
from services.telegram_alert_service import telegram_alert_service
from utils.email_utils import send_email

logger = get_logger(__name__)

DEFAULT_OBJECTIVE = "Improve net PnL while reducing drawdown and preserving win rate."

_TUNER_CLAMPS: dict[str, tuple[float, float, type]] = {
    "momentum_ticks": (2, 10, int),
    "tp_points": (1.0, 30.0, float),
    "sl_points": (2.0, 30.0, float),
    "trail_distance": (0.05, 20.0, float),
    "trail_step": (0.01, 1.0, float),
    "index_bias_min_score": (1, 5, int),
    "index_rsi_bull": (50.0, 70.0, float),
    "index_rsi_bear": (30.0, 50.0, float),
    "index_adx_min": (10.0, 40.0, float),
    "index_vwap_buffer": (0.0, 5.0, float),
}


def _now_epoch() -> float:
    return time.time()


def _iso_from_epoch(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_provider(provider: str | None) -> str:
    if not provider:
        return "none"
    return str(provider).lower().strip()


def _provider_ready(provider: str, base_url: str | None = None) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "ollama":
        return bool(base_url or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434")
    return False


def _model_ready(provider: str, model: str | None) -> bool:
    if model:
        return True
    if provider == "openai":
        return bool(os.getenv("OPENAI_ADVISOR_MODEL"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_ADVISOR_MODEL"))
    if provider == "ollama":
        return bool(os.getenv("OLLAMA_ADVISOR_MODEL"))
    return False


class ModelTunerStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_tuning_runs (
                run_id TEXT PRIMARY KEY,
                created_ts REAL,
                created_iso TEXT,
                status TEXT,
                provider TEXT,
                model TEXT,
                underlying TEXT,
                objective TEXT,
                score REAL,
                recommendations_json TEXT,
                applied_changes_json TEXT,
                notes TEXT,
                applied INTEGER,
                applied_ts REAL,
                applied_iso TEXT,
                applied_by TEXT,
                requested_by TEXT,
                error TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_model_tuning_created ON model_tuning_runs(created_ts);")
        self._ensure_column(conn, "model_tuning_runs", "underlying", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column in cols:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def create_run(
        self,
        provider: str,
        model: str | None,
        objective: str,
        requested_by: str,
        underlying: str | None,
    ) -> str:
        run_id = str(uuid.uuid4())
        ts = _now_epoch()
        iso = _iso_from_epoch(ts)
        with self.lock:
            conn = self._connect()
            self._init_db(conn)
            conn.execute(
                """
                INSERT INTO model_tuning_runs
                (run_id, created_ts, created_iso, status, provider, model, underlying, objective, applied, requested_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, ts, iso, "running", provider, model, underlying, objective, 0, requested_by),
            )
            conn.commit()
            conn.close()
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        score: float | None,
        recommendations: Dict[str, Any] | None,
        notes: str,
        applied: bool,
        applied_by: str | None,
        applied_changes: Dict[str, Any] | None,
    ) -> None:
        applied_ts = _now_epoch() if applied else None
        applied_iso = _iso_from_epoch(applied_ts) if applied_ts else None
        with self.lock:
            conn = self._connect()
            self._init_db(conn)
            conn.execute(
                """
                UPDATE model_tuning_runs
                SET status = ?, score = ?, recommendations_json = ?, notes = ?, applied = ?,
                    applied_ts = ?, applied_iso = ?, applied_by = ?, applied_changes_json = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    score,
                    json.dumps(recommendations or {}),
                    notes,
                    1 if applied else 0,
                    applied_ts,
                    applied_iso,
                    applied_by,
                    json.dumps(applied_changes or {}) if applied_changes is not None else None,
                    run_id,
                ),
            )
            conn.commit()
            conn.close()

    def fail_run(self, run_id: str, error: str) -> None:
        with self.lock:
            conn = self._connect()
            self._init_db(conn)
            conn.execute(
                "UPDATE model_tuning_runs SET status = ?, error = ? WHERE run_id = ?",
                ("error", error, run_id),
            )
            conn.commit()
            conn.close()

    def mark_applied(self, run_id: str, applied_by: str, applied_changes: Dict[str, Any]) -> None:
        applied_ts = _now_epoch()
        applied_iso = _iso_from_epoch(applied_ts)
        with self.lock:
            conn = self._connect()
            self._init_db(conn)
            conn.execute(
                """
                UPDATE model_tuning_runs
                SET applied = ?, applied_ts = ?, applied_iso = ?, applied_by = ?, applied_changes_json = ?
                WHERE run_id = ?
                """,
                (1, applied_ts, applied_iso, applied_by, json.dumps(applied_changes or {}), run_id),
            )
            conn.commit()
            conn.close()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            conn = self._connect()
            self._init_db(conn)
            row = conn.execute(
                """
                SELECT run_id, created_ts, created_iso, status, provider, model, underlying, objective, score,
                       recommendations_json, applied_changes_json, notes, applied, applied_ts,
                       applied_iso, applied_by, requested_by, error
                FROM model_tuning_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            conn.close()
        if not row:
            return None
        return self._row_to_dict(row)

    def fetch_runs(self, limit: int = 20) -> list[Dict[str, Any]]:
        with self.lock:
            conn = self._connect()
            self._init_db(conn)
            rows = conn.execute(
                """
                SELECT run_id, created_ts, created_iso, status, provider, model, underlying, objective, score,
                       recommendations_json, applied_changes_json, notes, applied, applied_ts,
                       applied_iso, applied_by, requested_by, error
                FROM model_tuning_runs
                ORDER BY created_ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            conn.close()
        return [self._row_to_dict(row) for row in rows]

    def latest(self) -> Optional[Dict[str, Any]]:
        runs = self.fetch_runs(limit=1)
        return runs[0] if runs else None

    def _row_to_dict(self, row: tuple[Any, ...]) -> Dict[str, Any]:
        (
            run_id,
            created_ts,
            created_iso,
            status,
            provider,
            model,
            underlying,
            objective,
            score,
            recommendations_json,
            applied_changes_json,
            notes,
            applied,
            applied_ts,
            applied_iso,
            applied_by,
            requested_by,
            error,
        ) = row
        return {
            "run_id": run_id,
            "created_ts": created_ts,
            "created_iso": created_iso,
            "status": status,
            "provider": provider,
            "model": model,
            "underlying": underlying,
            "objective": objective,
            "score": score,
            "recommendations": json.loads(recommendations_json) if recommendations_json else {},
            "applied_changes": json.loads(applied_changes_json) if applied_changes_json else {},
            "notes": notes or "",
            "applied": bool(applied),
            "applied_ts": applied_ts,
            "applied_iso": applied_iso,
            "applied_by": applied_by,
            "requested_by": requested_by,
            "error": error,
        }


class ModelTuningService:
    def __init__(self, config: ModelTunerConfig) -> None:
        self.config = config
        self.store = ModelTunerStore(config.db_path)
        self.queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self.snapshot_lock = threading.Lock()
        self.snapshot_cache: Dict[str, Any] = {}
        self.snapshot_ts = 0.0
        self.snapshot_pending = False

    def update_config(self, config: ModelTunerConfig) -> None:
        self.config = config
        if self.store.db_path != config.db_path:
            self.store = ModelTunerStore(config.db_path)

    def enqueue_run(
        self,
        manager=None,
        objective: str | None = None,
        requested_by: str = "api",
    ) -> tuple[bool, str, Optional[str]]:
        if not self.config.enabled:
            return False, "Model tuner disabled", None
        provider = _normalize_provider(self.config.provider)
        if provider not in ("openai", "anthropic", "ollama"):
            return False, "Unsupported provider", None
        if not _provider_ready(provider, self.config.base_url):
            return False, "Provider API key missing", None
        if not _model_ready(provider, self.config.model):
            return False, "Model not configured", None
        underlying = self._resolve_underlying(manager)
        run_id = self.store.create_run(
            provider,
            self.config.model,
            objective or DEFAULT_OBJECTIVE,
            requested_by,
            underlying,
        )
        self.queue.put(
            {
                "type": "run",
                "run_id": run_id,
                "objective": objective or DEFAULT_OBJECTIVE,
                "manager": manager,
                "underlying": underlying,
            }
        )
        return True, "Queued", run_id

    def notify_trade_exit(self) -> None:
        if not self.config.enabled:
            return
        now = _now_epoch()
        if now - self.snapshot_ts < 10:
            return
        if self.snapshot_pending:
            return
        self.snapshot_pending = True
        self.queue.put({"type": "snapshot"})

    def get_status(self, manager=None, scheduler=None) -> Dict[str, Any]:
        schedule = scheduler.get_schedule_info() if scheduler else {"enabled": False}
        current = self._current_config_snapshot(manager)
        agent_running = bool(getattr(manager, "agent", None)) if manager else False
        paper_mode = None
        if manager and manager.agent:
            paper_mode = bool(manager.agent.agent_config.paper_mode)
        return {
            "enabled": self.config.enabled,
            "provider": self.config.provider,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "auto_apply_paper": self.config.auto_apply_paper,
            "min_trades": self.config.min_trades,
            "apply_clamps": self.config.apply_clamps,
            "notify_email": self.config.notify_email,
            "notify_telegram": self.config.notify_telegram,
            "underlying": self.config.underlying,
            "paper_mode": paper_mode,
            "agent_running": agent_running,
            "last_run": self.store.latest(),
            "schedule": schedule,
            "current": current,
        }

    def get_runs(self, limit: int = 20) -> list[Dict[str, Any]]:
        return self.store.fetch_runs(limit)

    def apply_recommendation(self, run_id: str, manager, applied_by: str = "manual") -> tuple[bool, str]:
        if not manager or not manager.agent:
            return False, "Auto scalper not running"
        run = self.store.get_run(run_id)
        if not run:
            return False, "Recommendation not found"
        changes = run.get("recommendations") or {}
        safe_changes = self._clamp_changes(changes)
        if not safe_changes:
            return False, "No valid changes to apply"
        ok, message = manager.apply_model_tuning(safe_changes)
        if ok:
            self.store.mark_applied(run_id, applied_by, safe_changes)
        return ok, message

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                task = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if task.get("type") == "snapshot":
                    self._refresh_snapshot()
                    self.snapshot_pending = False
                elif task.get("type") == "run":
                    self._execute_run(task)
            except Exception as exc:
                logger.debug("Model tuner task error: %s", exc)

    def _refresh_snapshot(self, underlying: str | None = None) -> None:
        log_store = get_auto_trade_log_store()
        learning_store = get_learning_store()
        analytics = log_store.analytics(
            limit=2000, interval_min=10, bucket=50, underlying=underlying
        )
        learning_summary = learning_store.summary(limit=500)
        recent_trades = learning_store.fetch_trades(limit=80)
        if underlying:
            recent_trades = [
                trade
                for trade in recent_trades
                if str(trade.get("symbol") or "").upper().startswith(underlying)
            ]
        trimmed_trades = [
            {
                "id": trade.get("id"),
                "ts_entry": trade.get("ts_entry"),
                "ts_exit": trade.get("ts_exit"),
                "side": trade.get("side"),
                "symbol": trade.get("symbol"),
                "quantity": trade.get("quantity"),
                "pnl": trade.get("pnl"),
                "reason": trade.get("reason"),
                "playbook": trade.get("playbook"),
                "mode": trade.get("mode"),
            }
            for trade in recent_trades
        ]
        snapshot = {
            "underlying": underlying,
            "analytics": {
                "summary": analytics.get("summary"),
                "distribution": analytics.get("distribution", [])[:20],
                "side_breakdown": analytics.get("side_breakdown", []),
                "reason_breakdown": analytics.get("reason_breakdown", []),
                "time_buckets": analytics.get("time_buckets", [])[:24],
            },
            "learning_summary": learning_summary,
            "recent_trades": trimmed_trades,
        }
        with self.snapshot_lock:
            self.snapshot_cache = snapshot
            self.snapshot_ts = _now_epoch()

    def _get_snapshot(self, underlying: str | None = None) -> Dict[str, Any]:
        with self.snapshot_lock:
            snapshot = dict(self.snapshot_cache)
        if snapshot and (not underlying or snapshot.get("underlying") == underlying):
            return snapshot
        self._refresh_snapshot(underlying)
        with self.snapshot_lock:
            return dict(self.snapshot_cache)

    def _current_config_snapshot(self, manager) -> Dict[str, Any]:
        if manager and manager.agent:
            playbook = manager.agent.playbook_manager.base
            agent_cfg = manager.agent.agent_config
            return {
                "momentum_ticks": playbook.momentum_ticks,
                "tp_points": playbook.tp_points,
                "sl_points": playbook.sl_points,
                "trail_distance": playbook.trail_distance,
                "trail_step": playbook.trail_step,
                "index_bias_min_score": agent_cfg.index_bias_min_score,
                "index_rsi_bull": agent_cfg.index_rsi_bull,
                "index_rsi_bear": agent_cfg.index_rsi_bear,
                "index_adx_min": agent_cfg.index_adx_min,
                "index_vwap_buffer": agent_cfg.index_vwap_buffer,
                "paper_mode": agent_cfg.paper_mode,
            }
        payload = getattr(manager, "last_config", {}) if manager else {}
        return {
            "momentum_ticks": payload.get("momentum_ticks"),
            "tp_points": payload.get("tp_points"),
            "sl_points": payload.get("sl_points"),
            "trail_distance": payload.get("trail_distance"),
            "trail_step": payload.get("trail_step"),
            "index_bias_min_score": payload.get("index_bias_min_score"),
            "index_rsi_bull": payload.get("index_rsi_bull"),
            "index_rsi_bear": payload.get("index_rsi_bear"),
            "index_adx_min": payload.get("index_adx_min"),
            "index_vwap_buffer": payload.get("index_vwap_buffer"),
        }

    def _build_context(self, manager, objective: str, underlying: str | None) -> Dict[str, Any]:
        snapshot = self._get_snapshot(underlying)
        constraints = {
            "allowed_fields": {
                name: {"min": spec[0], "max": spec[1]} for name, spec in _TUNER_CLAMPS.items()
            }
        }
        return {
            "objective": objective,
            "underlying": underlying,
            "constraints": constraints,
            "current_config": self._current_config_snapshot(manager),
            "analytics": snapshot.get("analytics"),
            "learning_summary": snapshot.get("learning_summary"),
            "recent_trades": snapshot.get("recent_trades"),
            "instructions": (
                "Reply ONLY with JSON: {\"changes\":{...},\"notes\":\"...\"}. "
                "Use only fields from allowed_fields and keep values within min/max."
            ),
        }

    def _execute_run(self, task: Dict[str, Any]) -> None:
        run_id = task.get("run_id")
        objective = task.get("objective") or DEFAULT_OBJECTIVE
        manager = task.get("manager")
        underlying = task.get("underlying")
        provider = _normalize_provider(self.config.provider)
        try:
            context = self._build_context(manager, objective, underlying)
            summary = (context.get("analytics") or {}).get("summary") or {}
            total_trades = int(summary.get("total_trades") or 0)
            if total_trades < self.config.min_trades:
                self.store.fail_run(run_id, "Insufficient trades for tuning")
                return
            advisor_cfg = AdvisorConfig(
                enabled=True,
                auto_apply=False,
                provider=provider,
                model=self.config.model,
                base_url=self.config.base_url,
                timeout_s=self.config.timeout_s,
            )
            advisor = build_advisor(advisor_cfg)
            update = advisor.get_update(context)
            if not update:
                self.store.fail_run(run_id, "No response from model")
                return
            raw_changes = update.changes or {}
            safe_changes = self._clamp_changes(raw_changes)
            score = self._score_snapshot(context.get("analytics", {}).get("summary") or {})
            notes = update.notes or ""
            applied = False
            applied_by = None
            applied_changes = None
            if (
                safe_changes
                and self.config.auto_apply_paper
                and manager
                and manager.agent
                and manager.agent.agent_config.paper_mode
            ):
                ok, _ = manager.apply_model_tuning(safe_changes)
                applied = ok
                if ok:
                    applied_by = "auto"
                    applied_changes = safe_changes
            self.store.finish_run(
                run_id=run_id,
                status="success",
                score=score,
                recommendations=safe_changes or raw_changes,
                notes=notes,
                applied=applied,
                applied_by=applied_by,
                applied_changes=applied_changes,
            )
            if safe_changes:
                self._notify_recommendation(
                    manager=manager,
                    changes=safe_changes,
                    notes=notes,
                    summary=summary,
                    underlying=underlying,
                )
        except Exception as exc:
            self.store.fail_run(run_id, str(exc))

    def _clamp_changes(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        if not changes:
            return {}
        safe: Dict[str, Any] = {}
        for key, value in changes.items():
            if key not in _TUNER_CLAMPS:
                continue
            min_val, max_val, cast = _TUNER_CLAMPS[key]
            try:
                coerced = cast(value)
            except (TypeError, ValueError):
                continue
            if not self.config.apply_clamps:
                safe[key] = coerced
                continue
            clamped = max(min_val, min(max_val, coerced))
            if cast is int:
                clamped = int(round(clamped))
            safe[key] = clamped
        return safe

    def _score_snapshot(self, summary: Dict[str, Any]) -> float:
        if not summary:
            return 0.0
        win_rate = float(summary.get("win_rate") or 0.0)
        profit_factor = float(summary.get("profit_factor") or 0.0)
        total_pnl = float(summary.get("total_pnl") or 0.0)
        max_dd = float(summary.get("max_drawdown") or 0.0)
        score = win_rate * 0.6 + min(profit_factor, 2.5) * 20.0
        if total_pnl < 0:
            score -= 10.0
        if total_pnl > 0 and max_dd > 0:
            score -= min(20.0, (max_dd / max(abs(total_pnl), 1.0)) * 10.0)
        return round(max(0.0, min(100.0, score)), 1)

    def _resolve_underlying(self, manager) -> Optional[str]:
        target = (self.config.underlying or "AUTO").upper()
        if target != "AUTO":
            return target
        if manager and manager.agent:
            underlying = getattr(manager.agent.agent_config, "underlying", None)
            return str(underlying).upper() if underlying else None
        payload = getattr(manager, "last_config", {}) if manager else {}
        underlying = payload.get("underlying")
        return str(underlying).upper() if underlying else None

    def _resolve_username(self, manager) -> Optional[str]:
        api_key = None
        if manager and manager.agent:
            api_key = manager.agent.api_key
        if not api_key and manager:
            payload = getattr(manager, "last_config", {}) or {}
            api_key = payload.get("apikey") or payload.get("api_key")
        if not api_key:
            return None
        return get_username_by_apikey(api_key)

    def _notify_recommendation(
        self,
        manager,
        changes: Dict[str, Any],
        notes: str,
        summary: Dict[str, Any],
        underlying: str | None,
    ) -> None:
        try:
            if not self.config.notify_email and not self.config.notify_telegram:
                return
            username = self._resolve_username(manager)
            if not username:
                return
            header = "New AI Scalper tuning suggestion"
            if underlying:
                header += f" ({underlying})"
            change_lines = "\n".join([f"- {k}: {v}" for k, v in changes.items()])
            win_rate = summary.get("win_rate")
            total_trades = summary.get("total_trades")
            text = (
                f"{header}\n\n"
                f"{change_lines}\n\n"
                f"Notes: {notes or 'None'}\n"
                f"Win rate: {win_rate}% | Trades: {total_trades}\n"
            )
            if self.config.notify_email:
                email = get_email_by_username(username)
                if email:
                    subject = header
                    html = (
                        "<h3>AI Scalper Tuning Suggestion</h3>"
                        f"<p><strong>Underlying:</strong> {underlying or '--'}</p>"
                        "<ul>"
                        + "".join([f"<li><strong>{k}</strong>: {v}</li>" for k, v in changes.items()])
                        + "</ul>"
                        f"<p><strong>Notes:</strong> {notes or 'None'}</p>"
                        f"<p><strong>Win rate:</strong> {win_rate}% | <strong>Trades:</strong> {total_trades}</p>"
                    )
                    send_email(email, subject, text, html_content=html)
            if self.config.notify_telegram:
                telegram_user = get_telegram_user_by_username(username)
                if telegram_user and telegram_user.get("notifications_enabled"):
                    telegram_id = telegram_user["telegram_id"]
                    telegram_alert_service.send_alert_sync(telegram_id, text)
        except Exception as exc:
            logger.debug("Model tuner notification failed: %s", exc)


_model_tuning_service: Optional[ModelTuningService] = None
_model_tuning_lock = threading.Lock()


def get_model_tuning_service(config: Optional[ModelTunerConfig] = None) -> ModelTuningService:
    global _model_tuning_service
    with _model_tuning_lock:
        if _model_tuning_service is None:
            _model_tuning_service = ModelTuningService(config or ModelTunerConfig())
        elif config is not None:
            _model_tuning_service.update_config(config)
        return _model_tuning_service
