# OpenAlgo Scalping & Auto-Trade System

## System Overview

This workspace contains an algorithmic trading platform (OpenAlgo) with a custom-built scalping and auto-trading system for Indian options markets (NIFTY/SENSEX). The primary file is `auto_trading_window.html` — a self-contained ~12K line HTML/CSS/JS application.

## Architecture

### File Structure
- **`auto_trading_window.html`** — Main auto-trading application (popup window)
- **`scalping_interface.html`** — Manual scalping interface (opens auto_trading_window as popup)
- **`chart_window.html`** — Chart trading window
- **`ai_scalper_architecture.md`** — Architecture documentation

### How It Works
1. Opened as popup from `scalping_interface.html` with URL params: `symbol`, `exchange`, `apikey`, `lotsize`
2. Left dock panel with controls, main chart area using Lightweight Charts library
3. **Dual mode**: Paper (virtual positions in browser) and LIVE (orders via `/api/v1/placeorder`)
4. WebSocket ticks drive all decision-making in real-time

### Data Flow
```
WebSocket ticks → handleAutoTradeTick(side, ltp)
  → updateAutoMomentum() → momentum detection
  → getMomentumVelocity() → velocity filter
  → isNoTradeZone() → chop filter
  → updateRegimeDetection() → market regime
  → autoCanEnter() → guardrails check
  → placeAutoEntry() → enterPaperPosition() OR placeOrderAtPrice()
  → recordAutoEntry() → log enriched ENTRY event

Exit flow:
  → updateAutoTrailing(side, price) → 5-stage trailing SL
  → checkVirtualTPSL() → TP/SL hit detection
  → executeVirtualTPSL() → closeAutoPosition()
  → closePaperPosition() OR closePosition()
  → recordAutoExit() → log enriched EXIT event → equity curve + stats
```

### Key State Object: `autoState`
Central state object holding ~80+ fields including:
- **Positions**: `paperPositions`, `positionEntryTs`, `activeTradeId`
- **Momentum**: `momentumCount`, `momentumVelocity`, `lastMomentumTick`
- **Trailing**: `trailCurrentStage`, `positionHighPrice`, `trailingAnchor`
- **P&L**: `realizedPnl`, `equityCurve`, `tradeHistory`
- **Guards**: `consecutiveLosses`, `winStreak`, `cooldownUntil`, `maxConsecLosses`
- **Regime**: `currentRegime`, `regimeHistory`
- **Config**: `momentumMinMovePts`, `consecutiveLossBreaker`, `reEntryWindowMs`

### Multi-Stage Trailing SL System
- **Stage 1 (BE)**: +1.5pts profit + 3s delay → SL at breakeven
- **Stage 2 (Lock)**: +3pts → SL at entry + 1pt
- **Stage 3 (Trail)**: +8pts → Trail at price - 3pts
- **Stage 4 (Tight)**: +8pts → Trail at price - 2pts
- **Stage 5 (Accel)**: +4pts in <10s → Trail at price - 1.5pts
- **Win-streak mode**: 3+ consecutive wins → wider trail at price - 4pts

### Market Regime Detection
- **TRENDING**: 60s price range > `regimeVolatileThreshold` (default 5) and directional (>0.3), OR mid-range with directionality >0.25
- **VOLATILE**: 60s range > threshold but choppy
- **RANGING**: 60s range < `regimeRangingThreshold` (default 3)
- Regime uses its own 60s window (separate from 30s no-trade-zone window)
- Regime affects: entry threshold (+2 ticks in RANGING), trail behavior

### Entry Quality Filters
1. **Momentum velocity**: Requires minimum price movement (1.5pts) over momentum window
2. **No-trade zone**: Skips entry when 30s range < 2pts (flat market)
3. **Consecutive loss breaker**: After 3 losses → +2 momentum ticks + 3× cooldown
4. **Regime adaptation**: Ranging market requires extra momentum confirmation

### LIVE Mode Specifics
- Orders placed via `placeOrderAtPrice()` → broker API
- Fill detection via `checkOrderFills()` polling
- **Decision-time context** captured in `autoState.pendingOrders[].autoMeta` (momentumCount, velocity, regime) because state may change between order placement and fill confirmation
- P&L display uses local tracking with broker API fallback (`getAutoPnlSnapshot()`)

### Logging Infrastructure
- **Browser**: In-memory queue (`autoLogQueue[]`) → batched POST to `/ai_scalper/logs` every 1.2s
- **Backend**: SQLite at `db/ai_scalper_logs.db`, extra fields in `meta_json` column
- **Learning**: `db/ai_scalper_ledger.db` with bandit tuner, model tuner
- **Analytics**: `GET /ai_scalper/analytics` (win rate, equity curve, time breakdown)
- **Enriched fields**: regime, momentumCount, momentumVelocity, consecutiveLosses, winStreak, sessionPnl, tradeNumber, bidAskRatio, spread, trailStage, highWaterMark, maxProfitPts, partialExitDone, isReEntry

### Presets System
Four presets in `AUTO_PRESETS`: `conservative`, `balanced`, `aggressive`, `scalper`
Applied via `applyAutoPreset()` which sets ~30+ config fields

### P&L Calculation (Critical)
- Options are always BUY side: Entry = BUY, Exit = SELL
- Per-trade P&L: `(exitPrice - entryPrice) × qty`
- `recordAutoExit()` always updates `autoState.realizedPnl` (no skipRealized guard)
- Equity curve stores cumulative `realizedPnl` after each exit
- `getAutoPnlSnapshot()` tries broker API first, falls back to local tracking if broker returns zeros
- **Known discrepancy**: Browser P&L vs broker P&L differs due to: (a) fill price slippage, (b) brokerage/STT/charges, (c) broker uses position-level average vs per-trade tracking

### Tick & Lot Configuration
- `CONFIG.tickSize = 0.05` for NIFTY/SENSEX options
- Lot sizes: NIFTY=75 (varies by contract), SENSEX=10, configurable via URL param
- `state.lotSize` set from URL, used for qty calculations

### UI Components
- **Equity Curve**: Canvas-based cumulative P&L chart (`renderEquityCurve()`)
- **Summary Stats**: Trades, Win%, PF, P&L, Avg W/L, Streaks, Avg Hold (`updateAutoSummaryStats()`)
- **Indicators Row**: Trail stage, regime badge, momentum gauge, velocity, consecutive losses (`updateAutoIndicatorsUI()`)
- **Trade Popup**: Animated overlay showing per-trade result (`showTradePopup()`)
- **P&L Display**: Auto P&L, Realized, Open fields (`updateAutoStatsUI()`)

### Important Functions Reference
| Function | Purpose |
|---|---|
| `getAutoEntryPrice(side)` | Per-trade entry price: prefers liveEntry for LIVE, paperPositions for paper |
| `handleAutoTradeTick(side, ltp)` | Main tick handler, entry/exit decisions |
| `updateAutoTrailing(side, price)` | 5-stage trailing SL logic |
| `recordAutoExit(side, exitPrice, reason, options)` | P&L calc, equity curve, trade history, logging |
| `recordAutoEntry(side)` | Entry logging with enriched context |
| `getAutoPnlSnapshot()` | Returns {total, open, realized, source} with broker/local fallback |
| `updateAutoStatsUI()` | Updates P&L display, cooldown, trades/min, signals + calls updateAutoIndicatorsUI() |
| `updateAutoIndicatorsUI()` | Updates trail stage, regime, momentum gauge, velocity, losses |
| `updateAutoSummaryStats()` | Computes and renders session stats panel |
| `renderEquityCurve()` | Canvas-based cumulative P&L chart |
| `placeAutoEntry()` | Entry orchestrator (paper or live) |
| `closeAutoPosition(side, reason)` | Exit orchestrator (paper or live) |
| `autoCanEnter(side)` | Guardrail check (cooldown, loss limit, max trades, etc.) |
| `getAutoGuardrailBlockReason(side)` | Returns human-readable block reason |
| `getMomentumVelocity(side)` | Price change over momentum window |
| `isNoTradeZone()` | Detects flat/choppy market |
| `updateRegimeDetection()` | Classifies TRENDING/RANGING/VOLATILE |
| `executePartialExit(side, price)` | Exits 50% at profit target |
| `applyAutoPreset(name)` | Applies preset config |

### Common Bugs & Pitfalls
1. **Duplicate function names**: If you add a new function, ensure it doesn't shadow an existing one (JS silently overrides). The `updateAutoStatsUI` duplication bug caused P&L display to never update.
2. **State reset ordering**: Always log/capture state BEFORE resetting fields like `positionHighPrice`, `trailCurrentStage`, `partialExitDone`
3. **LIVE mode timing**: Capture decision-time context (momentum, regime) in `autoMeta` at order placement, not at fill confirmation time
4. **realizedPnl must always accumulate**: Never gate it behind skipRealized or similar flags
5. **Canvas rendering**: `renderEquityCurve()` needs `parentElement.offsetWidth > 0`; handle single-point case by prepending zero baseline
6. **Entry price source**: In LIVE mode, NEVER use `position.average_price` for per-trade P&L — it's the broker's position-level average across ALL entries on the same strike. Always use `getAutoEntryPrice(side)` which prefers `autoState.liveEntry[side].avg`
7. **Hot path throttling**: `updateAutoStatsUI()` and `updateRegimeDetection()` are throttled to 250ms in the tick handler. Direct calls from `recordAutoExit()` bypass the throttle (intentional — exit events need immediate UI update)

### Development Notes
- **Always use `uv run`** for Python commands (never global Python)
- CSS compiled via root-level `npm run build` (Tailwind + DaisyUI for Jinja2 templates)
- React frontend at `/frontend/` is separate — `npm run build` there
- All broker integrations follow plugin pattern in `broker/{name}/`
- WebSocket proxy on port 8765, ZeroMQ on port 5555
