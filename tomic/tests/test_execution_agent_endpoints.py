from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest

from tomic.agents.execution_agent import BrokerRejectError
from tomic.agents.execution_agent import ExecutionAgent
from tomic.command_store import CommandRow
from tomic.position_book import PositionBook


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self) -> dict:
        return self._payload


def _dummy_agent() -> ExecutionAgent:
    agent = ExecutionAgent.__new__(ExecutionAgent)
    agent._base_url = "http://127.0.0.1:5002"
    agent._api_key = "k_test"
    agent.logger = logging.getLogger("test.execution_agent")
    agent._sandbox = type(
        "_Sandbox",
        (),
        {"is_sandbox": False, "wrap_order": staticmethod(lambda payload: payload)},
    )()
    agent._recent_order_meta = {}
    agent._ws_data_manager = None
    agent._virtual_enabled = False
    agent._virtual_positions = {}
    agent._virtual_sl_pct = 0.25
    agent._virtual_tp_pct = 0.35
    agent._virtual_trail_enabled = True
    agent._virtual_trail_trigger_pct = 0.15
    agent._virtual_trail_offset_pct = 0.08
    agent._virtual_tick_max_age_s = 15.0
    agent._position_book = PositionBook(db_path=":memory:")
    agent._last_broker_positions_error = ""
    return agent


def test_fetch_broker_positions_uses_post_with_apikey(monkeypatch) -> None:
    calls = {"post": 0, "get": 0}

    def fake_post(url, json, timeout):
        calls["post"] += 1
        assert url.endswith("/api/v1/positionbook")
        assert json == {"apikey": "k_test"}
        return _Response(200, {"status": "success", "data": [{"symbol": "NIFTY"}]})

    def fake_get(url, headers, timeout):
        calls["get"] += 1
        return _Response(200, {"status": "success", "data": []})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr("tomic.agents.execution_agent.requests.get", fake_get)

    positions = ExecutionAgent._fetch_broker_positions(_dummy_agent())  # type: ignore[arg-type]
    assert positions == [{"symbol": "NIFTY"}]
    assert calls["post"] == 1
    assert calls["get"] == 0


def test_fetch_broker_positions_falls_back_to_get_on_405(monkeypatch) -> None:
    calls = {"post": 0, "get": 0}

    def fake_post(url, json, timeout):
        calls["post"] += 1
        return _Response(405, {"status": "error"})

    def fake_get(url, headers, timeout):
        calls["get"] += 1
        assert "Authorization" in headers
        return _Response(200, {"status": "success", "data": [{"symbol": "BANKNIFTY"}]})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr("tomic.agents.execution_agent.requests.get", fake_get)

    positions = ExecutionAgent._fetch_broker_positions(_dummy_agent())  # type: ignore[arg-type]
    assert positions == [{"symbol": "BANKNIFTY"}]
    assert calls["post"] == 1
    assert calls["get"] == 1


def test_fetch_broker_positions_tracks_error_state(monkeypatch) -> None:
    def fake_post(url, json, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    agent = _dummy_agent()
    positions = ExecutionAgent._fetch_broker_positions(agent)  # type: ignore[arg-type]
    assert positions == []
    assert "network down" in agent._last_broker_positions_error


def test_idempotency_check_reads_orderbook_via_post(monkeypatch) -> None:
    def fake_post(url, json, timeout):
        assert url.endswith("/api/v1/orderbook")
        assert json == {"apikey": "k_test"}
        return _Response(
            200,
            {
                "status": "success",
                "data": [
                    {"strategy": "TOMIC", "status": "COMPLETE"},
                ],
            },
        )

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    result = ExecutionAgent._check_broker_idempotency(  # type: ignore[arg-type]
        _dummy_agent(),
        idempotency_key="abc123",
        strategy_tag="TOMIC",
    )
    assert result is True


def test_place_single_order_400_is_non_retryable_reject(monkeypatch) -> None:
    def fake_post(url, json, headers, timeout):
        return _Response(400, {"status": "error", "message": "Invalid symbol"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    with pytest.raises(BrokerRejectError, match="Invalid symbol"):
        ExecutionAgent._place_single_order(  # type: ignore[arg-type]
            _dummy_agent(),
            {
                "instrument": "NIFTY26FEB26000CE",
                "direction": "BUY",
                "quantity": 50,
                "order_type": "MARKET",
                "product": "MIS",
            },
        )


def test_ditm_underlying_routes_via_optionsorder(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/api/v1/optionsorder"):
            return _Response(200, {"status": "success", "orderid": "OID123"})
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_front_expiry_for_underlying",
        lambda self, underlying, exchange: "27FEB26",
    )

    order_id = ExecutionAgent._place_single_order(  # type: ignore[arg-type]
        _dummy_agent(),
        {
            "instrument": "NIFTY",
            "strategy_type": "DITM_CALL",
            "direction": "BUY",
            "quantity": 50,
            "order_type": "MARKET",
            "product": "MIS",
            "strategy_tag": "TOMIC_TEST",
        },
    )
    assert order_id == "OID123"
    assert any(call[0].endswith("/api/v1/optionsorder") for call in calls)


def test_spread_routes_via_optionsmultiorder(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/api/v1/optionsmultiorder"):
            return _Response(
                200,
                {
                    "status": "success",
                    "results": [
                        {"leg": 1, "orderid": "LEG1"},
                        {"leg": 2, "orderid": "LEG2"},
                    ],
                },
            )
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    order_ref = ExecutionAgent._execute_with_legging(  # type: ignore[arg-type]
        _dummy_agent(),
        policy=None,
        payload={
            "instrument": "NIFTY",
            "strategy_type": "BULL_PUT_SPREAD",
            "direction": "SELL",
            "quantity": 50,
            "strategy_tag": "TOMIC_SPREAD_TEST",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"leg_type": "BUY_PUT", "strike": 23600, "expiry": "2026-03-19", "direction": "BUY", "quantity": 50},
            {"leg_type": "SELL_PUT", "strike": 23800, "expiry": "2026-03-19", "direction": "SELL", "quantity": 50},
        ],
    )

    assert order_ref == "LEG1|LEG2"
    assert any(call[0].endswith("/api/v1/optionsmultiorder") for call in calls)


def test_bear_call_spread_routes_via_optionsmultiorder(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/api/v1/optionsmultiorder"):
            return _Response(
                200,
                {
                    "status": "success",
                    "results": [
                        {"leg": 1, "orderid": "BC1"},
                        {"leg": 2, "orderid": "BC2"},
                    ],
                },
            )
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    order_ref = ExecutionAgent._execute_with_legging(  # type: ignore[arg-type]
        _dummy_agent(),
        policy=None,
        payload={
            "instrument": "NIFTY",
            "strategy_type": "BEAR_CALL_SPREAD",
            "direction": "SELL",
            "quantity": 50,
            "strategy_tag": "TOMIC_BEAR_CALL_TEST",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"leg_type": "BUY_CALL", "offset": "OTM2", "option_type": "CE", "direction": "BUY", "quantity": 50},
            {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL", "quantity": 50},
        ],
    )

    assert order_ref == "BC1|BC2"
    assert any(call[0].endswith("/api/v1/optionsmultiorder") for call in calls)


def test_jade_lizard_routes_via_optionsmultiorder(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/api/v1/optionsmultiorder"):
            return _Response(
                200,
                {
                    "status": "success",
                    "results": [
                        {"leg": 1, "orderid": "JL1"},
                        {"leg": 2, "orderid": "JL2"},
                        {"leg": 3, "orderid": "JL3"},
                    ],
                },
            )
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    order_ref = ExecutionAgent._execute_with_legging(  # type: ignore[arg-type]
        _dummy_agent(),
        policy=None,
        payload={
            "instrument": "NIFTY",
            "strategy_type": "JADE_LIZARD",
            "direction": "SELL",
            "quantity": 50,
            "strategy_tag": "TOMIC_JADE_TEST",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL", "quantity": 50},
            {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL", "quantity": 50},
            {"leg_type": "BUY_CALL", "offset": "OTM3", "option_type": "CE", "direction": "BUY", "quantity": 50},
        ],
    )

    assert order_ref == "JL1|JL2|JL3"
    assert any(call[0].endswith("/api/v1/optionsmultiorder") for call in calls)


def test_short_strangle_routes_via_optionsmultiorder(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/api/v1/optionsmultiorder"):
            return _Response(
                200,
                {
                    "status": "success",
                    "results": [
                        {"leg": 1, "orderid": "SS1"},
                        {"leg": 2, "orderid": "SS2"},
                    ],
                },
            )
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    order_ref = ExecutionAgent._execute_with_legging(  # type: ignore[arg-type]
        _dummy_agent(),
        policy=None,
        payload={
            "instrument": "BANKNIFTY",
            "strategy_type": "SHORT_STRANGLE",
            "direction": "SELL",
            "quantity": 30,
            "strategy_tag": "TOMIC_STRANGLE_TEST",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL", "quantity": 30},
            {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL", "quantity": 30},
        ],
    )

    assert order_ref == "SS1|SS2"
    assert any(call[0].endswith("/api/v1/optionsmultiorder") for call in calls)


def test_short_straddle_routes_via_optionsmultiorder(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/api/v1/optionsmultiorder"):
            return _Response(
                200,
                {
                    "status": "success",
                    "results": [
                        {"leg": 1, "orderid": "STD1"},
                        {"leg": 2, "orderid": "STD2"},
                    ],
                },
            )
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)

    order_ref = ExecutionAgent._execute_with_legging(  # type: ignore[arg-type]
        _dummy_agent(),
        policy=None,
        payload={
            "instrument": "BANKNIFTY",
            "strategy_type": "SHORT_STRADDLE",
            "direction": "SELL",
            "quantity": 30,
            "strategy_tag": "TOMIC_STRADDLE_TEST",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"leg_type": "SELL_PUT", "offset": "ATM", "option_type": "PE", "direction": "SELL", "quantity": 30},
            {"leg_type": "SELL_CALL", "offset": "ATM", "option_type": "CE", "direction": "SELL", "quantity": 30},
        ],
    )

    assert order_ref == "STD1|STD2"
    assert any(call[0].endswith("/api/v1/optionsmultiorder") for call in calls)


def test_ditm_put_defaults_to_pe_optionsorder(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        if url.endswith("/api/v1/optionsorder"):
            captured["body"] = json
            return _Response(200, {"status": "success", "orderid": "PUTOID"})
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_front_expiry_for_underlying",
        lambda self, underlying, exchange: "27FEB26",
    )

    order_id = ExecutionAgent._place_single_order(  # type: ignore[arg-type]
        _dummy_agent(),
        {
            "instrument": "NIFTY",
            "strategy_type": "DITM_PUT",
            "direction": "BUY",
            "quantity": 50,
            "order_type": "MARKET",
            "product": "MIS",
            "strategy_tag": "TOMIC_DITM_PUT",
        },
    )

    assert order_id == "PUTOID"
    assert captured["body"]["option_type"] == "PE"


def test_ditm_optionsorder_autofills_expiry(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        if url.endswith("/api/v1/optionsorder"):
            captured["body"] = json
            return _Response(200, {"status": "success", "orderid": "OIDEXP"})
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_front_expiry_for_underlying",
        lambda self, underlying, exchange: "27FEB26",
    )

    order_id = ExecutionAgent._place_single_order(  # type: ignore[arg-type]
        _dummy_agent(),
        {
            "instrument": "BANKNIFTY",
            "strategy_type": "DITM_CALL",
            "direction": "BUY",
            "quantity": 15,
            "order_type": "MARKET",
            "product": "MIS",
            "strategy_tag": "TOMIC_EXPIRY_AUTOFILL",
        },
    )
    assert order_id == "OIDEXP"
    assert captured["body"]["expiry_date"] == "27FEB26"


def test_optionsmultiorder_autofills_expiry_for_offset_legs(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        if url.endswith("/api/v1/optionsmultiorder"):
            captured["body"] = json
            return _Response(200, {"status": "success", "orderid": "MIDEXP"})
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_front_expiry_for_underlying",
        lambda self, underlying, exchange: "27FEB26",
    )

    order_ref = ExecutionAgent._place_options_multi_order(  # type: ignore[arg-type]
        _dummy_agent(),
        payload={
            "instrument": "NIFTY",
            "strategy_type": "IRON_CONDOR",
            "direction": "SELL",
            "quantity": 50,
            "strategy_tag": "TOMIC_MULTI_EXPIRY",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"offset": "OTM1", "option_type": "PE", "direction": "BUY", "quantity": 50},
            {"offset": "ATM", "option_type": "PE", "direction": "SELL", "quantity": 50},
        ],
    )

    assert order_ref == "MIDEXP"
    assert captured["body"]["expiry_date"] == "27FEB26"


def test_optionsmultiorder_maps_expiry_offset_for_calendar_legs(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        if url.endswith("/api/v1/optionsmultiorder"):
            captured["body"] = json
            return _Response(200, {"status": "success", "orderid": "CAL1"})
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_expiry_by_offset_for_underlying",
        lambda self, underlying, exchange, expiry_offset=0: "27FEB26" if int(expiry_offset) == 0 else "06MAR26",
    )

    order_ref = ExecutionAgent._place_options_multi_order(  # type: ignore[arg-type]
        _dummy_agent(),
        payload={
            "instrument": "NIFTY",
            "strategy_type": "CALENDAR_DIAGONAL",
            "direction": "SELL",
            "quantity": 50,
            "strategy_tag": "TOMIC_CALENDAR_EXPIRY_OFFSET",
            "exchange": "NSE_INDEX",
        },
        legs=[
            {"offset": "ATM", "option_type": "CE", "direction": "SELL", "quantity": 50, "expiry_offset": 0},
            {"offset": "ATM", "option_type": "CE", "direction": "BUY", "quantity": 50, "expiry_offset": 1},
        ],
    )

    assert order_ref == "CAL1"
    legs = captured["body"]["legs"]
    assert legs[0]["expiry_date"] == "27FEB26"
    assert legs[1]["expiry_date"] == "06MAR26"


def test_base_underlying_normalizes_exchange_prefixed_symbol() -> None:
    assert ExecutionAgent._base_underlying("NSE_INDEX:NIFTY") == "NIFTY"
    assert ExecutionAgent._base_underlying("BANKNIFTY.NSE_INDEX") == "BANKNIFTY"
    assert ExecutionAgent._base_underlying("NIFTY 50") == "NIFTY"
    assert ExecutionAgent._base_underlying("NIFTY26FEB2623000CE") == "NIFTY"
    assert ExecutionAgent._base_underlying("BANKNIFTY26FEB60500PE") == "BANKNIFTY"


def test_ditm_optionsorder_aligns_quantity_to_lot_size(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        if url.endswith("/api/v1/optionsorder"):
            captured["body"] = json
            return _Response(200, {"status": "success", "orderid": "OIDLOT"})
        return _Response(500, {"status": "error", "message": "wrong endpoint"})

    monkeypatch.setattr("tomic.agents.execution_agent.requests.post", fake_post)
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_front_expiry_for_underlying",
        lambda self, underlying, exchange: "27FEB26",
    )
    monkeypatch.setattr(
        ExecutionAgent,
        "_resolve_option_lot_size",
        lambda self, underlying, exchange, expiry, option_type: 30,
    )

    order_id = ExecutionAgent._place_single_order(  # type: ignore[arg-type]
        _dummy_agent(),
        {
            "instrument": "BANKNIFTY",
            "strategy_type": "DITM_PUT",
            "direction": "BUY",
            "quantity": 50,
            "order_type": "MARKET",
            "product": "MIS",
            "strategy_tag": "TOMIC_LOT_ALIGN",
        },
    )

    assert order_id == "OIDLOT"
    assert captured["body"]["quantity"] == 30


def test_virtual_guard_exits_on_sl(monkeypatch, tmp_path) -> None:
    agent = _dummy_agent()
    agent._position_book = PositionBook(db_path=str(tmp_path / "positions.db"))
    agent._virtual_enabled = True

    class _WS:
        def __init__(self) -> None:
            self._price = 100.0

        def set_price(self, value: float) -> None:
            self._price = value

        def get_last_price(self, symbol: str, exchange: str, max_age_s: float = 15.0) -> float:
            return float(self._price)

    ws = _WS()
    agent._ws_data_manager = ws

    # Pretend broker returned resolved option symbol so PositionBook stores exact fill symbol.
    agent._stash_order_meta(
        "OIDV1",
        {
            "symbol": "NIFTY26FEB2623000CE",
            "exchange": "NFO",
            "product": "MIS",
            "avg_price": 100.0,
        },
    )

    payload = {
        "instrument": "NIFTY",
        "strategy_id": "TOMIC_DITM_CALL_NIFTY",
        "strategy_type": "DITM_CALL",
        "direction": "BUY",
        "quantity": 50,
        "product": "MIS",
        "strategy_tag": "TOMIC_DITM_CALL_NIFTY",
    }
    ExecutionAgent._update_position_book(agent, payload, broker_ref="OIDV1")

    # Stop price should be 75.0 with default 25% SL.
    key = "NIFTY26FEB2623000CE|TOMIC_DITM_CALL_NIFTY"
    guard = agent._virtual_positions.get(key)
    assert guard is not None
    assert guard["stop_price"] == pytest.approx(75.0)

    closed = {}

    def fake_place_single_order(params):
        closed["params"] = params
        return "EXIT1"

    monkeypatch.setattr(agent, "_place_single_order", fake_place_single_order)

    # Price falls below SL -> should trigger SELL exit.
    ws.set_price(74.5)
    ExecutionAgent._monitor_virtual_positions(agent)

    snap = agent._position_book.read_snapshot()
    assert key not in snap.positions
    assert key not in agent._virtual_positions
    assert closed["params"]["direction"] == "SELL"
    assert closed["params"]["instrument"] == "NIFTY26FEB2623000CE"


def test_tick_drops_stale_commands_before_execution(monkeypatch) -> None:
    class _Store:
        def __init__(self, cmd: CommandRow) -> None:
            self._cmd = cmd
            self.failed = []

        def dequeue(self):
            cmd = self._cmd
            self._cmd = None
            return cmd

        def mark_failed(self, row_id, owner_token, error):
            self.failed.append((row_id, owner_token, error))
            return True

    stale_cmd = CommandRow(
        id=11,
        event_id="evt-11",
        correlation_id="corr-11",
        idempotency_key="key-11",
        event_type="ORDER_REQUEST",
        event_version=1,
        source_agent="risk_agent",
        payload={"instrument": "NIFTY", "strategy_id": "S1"},
        status="PROCESSING",
        attempt_count=1,
        max_attempts=3,
        last_error=None,
        next_retry_at=None,
        owner_token="tok-11",
        lease_expires=None,
        broker_order_id=None,
        created_at=(datetime.utcnow() - timedelta(seconds=11)).isoformat(),
        processed_at=None,
    )

    store = _Store(stale_cmd)
    agent = _dummy_agent()
    agent._command_store = store
    agent._max_queue_age_s = 10.0
    agent._monitor_virtual_positions = lambda: None
    agent._check_unhedged_exposure = lambda: None

    executed = {"called": False}

    def _never_execute(cmd):  # noqa: ANN001
        executed["called"] = True

    monkeypatch.setattr(agent, "_execute_order", _never_execute)

    ExecutionAgent._tick(agent)

    assert executed["called"] is False
    assert len(store.failed) == 1
    assert "Stale queue command dropped" in store.failed[0][2]
