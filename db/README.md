# OpenAlgo database files

This folder holds SQLite (and optionally DuckDB) database files. They are created at runtime when the app first uses them.

## Database locations (relative to project root)

| Database | Path | Purpose |
|----------|------|---------|
| Main app | `db/openalgo.db` | Users, orders, positions, settings |
| API/traffic logs | `db/logs.db` | Traffic and API logs |
| Latency | `db/latency.db` | Latency monitoring |
| Sandbox/Analyzer | `db/sandbox.db` | Paper trading (analyzer mode) |
| Historify | `db/historify.duckdb` | Historical market data (DuckDB) |
| **Auto trade logs** | `db/ai_scalper_logs.db` | Auto-trade events (ENTRY/EXIT) for analytics & tuning |
| **Manual trade logs** | `db/manual_trade_logs.db` | Manual trades from Scalping & Chart windows |
| **Model tuning runs** | `db/ai_scalper_tuning.db` | Model tuning job history and recommendations |
| **Mock trading** | `db/mock_trading.db` | Mock orders, positions, trades from Mock Replay UI (after-hours testing) |

---

## Running SQL queries

There is **no in-app SQL runner**. Use one of these:

### 1. Command line (sqlite3)

From the **project root** (so paths below are correct):

```bash
# Windows (PowerShell or cmd)
sqlite3 db\manual_trade_logs.db

# Linux / macOS
sqlite3 db/manual_trade_logs.db
```

Then type SQL and end with `;`. Examples:

```sql
-- Manual trade logs: last 20 exit events with PnL
SELECT ts_iso, event_type, source, side, symbol, action, qty, price, pnl, reason
FROM manual_trade_logs
WHERE event_type = 'EXIT' AND pnl IS NOT NULL
ORDER BY ts DESC
LIMIT 20;

-- Auto trade logs: same idea
SELECT ts_iso, event_type, source, mode, side, symbol, pnl, reason
FROM auto_trade_logs
WHERE event_type = 'EXIT' AND pnl IS NOT NULL
ORDER BY ts DESC
LIMIT 20;

-- Tuning runs (db/ai_scalper_tuning.db)
SELECT run_id, created_iso, status, provider, model, underlying, score, applied
FROM model_tuning_runs
ORDER BY created_ts DESC
LIMIT 10;
```

Exit sqlite3 with `.quit` or Ctrl+D.

### 2. One-off query from shell (no interactive session)

```bash
# Windows
sqlite3 db\manual_trade_logs.db "SELECT COUNT(*) FROM manual_trade_logs;"

# Linux / macOS
sqlite3 db/manual_trade_logs.db "SELECT COUNT(*) FROM manual_trade_logs;"
```

### 3. GUI tools

- **DB Browser for SQLite** (https://sqlitebrowser.org/) – open `db/manual_trade_logs.db` or `db/ai_scalper_logs.db`, then use “Execute SQL”.
- **VS Code** – SQLite extension: right‑click the `.db` file → “Open Database”, then run queries in the SQLite explorer.

---

## Table schemas (scalping-related DBs)

### `manual_trade_logs` (db/manual_trade_logs.db)

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| event_id | TEXT | Unique event id (from frontend) |
| trade_id | TEXT | Links ENTRY/EXIT for same trade |
| ts | REAL | Unix timestamp |
| ts_iso | TEXT | ISO timestamp |
| event_type | TEXT | `ENTRY` or `EXIT` |
| source | TEXT | `scalping` or `chart` |
| mode | TEXT | e.g. `LIVE` |
| side | TEXT | `CE` / `PE` |
| symbol | TEXT | Full symbol |
| action | TEXT | `BUY` / `SELL` |
| qty | INTEGER | Quantity |
| price | REAL | Price |
| pnl | REAL | PnL (mainly on EXIT) |
| reason | TEXT | Exit reason |
| hold_ms | REAL | Hold duration (ms) |
| underlying | TEXT | e.g. NIFTY, SENSEX |
| exchange | TEXT | NFO, BFO |
| meta_json | TEXT | Extra JSON |

### `auto_trade_logs` (db/ai_scalper_logs.db)

Same shape as `manual_trade_logs`; `source` is typically `local` or `server`, `mode` is `PAPER` or `LIVE`. Additional column:

| Column | Type | Description |
|--------|------|-------------|
| conditions_json | TEXT | JSON of **matched conditions** at trade level: for ENTRY (checks_passed, signal_side, trade_side, trade_ltp, etc.), for EXIT (trigger, reason, exit_ltp). Used for analysis of why a trade was taken or closed. |

### `model_tuning_runs` (db/ai_scalper_tuning.db)

| Column | Type | Description |
|--------|------|-------------|
| run_id | TEXT | UUID primary key |
| created_ts | REAL | Unix timestamp |
| created_iso | TEXT | ISO timestamp |
| status | TEXT | e.g. completed, failed |
| provider | TEXT | openai, anthropic, ollama, etc. |
| model | TEXT | Model name |
| underlying | TEXT | NIFTY, SENSEX, etc. |
| objective | TEXT | Tuning objective |
| score | REAL | Run score |
| recommendations_json | TEXT | Suggested param changes |
| applied_changes_json | TEXT | What was applied |
| applied | INTEGER | 0/1 |
| applied_ts | REAL | When applied |
| error | TEXT | Error message if failed |

---

## Notes

- **Stop the app** (or avoid heavy writes) before long ad‑hoc queries if you want consistent reads.
- SQLite is in **WAL** mode for these logs; multiple readers are fine.
- For Historify use **DuckDB** CLI: `duckdb db/historify.duckdb` then run SQL.

If you want an **in-app read-only SQL runner** (e.g. under Admin, restricted to these DBs and SELECT-only), that would require a new endpoint and UI with strict safety checks.
