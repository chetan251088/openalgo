from __future__ import annotations

from datetime import date
from dataclasses import replace

from tomic.agents.sniper_agent import PatternType, SniperSignal
from tomic.agents.volatility_agent import VolSignal
from tomic.config import RegimePhase, StrategyType, TomicConfig
from tomic.runtime import TomicRuntime


def test_runtime_signal_quality_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TOMIC_FEED_API_KEY", raising=False)

    config = TomicConfig.load("sandbox")
    config.endpoints = replace(
        config.endpoints,
        feed_primary_ws="",
        feed_fallback_ws="",
        execution_rest="http://127.0.0.1:5000",
        execution_api_key="runtime_test_key",
    )
    runtime = TomicRuntime(config=config, zmq_port=5599)

    runtime.regime_state.update(
        phase=RegimePhase.BULLISH,
        score=8,
        vix=17.5,
        vix_flags=[],
        ichimoku_signal="BULLISH",
        impulse_color="GREEN",
        congestion=False,
        blowoff=False,
    )

    sniper_signal = SniperSignal(
        instrument="NIFTY",
        pattern=PatternType.VCP,
        direction="BUY",
        entry_price=100.0,
        stop_price=95.0,
        signal_score=82.0,
    )
    vol_signal = VolSignal(
        underlying="NIFTY",
        strategy_type=StrategyType.IRON_CONDOR,
        direction="BUY",
        signal_strength=68.0,
        reason="test",
    )

    monkeypatch.setattr(runtime.sniper_agent, "scan", lambda: [sniper_signal])
    monkeypatch.setattr(runtime.volatility_agent, "scan", lambda: [vol_signal])
    monkeypatch.setattr(
        runtime,
        "_market_session_state",
        lambda: (
            True,
            "",
            {
                "enforced": True,
                "open": True,
                "reason": "",
                "offhours_scan_override": False,
                "tz": "Asia/Kolkata",
                "now": "2026-02-13 11:00:00",
                "weekday": "Friday",
                "window": "09:15-15:30",
            },
        ),
    )

    snapshot = runtime.get_signal_quality(run_scan=True)

    assert snapshot["regime"]["phase"] == "BULLISH"
    assert snapshot["signals"]["sniper_count"] == 1
    assert snapshot["signals"]["volatility_count"] == 1
    assert snapshot["signals"]["routed_count"] == 1
    assert snapshot["signals"]["decision_breakdown"]["ACCEPT"] == 1
    assert snapshot["signals"]["top_routed"][0]["instrument"] == "NIFTY"

    cached = runtime.get_signal_quality(run_scan=False)
    assert cached["cached"] is True


def test_runtime_enqueue_dedupe_cooldown(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5600)
    runtime._signal_enqueue_cooldown_s = 60.0

    captured = []
    monkeypatch.setattr(runtime.risk_agent, "enqueue_signal", lambda payload: captured.append(payload))

    class _Routed:
        def __init__(self, signal_dict):
            self.signal_dict = signal_dict
            self.priority_score = 77.5

            class _Action:
                value = "ACCEPT"

            class _Source:
                value = "SNIPER"

            class _Decision:
                reason = "bullish: sniper leads"
                action = _Action()
                source = _Source()

            self.route_decision = _Decision()

    routed = [
        _Routed({"instrument": "NIFTY", "strategy_type": "DITM_CALL", "direction": "BUY"}),
        _Routed({"instrument": "NIFTY", "strategy_type": "DITM_CALL", "direction": "BUY"}),
    ]

    first = runtime._enqueue_routed_signals(routed, source="test")
    second = runtime._enqueue_routed_signals(routed, source="test")

    assert first["enqueued_count"] == 1
    assert first["dedupe_skipped_count"] == 1
    assert second["enqueued_count"] == 0
    assert second["dedupe_skipped_count"] == 2
    assert len(captured) == 1
    assert captured[0]["router_reason"] == "bullish: sniper leads"
    assert captured[0]["router_action"] == "ACCEPT"
    assert captured[0]["router_source"] == "SNIPER"
    assert captured[0]["router_priority_score"] == 77.5


def test_runtime_no_action_reason_shows_auth_pending(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5601)

    monkeypatch.setattr(runtime.sniper_agent, "scan", lambda: [])
    monkeypatch.setattr(runtime.volatility_agent, "scan", lambda: [])

    monkeypatch.setattr(
        runtime.ws_data_manager,
        "get_status",
        lambda: {
            "connected": True,
            "authenticated": False,
            "api_key_configured": True,
            "last_auth_status": "pending",
            "last_auth_message": "",
            "subscribed_symbols": 3,
        },
    )
    monkeypatch.setattr(runtime.market_bridge, "get_status", lambda: {"subscriptions": 3})

    snapshot = runtime.get_signal_quality(run_scan=True)
    reasons = snapshot.get("diagnostics", {}).get("no_action_reasons", [])
    assert any("WS auth pending" in reason for reason in reasons)


def test_runtime_warm_start_seeds_agent_caches(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOMIC_WARM_START_ENABLED", "true")
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5602)

    def fake_fetch(symbol: str, exchange: str, start_date: date, end_date: date):
        rows = [
            {
                "timestamp": 1_700_000_000 + idx * 60,
                "open": 100.0 + idx,
                "high": 101.0 + idx,
                "low": 99.0 + idx,
                "close": 100.5 + idx,
                "volume": 1_000 + idx * 10,
            }
            for idx in range(40)
        ]
        return rows, "api", ""

    monkeypatch.setattr(runtime, "_fetch_history_candles", fake_fetch)

    runtime._prime_market_state_from_history()

    assert len(runtime.regime_agent._closes) > 0
    assert len(runtime.sniper_agent._ohlcv_cache) > 0
    assert len(runtime.volatility_agent._price_cache) > 0
    assert runtime._warm_start_status["status"] == "ok"
    assert runtime._warm_start_status["loaded_symbols"] > 0


def test_runtime_startup_guard_blocks_immediate_enqueue(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5603)
    runtime._started = True
    runtime._runtime_started_wall = 10_000.0
    runtime._runtime_started_mono = 10_000.0
    runtime._startup_enqueue_grace_s = 20.0
    runtime._require_live_tick_after_start = True

    class _Routed:
        def __init__(self, signal_dict):
            self.signal_dict = signal_dict
            self.priority_score = 1.0

            class _Source:
                value = "sniper"

            self.source = _Source()

    monkeypatch.setattr(runtime.sniper_agent, "scan", lambda: [])
    monkeypatch.setattr(runtime.volatility_agent, "scan", lambda: [])
    monkeypatch.setattr(runtime.conflict_router, "route", lambda sniper, vol: [_Routed({"instrument": "NIFTY", "strategy_type": "DITM_CALL", "direction": "BUY"})])
    monkeypatch.setattr(runtime.conflict_router, "diagnostics", lambda limit=30: {"blocking_reasons": {}})
    monkeypatch.setattr(runtime.ws_data_manager, "get_status", lambda: {"connected": True, "authenticated": True, "api_key_configured": False})
    monkeypatch.setattr(runtime.market_bridge, "get_status", lambda: {"subscriptions": 3, "last_tick_wall": 9_990.0})
    monkeypatch.setattr("tomic.runtime.time.monotonic", lambda: 10_001.0)
    monkeypatch.setattr("tomic.runtime.time.time", lambda: 10_001.0)

    captured = []
    monkeypatch.setattr(runtime.risk_agent, "enqueue_signal", lambda payload: captured.append(payload))

    snapshot = runtime._compute_signal_quality_snapshot(enqueue_signals=True, source="loop")
    reasons = snapshot.get("diagnostics", {}).get("no_action_reasons", [])

    assert snapshot["signals"]["enqueued_count"] == 0
    assert any("Startup guard active" in reason for reason in reasons)
    assert any("Waiting for first live tick after Start" in reason for reason in reasons)
    assert captured == []


def test_runtime_market_hours_guard_blocks_enqueue(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5605)
    runtime._started = True
    runtime._runtime_started_wall = 10_000.0
    runtime._runtime_started_mono = 10_000.0
    runtime._startup_enqueue_grace_s = 0.0
    runtime._require_live_tick_after_start = False

    class _Routed:
        def __init__(self, signal_dict):
            self.signal_dict = signal_dict
            self.priority_score = 1.0

            class _Source:
                value = "sniper"

            self.source = _Source()

    monkeypatch.setattr(runtime.sniper_agent, "scan", lambda: [])
    monkeypatch.setattr(runtime.volatility_agent, "scan", lambda: [])
    monkeypatch.setattr(
        runtime.conflict_router,
        "route",
        lambda sniper, vol: [_Routed({"instrument": "NIFTY", "strategy_type": "DITM_CALL", "direction": "BUY"})],
    )
    monkeypatch.setattr(runtime.conflict_router, "diagnostics", lambda limit=30: {"blocking_reasons": {}})
    monkeypatch.setattr(
        runtime.ws_data_manager,
        "get_status",
        lambda: {"connected": True, "authenticated": True, "api_key_configured": False},
    )
    monkeypatch.setattr(runtime.market_bridge, "get_status", lambda: {"subscriptions": 3, "last_tick_wall": 10_000.0})
    monkeypatch.setattr(
        runtime,
        "_market_session_state",
        lambda: (
            False,
            "Outside market hours (09:15-15:30 Asia/Kolkata)",
            {
                "enforced": True,
                "open": False,
                "reason": "Outside market hours (09:15-15:30 Asia/Kolkata)",
                "offhours_scan_override": False,
                "tz": "Asia/Kolkata",
                "now": "2026-02-13 16:56:00",
                "weekday": "Friday",
                "window": "09:15-15:30",
            },
        ),
    )

    captured = []
    monkeypatch.setattr(runtime.risk_agent, "enqueue_signal", lambda payload: captured.append(payload))

    snapshot = runtime._compute_signal_quality_snapshot(enqueue_signals=True, source="loop")
    reasons = snapshot.get("diagnostics", {}).get("no_action_reasons", [])

    assert snapshot["signals"]["enqueued_count"] == 0
    assert any("Outside market hours" in reason for reason in reasons)
    assert captured == []


def test_runtime_market_hours_guard_blocks_scan_without_override(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5606)
    runtime._allow_offhours_scan = False

    calls = {"sniper": 0, "vol": 0}

    def _sniper_scan():
        calls["sniper"] += 1
        return []

    def _vol_scan():
        calls["vol"] += 1
        return []

    monkeypatch.setattr(runtime.sniper_agent, "scan", _sniper_scan)
    monkeypatch.setattr(runtime.volatility_agent, "scan", _vol_scan)
    monkeypatch.setattr(
        runtime,
        "_market_session_state",
        lambda: (
            False,
            "Outside market hours (09:15-15:30 Asia/Kolkata)",
            {
                "enforced": True,
                "open": False,
                "reason": "Outside market hours (09:15-15:30 Asia/Kolkata)",
                "offhours_scan_override": False,
                "tz": "Asia/Kolkata",
                "now": "2026-02-13 18:00:00",
                "weekday": "Friday",
                "window": "09:15-15:30",
            },
        ),
    )
    monkeypatch.setattr(
        runtime.ws_data_manager,
        "get_status",
        lambda: {"connected": True, "authenticated": True, "api_key_configured": False},
    )
    monkeypatch.setattr(runtime.market_bridge, "get_status", lambda: {"subscriptions": 3, "last_tick_wall": 10_000.0})

    snapshot = runtime._compute_signal_quality_snapshot(enqueue_signals=False, source="api")
    reasons = snapshot.get("diagnostics", {}).get("no_action_reasons", [])

    assert calls["sniper"] == 0
    assert calls["vol"] == 0
    assert snapshot["signals"]["sniper_count"] == 0
    assert snapshot["signals"]["volatility_count"] == 0
    assert any("Signal scan paused: market session closed" in reason for reason in reasons)


def test_runtime_market_hours_guard_allows_scan_with_override(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    runtime = TomicRuntime(config=config, zmq_port=5607)
    runtime._allow_offhours_scan = True

    calls = {"sniper": 0, "vol": 0}

    def _sniper_scan():
        calls["sniper"] += 1
        return []

    def _vol_scan():
        calls["vol"] += 1
        return []

    monkeypatch.setattr(runtime.sniper_agent, "scan", _sniper_scan)
    monkeypatch.setattr(runtime.volatility_agent, "scan", _vol_scan)
    monkeypatch.setattr(
        runtime,
        "_market_session_state",
        lambda: (
            False,
            "Outside market hours (09:15-15:30 Asia/Kolkata)",
            {
                "enforced": True,
                "open": False,
                "reason": "Outside market hours (09:15-15:30 Asia/Kolkata)",
                "offhours_scan_override": True,
                "tz": "Asia/Kolkata",
                "now": "2026-02-13 18:00:00",
                "weekday": "Friday",
                "window": "09:15-15:30",
            },
        ),
    )
    monkeypatch.setattr(
        runtime.ws_data_manager,
        "get_status",
        lambda: {"connected": True, "authenticated": True, "api_key_configured": False},
    )
    monkeypatch.setattr(runtime.market_bridge, "get_status", lambda: {"subscriptions": 3, "last_tick_wall": 10_000.0})

    runtime._compute_signal_quality_snapshot(enqueue_signals=False, source="api")

    assert calls["sniper"] == 1
    assert calls["vol"] == 1


def test_runtime_start_rejects_stale_pending_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = TomicConfig.load("sandbox")
    config.endpoints = replace(config.endpoints, feed_primary_ws="", feed_fallback_ws="")
    runtime = TomicRuntime(config=config, zmq_port=5604)
    runtime._signal_loop_enabled = False
    runtime._startup_enqueue_grace_s = 0.0

    cmd_id = runtime.command_store.enqueue(
        event_id="evt-start-reset",
        correlation_id="corr-start-reset",
        idempotency_key="idem-start-reset",
        event_type="ORDER_REQUEST",
        source_agent="risk_agent",
        payload={"instrument": "NIFTY", "strategy_type": "DITM_CALL", "direction": "BUY", "quantity": 50},
    )
    assert cmd_id is not None
    assert runtime.command_store.count_pending() == 1

    runtime.start()
    runtime.stop()

    with runtime.command_store._conn() as conn:
        row = conn.execute("SELECT status, last_error FROM commands WHERE id = ?", (cmd_id,)).fetchone()

    assert row is not None
    assert row["status"] == "FAILED"
    assert "Runtime start reset" in (row["last_error"] or "")
