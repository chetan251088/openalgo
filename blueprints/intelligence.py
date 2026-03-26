"""
Intelligence Blueprint - API endpoints for the Intelligence Service.
Provides market intelligence data (MiroFish + Sector Rotation + Fundamentals)
to the frontend and other services.

Supports two auth modes:
  1. Session auth (browser users) — checks session["logged_in"]
  2. API key auth (service-to-service) — checks X-Intelligence-Key or ?key= param
     Set INTELLIGENCE_API_KEY in .env to enable service-to-service access.
"""

import os
import logging
import socket
import time
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request, session, current_app

logger = logging.getLogger(__name__)

intelligence_bp = Blueprint("intelligence", __name__, url_prefix="/intelligence")

_SERVICE_KEY = os.getenv("INTELLIGENCE_API_KEY", "")
_SERVICE_PROBES = [
    {"name": "OpenAlgo (Kotak)", "url": "http://127.0.0.1:5000", "key": "kotak", "kind": "http", "health_path": "/health/status"},
    {"name": "OpenAlgo (Dhan)", "url": "http://127.0.0.1:5001", "key": "dhan", "kind": "http", "health_path": "/health/status"},
    {"name": "OpenAlgo (Zerodha)", "url": "http://127.0.0.1:5002", "key": "zerodha", "kind": "http", "health_path": "/health/status"},
    {"name": "MiroFish API", "url": os.getenv("MIROFISH_URL", "http://127.0.0.1:5003"), "key": "mirofish", "kind": "http", "health_path": "/health"},
    {"name": "Sector Rotation", "url": os.getenv("SECTOR_ROTATION_URL", "http://127.0.0.1:8000"), "key": "rotation", "kind": "http", "health_path": "/api/health"},
    {"name": "WS Proxy (Kotak)", "url": "ws://127.0.0.1:8765", "key": "ws_kotak", "kind": "tcp"},
    {"name": "WS Proxy (Dhan)", "url": "ws://127.0.0.1:8766", "key": "ws_dhan", "kind": "tcp"},
    {"name": "WS Proxy (Zerodha)", "url": "ws://127.0.0.1:8767", "key": "ws_zerodha", "kind": "tcp"},
]


def _get_service():
    """Get the IntelligenceService from app extensions."""
    return current_app.extensions.get("intelligence_service")


def _probe_http_service(service_def: dict) -> dict:
    health_url = f"{service_def['url'].rstrip('/')}{service_def.get('health_path', '/health')}"
    started_at = time.perf_counter()

    try:
        response = requests.get(health_url, timeout=3.0, allow_redirects=False)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        status = "online" if response.status_code < 400 else "degraded"

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = response.json()
                payload_status = str(payload.get("status", "")).lower()
                if payload_status in {"warn", "fail", "error"} and status == "online":
                    status = "degraded"
            except ValueError:
                pass

        return {
            "name": service_def["name"],
            "url": service_def["url"],
            "status": status,
            "latencyMs": latency_ms,
            "lastCheck": int(time.time() * 1000),
            "httpStatus": response.status_code,
        }
    except requests.RequestException as exc:
        return {
            "name": service_def["name"],
            "url": service_def["url"],
            "status": "offline",
            "latencyMs": None,
            "lastCheck": int(time.time() * 1000),
            "error": str(exc),
        }


def _probe_tcp_service(service_def: dict) -> dict:
    parsed = urlparse(service_def["url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port

    if port is None:
        return {
            "name": service_def["name"],
            "url": service_def["url"],
            "status": "offline",
            "latencyMs": None,
            "lastCheck": int(time.time() * 1000),
            "error": "Missing port in service definition",
        }

    started_at = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            latency_ms = int((time.perf_counter() - started_at) * 1000)
        return {
            "name": service_def["name"],
            "url": service_def["url"],
            "status": "online",
            "latencyMs": latency_ms,
            "lastCheck": int(time.time() * 1000),
        }
    except OSError as exc:
        return {
            "name": service_def["name"],
            "url": service_def["url"],
            "status": "offline",
            "latencyMs": None,
            "lastCheck": int(time.time() * 1000),
            "error": str(exc),
        }


def _require_auth():
    """Check authentication: session-based OR API-key-based.
    
    Service-to-service callers (Sector Rotation Map, Flow nodes) pass
    the key via X-Intelligence-Key header or ?key= query param.
    Browser users authenticate via Flask session.
    """
    if session.get("logged_in"):
        return None

    if _SERVICE_KEY:
        provided = request.headers.get("X-Intelligence-Key") or request.args.get("key", "")
        if provided == _SERVICE_KEY:
            return None

    return jsonify({"status": "error", "message": "Not authenticated. Use session login or X-Intelligence-Key header."}), 401


@intelligence_bp.route("/status", methods=["GET"])
def intelligence_status():
    """Return full intelligence state including all sources, staleness, and health."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({
            "status": "error",
            "message": "Intelligence service not initialized",
        }), 503

    intel = service.get_intelligence()
    health = service.get_source_health()

    return jsonify({
        "status": "success",
        "data": {
            "intelligence": intel.to_dict() if intel else None,
            "health": health,
        },
    })


@intelligence_bp.route("/refresh", methods=["POST"])
def intelligence_refresh():
    """Trigger a full refresh of all intelligence sources.

    Optional JSON body:
        news: list of {headline, source, time}
        market_data: {vix, nifty, fii_net, ...}
        requirement: str
        symbols: list of stock symbols to screen
    """
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Intelligence service not initialized"}), 503

    data = request.get_json(silent=True) or {}

    try:
        intel = service.refresh(
            news=data.get("news"),
            market_data=data.get("market_data"),
            requirement=data.get("requirement", "Predict NIFTY direction for the next trading session"),
            symbols=data.get("symbols"),
        )
        return jsonify({
            "status": "success",
            "data": intel.to_dict(),
        })
    except Exception as e:
        logger.error("Intelligence refresh failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@intelligence_bp.route("/mirofish", methods=["GET"])
def get_mirofish():
    """Return just the MiroFish prediction signal."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    signal = service.mirofish.get_cached()
    return jsonify({
        "status": "success",
        "data": signal.to_dict() if signal else None,
    })


@intelligence_bp.route("/rotation", methods=["GET"])
def get_rotation():
    """Return the sector rotation signal."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    signal = service.rotation.get_cached()
    return jsonify({
        "status": "success",
        "data": signal.to_dict() if signal else None,
    })


@intelligence_bp.route("/rotation/stock/<symbol>", methods=["GET"])
def get_stock_rotation(symbol: str):
    """Return RRG data for a specific stock."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    sr = service.get_rotation_for_symbol(symbol.upper())
    return jsonify({
        "status": "success",
        "data": sr.to_dict() if sr else None,
    })


@intelligence_bp.route("/fundamentals", methods=["GET"])
def get_fundamentals():
    """Return all cached fundamental profiles."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    signal = service.screener.get_cached()
    return jsonify({
        "status": "success",
        "data": signal.to_dict() if signal else None,
    })


@intelligence_bp.route("/fundamentals/<symbol>", methods=["GET"])
def get_fundamental_profile(symbol: str):
    """Return fundamental profile for a single symbol."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    profile = service.screener._cache.get(symbol.upper())
    if not profile:
        return jsonify({"status": "error", "message": f"No data for {symbol}"}), 404

    return jsonify({
        "status": "success",
        "data": profile.to_dict(),
    })


@intelligence_bp.route("/clearance", methods=["GET"])
def get_clearance():
    """Return the cleared/blocked symbol map."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    signal = service.screener.get_cached()
    if not signal:
        return jsonify({
            "status": "success",
            "data": {"cleared": [], "blocked": {}, "total": 0},
        })

    return jsonify({
        "status": "success",
        "data": {
            "cleared": sorted(signal.cleared_symbols),
            "blocked": signal.blocked_symbols,
            "total": len(signal.profiles),
        },
    })


@intelligence_bp.route("/health", methods=["GET"])
def intelligence_health():
    """Quick health check for the intelligence service."""
    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Not initialized"}), 503

    return jsonify({
        "status": "success",
        "data": service.get_source_health(),
    })


@intelligence_bp.route("/service-health", methods=["GET"])
def service_health():
    """Server-side health checks for all Command Center dependencies.

    Command Center runs in the browser, where cross-port checks on localhost are
    brittle due to CORS and host mismatch between localhost/127.0.0.1. Probe the
    local services from the backend instead and return a normalized status map.
    """
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    results = {}
    for service_def in _SERVICE_PROBES:
        if service_def["kind"] == "tcp":
            result = _probe_tcp_service(service_def)
        else:
            result = _probe_http_service(service_def)
        results[service_def["key"]] = result

    return jsonify({
        "status": "success",
        "data": {
            "services": results,
        },
    })


# ---------------------------------------------------------------------------
# Gate Snapshot — Lightweight precomputed data for scalping tick loop
# ---------------------------------------------------------------------------

@intelligence_bp.route("/snapshot", methods=["GET"])
def get_gate_snapshot():
    """Lightweight endpoint returning only gate-ready fields.
    No narratives, no scenarios — just booleans, multipliers, and bias.
    Designed for high-frequency polling or SSE push."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    snapshot = service.get_gate_snapshot()
    return jsonify({
        "status": "success",
        "data": snapshot.to_dict(),
    })


@intelligence_bp.route("/snapshot/<symbol>/<side>", methods=["GET"])
def get_symbol_gate(symbol: str, side: str):
    """Get the precomputed gate for one symbol+side. Ultra-fast lookup."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    gate = service.get_gate_snapshot().get_gate(symbol.upper(), side.upper())
    return jsonify({
        "status": "success",
        "data": gate.to_dict() if gate else {
            "symbol": symbol, "side": side,
            "allowed": True, "size_multiplier": 1.0,
            "reason": "Not in precomputed universe (fail-open)",
        },
    })


# ---------------------------------------------------------------------------
# Decision Attribution — Trade decision logs for analytics
# ---------------------------------------------------------------------------

@intelligence_bp.route("/decisions", methods=["GET"])
def get_decisions():
    """Return recent trade decisions with full attribution for analytics."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    limit = request.args.get("limit", 100, type=int)
    decisions = service.decision_logger.get_recent_decisions(limit=limit)
    return jsonify({"status": "success", "data": decisions, "count": len(decisions)})


@intelligence_bp.route("/decisions/ablation", methods=["GET"])
def get_ablation_data():
    """Aggregated data for ablation testing — compare P&L by gate configuration."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    return jsonify({"status": "success", "data": service.decision_logger.get_ablation_data()})


# ---------------------------------------------------------------------------
# Kill Switch — Emergency toggle to disable all intelligence gates
# ---------------------------------------------------------------------------

@intelligence_bp.route("/kill-switch", methods=["POST"])
def toggle_kill_switch():
    """Activate or deactivate the intelligence kill switch.

    POST JSON: {"active": true, "reason": "optional reason"}
    When active, all intelligence gates pass through → pure technical trading.
    """
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    data = request.get_json(silent=True) or {}
    active = data.get("active", True)
    reason = data.get("reason", "Manual toggle from UI")

    if active:
        service.activate_kill_switch(reason)
    else:
        service.deactivate_kill_switch()

    return jsonify({
        "status": "success",
        "data": {
            "kill_switch_active": service.kill_switch,
            "reason": reason if active else "",
            "message": "Intelligence gates DISABLED — pure technical mode" if active
                       else "Intelligence gates RE-ENABLED",
        },
    })


@intelligence_bp.route("/kill-switch", methods=["GET"])
def get_kill_switch_status():
    """Check current kill switch state."""
    service = _get_service()
    if not service:
        return jsonify({"status": "error", "message": "Service not initialized"}), 503

    return jsonify({
        "status": "success",
        "data": {
            "active": service.kill_switch,
            "reason": service._kill_switch_reason,
            "activated_at": service._kill_switch_activated_at,
        },
    })
