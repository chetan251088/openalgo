from __future__ import annotations

import json
from dataclasses import replace

from tomic.config import TomicConfig
from tomic.freshness import FreshnessTracker
from tomic.ws_data_manager import WSDataManager


class _DummyWS:
    def __init__(self) -> None:
        self.sent = []
        self.closed = False

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def close(self) -> None:
        self.closed = True


def _build_manager(api_key: str = "test_key") -> tuple[WSDataManager, FreshnessTracker]:
    config = TomicConfig.load("sandbox")
    config.endpoints = replace(
        config.endpoints,
        feed_primary_ws="ws://127.0.0.1:8765",
        execution_api_key=api_key,
    )
    freshness = FreshnessTracker(config.freshness)
    manager = WSDataManager(config=config, freshness_tracker=freshness)
    return manager, freshness


def test_auth_then_subscribe_and_market_tick_dispatch(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    manager, freshness = _build_manager(api_key="feed_key")
    dummy = _DummyWS()
    ticks = []

    manager.subscribe([{"symbol": "NIFTY", "exchange": "NSE_INDEX"}], mode="QUOTE")
    manager.set_tick_callback(lambda tick: ticks.append(tick))
    manager._ws = dummy

    manager._on_open(dummy)
    assert dummy.sent[0]["action"] == "authenticate"
    assert dummy.sent[0]["api_key"] == "feed_key"

    manager._on_message(dummy, json.dumps({"type": "auth", "status": "success"}))
    assert dummy.sent[1]["action"] == "subscribe"
    assert dummy.sent[1]["symbols"] == [{"symbol": "NIFTY", "exchange": "NSE_INDEX"}]
    assert dummy.sent[1]["mode"] == "QUOTE"

    manager._on_message(
        dummy,
        json.dumps(
            {
                "type": "market_data",
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "mode": 3,
                "data": {"ltp": 25100.0, "timestamp": 1_700_000_000},
            }
        ),
    )

    assert len(ticks) == 1
    assert ticks[0]["symbol"] == "NIFTY"
    assert ticks[0]["exchange"] == "NSE_INDEX"
    assert ticks[0]["mode"] == "DEPTH"
    assert "_recv_mono" in ticks[0]
    assert "_recv_wall" in ticks[0]

    # Depth/quote gates should pass after one depth frame.
    report = freshness.check_order_gates("NIFTY", needs_depth=True)
    assert report.passed is True
    # Canonical symbol aliases should also pass.
    report_alias = freshness.check_order_gates("NSE_INDEX:NIFTY", needs_depth=True)
    assert report_alias.passed is True


def test_freshness_normalizes_index_alias_from_feed_symbol(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    manager, freshness = _build_manager(api_key="feed_key")
    dummy = _DummyWS()
    manager._ws = dummy

    manager._on_open(dummy)
    manager._on_message(dummy, json.dumps({"type": "auth", "status": "success"}))
    manager._on_message(
        dummy,
        json.dumps(
            {
                "type": "market_data",
                "symbol": "NIFTY 50",
                "exchange": "NSE_INDEX",
                "mode": "QUOTE",
                "data": {"ltp": 25100.0, "timestamp": 1_700_000_001},
            }
        ),
    )

    report = freshness.check_order_gates("NIFTY")
    assert report.passed is True


def test_subscribe_symbol_normalization_and_unsubscribe(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    manager, _ = _build_manager(api_key="")
    dummy = _DummyWS()
    manager._ws = dummy
    manager._connected = True
    manager._authenticated = True

    manager.subscribe(["NSE_INDEX:NIFTY", "BANKNIFTY"], mode="Quote")
    payload = dummy.sent[-1]
    assert payload["action"] == "subscribe"
    assert payload["mode"] == "QUOTE"
    assert {"symbol": "NIFTY", "exchange": "NSE_INDEX"} in payload["symbols"]
    assert {"symbol": "BANKNIFTY", "exchange": "NSE_INDEX"} in payload["symbols"]

    manager.remove_symbols(["NSE_INDEX:NIFTY"])
    assert dummy.sent[-1]["action"] == "unsubscribe"
    assert dummy.sent[-1]["symbols"] == [{"symbol": "NIFTY", "exchange": "NSE_INDEX"}]


def test_auth_error_frame_updates_status_and_closes_socket(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    manager, _ = _build_manager(api_key="bad_key")
    dummy = _DummyWS()
    manager._ws = dummy

    manager._on_open(dummy)
    manager._on_message(
        dummy,
        json.dumps(
            {
                "status": "error",
                "code": "AUTHENTICATION_ERROR",
                "message": "Invalid API key",
            }
        ),
    )

    status = manager.get_status()
    assert status["authenticated"] is False
    assert status["last_auth_status"] == "error"
    assert status["last_auth_message"] == "Invalid API key"
    assert "AUTHENTICATION_ERROR" in status["last_error"]
    assert dummy.closed is True


def test_legacy_auth_success_without_type_is_accepted(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    manager, _ = _build_manager(api_key="feed_key")
    dummy = _DummyWS()
    manager.subscribe([{"symbol": "NIFTY", "exchange": "NSE_INDEX"}], mode="QUOTE")
    manager._ws = dummy

    manager._on_open(dummy)
    manager._on_message(
        dummy,
        json.dumps(
            {
                "status": "success",
                "message": "Authentication successful",
                "broker": "zerodha",
            }
        ),
    )

    status = manager.get_status()
    assert status["authenticated"] is True
    assert status["last_auth_status"] == "success"
    assert dummy.sent[-1]["action"] == "subscribe"


def test_last_price_cache_returns_latest_ltp(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    manager, _ = _build_manager(api_key="")
    dummy = _DummyWS()
    manager._ws = dummy
    manager._connected = True
    manager._authenticated = True

    manager._on_message(
        dummy,
        json.dumps(
            {
                "type": "market_data",
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "mode": "QUOTE",
                "data": {"ltp": 25222.5, "timestamp": 1_700_000_010},
            }
        ),
    )

    ltp = manager.get_last_price("NIFTY", "NSE_INDEX", max_age_s=60.0)
    assert ltp == 25222.5
