-- TOMIC Migration 002: Positions Table
-- Versioned position store (single writer: Execution Agent)

CREATE TABLE IF NOT EXISTS positions (
    key         TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE TABLE IF NOT EXISTS position_meta (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO position_meta (id, version) VALUES (1, 0);
