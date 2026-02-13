"""
Test Suite: TOMIC Contracts — Event Schemas, Idempotency, Freshness
=====================================================================
Tests Pydantic event schemas, idempotency field enforcement,
sensitive field masking, and event routing helpers.
"""

import json
import pytest
from tomic.events import (
    TomicEvent,
    EventType,
    AlertLevel,
    OrderRequestEvent,
    OrderFillEvent,
    OrderRejectEvent,
    RegimeUpdateEvent,
    HeartbeatEvent,
    AlertEvent,
    SignalEvent,
    PositionUpdateEvent,
    COMMAND_EVENT_TYPES,
    TELEMETRY_EVENT_TYPES,
    is_command_event,
    mask_sensitive_fields,
)


class TestEventSchemas:
    """Verify Pydantic schemas enforce required fields."""

    def test_order_request_has_required_fields(self):
        e = OrderRequestEvent(instrument="NIFTY", quantity=50)
        assert e.event_type == EventType.ORDER_REQUEST
        assert e.event_id  # UUID auto-generated
        assert len(e.event_id) == 36  # UUID format
        assert e.instrument == "NIFTY"
        assert e.quantity == 50

    def test_order_fill_carries_correlation(self):
        e = OrderFillEvent(
            correlation_id="corr-123",
            strategy_id="TOMIC_BPS_NIFTY",
            idempotency_key="TOMIC_BPS_NIFTY:leg1:BUY",
            broker_order_id="ORD-456",
        )
        assert e.correlation_id == "corr-123"
        assert e.broker_order_id == "ORD-456"
        assert e.event_type == EventType.ORDER_FILL

    def test_order_reject_has_reason(self):
        e = OrderRejectEvent(
            correlation_id="corr-789",
            strategy_id="TOMIC_IC_BNF",
            idempotency_key="TOMIC_IC_BNF:leg2:SELL",
            reject_reason="Insufficient margin",
            error_class="broker_reject",
        )
        assert "margin" in e.reject_reason.lower()
        assert e.error_class == "broker_reject"

    def test_regime_update_schema(self):
        e = RegimeUpdateEvent(
            phase="BULLISH",
            score=12,
        )
        assert e.phase == "BULLISH"
        assert e.score == 12
        assert e.event_type == EventType.REGIME_UPDATE
        assert e.source_agent == "regime_agent"

    def test_heartbeat_schema(self):
        e = HeartbeatEvent(
            source_agent="execution",
            agent_status="healthy",
            uptime_seconds=3600.5,
        )
        assert e.agent_status == "healthy"
        assert e.uptime_seconds == 3600.5

    def test_alert_schema(self):
        e = AlertEvent(
            source_agent="supervisor",
            alert_level=AlertLevel.CRITICAL,
            message="Kill switch activated",
        )
        assert e.alert_level == AlertLevel.CRITICAL

    def test_signal_schema(self):
        e = SignalEvent(
            source_agent="sniper",
            instrument="RELIANCE",
            signal_type="VCP_BREAKOUT",
            strategy_type="DITM_CALL",
        )
        assert e.strategy_type == "DITM_CALL"
        assert e.instrument == "RELIANCE"


class TestIdempotencyContract:
    """Every event must carry idempotency fields."""

    def test_event_id_unique(self):
        e1 = OrderRequestEvent(instrument="NIFTY", quantity=50)
        e2 = OrderRequestEvent(instrument="NIFTY", quantity=50)
        assert e1.event_id != e2.event_id

    def test_event_version_defaults_to_1(self):
        e = OrderRequestEvent(instrument="NIFTY", quantity=50)
        assert e.event_version == 1

    def test_correlation_id_propagates(self):
        req = OrderRequestEvent(
            instrument="NIFTY", quantity=50,
            correlation_id="chain-001",
        )
        fill = OrderFillEvent(
            correlation_id="chain-001",
            strategy_id="s1",
            idempotency_key="k1",
            broker_order_id="ref1",
        )
        assert req.correlation_id == fill.correlation_id

    def test_idempotency_key_format(self):
        e = OrderRequestEvent(
            instrument="NIFTY", quantity=50,
            strategy_id="TOMIC_BPS_NIFTY_20260215",
            idempotency_key="TOMIC_BPS_NIFTY_20260215:leg1:BUY",
        )
        parts = e.idempotency_key.split(":")
        assert len(parts) == 3
        assert parts[0] == "TOMIC_BPS_NIFTY_20260215"

    def test_build_idempotency_key(self):
        e = OrderRequestEvent(
            instrument="NIFTY", quantity=50,
            strategy_id="TOMIC_BPS_NIFTY",
        )
        key = e.build_idempotency_key(leg="leg1", action="BUY")
        assert key == "TOMIC_BPS_NIFTY:leg1:BUY"


class TestEventRouting:
    """Command vs telemetry routing."""

    def test_command_event_types(self):
        assert EventType.ORDER_REQUEST in COMMAND_EVENT_TYPES
        assert EventType.ORDER_FILL in COMMAND_EVENT_TYPES
        assert EventType.ORDER_REJECT in COMMAND_EVENT_TYPES

    def test_telemetry_event_types(self):
        assert EventType.REGIME_UPDATE in TELEMETRY_EVENT_TYPES
        assert EventType.HEARTBEAT in TELEMETRY_EVENT_TYPES
        assert EventType.ALERT in TELEMETRY_EVENT_TYPES

    def test_is_command_event_order_request(self):
        assert is_command_event(EventType.ORDER_REQUEST) is True

    def test_is_command_event_heartbeat(self):
        assert is_command_event(EventType.HEARTBEAT) is False


class TestSensitiveFieldMasking:
    """API keys and broker order IDs are masked in logs."""

    def test_mask_api_key(self):
        data = {"api_key": "mysecretapikey123"}
        masked = mask_sensitive_fields(data)
        assert masked["api_key"] != "mysecretapikey123"
        assert "***" in masked["api_key"]

    def test_mask_preserves_non_sensitive(self):
        data = {"instrument": "NIFTY", "quantity": 50}
        masked = mask_sensitive_fields(data)
        assert masked["instrument"] == "NIFTY"
        assert masked["quantity"] == 50

    def test_mask_short_value(self):
        # Short values (<=4 chars) are not masked
        data = {"api_key": "ab"}
        masked = mask_sensitive_fields(data)
        assert masked["api_key"] == "ab"

    def test_mask_empty_dict(self):
        assert mask_sensitive_fields({}) == {}

    def test_mask_broker_order_id(self):
        data = {"broker_order_id": "ORD-123456789"}
        masked = mask_sensitive_fields(data)
        assert "***" in masked["broker_order_id"]


class TestEventSerialization:
    """Events serialize to JSON for transport."""

    def test_json_roundtrip(self):
        e = OrderRequestEvent(instrument="NIFTY", quantity=50)
        json_str = e.model_dump_json()
        data = json.loads(json_str)
        assert data["instrument"] == "NIFTY"
        assert data["event_type"] == "ORDER_REQUEST"
        assert data["event_id"] == e.event_id

    def test_regime_serialization(self):
        e = RegimeUpdateEvent(phase="BEARISH", score=-8)
        data = json.loads(e.model_dump_json())
        assert data["phase"] == "BEARISH"
        assert data["score"] == -8
