-- TOMIC Migration 001: Commands Table
-- Durable command table for order-critical events
-- At-least-once delivery + idempotency enforcement

CREATE TABLE IF NOT EXISTS commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT UNIQUE NOT NULL,
    correlation_id  TEXT NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    event_type      TEXT NOT NULL,
    event_version   INTEGER DEFAULT 1,
    source_agent    TEXT NOT NULL,
    payload         TEXT NOT NULL,
    status          TEXT DEFAULT 'PENDING',
    -- Retry mechanics
    attempt_count   INTEGER DEFAULT 0,
    max_attempts    INTEGER DEFAULT 3,
    last_error      TEXT,
    next_retry_at   TEXT,
    -- Lease mechanics
    owner_token     TEXT,
    lease_expires   TEXT,
    -- Broker reconciliation
    broker_order_id TEXT,
    -- Timestamps
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    processed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_commands_idempotency ON commands(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_commands_correlation ON commands(correlation_id);
