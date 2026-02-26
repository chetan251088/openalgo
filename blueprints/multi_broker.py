from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request, session

from database.auth_db import get_api_key_for_tradingview
from utils.logging import get_logger

logger = get_logger(__name__)

multi_broker_bp = Blueprint("multi_broker", __name__, url_prefix="/api/multibroker")

BROKER_IDS = ("kotak", "dhan", "zerodha")
DEFAULT_BROKER_URLS = {
    "kotak": "http://127.0.0.1:5000",
    "dhan": "http://127.0.0.1:5001",
    "zerodha": "http://127.0.0.1:5002",
}
FLASK_TO_WS_PORT = {
    "5000": "8765",
    "5001": "8766",
    "5002": "8767",
}
FEED_MODES = {"auto", "dhan", "zerodha"}
API_KEY_CACHE_SESSION_KEY = "_multi_broker_target_api_keys"


def _parse_cache_ttl_seconds() -> int:
    raw = os.getenv("MULTI_BROKER_APIKEY_CACHE_TTL_S", "120")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 120


API_KEY_CACHE_TTL_SECONDS = _parse_cache_ttl_seconds()


def _normalize_base_url(url: str) -> str:
    return url.strip().rstrip("/")


def _get_broker_base_urls() -> dict[str, str]:
    urls: dict[str, str] = {}
    for broker in BROKER_IDS:
        env_key = f"MULTI_BROKER_{broker.upper()}_URL"
        configured = os.getenv(env_key, DEFAULT_BROKER_URLS[broker])
        urls[broker] = _normalize_base_url(configured)
    return urls


def _require_session_user() -> tuple[bool, str | None]:
    username = session.get("user")
    if not username:
        return False, None
    return True, username


def _read_cached_target_openalgo_apikey(broker: str, username: str | None = None) -> str | None:
    if API_KEY_CACHE_TTL_SECONDS <= 0:
        return None

    cache = session.get(API_KEY_CACHE_SESSION_KEY)
    if not isinstance(cache, dict):
        return None

    entry = cache.get(broker)
    if not isinstance(entry, dict):
        return None

    api_key = str(entry.get("api_key", "")).strip()
    if not api_key:
        return None

    cached_user = str(entry.get("username", "")).strip()
    if username and cached_user and cached_user != username:
        return None

    cached_at_raw = entry.get("cached_at")
    try:
        cached_at = float(cached_at_raw)
    except (TypeError, ValueError):
        return None
    if cached_at <= 0:
        return None

    if (time.time() - cached_at) > API_KEY_CACHE_TTL_SECONDS:
        return None

    return api_key


def _write_cached_target_openalgo_apikey(broker: str, api_key: str, username: str | None = None) -> None:
    if API_KEY_CACHE_TTL_SECONDS <= 0:
        return

    normalized_key = str(api_key).strip()
    if not normalized_key:
        return

    cache = session.get(API_KEY_CACHE_SESSION_KEY)
    if not isinstance(cache, dict):
        cache = {}

    cache[broker] = {
        "api_key": normalized_key,
        "username": str(username or ""),
        "cached_at": time.time(),
    }
    session[API_KEY_CACHE_SESSION_KEY] = cache
    session.modified = True


def _clear_cached_target_openalgo_apikey(broker: str) -> None:
    cache = session.get(API_KEY_CACHE_SESSION_KEY)
    if not isinstance(cache, dict) or broker not in cache:
        return

    cache.pop(broker, None)
    session[API_KEY_CACHE_SESSION_KEY] = cache
    session.modified = True


def _resolve_ws_url(broker: str, base_url: str) -> str:
    explicit = os.getenv(f"MULTI_BROKER_{broker.upper()}_WS_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    flask_port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
    ws_port = FLASK_TO_WS_PORT.get(flask_port, os.getenv("WEBSOCKET_PORT", "8765"))
    return f"{scheme}://{host}:{ws_port}"


def _resolve_target_openalgo_apikey(broker: str, base_url: str, username: str | None = None) -> str | None:
    """
    Resolve the OpenAlgo API key for the target broker instance.

    Priority:
    1) Explicit env override (useful for headless deployments)
    2) Target instance `/api/websocket/apikey` using forwarded browser cookies
    3) Local DB fallback for current user (works only in shared-DB setups)
    """
    # Optional env overrides (support both names).
    env_candidates = (
        f"MULTI_BROKER_{broker.upper()}_OPENALGO_API_KEY",
        f"MULTI_BROKER_{broker.upper()}_OPENALGO_APIKEY",
    )
    for key in env_candidates:
        value = (os.getenv(key) or "").strip()
        if value:
            return value

    cached_key = _read_cached_target_openalgo_apikey(broker, username=username)
    if cached_key:
        return cached_key

    # Try target instance session-backed API key endpoint.
    cookie_header = request.headers.get("Cookie", "")
    forward_headers = {"Cookie": cookie_header} if cookie_header else {}
    try:
        response = requests.get(
            f"{base_url}/api/websocket/apikey",
            headers=forward_headers,
            timeout=4,
        )
        if response.status_code == 200:
            data = response.json() if response.content else {}
            api_key = str(data.get("api_key", "")).strip()
            if data.get("status") == "success" and api_key:
                _write_cached_target_openalgo_apikey(
                    broker, api_key, username=username
                )
                return api_key
    except requests.RequestException:
        pass
    except ValueError:
        pass

    # Fallback for shared-database deployments.
    if username:
        try:
            api_key = get_api_key_for_tradingview(username)
            if api_key:
                _write_cached_target_openalgo_apikey(
                    broker, api_key, username=username
                )
                return api_key
        except Exception:
            pass

    return None


@multi_broker_bp.route("/config", methods=["GET"], strict_slashes=False)
def get_multi_broker_config():
    ok, _ = _require_session_user()
    if not ok:
        return jsonify({"status": "error", "message": "Not authenticated"}), 401

    broker_urls = _get_broker_base_urls()
    data = {}
    for broker, base_url in broker_urls.items():
        data[broker] = {
            "base_url": base_url,
            "websocket_url": _resolve_ws_url(broker, base_url),
        }

    return jsonify({"status": "success", "brokers": data}), 200


@multi_broker_bp.route("/ws-config", methods=["POST"], strict_slashes=False)
def get_multi_broker_ws_config():
    ok, username = _require_session_user()
    if not ok or not username:
        return jsonify({"status": "error", "message": "Not authenticated"}), 401

    payload = request.get_json(silent=True) or {}
    feed = str(payload.get("feed", "auto")).strip().lower()
    if feed not in FEED_MODES:
        return (
            jsonify({"status": "error", "message": "feed must be one of: auto, zerodha, dhan"}),
            400,
        )

    broker_urls = _get_broker_base_urls()
    sequence = ["zerodha", "dhan"] if feed == "auto" else [feed]
    targets = []
    for target_broker in sequence:
        target_base_url = broker_urls[target_broker]
        target_api_key = _resolve_target_openalgo_apikey(
            target_broker, target_base_url, username=username
        )
        if not target_api_key:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": (
                            f"API key unavailable for {target_broker}. "
                            f"Login to {target_base_url} and generate API key on /apikey."
                        ),
                    }
                ),
                401,
            )

        targets.append(
            {
                "broker": target_broker,
                "websocket_url": _resolve_ws_url(target_broker, target_base_url),
                "api_key": target_api_key,
            }
        )

    return (
        jsonify(
            {
                "status": "success",
                "feed": feed,
                # Keep top-level key for backwards compatibility.
                "api_key": targets[0]["api_key"] if targets else "",
                "targets": targets,
            }
        ),
        200,
    )


@multi_broker_bp.route("/v1", methods=["POST"], strict_slashes=False)
def proxy_multi_broker_v1():
    ok, username = _require_session_user()
    if not ok or not username:
        return jsonify({"status": "error", "message": "Not authenticated"}), 401

    payload = request.get_json(silent=True) or {}
    broker = str(payload.get("broker", "")).strip().lower()
    path = str(payload.get("path", "")).strip().lstrip("/")
    method = str(payload.get("method", "POST")).strip().upper()
    body = payload.get("payload", None)
    params = payload.get("params", None)

    if broker not in BROKER_IDS:
        return jsonify({"status": "error", "message": "Unsupported broker"}), 400
    if not path:
        return jsonify({"status": "error", "message": "path is required"}), 400
    if method not in {"GET", "POST", "PUT", "DELETE"}:
        return jsonify({"status": "error", "message": "Unsupported method"}), 400

    timeout_ms = payload.get("timeout_ms", 8000)
    try:
        timeout_ms_num = int(timeout_ms)
    except (TypeError, ValueError):
        timeout_ms_num = 8000
    timeout_seconds = max(1, min(timeout_ms_num, 30000)) / 1000

    broker_urls = _get_broker_base_urls()
    target_base_url = broker_urls[broker]
    target_url = f"{target_base_url}/api/v1/{path}"

    proxied_body = body
    if isinstance(proxied_body, dict) and "apikey" in proxied_body:
        target_api_key = _resolve_target_openalgo_apikey(
            broker, target_base_url, username=username
        )
        if not target_api_key:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": (
                            f"Target API key unavailable for {broker}. "
                            f"Login to {target_base_url} and generate API key on /apikey."
                        ),
                    }
                ),
                401,
            )
        proxied_body = dict(proxied_body)
        proxied_body["apikey"] = target_api_key

    forward_headers = {}
    cookie_header = request.headers.get("Cookie", "")
    if cookie_header:
        # Preserve all session cookies so target instance can resolve user context if needed.
        forward_headers["Cookie"] = cookie_header

    try:
        response = requests.request(
            method=method,
            url=target_url,
            json=proxied_body if method != "GET" else None,
            params=params if isinstance(params, dict) else None,
            headers=forward_headers or None,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        logger.warning("Multi-broker proxy error for %s %s: %s", broker, path, exc)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Failed to reach {broker} instance",
                }
            ),
            502,
        )

    if response.status_code >= 500:
        logger.warning(
            "Multi-broker upstream failure broker=%s path=%s status=%s",
            broker,
            path,
            response.status_code,
        )

    try:
        proxied_json = response.json()

        if (
            response.status_code in {401, 403}
            and isinstance(proxied_body, dict)
            and "apikey" in proxied_body
            and isinstance(proxied_json, dict)
        ):
            message = str(proxied_json.get("message", "")).lower()
            if "apikey" in message or "api key" in message:
                _clear_cached_target_openalgo_apikey(broker)

        if response.status_code >= 500 and isinstance(proxied_json, dict):
            proxied_json = dict(proxied_json)
            proxied_json["proxy_broker"] = broker
            proxied_json["proxy_path"] = path

        return jsonify(proxied_json), response.status_code
    except ValueError:
        snippet = (response.text or "")[:500]
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Non-JSON response from {broker}",
                    "proxy_broker": broker,
                    "proxy_path": path,
                    "raw": snippet,
                }
            ),
            502,
        )
