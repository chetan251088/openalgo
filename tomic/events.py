"""
TOMIC Events — Pydantic Event Schemas with Idempotency Contract
================================================================
Every event carries: event_id, correlation_id, strategy_id,
idempotency_key, event_version.

Used across both channels:
  - Telemetry Bus (ZeroMQ Pub/Sub): REGIME_UPDATE, HEARTBEAT, ALERT, POSITION_UPDATE
  - Command Table (SQLite WAL):     ORDER_REQUEST, ORDER_FILL, ORDER_REJECT
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Event Types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    # Telemetry (ZeroMQ Pub/Sub — best effort)
    REGIME_UPDATE = "REGIME_UPDATE"
    HEARTBEAT = "HEARTBEAT"
    ALERT = "ALERT"
    POSITION_UPDATE = "POSITION_UPDATE"
    SIGNAL = "SIGNAL"

    # Command Table (SQLite WAL — at-least-once + idempotency)
    ORDER_REQUEST = "ORDER_REQUEST"
    ORDER_FILL = "ORDER_FILL"
    ORDER_REJECT = "ORDER_REJECT"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"     # agent crash, API disconnect, unhedged exposure
    RISK = "RISK"             # drawdown, margin, order rate
    SIGNAL = "SIGNAL"         # trade entry/exit
    REGIME = "REGIME"         # regime state change
    INFO = "INFO"             # general telemetry


# ---------------------------------------------------------------------------
# Base Event
# ---------------------------------------------------------------------------

class TomicEvent(BaseModel):
    """Base event. All events carry idempotency fields."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default="")
    strategy_id: str = Field(default="")
    idempotency_key: str = Field(default="")
    event_version: int = Field(default=1)
    event_type: EventType
    source_agent: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict[str, Any] = Field(default_factory=dict)

    def build_idempotency_key(self, leg: str = "", action: str = "") -> str:
        """Build idempotency key: {strategy_id}:{leg}:{action}"""
        parts = [self.strategy_id, leg, action]
        return ":".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Telemetry Events
# ---------------------------------------------------------------------------

class RegimeUpdateEvent(TomicEvent):
    """Published by Regime Agent via ZeroMQ."""
    event_type: EventType = EventType.REGIME_UPDATE
    source_agent: str = "regime_agent"
    # Payload fields surfaced for type safety
    phase: str = ""           # BULLISH / BEARISH / CONGESTION / BLOWOFF
    score: int = 0            # -20 to +20
    vix: float = 0.0
    vix_flags: list = Field(default_factory=list)  # e.g. ["HALF_SIZE", "DEFINED_RISK_ONLY"]


class HeartbeatEvent(TomicEvent):
    """Published by every agent every 60s."""
    event_type: EventType = EventType.HEARTBEAT
    agent_status: str = "healthy"
    uptime_seconds: float = 0.0


class AlertEvent(TomicEvent):
    """Published for operational alerts via ZeroMQ."""
    event_type: EventType = EventType.ALERT
    alert_level: AlertLevel = AlertLevel.INFO
    message: str = ""


class PositionUpdateEvent(TomicEvent):
    """Published by Execution Agent when positions change."""
    event_type: EventType = EventType.POSITION_UPDATE
    source_agent: str = "execution_agent"
    snapshot_version: int = 0


class SignalEvent(TomicEvent):
    """Published by Sniper / Volatility agents via ZeroMQ."""
    event_type: EventType = EventType.SIGNAL
    instrument: str = ""
    direction: str = ""       # BUY / SELL
    entry_price: float = 0.0
    stop_price: float = 0.0
    rs_score: float = 0.0     # 50-day relative strength
    strategy_type: str = ""   # StrategyType value
    legs: list = Field(default_factory=list)
    expected_credit: float = 0.0
    expected_debit: float = 0.0
    dte: int = 0


# ---------------------------------------------------------------------------
# Command Events (for durable command table)
# ---------------------------------------------------------------------------

class OrderRequestEvent(TomicEvent):
    """Enqueued by Risk Agent into command table."""
    event_type: EventType = EventType.ORDER_REQUEST
    source_agent: str = "risk_agent"
    instrument: str = ""
    strategy_type: str = ""
    direction: str = ""
    quantity: int = 0
    legs: list = Field(default_factory=list)
    signal_direction: str = ""   # directional intent from source signal (BUY/SELL bias)
    option_type: str = ""        # CE/PE when available
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    sizing_chain: list = Field(default_factory=list)  # full 8-step log
    regime_snapshot: Dict[str, Any] = Field(default_factory=dict)
    strategy_tag: str = ""    # e.g. TOMIC_VOL_CREDIT_SPREAD
    entry_reason: str = ""    # human-readable reason why this entry was taken
    entry_reason_meta: Dict[str, Any] = Field(default_factory=dict)  # structured rationale context


class OrderFillEvent(TomicEvent):
    """Written by Execution Agent after broker confirms fill."""
    event_type: EventType = EventType.ORDER_FILL
    source_agent: str = "execution_agent"
    broker_order_id: str = ""
    fill_price: float = 0.0
    filled_quantity: int = 0
    slippage_ticks: float = 0.0
    latency_ms: float = 0.0


class OrderRejectEvent(TomicEvent):
    """Written by Execution Agent when order is rejected."""
    event_type: EventType = EventType.ORDER_REJECT
    source_agent: str = "execution_agent"
    reject_reason: str = ""
    error_class: str = ""     # network_timeout, broker_reject, validation, unknown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Event types that go to durable command table (not ZMQ)
COMMAND_EVENT_TYPES = frozenset({
    EventType.ORDER_REQUEST,
    EventType.ORDER_FILL,
    EventType.ORDER_REJECT,
})

# Event types that go to ZMQ telemetry bus
TELEMETRY_EVENT_TYPES = frozenset({
    EventType.REGIME_UPDATE,
    EventType.HEARTBEAT,
    EventType.ALERT,
    EventType.POSITION_UPDATE,
    EventType.SIGNAL,
})


def is_command_event(event_type: EventType) -> bool:
    """Check if event type should be routed to durable command table."""
    return event_type in COMMAND_EVENT_TYPES


def mask_sensitive_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Mask API keys and broker order IDs in log output per §10."""
    sensitive_keys = {"api_key", "execution_api_key", "broker_order_id"}
    masked = {}
    for k, v in payload.items():
        if k in sensitive_keys and isinstance(v, str) and len(v) > 4:
            masked[k] = v[:2] + "***" + v[-2:]
        else:
            masked[k] = v
    return masked
