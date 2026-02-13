-- TOMIC Migration 003: Journal Table
-- Trade journal for Journaling Agent

CREATE TABLE IF NOT EXISTS journal_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT UNIQUE NOT NULL,
    correlation_id  TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    strategy_tag    TEXT NOT NULL,
    -- Instrument
    instrument      TEXT NOT NULL,
    exchange        TEXT DEFAULT 'NSE',
    -- Trade data
    direction       TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    pnl             REAL DEFAULT 0,
    hold_duration_s INTEGER DEFAULT 0,
    exit_reason     TEXT,
    -- Regime context at entry
    regime_phase    TEXT,
    regime_score    INTEGER,
    vix_at_entry    REAL,
    -- Volatility context
    iv_rank         REAL,
    pcr_at_entry    REAL,
    max_pain_dist   REAL,
    -- Sniper context
    zone_freshness  TEXT,
    -- Sizing chain (full 8-step log as JSON)
    sizing_chain    TEXT,
    -- Risk parameters
    kelly_value     REAL,
    sector_heat_pct REAL,
    -- Execution quality
    slippage_ticks  REAL DEFAULT 0,
    latency_ms      REAL DEFAULT 0,
    fill_rate       REAL DEFAULT 1.0,
    -- Timestamps
    entry_time      TEXT,
    exit_time       TEXT,
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_strategy ON journal_entries(strategy_tag);
CREATE INDEX IF NOT EXISTS idx_journal_instrument ON journal_entries(instrument);
CREATE INDEX IF NOT EXISTS idx_journal_created ON journal_entries(created_at);

-- Rolling performance metrics (updated by Journaling Agent)
CREATE TABLE IF NOT EXISTS performance_metrics (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    rolling_expectancy  REAL DEFAULT 0,
    rolling_win_rate    REAL DEFAULT 0,
    rolling_avg_win     REAL DEFAULT 0,
    rolling_avg_loss    REAL DEFAULT 0,
    total_trades        INTEGER DEFAULT 0,
    sharpe_ratio        REAL DEFAULT 0,
    max_drawdown_pct    REAL DEFAULT 0,
    last_updated        TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

INSERT OR IGNORE INTO performance_metrics (id) VALUES (1);
