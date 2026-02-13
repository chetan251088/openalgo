-- TOMIC Migration 005: Metrics Table
-- Observability metrics (1-min buckets per §13)

CREATE TABLE IF NOT EXISTS metrics (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_time             TEXT NOT NULL,       -- ISO timestamp, 1-min aligned
    -- Order metrics
    orders_placed           INTEGER DEFAULT 0,
    orders_filled           INTEGER DEFAULT 0,
    orders_rejected         INTEGER DEFAULT 0,
    order_latency_avg_ms    REAL DEFAULT 0,
    order_latency_p99_ms    REAL DEFAULT 0,
    -- Data quality
    stale_data_blocks       INTEGER DEFAULT 0,
    feed_failovers          INTEGER DEFAULT 0,
    -- Safety
    unhedged_incidents      INTEGER DEFAULT 0,
    dead_letter_count       INTEGER DEFAULT 0,
    lease_reclaims          INTEGER DEFAULT 0,
    circuit_breaker_trips   INTEGER DEFAULT 0,
    -- Execution quality
    avg_slippage_ticks      REAL DEFAULT 0,
    fill_rate_pct           REAL DEFAULT 100,
    -- Timestamps
    created_at              TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_bucket ON metrics(bucket_time);
