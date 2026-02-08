# Handover Session Notes (AI Scalper & Scalping)

Date: 2026-02-05
Repo: `c:\algo\openalgov2\openalgo`

## Current Focus
- Model-driven tuning pipeline for AI Scalper using cloud LLM providers.
- **Separate storage and analytics for manual vs auto trades** (no mixing for model analysis).
- Auto-apply only in paper mode; live mode requires manual apply.
- Keep trading loop non-blocking (async queue + scheduler).

---

## Latest: Manual Trades Separation & Manual Analytics (2026-02-05)

### Why
- Auto and manual trades must not be mixed for model training/analysis.
- Manual trades (from Scalping window and Chart window) are stored in a **separate DB** and have a **dedicated React analytics page**.

### Backend
- **Manual trade log store**
  - `services/manual_trade_log_store.py` — async SQLite store for manual trade events.
  - DB: `db/manual_trade_logs.db` (separate from `db/ai_scalper_logs.db`).
  - Same pattern as AutoTrade log store: queue, worker thread, WAL, analytics aggregation (summary, equity, distribution, time/reason/side breakdowns).
- **Blueprint**
  - `blueprints/manual_trades.py`:
    - `POST /manual_trades/logs` — ingest batched events from frontend.
    - `GET /manual_trades/logs` — fetch raw logs (filters: mode, source, symbol, side, underlying, since, until).
    - `GET /manual_trades/analytics` — aggregated analytics (same shape as auto-trade analytics).
- **App**
  - `app.py`: registers `manual_trades_bp`.

### Frontend logging (manual events to backend)
- **Scalping window** (`scalping_interface.html`):
  - Queue + flush: `manualLogQueue`, `flushManualTradeLogs()`, `logManualTrade()` (same pattern as auto logs).
  - On **order place success** (main strike order): `addOrder(orderId, { symbol, action, exchange, strategy: 'scalping', quantity })`.
  - On **order fill** (`handleOrderFill`): if order has `strategy === 'scalping'`, log **ENTRY** (tradeId, symbol, side, action, qty, price).
  - On **position close** (existing block): log **EXIT** (tradeId, symbol, side, action, qty, price, pnl, reason).
- **Chart window** (`chart_window.html`):
  - Same queue/flush/`logManualTrade()` with `source: 'chart'`.
  - **ENTRY**: when `updatePositionDisplay()` sets a new position (qty ≠ 0), log once per position (tracked via `chartManualEntryLogged` set).
  - **EXIT**: in `closePosition()` and `closePositionAuto()` on close success, log EXIT with PnL from `getActivePositionPnl()` before clearing visuals.
  - `clearPositionVisuals()` removes key from `chartManualEntryLogged` so next open logs ENTRY again.

### React: Manual Trade Analytics
- **API**: `frontend/src/api/manual-trades.ts` — `fetchManualTradeAnalytics()`, `fetchManualTradeLogs()`.
- **Page**: `frontend/src/pages/ManualTradeAnalytics.tsx` — same layout as Auto Analytics (filters, summary cards, equity/drawdown chart, PnL distribution, side/reason/time tables, insights). Source filter: **Scalping** / **Chart** (not local/server).
- **Route**: `/manual-trades/analytics` in `App.tsx` (lazy).
- **Nav**: “Manual Analytics” in `navItems` and `profileMenuItems` in `config/navigation.ts`.

### Data flow summary
| Source        | DB / API                    | React page                |
|---------------|----------------------------|---------------------------|
| Auto trades   | `db/ai_scalper_logs.db`     | `/auto-trade/analytics`   |
| Manual trades | `db/manual_trade_logs.db`   | `/manual-trades/analytics`|

---

## Earlier: Model Tuning Pipeline (2026-02-05)

### Backend
- Added model tuning service and storage:
  - `services/ai_scalper/model_tuner.py`
  - Uses AutoTrade logs + Learning ledger for tuning context.
  - Stores runs in `db/ai_scalper_tuning.db`.
- Added scheduler:
  - `services/ai_scalper/model_tuner_scheduler.py` (APScheduler).
  - Scheduler table: `model_tuner_apscheduler_jobs` in `db/openalgo.db`.
- Agent hook on trade exit:
  - `services/ai_scalper/agent.py` notifies tuner after exits.
- Config + manager:
  - `services/ai_scalper/config.py` adds `ModelTunerConfig`.
  - `services/ai_scalper/manager.py` adds `apply_model_tuning()` and updates tuner config.
- API endpoints:
  - `POST /ai_scalper/model/run`
  - `GET /ai_scalper/model/status`
  - `GET /ai_scalper/model/recommendations`
  - `POST /ai_scalper/model/apply`
- App init:
  - `app.py` initializes model tuner scheduler on startup.

### Frontend
- New tuning UI:
  - `frontend/src/pages/AutoTradeModelTuning.tsx`
  - Route: `/auto-trade/tuning`
- Navigation and routes updated:
  - `frontend/src/App.tsx`
  - `frontend/src/config/navigation.ts` + tests
- API client + types:
  - `frontend/src/api/ai-scalper.ts`
  - `frontend/src/types/ai-scalper.ts`

## Behavior Summary
- Supported providers: OpenAI, Anthropic (cloud only).
- Model required: set `OPENAI_ADVISOR_MODEL` or `ANTHROPIC_ADVISOR_MODEL` (or provide `model` in UI).
- Auto-apply only when paper mode is active.
- Safety clamps enforce ranges for momentum/TP/SL/trailing/index bias params.
- Min trades gating (default 30) prevents low-sample runs.

## Environment Variables
- `OPENAI_API_KEY`, `OPENAI_ADVISOR_MODEL`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_ADVISOR_MODEL`
- Optional base URLs via API payload: `model_tuner_base_url`

## Key Files Touched

**Manual trades (latest)**
- `services/manual_trade_log_store.py`
- `blueprints/manual_trades.py`
- `app.py` (register manual_trades_bp)
- `scalping_interface.html` (manual log queue, ENTRY on fill, EXIT on close)
- `chart_window.html` (manual log queue, ENTRY on position display, EXIT on close/auto-close)
- `frontend/src/api/manual-trades.ts`
- `frontend/src/pages/ManualTradeAnalytics.tsx`
- `frontend/src/App.tsx` (route `/manual-trades/analytics`)
- `frontend/src/config/navigation.ts` (Manual Analytics nav)

**Model tuning (earlier)**
- `services/ai_scalper/model_tuner.py`
- `services/ai_scalper/model_tuner_scheduler.py`
- `services/ai_scalper/agent.py`
- `services/ai_scalper/config.py`
- `services/ai_scalper/manager.py`
- `blueprints/ai_scalper.py`
- `frontend/src/pages/AutoTradeModelTuning.tsx`
- `frontend/src/api/ai-scalper.ts`
- `frontend/src/types/ai-scalper.ts`

## Quick Verification
1. Restart Flask; ensure `db/manual_trade_logs.db` is created when first manual log is sent.
2. **Manual trades**: Place/close a trade from Scalping or Chart window; open `/manual-trades/analytics` and Apply Filters — data should appear (after frontend build).
3. **Auto trades**: Confirm `/auto-trade/analytics` still loads from `db/ai_scalper_logs.db`.
4. **Tuning**: Open `/auto-trade/tuning`, set provider + model, Run Tuning; verify runs in `db/ai_scalper_tuning.db` and auto-apply in paper mode.

## Next Suggested Steps
1. Add offline provider (Ollama or HTTP) if needed.
2. Add param diff and risk labels in tuning UI.
3. Add nightly scheduled run preset in UI.
4. Optional: raw manual logs table/view in Manual Analytics page (from `GET /manual_trades/logs`).

---
---

# Session Handover — Auto-Trade Scalping Improvements (2026-02-06)

**Session ID**: 698d4ed5-3296-4928-ba48-3bc0ce058c01
**File Modified**: `auto_trading_window.html` (only file changed)

---

## What Was Done (8 Phases)

### Phase 1: P&L Calculation Bug Fixes ✅
- `recordAutoExit()`: Per-trade P&L strictly `(exitPrice - entryPrice) × qty`
- Sanity guard caps loss at `qty × slPoints × 2`
- Consecutive loss/win tracking added

### Phase 2: Multi-Stage Trailing SL ✅
- Complete rewrite of `updateAutoTrailing()` — 5 stages: BE → Lock → Trail → Tight → Accel
- Breakeven requires +1.5pts AND 3s delay (was 1s + any profit)
- Trail acceleration for fast moves, win-streak wide trail mode
- Position high water mark tracking

### Phase 3: Entry Quality Improvements ✅
- `getMomentumVelocity()` — velocity filter (min 1.5pts price change required)
- `isNoTradeZone()` — skip entry when 30s range < 2pts
- Regime-adaptive entry (+2 extra ticks in RANGING)
- Consecutive loss breaker: 3+ losses → +2 ticks + 3× cooldown
- Default momentum ticks: 4→6, cooldown: 25s→45s

### Phase 4: Exit Improvements ✅
- `executePartialExit()` — 50% exit at +5pts, rest trails tighter
- Momentum-based exit override for strong reversals
- Re-entry within 15s of profitable exit
- Max trade duration: 60s→180s

### Phase 5: UI Enhancements ✅
- Equity curve canvas (`renderEquityCurve()`)
- Summary stats panel (Trades, Win%, PF, P&L, Avg W/L, Streaks, Avg Hold)
- Indicators row: trail stage, regime badge, momentum gauge, velocity, consecutive losses
- Per-trade popup overlay on chart

### Phase 6: Advanced Strategy ✅
- `updateRegimeDetection()` — TRENDING/RANGING/VOLATILE classification
- Regime adapts entry requirements
- Win-streak trail mode (3+ wins → wider trail)

### Phase 7: Trade Log Enrichment for ML ✅
- ENTRY/EXIT/PARTIAL_EXIT logs enriched with: regime, momentumCount, momentumVelocity, consecutiveLosses, winStreak, sessionPnl, tradeNumber, bidAskRatio, spread, trailStage, highWaterMark, maxProfitPts, partialExitDone, isReEntry
- All stored in `meta_json` column by backend automatically

### Phase 8: Post-Test Bug Fixes ✅
- **8.1**: `highWaterMark`/`trailStage` always 0 in exit logs — state was reset BEFORE logging; moved resets AFTER `logAutoTrade()` call
- **8.2**: LIVE entry `momentumCount` wrong — was logged at broker fill time, not decision time; fixed by capturing in `autoMeta` at `placeOrderAtPrice()`
- **8.3**: LIVE P&L display always ₹0 — **THREE root causes**:
  - (a) `getAutoPnlSnapshot()` returned broker data even when broker API returned all zeros; added LOCAL fallback
  - (b) `recordAutoExit()` had `skipRealized: true` for LIVE exits, so `autoState.realizedPnl` never accumulated; removed the guard
  - (c) **Duplicate `updateAutoStatsUI()` function** — new Phase 5 indicators function silently shadowed the original P&L display function; renamed to `updateAutoIndicatorsUI()` and called from within original
- **8.4**: Equity curve flat at 0 — caused by same skipRealized bug (cumulative pnl was 0); also added single-point handling

---

## Open Issue: P&L Mismatch (Browser vs Broker App)

**Status**: Under investigation

### Problem
- Auto-trader shows **+₹1268 realized P&L** in browser
- Broker phone app shows **-₹1427 total MTM** on same LIVE positions
- Discrepancy of ~₹2695

### Likely Causes
1. **Brokerage + STT + charges**: Not accounted for in browser. Indian options: brokerage (₹20/order), STT (0.0625% on sell), exchange charges, GST. Many trades = significant cost.
2. **Fill price slippage**: Browser logs exit at tick price that triggered signal, actual broker fill may differ.
3. **Multiple entries on same strike**: Broker averages all entries into one position; auto-trader tracks per-trade.
4. **Partial fills**: Auto-trader may not account for remaining qty.

### Investigation Steps
1. Read `auto_trade_log_2026-02-06 (5).json` — pair ENTRY/EXIT by `tradeId`
2. Compare entry/exit prices with broker's trade-by-trade execution report
3. Calculate sum of `(exit-entry)*qty` for all trades vs broker's realized P&L
4. Difference = slippage + charges + averaging discrepancy

---

## Key Code Locations in `auto_trading_window.html`

| Section | ~Line |
|---|---|
| CSS (new UI styles) | 746 |
| HTML indicators + equity curve + summary | 2755 |
| `autoState` defaults (~80 fields) | 3047 |
| `getAutoPnlSnapshot()` (local fallback) | 5176 |
| `updateAutoStatsUI()` (P&L display) | 5234 |
| `AUTO_PRESETS` (4 presets) | 6437 |
| `recordAutoExit()` (P&L, equity, logging) | 6853 |
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

## Trade Log Files
- `auto_trade_log_2026-02-06 (5).json` — Latest (needs P&L mismatch analysis)
- `auto_trade_log_2026-02-06 (4).json` — Earlier Feb 6 (first enriched logs)
- `auto_trade_log_2026-02-05*.json` — Feb 5 logs (pre-improvement baseline)

## Skill File
- `.github/instructions/scalping-auto-trade.instructions.md` — Comprehensive skill for future sessions

## Environment
- PowerShell 7 (pwsh) was unavailable — session restart needed to pick up PATH changes
- Working directory: `C:\algo\openalgov2\openalgo`
- Uses `uv` for Python, Node.js for frontend

---

## Next Steps for New Session

1. ~~**Confirm PowerShell works** after restart~~ ✅
2. ~~**Analyze P&L mismatch**~~ ✅ — 87.7% of trade log entries had wrong pnl from pre-fix code; formula is correct
3. ~~**Consider adding estimated charges**~~ ✅ — Net(est) added to P&L status bar and summary panel
4. **Test equity curve** — should render correctly now
5. **Monitor trailing stages** — need trending market to see stages 3-5
6. ~~**Tune regime thresholds**~~ ✅ — per-preset tuning applied

---

# Session Handover — Scalper Optimization (2026-02-06 Session 2)

**File Modified**: `auto_trading_window.html` (only file changed)

## What Was Done

### 1. P&L Mismatch Analysis ✅
- Parsed `auto_trade_log_2026-02-06 (5).json`: 57 trades, 24 winners (42.1%), 33 losers
- Gross P&L (calculated correctly): ₹1,326
- Event log P&L values were wrong (₹-21,567) — artifact of pre-Phase 8 buggy code, not current bug
- After estimated charges: ₹-2,961 net (brokerage ₹2,280 + STT ₹529 + exchange ₹904 + GST ₹573)
- **No code fix needed** — `recordAutoExit()` formula is correct

### 2. Estimated Charges Display ✅
- Added `Net(est)` field to P&L status grid (HTML element `#autoNetPnl`)
- Real-time charge calculation in `updateAutoStatsUI()`: brokerage (₹20/order), STT (0.0625%), exchange (0.053%), GST (18%)
- Summary stats panel already had Gross/Net/Charges — verified working

### 3. Regime Detection Tuning ✅
- **Problem**: All entries classified as RANGING due to overly tight thresholds
- **Per-preset thresholds now**:
  - NIFTY Normal: volatile=4, ranging=1.5
  - NIFTY Expiry: volatile=6, ranging=2
  - SENSEX Normal: volatile=5, ranging=2
  - SENSEX Expiry: volatile=8, ranging=3
- Added `regimeVolatileThreshold` and `regimeRangingThreshold` to presets + `applyAutoPreset()`

### 4. Trailing SL Scalper Optimization ✅
- **Philosophy**: Cut losses fast, trail profits aggressively
- **Defaults changed** (more aggressive):
  - Stage 1 trigger: 3→2pts, Stage 2: 6→4pts, Stage 3: 8→6pts
  - Stage 2 lock SL: 1→0.5pts above entry
  - Stage 3 distance: 3→2.5pts, Stage 4: 2→1.5pts
  - Accel: 4pts/10s→3pts/8s, distance 1.5→1pt
  - Win-streak trail: 4→3.5pts
- **Per-preset trailing** (new — previously not configurable):
  - NIFTY Expiry: fastest BE at +1.5pts, tightest trail (Stage 4 at 1.5pts), SL=4pts
  - NIFTY Normal: BE at +2pts, balanced trail, SL=4pts
  - SENSEX Expiry: BE at +2pts, wider trail for wider ticks, SL=5pts
  - SENSEX Normal: BE at +2.5pts, widest trail, SL=5pts
- **Hardcoded 1.5 breakeven eliminated** — now uses `Math.min(trailStage1Trigger, 1.5)` (configurable)
- Added 10 trailing stage params to `applyAutoPreset()`

### 5. Preset SL Tightened ✅
- NIFTY presets: SL 5→4pts (faster loss cut for smaller tick index)
- SENSEX presets: SL stays at 5pts (wider ticks justify wider SL)
- BE delay reduced to 2s for expiry presets (was 3s)

## Key Code Locations Changed

| Change | Lines |
|--------|-------|
| HTML: Net(est) P&L display | ~2749 |
| autoUi: netPnl element | ~3071 |
| updateAutoStatsUI(): net P&L calc | ~5257 |
| autoState defaults: trailing stages | ~3226 |
| AUTO_PRESETS: 4 presets with regime + trailing | ~6468 |
| applyAutoPreset(): regime + trailing params | ~6721 |
| updateAutoTrailing(): beMinProfit (long) | ~8352 |
| updateAutoTrailing(): beMinProfit (short) | ~8424 |

## Current Trailing Stage Design (Per Preset)

| Preset | SL | S1(BE) | S2(Lock) | S3(Trail) | S4(Tight) | S5(Accel) |
|--------|-----|--------|----------|-----------|-----------|-----------|
| NIFTY Expiry | 4pt | +1.5→entry-0.5 | +3→entry+0.5 | +5→price-2.5 | +6→price-1.5 | 3pt/8s→price-1 |
| NIFTY Normal | 4pt | +2→entry-0.5 | +3.5→entry+0.5 | +5→price-2.5 | +6→price-1.5 | 3.5pt/10s→price-1.5 |
| SENSEX Expiry | 5pt | +2→entry-0.5 | +4→entry+1 | +6→price-3 | +8→price-2 | 4pt/8s→price-1.5 |
| SENSEX Normal | 5pt | +2.5→entry-0.5 | +4→entry+1 | +6→price-3 | +8→price-2 | 4pt/10s→price-1.5 |

## Next Steps
1. **Live test** trailing stages in trending market — verify stages 3-5 activate
2. **Monitor Net(est)** accuracy — compare with actual broker statement end of day
3. **Tune regime** — verify NIFTY/SENSEX now correctly classify TRENDING in volatile sessions
4. **Win rate improvement** — 42% is below breakeven after charges; consider stricter entry filters
