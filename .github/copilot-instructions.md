# OpenAlgo — Copilot Instructions

## Project Overview

OpenAlgo is a production-ready algorithmic trading platform built with Flask (backend) and React 19 (frontend). It provides a unified API layer across 24+ Indian brokers. The platform includes a custom-built scalping and auto-trading system for Indian options markets (NIFTY/SENSEX).

**Repo**: `C:\algo\openalgov2\openalgo`
**Docs**: https://docs.openalgo.in

---

## Development Environment

### Mandatory: Use `uv` for Python

**Never use global Python or pip directly.** Always prefix with `uv run`:

```bash
uv run app.py                # Run app
uv run python script.py      # Run any script
uv run pytest test/ -v        # Run tests
uv add package_name           # Install new package
uv sync                       # Sync deps after pull
```

### Frontend Builds

```bash
# Root-level CSS (Jinja2 templates — Tailwind 4 + DaisyUI)
npm run build          # Production
npm run dev            # Watch mode
# NEVER edit static/css/main.css directly — only edit src/css/styles.css

# React frontend
cd frontend && npm install && npm run build
```

### Running the App

```bash
uv run app.py                                    # Dev (auto-reload)
uv run gunicorn --worker-class eventlet -w 1 app:app  # Prod (Linux, single worker required)
```

Access: http://127.0.0.1:5000 | API docs: /api/docs | React: /react

---

## Architecture

### Dual Frontend System

| System | Location | Tech | Purpose |
|--------|----------|------|---------|
| Jinja2 Templates | `/templates/`, `/static/` | Tailwind CSS 4 + DaisyUI | Traditional Flask views |
| React 19 SPA | `/frontend/` | TypeScript, Vite, shadcn/ui, TanStack Query | Modern UI at `/react` |

Both served by the same Flask app. `frontend/dist/` is gitignored — built by CI/CD.

### Backend Structure

| Directory | Purpose |
|-----------|---------|
| `app.py` | Flask entry point |
| `blueprints/` | Route handlers (UI, webhooks, AI scalper, manual trades) |
| `restx_api/` | REST API `/api/v1/` (Flask-RESTX, Swagger at /api/docs) |
| `broker/` | 24+ broker integrations, each with `api/`, `database/`, `mapping/`, `streaming/`, `plugin.json` |
| `services/` | Business logic (including `services/ai_scalper/`) |
| `database/` | SQLAlchemy models |
| `utils/` | Shared utilities |
| `websocket_proxy/` | Unified WebSocket server (port 8765) |

### 7 Databases (Isolated)

| Database | Purpose |
|----------|---------|
| `db/openalgo.db` | Users, orders, positions, settings, scheduler jobs |
| `db/logs.db` | Traffic and API logs |
| `db/latency.db` | Latency monitoring |
| `db/sandbox.db` | Analyzer/sandbox virtual trading |
| `db/historify.duckdb` | Historical market data (DuckDB) |
| `db/ai_scalper_logs.db` | Auto-trade logs (ENTRY/EXIT events with enriched meta) |
| `db/ai_scalper_ledger.db` | Learning ledger (bandit tuner, model tuner) |
| `db/ai_scalper_tuning.db` | Model tuning runs |
| `db/manual_trade_logs.db` | Manual trade logs (scalping + chart windows) |

### Broker Plugin Pattern

All brokers in `broker/{name}/` follow identical structure:
- `api/auth_api.py`, `api/order_api.py`, `api/data.py`, `api/funds.py`
- `mapping/` — OpenAlgo ↔ broker symbol format
- `streaming/` — WebSocket adapter
- `database/master_contract_db.py` — Symbol mapping
- `plugin.json` — Metadata (loaded dynamically by `utils/plugin_loader.py`)

Reference implementations: `broker/zerodha/`, `broker/dhan/`, `broker/angel/`

### Real-Time Stack

- **Flask-SocketIO**: Order/trade/position updates
- **WebSocket Proxy** (port 8765): Market data streaming
- **ZeroMQ** (port 5555): Internal message bus
- Connection pooling: 1000 symbols × 3 connections = 3000 symbols max

---

## Scalping & Auto-Trade System

### File Map

| File | Purpose |
|------|---------|
| `auto_trading_window.html` | Main auto-trading app (~12K lines, self-contained HTML/CSS/JS) |
| `scalping_interface.html` | Manual scalping interface (opens auto_trading_window as popup) |
| `chart_window.html` | Chart trading window |
| `services/ai_scalper/` | Backend: agent, config, manager, model tuner, scheduler |
| `services/manual_trade_log_store.py` | Manual trade SQLite store |
| `blueprints/ai_scalper.py` | Auto-trade API endpoints |
| `blueprints/manual_trades.py` | Manual trade API endpoints |

### Auto-Trade Data Flow

```
WebSocket ticks → handleAutoTradeTick(side, ltp)
  → updateAutoMomentum() → getMomentumVelocity() → isNoTradeZone()
  → updateRegimeDetection() → autoCanEnter() → placeAutoEntry()
  → recordAutoEntry() → log enriched ENTRY

Exit: updateAutoTrailing() → checkVirtualTPSL() → executeVirtualTPSL()
  → closeAutoPosition() → recordAutoExit() → equity curve + stats
```

### Key State: `autoState` (~80+ fields)

Positions, momentum, trailing (5-stage), P&L, guards, regime, config. Central to all logic.

### Multi-Stage Trailing SL

| Stage | Trigger (default) | SL Level |
|-------|---------|----------|
| 1 (BE) | +beMinProfit (min of stage1Trigger, 1.5) + delay | Breakeven (entry ± buffer) |
| 2 (Lock) | +stage1Trigger pts | Entry + stage2SL pts |
| 3 (Trail) | +stage2Trigger pts | Price ± stage3Distance |
| 4 (Tight) | +stage3Trigger pts | Price ± stage4Distance |
| 5 (Accel) | +accelMovePts in accelTimeMs | Price ± accelDistance |
| Win-streak | 3+ consecutive wins + stage3Trigger | Price ± winStreakTrailDistance |

**Per-Preset Trailing (Scalper-Optimized):**

| Preset | SL | S1(BE) | S2(Lock) | S3(Trail) | S4(Tight) | S5(Accel) |
|--------|-----|--------|----------|-----------|-----------|-----------|
| NIFTY Expiry | 4pt | +1.5→BE | +3→+0.5 | +5→2.5pt | +6→1.5pt | 3pt/8s→1pt |
| NIFTY Normal | 4pt | +2→BE | +3.5→+0.5 | +5→2.5pt | +6→1.5pt | 3.5pt/10s→1.5pt |
| SENSEX Expiry | 5pt | +2→BE | +4→+1 | +6→3pt | +8→2pt | 4pt/8s→1.5pt |
| SENSEX Normal | 5pt | +2.5→BE | +4→+1 | +6→3pt | +8→2pt | 4pt/10s→1.5pt |

### Market Regime Detection (60s window)

- **TRENDING**: Range > volatileThreshold and directional (>0.3)
- **VOLATILE**: Range > volatileThreshold but choppy
- **RANGING**: Range < rangingThreshold → +2 extra momentum ticks required

**Per-Preset Regime Thresholds:**

| Preset | Volatile Threshold | Ranging Threshold |
|--------|-------------------|-------------------|
| NIFTY Normal | 4 | 1.5 |
| NIFTY Expiry | 6 | 2 |
| SENSEX Normal | 5 | 2 |
| SENSEX Expiry | 8 | 3 |

### Entry Filters

1. Momentum velocity ≥ 1.5pts
2. No-trade zone: skip if 30s range < 2pts
3. Consecutive loss breaker: 3+ losses → +2 ticks + 3× cooldown
4. Regime adaptation: RANGING → extra confirmation

### LIVE Mode

- Orders via `placeOrderAtPrice()` → broker API
- Fill detection via `checkOrderFills()` polling
- **Decision-time context** saved in `autoState.pendingOrders[].autoMeta` (momentum, velocity, regime captured at order time, NOT fill time)
- P&L: local tracking with broker fallback (`getAutoPnlSnapshot()`)

### Logging & Analytics

| Layer | Details |
|-------|---------|
| Browser | `autoLogQueue[]` → batched POST `/ai_scalper/logs` every 1.2s |
| Backend | SQLite `db/ai_scalper_logs.db`, extra fields in `meta_json` |
| Manual trades | `manualLogQueue` → POST `/manual_trades/logs` (separate DB) |
| Analytics | `GET /ai_scalper/analytics`, `GET /manual_trades/analytics` |
| Enriched fields | regime, momentumCount, momentumVelocity, consecutiveLosses, winStreak, sessionPnl, tradeNumber, bidAskRatio, spread, trailStage, highWaterMark, maxProfitPts, partialExitDone, isReEntry |

### P&L Calculation Rules

- Options always BUY side: P&L = `(exitPrice - entryPrice) × qty`
- `recordAutoExit()` ALWAYS accumulates `autoState.realizedPnl` (never gate behind skipRealized)
- Equity curve = cumulative realizedPnl after each exit
- In LIVE mode: NEVER use `position.average_price` — use `getAutoEntryPrice(side)` which reads `autoState.liveEntry[side].avg`
- Known browser vs broker discrepancy: slippage, charges (₹20/order + STT + exchange), position-level averaging

### Presets

Four presets in `AUTO_PRESETS`: `conservative`, `balanced`, `aggressive`, `scalper` — applied via `applyAutoPreset()` setting ~30+ config fields.

---

## Critical Functions Reference

| Function | Purpose |
|----------|---------|
| `handleAutoTradeTick(side, ltp)` | Main tick handler — all entry/exit decisions |
| `updateAutoTrailing(side, price)` | 5-stage trailing SL logic |
| `recordAutoExit(side, exitPrice, reason, options)` | P&L calc, equity curve, trade history, logging |
| `recordAutoEntry(side)` | Entry logging with enriched context |
| `getAutoEntryPrice(side)` | Per-trade entry price (liveEntry for LIVE, paperPositions for paper) |
| `getAutoPnlSnapshot()` | Returns {total, open, realized, source} with broker/local fallback |
| `updateAutoStatsUI()` | P&L display, cooldown, trades/min — calls updateAutoIndicatorsUI() |
| `updateAutoIndicatorsUI()` | Trail stage, regime badge, momentum gauge, velocity, losses |
| `updateAutoSummaryStats()` | Session stats panel (Trades, Win%, PF, Avg W/L, Streaks) |
| `renderEquityCurve()` | Canvas-based cumulative P&L chart |
| `placeAutoEntry()` | Entry orchestrator (paper or live) |
| `closeAutoPosition(side, reason)` | Exit orchestrator (paper or live) |
| `autoCanEnter(side)` | Guardrail check (cooldown, loss limit, max trades) |
| `getMomentumVelocity(side)` | Price change over momentum window |
| `isNoTradeZone()` | Flat/choppy market detection |
| `updateRegimeDetection()` | TRENDING/RANGING/VOLATILE classification |
| `executePartialExit(side, price)` | 50% exit at profit target |

---

## Model Tuning Pipeline

### Backend

- `services/ai_scalper/model_tuner.py` — Cloud LLM tuning (OpenAI, Anthropic)
- `services/ai_scalper/model_tuner_scheduler.py` — APScheduler for automated runs
- `services/ai_scalper/agent.py` — Notifies tuner on trade exit
- `services/ai_scalper/config.py` — `ModelTunerConfig`
- `services/ai_scalper/manager.py` — `apply_model_tuning()`

### API Endpoints

- `POST /ai_scalper/model/run` — Trigger tuning
- `GET /ai_scalper/model/status` — Check status
- `GET /ai_scalper/model/recommendations` — Get recommendations
- `POST /ai_scalper/model/apply` — Apply recommendations

### Rules

- Auto-apply only in paper mode; live requires manual apply
- Safety clamps enforce ranges for all tunable params
- Min 30 trades before tuning run is allowed
- Env vars: `OPENAI_API_KEY`, `OPENAI_ADVISOR_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_ADVISOR_MODEL`

---

## Manual Trades Separation

Auto and manual trades are stored in **separate databases** to avoid mixing for model training:

| Source | DB | React Page |
|--------|----|------------|
| Auto trades | `db/ai_scalper_logs.db` | `/auto-trade/analytics` |
| Manual trades | `db/manual_trade_logs.db` | `/manual-trades/analytics` |

- Scalping window: logs ENTRY on fill, EXIT on close
- Chart window: logs ENTRY on position display, EXIT on close/auto-close
- Backend: `services/manual_trade_log_store.py`, `blueprints/manual_trades.py`
- Frontend: `frontend/src/pages/ManualTradeAnalytics.tsx`

---

## Known Bugs & Pitfalls (CRITICAL)

1. **Duplicate function names**: JS silently overrides — always check for existing functions before adding new ones. The `updateAutoStatsUI` duplication bug broke P&L display.
2. **State reset ordering**: Always log/capture state BEFORE resetting `positionHighPrice`, `trailCurrentStage`, `partialExitDone`.
3. **LIVE mode timing**: Capture decision-time context in `autoMeta` at order placement, NOT at fill time.
4. **realizedPnl must always accumulate**: Never gate behind skipRealized or similar flags.
5. **Canvas rendering**: `renderEquityCurve()` needs `parentElement.offsetWidth > 0`; handle single-point case.
6. **Entry price source**: In LIVE mode, NEVER use `position.average_price` for per-trade P&L — use `getAutoEntryPrice(side)`.
7. **Hot path throttling**: `updateAutoStatsUI()` and `updateRegimeDetection()` throttled to 250ms in tick handler. Direct calls from `recordAutoExit()` bypass throttle (intentional).
8. **P&L mismatch (browser vs broker)**: Expected. Causes: fill slippage, brokerage+STT+charges, position-level averaging. Consider showing estimated net P&L.

---

## Code Style

### Python
- PEP 8, 4 spaces, Google-style docstrings
- Imports: stdlib → third-party → local
- Always SQLAlchemy ORM (never raw SQL)
- Consistent JSON responses: `{'status': 'success'|'error', 'message': '...', 'data': {...}}`

### JavaScript (auto_trading_window.html)
- All auto-trade logic in single HTML file (~12K lines)
- State centralized in `autoState` object
- Throttle hot-path UI updates (250ms in tick handler)
- Canvas-based charts (no external charting lib for custom UI)

### React/TypeScript
- Biome.js linting (`frontend/biome.json`)
- Functional components + hooks
- TanStack Query for server state
- PascalCase component files

### Git Commits
- Conventional: `feat:`, `fix:`, `docs:`, `refactor:`

---

## API Patterns

### Authentication
```python
# Body (recommended):
{"apikey": "YOUR_API_KEY", "symbol": "SBIN", ...}
# Header:
X-API-KEY: YOUR_API_KEY
```

### Symbol Format
```
NSE:SBIN-EQ           # Equity
NFO:NIFTY24JAN24000CE # Options
NSE:NIFTY-INDEX       # Index
```

---

## Key Code Locations in `auto_trading_window.html`

| Section | ~Line |
|---------|-------|
| CSS (UI styles) | 746 |
| HTML indicators + equity curve + summary | 2755 |
| `autoState` defaults (~80 fields) | 3047 |
| `getAutoPnlSnapshot()` | 5176 |
| `updateAutoStatsUI()` (P&L display) | 5234 |
| `AUTO_PRESETS` (4 presets) | 6437 |
| `recordAutoExit()` | 6853 |
| `getMomentumVelocity()` + `isNoTradeZone()` | 7114 |
| `autoCanEnter()` / guardrails | 7145 |
| `handleAutoTradeTick()` (main loop) | 7552 |
| `executePartialExit()` | 7848 |
| `updateRegimeDetection()` | 7877 |
| `updateAutoSummaryStats()` | 7900 |
| `renderEquityCurve()` | 8016 |
| `updateAutoIndicatorsUI()` | 8077 |
| `updateAutoTrailing()` (5-stage) | 8099 |
| `placeOrderAtPrice()` + autoMeta | 8913 |

---

## Open Items / Next Steps

1. **P&L mismatch investigation**: Parse trade logs, compare per-trade P&L with broker execution report, quantify slippage + charges
2. **Estimated net P&L**: Show "Net P&L (est.)" deducting ~₹20/trade + STT
3. **Regime threshold tuning**: Most entries classified as RANGING — consider adjusting `regimeRangingThreshold` (currently 2) and `regimeVolatileThreshold` (currently 8)
4. **Trailing stage testing**: Need trending market to exercise stages 3-5
5. **Offline LLM provider**: Add Ollama or HTTP provider for model tuning
6. **Tuning UI enhancements**: Param diff, risk labels, nightly schedule preset
7. **Manual analytics**: Optional raw logs table in Manual Analytics page
