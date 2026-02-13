"""
TOMIC Flask Blueprint — Control & Status APIs
================================================
Auth-protected control APIs: POST /tomic/start|stop|pause
Status/data APIs: GET /tomic/status|positions|journal|analytics|metrics

All control actions logged to audit table.
"""

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request, session

from utils.logging import get_logger

logger = get_logger(__name__)

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "50 per second")

tomic_bp = Blueprint("tomic_bp", __name__, url_prefix="/tomic")

# ---------------------------------------------------------------------------
# Module-level state (initialized on first call, lazy)
# ---------------------------------------------------------------------------
_tomic_runtime = None
_audit_db_path = "db/tomic_audit.db"
_last_position_sync_mono = 0.0
_position_sync_min_interval_s = float(os.getenv("TOMIC_POSITIONS_SYNC_MIN_INTERVAL_S", "2.0"))


def _get_runtime():
    """Lazy-init TOMIC runtime reference."""
    global _tomic_runtime
    return _tomic_runtime


def set_tomic_runtime(runtime):
    """Called during app startup to wire in TOMIC runtime."""
    global _tomic_runtime
    _tomic_runtime = runtime


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_auth():
    """Ensure session is authenticated. Returns error response or None."""
    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    return None


def _audit_log(action: str, details: str = "") -> None:
    """Log control action to audit table."""
    try:
        Path(_audit_db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_audit_db_path, timeout=5.0)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                user_id    TEXT,
                action     TEXT NOT NULL,
                details    TEXT,
                ip_address TEXT
            )
        """)
        conn.execute(
            "INSERT INTO audit_log (user_id, action, details, ip_address) VALUES (?,?,?,?)",
            (
                session.get("user", "unknown"),
                action,
                details,
                request.remote_addr or "unknown",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Audit log failed: %s", e)


def _position_sync_status(
    synced: bool,
    mode: str,
    rows: int = 0,
    message: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "synced": synced,
        "mode": mode,
        "rows": int(rows),
    }
    if message:
        payload["message"] = message
    return payload


def _sync_position_book_from_broker(runtime) -> Dict[str, Any]:
    """
    Reconcile PositionBook from broker snapshot (throttled).

    This keeps /tomic/risk exposure aligned with broker truth instead of stale
    in-memory rows.
    """
    global _last_position_sync_mono

    now = time.monotonic()
    if now - _last_position_sync_mono < _position_sync_min_interval_s:
        return _position_sync_status(
            synced=False,
            mode="throttled",
            message=f"min_interval={_position_sync_min_interval_s:.1f}s",
        )

    execution_agent = getattr(runtime, "execution_agent", None)
    fetch_positions = getattr(execution_agent, "_fetch_broker_positions", None)
    if not callable(fetch_positions):
        return _position_sync_status(
            synced=False,
            mode="unavailable",
            message="execution broker sync unavailable",
        )

    try:
        broker_positions = fetch_positions()
        fetch_error = str(getattr(execution_agent, "_last_broker_positions_error", "") or "").strip()
        if fetch_error:
            return _position_sync_status(
                synced=False,
                mode="error",
                message=fetch_error,
            )
        if not isinstance(broker_positions, list):
            return _position_sync_status(
                synced=False,
                mode="invalid",
                message="broker position payload is not a list",
            )

        runtime.position_book.reconcile(broker_positions)
        _last_position_sync_mono = now
        return _position_sync_status(
            synced=True,
            mode="broker",
            rows=len(broker_positions),
        )
    except Exception as exc:
        logger.warning("TOMIC broker position sync failed: %s", exc)
        return _position_sync_status(
            synced=False,
            mode="error",
            message=str(exc),
        )


def _serialize_open_positions(snapshot) -> List[Dict[str, Any]]:
    """Serialize only open, valid positions for API/UI consumption."""
    serialized: List[Dict[str, Any]] = []
    for pos in snapshot.positions.values():
        instrument = str(getattr(pos, "instrument", "") or "").strip().upper()
        quantity = int(getattr(pos, "quantity", 0) or 0)
        if not instrument or quantity == 0:
            continue

        direction = str(getattr(pos, "direction", "") or "").strip().upper()
        if not direction:
            direction = "BUY" if quantity > 0 else "SELL"

        serialized.append(
            {
                "instrument": instrument,
                "strategy_id": str(getattr(pos, "strategy_id", "") or ""),
                "strategy_tag": str(getattr(pos, "strategy_tag", "") or ""),
                "direction": direction,
                "exchange": str(getattr(pos, "exchange", "") or "").strip().upper(),
                "product": str(getattr(pos, "product", "") or "").strip().upper(),
                "quantity": quantity,
                "avg_price": float(getattr(pos, "avg_price", 0.0) or 0.0),
                "ltp": float(getattr(pos, "ltp", 0.0) or 0.0),
                "pnl": float(getattr(pos, "pnl", 0.0) or 0.0),
                "entry_time": str(getattr(pos, "entry_time", "") or ""),
            }
        )
    return serialized


# ---------------------------------------------------------------------------
# Control endpoints (auth-protected, audited)
# ---------------------------------------------------------------------------

@tomic_bp.route("/start", methods=["POST"])
def start_system():
    """Start the TOMIC multi-agent system."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "TOMIC runtime not initialized"}), 503

    try:
        runtime.start()
        _audit_log("START", "TOMIC system started")
        return jsonify({"status": "success", "message": "TOMIC system started"})
    except Exception as e:
        logger.error("TOMIC start failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/stop", methods=["POST"])
def stop_system():
    """Stop the TOMIC system (safe shutdown)."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "TOMIC runtime not initialized"}), 503

    try:
        runtime.stop()
        _audit_log("STOP", "TOMIC system stopped (safe shutdown)")
        return jsonify({"status": "success", "message": "TOMIC system stopped"})
    except Exception as e:
        logger.error("TOMIC stop failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/pause", methods=["POST"])
def pause_system():
    """Pause the TOMIC system (kill switch)."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "TOMIC runtime not initialized"}), 503

    reason = request.json.get("reason", "Manual pause") if request.is_json else "Manual pause"

    try:
        runtime.kill_switch(reason)
        _audit_log("PAUSE/KILL_SWITCH", f"Reason: {reason}")
        return jsonify({"status": "success", "message": f"TOMIC paused: {reason}"})
    except Exception as e:
        logger.error("TOMIC pause failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/resume", methods=["POST"])
def resume_system():
    """Resume a paused TOMIC system."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "TOMIC runtime not initialized"}), 503

    try:
        runtime.resume()
        _audit_log("RESUME", "TOMIC system resumed")
        return jsonify({"status": "success", "message": "TOMIC system resumed"})
    except Exception as e:
        logger.error("TOMIC resume failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Status endpoints (auth-protected, read-only)
# ---------------------------------------------------------------------------

@tomic_bp.route("/status", methods=["GET"])
def system_status():
    """Get TOMIC system status."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({
            "status": "offline",
            "message": "TOMIC runtime not initialized",
        })

    try:
        return jsonify({
            "status": "success",
            "data": runtime.get_status(),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/positions", methods=["GET"])
def get_positions():
    """Get current TOMIC positions."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    try:
        sync = _sync_position_book_from_broker(runtime)
        snap = runtime.position_book.read_snapshot()
        positions = _serialize_open_positions(snap)
        return jsonify({
            "status": "success",
            "version": snap.version,
            "positions": positions,
            "sync": sync,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/journal", methods=["GET"])
def get_journal():
    """Get recent journal entries."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    limit = request.args.get("limit", 50, type=int)
    try:
        trades = runtime.journaling_agent.get_recent_trades(limit=limit)
        return jsonify({
            "status": "success",
            "trades": trades,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/analytics", methods=["GET"])
def get_analytics():
    """Get performance analytics."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    try:
        metrics = runtime.journaling_agent.get_performance_metrics()
        breakdown = runtime.journaling_agent.get_strategy_breakdown()
        return jsonify({
            "status": "success",
            "metrics": metrics,
            "strategy_breakdown": breakdown,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/metrics", methods=["GET"])
def get_metrics():
    """Get operational metrics (circuit breakers, freshness, latency)."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    try:
        circuit_breakers_data = {}
        if runtime.circuit_breakers:
            if hasattr(runtime.circuit_breakers, "status_summary"):
                circuit_breakers_data = runtime.circuit_breakers.status_summary()
            elif hasattr(runtime.circuit_breakers, "get_status_summary"):
                circuit_breakers_data = runtime.circuit_breakers.get_status_summary()

        freshness_data = {}
        if runtime.freshness_tracker:
            if hasattr(runtime.freshness_tracker, "diagnostic_summary"):
                freshness_data = runtime.freshness_tracker.diagnostic_summary()
            elif hasattr(runtime.freshness_tracker, "get_all_ages"):
                freshness_data = runtime.freshness_tracker.get_all_ages()

        data = {
            "circuit_breakers": circuit_breakers_data,
            "freshness": freshness_data,
            "ws_data": runtime.ws_data_manager.get_status()
                if runtime.ws_data_manager else {},
            "market_bridge": runtime.market_bridge.get_status()
                if hasattr(runtime, "market_bridge") and runtime.market_bridge else {},
        }
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/signals/quality", methods=["GET"])
def get_signal_quality():
    """Get latest signal quality snapshot from Sniper/Volatility/Router."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    run_scan_raw = str(request.args.get("run_scan", "true")).strip().lower()
    run_scan = run_scan_raw not in {"0", "false", "no", "off"}

    try:
        payload = runtime.get_signal_quality(run_scan=run_scan)
        return jsonify({"status": "success", "data": payload})
    except Exception as e:
        logger.error("Signal quality snapshot failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/audit", methods=["GET"])
def get_audit_log():
    """Get recent audit log entries."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    limit = request.args.get("limit", 100, type=int)
    try:
        conn = sqlite3.connect(_audit_db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return jsonify({
            "status": "success",
            "entries": [dict(r) for r in rows],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
