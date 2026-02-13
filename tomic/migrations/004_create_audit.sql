-- TOMIC Migration 004: Audit Table
-- Audit trail for control API actions (auth/RBAC per §10)

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,           -- start, stop, pause, config_change
    user_id     TEXT,                     -- session user (masked in logs)
    ip_address  TEXT,
    details     TEXT,                     -- JSON, sensitive fields masked
    result      TEXT DEFAULT 'success',   -- success, denied, error
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
