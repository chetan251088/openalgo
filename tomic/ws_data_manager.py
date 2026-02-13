"""
TOMIC WebSocket Data Manager — Role-Based Market Data Consumer
================================================================
Consumes live data from OpenAlgo's WebSocket proxy using role-based config.
Regime/Sniper → QUOTE mode, Execution → DEPTH mode.
Auto-failover with cooldown per freshness gate.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import suppress
from typing import Any, Callable, Dict, List, Optional, Union

import websocket

from tomic.config import TomicConfig
from tomic.freshness import FreshnessTracker

logger = logging.getLogger(__name__)

SubscriptionInput = Union[str, Dict[str, str]]


class WSDataManager:
    """
    Manages WebSocket connections to OpenAlgo's proxy.

    Proxy protocol highlights:
      - Client authenticates using: {"action":"authenticate","api_key":"..."}
      - Market data frames arrive as: {"type":"market_data", "symbol", "exchange", "mode", "data": {...}}
      - Subscription payload expects symbols as [{"symbol":"...", "exchange":"..."}]
    """

    _MODE_INT_TO_STR = {1: "LTP", 2: "QUOTE", 3: "DEPTH"}
    _MODE_STR_TO_INT = {"LTP": 1, "QUOTE": 2, "DEPTH": 3}
    _NSE_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX"}
    _BSE_INDEX_SYMBOLS = {"SENSEX", "BANKEX"}

    def __init__(
        self,
        config: TomicConfig,
        freshness_tracker: Optional[FreshnessTracker] = None,
    ):
        self._config = config
        self._endpoints = config.endpoints
        self._freshness = freshness_tracker

        self._primary_url = (self._endpoints.feed_primary_ws or "").strip()
        self._fallback_url = (self._endpoints.feed_fallback_ws or "").strip()
        self._current_url = self._primary_url or self._fallback_url

        shared_feed_api_key = (
            os.getenv("TOMIC_FEED_API_KEY", "").strip()
            or (self._endpoints.execution_api_key or "").strip()
            or os.getenv("OPENALGO_API_KEY", "").strip()
            or os.getenv("API_KEY", "").strip()
        )
        self._primary_feed_api_key = (
            os.getenv("TOMIC_FEED_PRIMARY_API_KEY", "").strip()
            or shared_feed_api_key
        )
        self._fallback_feed_api_key = (
            os.getenv("TOMIC_FEED_FALLBACK_API_KEY", "").strip()
            or self._primary_feed_api_key
        )

        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._authenticated = False
        self._using_fallback = False
        self._last_message_wall = 0.0
        self._last_error = ""
        self._last_error_wall = 0.0
        self._last_auth_message = ""
        self._last_auth_status = ""

        self._subscriptions: Dict[str, Dict[str, str]] = {}
        self._mode: str = "QUOTE"  # LTP | QUOTE | DEPTH
        self._tick_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}

        self._reconnect_count = 0
        self._max_reconnects = 10
        self._reconnect_delay = 1.0

        self._lock = threading.Lock()

        if not self._primary_url and self._fallback_url:
            self._using_fallback = True

    # -----------------------------------------------------------------------
    # Configuration
    # -----------------------------------------------------------------------

    def subscribe(self, symbols: List[SubscriptionInput], mode: str = "QUOTE") -> None:
        """Set full subscription list. Symbols can be strings or {symbol, exchange} dicts."""
        normalized = self._normalize_symbols(symbols)
        mode_norm = self._normalize_mode(mode)

        with self._lock:
            self._subscriptions = {
                self._subscription_key(item["symbol"], item["exchange"]): item
                for item in normalized
            }
            self._mode = mode_norm

        logger.info("WSDataManager: subscribe %d symbols, mode=%s", len(normalized), mode_norm)
        if normalized and self._connected and self._authenticated and self._ws:
            self._send_subscribe(normalized)

    def set_tick_callback(self, callback: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        """Set callback function for incoming market ticks."""
        self._tick_callback = callback

    def add_symbols(self, symbols: List[SubscriptionInput]) -> None:
        """Add symbols to active subscription (hot-add)."""
        normalized = self._normalize_symbols(symbols)
        if not normalized:
            return

        newly_added: List[Dict[str, str]] = []
        with self._lock:
            for item in normalized:
                key = self._subscription_key(item["symbol"], item["exchange"])
                if key not in self._subscriptions:
                    self._subscriptions[key] = item
                    newly_added.append(item)

        if newly_added and self._connected and self._authenticated and self._ws:
            self._send_subscribe(newly_added)

    def remove_symbols(self, symbols: List[SubscriptionInput]) -> None:
        """Remove symbols from active subscription."""
        normalized = self._normalize_symbols(symbols)
        if not normalized:
            return

        removed: List[Dict[str, str]] = []
        with self._lock:
            for item in normalized:
                key = self._subscription_key(item["symbol"], item["exchange"])
                existing = self._subscriptions.pop(key, None)
                if existing:
                    removed.append(existing)

        if removed and self._connected and self._authenticated and self._ws:
            self._send_unsubscribe(removed)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start WebSocket connection in background thread."""
        if self._running:
            return

        if not self._current_url:
            logger.warning("WSDataManager start skipped: no feed websocket URL configured")
            return

        self._using_fallback = bool(not self._primary_url and self._fallback_url)
        self._running = True
        self._reconnect_count = 0
        self._thread = threading.Thread(
            target=self._connection_loop, daemon=True, name="tomic-ws"
        )
        self._thread.start()
        logger.info("WSDataManager started, connecting to %s", self._current_url)

    def stop(self) -> None:
        """Disconnect and stop."""
        self._running = False
        if self._ws:
            with suppress(Exception):
                self._ws.close()
        if self._thread:
            self._thread.join(timeout=5.0)

        self._connected = False
        self._authenticated = False
        logger.info("WSDataManager stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    # -----------------------------------------------------------------------
    # Connection loop with failover
    # -----------------------------------------------------------------------

    def _connection_loop(self) -> None:
        """Main connection loop — handles reconnection and failover."""
        while self._running and self._reconnect_count < self._max_reconnects:
            if not self._current_url:
                logger.warning("WSDataManager waiting for a valid websocket URL")
                time.sleep(2.0)
                continue

            try:
                self._connect(self._current_url)
            except Exception as e:
                logger.error("WS connection error: %s", e)

            if not self._running:
                break

            self._connected = False
            self._authenticated = False
            self._reconnect_count += 1

            # Try failover after primary failures.
            if not self._using_fallback and self._fallback_url and self._reconnect_count >= 3:
                logger.warning("Switching to fallback feed: %s", self._fallback_url)
                self._current_url = self._fallback_url
                self._using_fallback = True
                self._reconnect_count = 0

                if self._freshness:
                    self._freshness.record_feed_switch()

            delay = min(self._reconnect_delay * (2 ** min(self._reconnect_count, 5)), 30.0)
            logger.info("WS reconnecting in %.1fs (attempt %d)", delay, self._reconnect_count)
            time.sleep(delay)

        if self._reconnect_count >= self._max_reconnects:
            logger.critical("WS max reconnects exhausted — feed unavailable")

    def _connect(self, url: str) -> None:
        """Establish WebSocket connection."""
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    # -----------------------------------------------------------------------
    # WebSocket callbacks
    # -----------------------------------------------------------------------

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        """Connection established — authenticate then subscribe."""
        self._connected = True
        self._authenticated = False
        self._reconnect_count = 0
        logger.info("WS connected: %s (fallback=%s)", self._current_url, self._using_fallback)

        if self._feed_api_key:
            self._send_auth()
            return

        # Fallback if proxy auth is disabled.
        logger.warning("WSDataManager: no API key configured, attempting unauthenticated subscribe")
        self._authenticated = True
        self._send_subscribe(self._snapshot_subscriptions())

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """Process incoming control frames and market ticks."""
        self._last_message_wall = time.time()
        try:
            raw = json.loads(message)
        except json.JSONDecodeError:
            return

        if not isinstance(raw, dict):
            return

        msg_type = str(raw.get("type", "")).strip().lower()
        status = str(raw.get("status", "")).strip().lower()

        if msg_type == "auth":
            self._handle_auth(raw)
            return

        # Backward compatibility: some proxy paths emit auth success without type="auth".
        if self._looks_like_legacy_auth_success(raw):
            self._handle_auth(raw)
            return

        # Proxy send_error() frames use {"status":"error","code":"...","message":"..."}.
        if msg_type == "error" or status == "error":
            self._handle_proxy_error(raw)
            return

        tick = self._normalize_market_tick(raw)
        if tick is None:
            return

        tick["_recv_mono"] = time.monotonic()
        tick["_recv_wall"] = time.time()

        symbol = str(tick.get("symbol", "")).upper()
        mode = self._normalize_mode(tick.get("mode"))
        if symbol and self._freshness:
            if mode == "DEPTH":
                self._freshness.update_depth(symbol)
            self._freshness.update_quote(symbol)
            if symbol.endswith("CE") or symbol.endswith("PE"):
                self._freshness.update_option_quote(symbol)

        callback = self._tick_callback
        self._store_latest_tick(tick)
        if callback is not None:
            try:
                callback(tick)
            except Exception as e:
                logger.error("Tick callback error for %s: %s", symbol or "unknown", e)

    def _store_latest_tick(self, tick: Dict[str, Any]) -> None:
        symbol = str(tick.get("symbol", "")).strip().upper()
        exchange = str(tick.get("exchange", "")).strip().upper()
        if not symbol or not exchange:
            return
        payload = tick.get("data", {}) if isinstance(tick.get("data"), dict) else {}
        ltp = self._extract_ltp(payload)
        snapshot = {
            "symbol": symbol,
            "exchange": exchange,
            "mode": self._normalize_mode(tick.get("mode")),
            "ltp": ltp,
            "recv_wall": float(tick.get("_recv_wall", time.time()) or time.time()),
            "data": payload,
        }
        key = self._subscription_key(symbol, exchange)
        with self._lock:
            self._latest_ticks[key] = snapshot

    @staticmethod
    def _extract_ltp(payload: Dict[str, Any]) -> float:
        for key in ("ltp", "last_price", "close", "lp", "price"):
            try:
                value = float(payload.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
        return 0.0

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        """Log WebSocket error."""
        self._last_error = str(error)
        self._last_error_wall = time.time()
        logger.error("WS error: %s", error)
        self._connected = False
        self._authenticated = False

    def _on_close(self, ws: websocket.WebSocketApp, close_code: int, close_msg: str) -> None:
        """Log WebSocket close."""
        self._connected = False
        self._authenticated = False
        logger.warning("WS closed: code=%s msg=%s", close_code, close_msg)

    # -----------------------------------------------------------------------
    # Proxy protocol helpers
    # -----------------------------------------------------------------------

    def _send_auth(self) -> None:
        if not self._ws or not self._feed_api_key:
            return
        self._last_auth_status = "pending"
        self._last_auth_message = ""
        payload = {"action": "authenticate", "api_key": self._feed_api_key}
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:
            logger.error("WS auth send failed: %s", exc)

    def _handle_auth(self, message: Dict[str, Any]) -> None:
        status = str(message.get("status", "")).strip().lower()
        self._last_auth_status = status or "unknown"
        self._last_auth_message = str(message.get("message", "")).strip()
        if status == "success":
            self._authenticated = True
            logger.info("WS authenticated successfully")
            self._send_subscribe(self._snapshot_subscriptions())
            return

        self._authenticated = False
        err = self._last_auth_message or "unknown"
        self._last_error = f"auth_failed: {err}"
        self._last_error_wall = time.time()
        logger.error("WS authentication failed: %s", err)
        if self._ws:
            with suppress(Exception):
                self._ws.close()

    def _looks_like_legacy_auth_success(self, message: Dict[str, Any]) -> bool:
        """Detect auth success control frames that do not include type='auth'."""
        msg_type = str(message.get("type", "")).strip().lower()
        if msg_type:
            return False

        status = str(message.get("status", "")).strip().lower()
        if status != "success":
            return False

        text = str(message.get("message", "")).strip().lower()
        has_auth_text = "auth" in text
        has_auth_fields = any(key in message for key in ("user_id", "supported_features", "broker"))
        return has_auth_text or has_auth_fields

    def _handle_proxy_error(self, message: Dict[str, Any]) -> None:
        """Handle error control frames from proxy and surface auth failures."""
        code = str(message.get("code", "")).strip().upper()
        err = str(message.get("message", "unknown")).strip() or "unknown"
        error_text = f"{code}: {err}" if code else err
        self._last_error = error_text
        self._last_error_wall = time.time()

        auth_error_codes = {"AUTHENTICATION_ERROR", "NOT_AUTHENTICATED"}
        looks_like_auth_error = (
            code in auth_error_codes
            or "AUTH" in code
            or "API KEY" in err.upper()
            or "UNAUTHORIZED" in err.upper()
            or "FORBIDDEN" in err.upper()
        )

        # Treat early control errors as auth failures when API key auth is expected.
        if looks_like_auth_error or (self._feed_api_key and not self._authenticated):
            self._last_auth_status = "error"
            self._last_auth_message = err
            self._authenticated = False
            logger.error("WS authentication error: %s", error_text)
            if self._ws and looks_like_auth_error:
                with suppress(Exception):
                    self._ws.close()
            return

        logger.error("WS proxy error: %s", error_text)

    def _normalize_market_tick(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_type = str(frame.get("type", "")).strip().lower()
        if msg_type and msg_type != "market_data":
            return None

        payload = frame.get("data")
        if not isinstance(payload, dict):
            payload = frame

        symbol = str(frame.get("symbol") or payload.get("symbol") or "").strip().upper()
        exchange = str(frame.get("exchange") or payload.get("exchange") or "").strip().upper()
        if not symbol or not exchange:
            return None

        mode = self._normalize_mode(frame.get("mode", payload.get("mode", self._mode)))
        return {
            "type": "market_data",
            "symbol": symbol,
            "exchange": exchange,
            "mode": mode,
            "data": payload,
        }

    # -----------------------------------------------------------------------
    # Subscription messages
    # -----------------------------------------------------------------------

    def _send_subscribe(self, symbols: List[Dict[str, str]]) -> None:
        """Send subscription message to WebSocket proxy."""
        if not self._ws or not symbols:
            return

        if self._feed_api_key and not self._authenticated:
            logger.debug("WS subscribe deferred until authentication success")
            return

        msg = {
            "action": "subscribe",
            "symbols": symbols,
            "mode": self._mode,
        }
        try:
            self._ws.send(json.dumps(msg))
            logger.info("WS subscribed: %d symbols (%s)", len(symbols), self._mode)
        except Exception as e:
            logger.error("WS subscribe failed: %s", e)

    def _send_unsubscribe(self, symbols: List[Dict[str, str]]) -> None:
        """Send unsubscription message."""
        if not self._ws or not symbols:
            return

        msg = {
            "action": "unsubscribe",
            "symbols": symbols,
            "mode": self._mode,
        }
        try:
            self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.error("WS unsubscribe failed: %s", e)

    # -----------------------------------------------------------------------
    # Normalization
    # -----------------------------------------------------------------------

    def _snapshot_subscriptions(self) -> List[Dict[str, str]]:
        with self._lock:
            return [item.copy() for item in self._subscriptions.values()]

    def _normalize_symbols(self, symbols: List[SubscriptionInput]) -> List[Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for item in symbols or []:
            normalized = self._normalize_symbol(item)
            if normalized is None:
                continue
            key = self._subscription_key(normalized["symbol"], normalized["exchange"])
            out[key] = normalized
        return list(out.values())

    def _normalize_symbol(self, item: SubscriptionInput) -> Optional[Dict[str, str]]:
        symbol = ""
        exchange = ""

        if isinstance(item, dict):
            symbol = str(item.get("symbol", "")).strip().upper()
            exchange = str(item.get("exchange", "")).strip().upper()
        else:
            raw = str(item or "").strip()
            if not raw:
                return None

            if ":" in raw:
                left, right = raw.split(":", 1)
                left_upper = left.strip().upper()
                right_upper = right.strip().upper()
                if self._looks_like_exchange(left_upper):
                    exchange, symbol = left_upper, right_upper
                else:
                    symbol, exchange = left_upper, right_upper
            elif "." in raw:
                left, right = raw.split(".", 1)
                left_upper = left.strip().upper()
                right_upper = right.strip().upper()
                if self._looks_like_exchange(right_upper):
                    symbol, exchange = left_upper, right_upper
                else:
                    symbol, exchange = left_upper, ""
            else:
                symbol = raw.upper()

        if not symbol:
            return None
        if not exchange:
            exchange = self._infer_exchange(symbol)
        return {"symbol": symbol, "exchange": exchange}

    @staticmethod
    def _subscription_key(symbol: str, exchange: str) -> str:
        return f"{exchange}:{symbol}"

    @classmethod
    def _looks_like_exchange(cls, token: str) -> bool:
        return token in {
            "NSE",
            "BSE",
            "NFO",
            "BFO",
            "MCX",
            "NSE_INDEX",
            "BSE_INDEX",
            "NCD",
            "BCD",
        }

    @classmethod
    def _infer_exchange(cls, symbol: str) -> str:
        sym = symbol.upper()
        if sym in cls._NSE_INDEX_SYMBOLS:
            return "NSE_INDEX"
        if sym in cls._BSE_INDEX_SYMBOLS:
            return "BSE_INDEX"
        if sym.endswith("CE") or sym.endswith("PE"):
            if sym.startswith("SENSEX") or sym.startswith("BANKEX") or (sym.startswith("B") and sym[1:7].isdigit()):
                return "BFO"
            return "NFO"
        return "NSE"

    @classmethod
    def _normalize_mode(cls, mode: Any) -> str:
        if isinstance(mode, int):
            return cls._MODE_INT_TO_STR.get(mode, "QUOTE")
        text = str(mode or "").strip().upper()
        if text.isdigit():
            return cls._MODE_INT_TO_STR.get(int(text), "QUOTE")
        return text if text in cls._MODE_STR_TO_INT else "QUOTE"

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Status summary for observability."""
        with self._lock:
            subscribed = len(self._subscriptions)
            last_tick_cache = len(self._latest_ticks)
        last_msg_age = -1.0
        if self._last_message_wall > 0:
            last_msg_age = max(0.0, time.time() - self._last_message_wall)
        last_err_age = -1.0
        if self._last_error_wall > 0:
            last_err_age = max(0.0, time.time() - self._last_error_wall)
        return {
            "connected": self._connected,
            "authenticated": self._authenticated,
            "url": self._current_url,
            "using_fallback": self._using_fallback,
            "subscribed_symbols": subscribed,
            "mode": self._mode,
            "reconnect_count": self._reconnect_count,
            "api_key_configured": bool(self._feed_api_key),
            "using_fallback_api_key": self._using_fallback,
            "last_message_age_s": round(last_msg_age, 2) if last_msg_age >= 0 else -1.0,
            "last_error": self._last_error,
            "last_error_age_s": round(last_err_age, 2) if last_err_age >= 0 else -1.0,
            "last_auth_status": self._last_auth_status,
            "last_auth_message": self._last_auth_message,
            "latest_tick_cache": last_tick_cache,
        }

    def get_last_tick(self, symbol: str, exchange: str = "") -> Optional[Dict[str, Any]]:
        """Return latest cached tick snapshot for symbol/exchange, if any."""
        normalized = self._normalize_symbol({"symbol": symbol, "exchange": exchange} if exchange else symbol)
        if not normalized:
            return None
        key = self._subscription_key(normalized["symbol"], normalized["exchange"])
        with self._lock:
            cached = self._latest_ticks.get(key)
            if cached:
                return dict(cached)

            # Symbol may be available with a different inferred exchange.
            fallback = [value for value in self._latest_ticks.values() if value.get("symbol") == normalized["symbol"]]
            if fallback:
                return dict(fallback[-1])
        return None

    def get_last_price(self, symbol: str, exchange: str = "", max_age_s: float = 15.0) -> float:
        """Return latest cached LTP when tick age <= max_age_s; else 0.0."""
        tick = self.get_last_tick(symbol, exchange)
        if not tick:
            return 0.0
        recv_wall = float(tick.get("recv_wall", 0.0) or 0.0)
        if recv_wall <= 0:
            return 0.0
        age = max(0.0, time.time() - recv_wall)
        if age > max(0.0, float(max_age_s or 0.0)):
            return 0.0
        try:
            ltp = float(tick.get("ltp", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return ltp if ltp > 0 else 0.0

    @property
    def _feed_api_key(self) -> str:
        return self._fallback_feed_api_key if self._using_fallback else self._primary_feed_api_key
