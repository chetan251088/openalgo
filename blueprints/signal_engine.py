"""
Signal Engine blueprint — Phase 1–3

Execute modes (stored in signal_engine.db):
  OBSERVE — display only, no orders (default)
  MANUAL  — user clicks Confirm to place order
  AUTO    — auto-executes when signal is favorable + toggle is ON

Routes:
  GET  /signal-engine/api/signal          current signal + legs + settings
  POST /signal-engine/api/signal/refresh  force cache bust
  GET  /signal-engine/api/settings        read settings
  POST /signal-engine/api/settings        update settings (incl. toggle)
  POST /signal-engine/api/confirm         manually confirm & place order
  GET  /signal-engine/api/history         recent signal log
"""

import logging
import threading
import time

from flask import Blueprint, jsonify, request, session

log = logging.getLogger(__name__)

signal_engine_bp = Blueprint("signal_engine", __name__, url_prefix="/signal-engine")

_RATE_LIMIT_S = 5.0
_last_ts: dict[str, float] = {}
_db_ready = False

# Background auto-execute loop
_auto_thread: threading.Thread | None = None
_auto_stop = threading.Event()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_auth():
    if not session.get("user"):
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    return None


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    if now - _last_ts.get(key, 0) < _RATE_LIMIT_S:
        return True
    _last_ts[key] = now
    return False


def _ensure_db():
    global _db_ready
    if not _db_ready:
        try:
            from database.signal_engine_db import init_db
            init_db()
            _db_ready = True
        except Exception as exc:
            log.warning("Signal Engine DB init: %s", exc)


# ---------------------------------------------------------------------------
# Signal endpoint — returns signal + legs + execute_mode
# ---------------------------------------------------------------------------

@signal_engine_bp.route("/api/signal", methods=["GET"])
def get_signal():
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    _ensure_db()
    symbol = request.args.get("symbol", "NIFTY").upper()
    exchange = request.args.get("exchange", "NFO").upper()
    try:
        dte = int(request.args.get("dte", "4"))
    except ValueError:
        dte = 4

    try:
        from services.signal_engine_service import get_signal as _svc, get_cached_chain
        from services.signal_engine_execution import build_legs
        from database.signal_engine_db import get_all_settings, has_open_position

        signal = _svc(symbol=symbol, exchange=exchange, dte=dte)

        # Build leg suggestions when there's a viable strategy
        leg_data = {"legs": [], "net_credit": 0, "max_loss": 0,
                    "max_loss_per_lot": 0, "lot_size": 25, "error": None}
        chain = get_cached_chain(symbol, exchange, dte)
        if chain and signal.get("strategy", {}).get("confidence", 0) >= 50:
            leg_data = build_legs(signal, chain)

        settings = get_all_settings()
        execute_mode = settings.get("execute_mode", "OBSERVE")

        return jsonify({
            "status": "success",
            "signal": signal,
            "legs": leg_data,
            "execute_mode": execute_mode,
            "has_open_position": has_open_position(symbol),
            "settings": {
                "execute_mode": execute_mode,
                "default_lots": int(settings.get("default_lots", "1")),
                "max_lots": int(settings.get("max_lots", "3")),
                "risk_pct": float(settings.get("risk_pct", "1.0")),
                "product": settings.get("product", "NRML"),
            },
        })
    except Exception as exc:
        log.error("Signal endpoint error: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Force refresh
# ---------------------------------------------------------------------------

@signal_engine_bp.route("/api/signal/refresh", methods=["POST"])
def refresh_signal():
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    if _rate_limited("refresh"):
        return jsonify({"status": "error", "message": "Rate limited — wait 5s"}), 429

    try:
        from services.signal_engine_service import invalidate_cache
        invalidate_cache()
        return jsonify({"status": "success", "message": "Cache cleared"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@signal_engine_bp.route("/api/settings", methods=["GET"])
def get_settings():
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    _ensure_db()
    try:
        from database.signal_engine_db import get_all_settings
        raw = get_all_settings()
        safe = {k: v for k, v in raw.items() if k != "auto_execute_api_key"}
        return jsonify({"status": "success", "settings": safe})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@signal_engine_bp.route("/api/settings", methods=["POST"])
def update_settings():
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    _ensure_db()
    body = request.get_json(silent=True) or {}
    allowed = {"execute_mode", "default_lots", "max_lots", "risk_pct", "product"}

    if "execute_mode" in body:
        mode = str(body["execute_mode"]).upper()
        if mode not in ("OBSERVE", "MANUAL", "AUTO"):
            return jsonify({"status": "error",
                            "message": "execute_mode must be OBSERVE, MANUAL, or AUTO"}), 400
        body["execute_mode"] = mode

    try:
        from database.signal_engine_db import set_setting, get_all_settings
        for key, val in body.items():
            if key in allowed:
                set_setting(key, str(val))

        # Start / stop auto-loop based on mode
        if "execute_mode" in body:
            if body["execute_mode"] == "AUTO":
                # Persist the user_id so the background thread can retrieve their API key
                set_setting("auto_user_id", str(session.get("user", "")))
                _start_auto_loop()
            else:
                _stop_auto_loop()

        raw = get_all_settings()
        safe = {k: v for k, v in raw.items() if k != "auto_execute_api_key"}
        return jsonify({"status": "success", "settings": safe})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Manual confirm — place order on user request
# ---------------------------------------------------------------------------

@signal_engine_bp.route("/api/confirm", methods=["POST"])
def confirm_order():
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    if _rate_limited("confirm"):
        return jsonify({"status": "error", "message": "Rate limited"}), 429

    _ensure_db()
    body = request.get_json(silent=True) or {}
    symbol   = body.get("symbol", "NIFTY").upper()
    exchange = body.get("exchange", "NFO").upper()
    try:
        dte = int(body.get("dte", 4))
    except (TypeError, ValueError):
        dte = 4

    try:
        from services.signal_engine_service import get_signal as _svc, get_cached_chain
        from services.signal_engine_execution import build_legs, place_orders, calc_lots
        from database.signal_engine_db import (
            get_all_settings, log_signal, update_signal_executed, has_open_position,
        )

        if has_open_position(symbol):
            return jsonify({"status": "error",
                            "message": f"Already have an open position for {symbol}"}), 409

        settings = get_all_settings()
        from database.auth_db import get_api_key_for_tradingview
        api_key = get_api_key_for_tradingview(session.get("user"))
        if not api_key:
            return jsonify({"status": "error",
                            "message": "No API key found. Generate one at /apikey."}), 400

        signal = _svc(symbol=symbol, exchange=exchange, dte=dte)

        # Capital preservation hard stop
        high_flags = [f for f in signal.get("capital_preservation_flags", []) if f["severity"] == "high"]
        if high_flags:
            return jsonify({"status": "error",
                            "message": f"Capital protection stop: {high_flags[0]['rule']}"}), 403

        chain = get_cached_chain(symbol, exchange, dte)
        leg_data = build_legs(signal, chain)
        if not leg_data.get("legs"):
            return jsonify({"status": "error",
                            "message": f"Leg build failed: {leg_data.get('error')}"}), 400

        lots = body.get("lots") or calc_lots(
            leg_data["max_loss_per_lot"],
            default_lots=int(settings.get("default_lots", "1")),
            max_lots=int(settings.get("max_lots", "3")),
        )
        product = settings.get("product", "NRML")

        row_id = log_signal(
            symbol=symbol, exchange=exchange, dte=dte,
            regime=signal.get("regime", ""),
            iv_rank=signal.get("iv_rank"),
            strategy=signal.get("strategy", {}).get("name", ""),
            favorable=signal.get("favorable_to_trade", False),
            executed=False, exec_mode="MANUAL",
            lots=lots, net_credit=leg_data.get("net_credit"),
            legs=leg_data.get("legs"),
        )

        result = place_orders(
            legs=leg_data["legs"], lots=lots, api_key=api_key,
            strategy_tag="SignalEngine", product=product,
        )

        if result["success"]:
            update_signal_executed(row_id, lots, leg_data.get("net_credit"), "MANUAL confirm")

        return jsonify({
            "status": "success" if result["success"] else "partial",
            "order_results": result["results"],
            "errors": result["errors"],
            "lots": lots,
            "net_credit": leg_data.get("net_credit"),
            "legs": leg_data.get("legs"),
        })

    except Exception as exc:
        log.error("confirm_order error: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Signal history
# ---------------------------------------------------------------------------

@signal_engine_bp.route("/api/history", methods=["GET"])
def signal_history():
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    _ensure_db()
    try:
        from database.signal_engine_db import get_recent_signals
        limit = min(int(request.args.get("limit", "20")), 100)
        return jsonify({"status": "success", "records": get_recent_signals(limit)})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Auto-execute background loop
# ---------------------------------------------------------------------------

def _auto_execute_once(symbol: str = "NIFTY", exchange: str = "NFO", dte: int = 4) -> str:
    """One auto-execution cycle. Returns status string."""
    try:
        from services.signal_engine_service import get_signal as _svc, invalidate_cache, get_cached_chain
        from services.signal_engine_execution import build_legs, place_orders, calc_lots
        from database.signal_engine_db import (
            get_all_settings, log_signal, update_signal_executed, has_open_position,
        )

        settings = get_all_settings()
        if settings.get("execute_mode", "OBSERVE") != "AUTO":
            return "not-AUTO"

        from database.auth_db import get_api_key_for_tradingview
        user_id = settings.get("auto_user_id", "")
        api_key = get_api_key_for_tradingview(user_id) if user_id else None
        if not api_key:
            return "no-api-key"

        if has_open_position(symbol):
            return "already-positioned"

        invalidate_cache()
        signal = _svc(symbol=symbol, exchange=exchange, dte=dte)

        if not signal.get("favorable_to_trade"):
            return "not-favorable"

        high_flags = [f for f in signal.get("capital_preservation_flags", []) if f["severity"] == "high"]
        if high_flags:
            return f"preservation-stop:{high_flags[0]['rule']}"

        chain = get_cached_chain(symbol, exchange, dte)
        leg_data = build_legs(signal, chain)
        if not leg_data.get("legs"):
            return f"no-legs:{leg_data.get('error')}"

        default_lots = int(settings.get("default_lots", "1"))
        max_lots = int(settings.get("max_lots", "3"))
        lots = calc_lots(leg_data["max_loss_per_lot"], default_lots=default_lots, max_lots=max_lots)
        product = settings.get("product", "NRML")

        row_id = log_signal(
            symbol=symbol, exchange=exchange, dte=dte,
            regime=signal.get("regime", ""),
            iv_rank=signal.get("iv_rank"),
            strategy=signal.get("strategy", {}).get("name", ""),
            favorable=True, executed=False, exec_mode="AUTO",
            lots=lots, net_credit=leg_data.get("net_credit"),
            legs=leg_data.get("legs"),
        )

        result = place_orders(
            legs=leg_data["legs"], lots=lots, api_key=api_key,
            strategy_tag="SignalEngine-AUTO", product=product,
        )

        if result["success"]:
            update_signal_executed(row_id, lots, leg_data.get("net_credit"), "AUTO")
            return f"executed:{lots}lots"
        return f"order-failed:{result['errors']}"

    except Exception as exc:
        log.error("auto_execute_once error: %s", exc, exc_info=True)
        return f"error:{exc}"


def _auto_loop(interval_s: int = 60) -> None:
    log.info("SE auto-loop started (interval=%ds)", interval_s)
    while not _auto_stop.is_set():
        try:
            status = _auto_execute_once()
            if "executed" in status:
                log.info("SE AUTO executed: %s", status)
            elif status not in ("not-AUTO", "already-positioned", "not-favorable", "no-api-key"):
                log.debug("SE AUTO cycle: %s", status)
        except Exception as exc:
            log.error("SE auto loop error: %s", exc)
        _auto_stop.wait(interval_s)
    log.info("SE auto-loop stopped")


def _start_auto_loop(interval_s: int = 60) -> None:
    global _auto_thread, _auto_stop
    if _auto_thread and _auto_thread.is_alive():
        return
    _auto_stop.clear()
    _auto_thread = threading.Thread(
        target=_auto_loop, args=(interval_s,), daemon=True, name="se-auto-loop"
    )
    _auto_thread.start()


def _stop_auto_loop() -> None:
    _auto_stop.set()


# ---------------------------------------------------------------------------
# Called from app.py after blueprint registration
# ---------------------------------------------------------------------------

def init_signal_engine(app) -> None:
    """Restore AUTO mode if it was active before restart."""
    with app.app_context():
        try:
            _ensure_db()
            from database.signal_engine_db import get_setting
            if get_setting("execute_mode") == "AUTO":
                _start_auto_loop()
                log.info("Signal Engine AUTO mode restored on startup")
        except Exception as exc:
            log.warning("Signal Engine startup init: %s", exc)
