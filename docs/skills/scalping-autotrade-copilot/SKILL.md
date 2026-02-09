---
name: scalping-autotrade-copilot
description: Build, debug, and optimize OpenAlgo's React scalping stack at /scalping. Use when implementing or fixing manual and auto-trade behavior, chart data flow, virtual TP/SL and trigger logic, broker parity (Dhan/Kotak/Zerodha), live P&L sync, WebSocket-vs-REST routing, and legacy-route compatibility.
---

# Scalping Autotrade Copilot

Follow this workflow for all `/scalping` work.

## Load Context First

Read these files in order:

1. `docs/design/scalping-dashboard-architecture.md`
2. `docs/design/unified-scalping-dashboard-plan.md`
3. `docs/design/scalping-autotrade-optimization-plan.md`
4. `docs/memory/scalping-session-memory.md`

## Preserve These Invariants

1. Keep React route live at `/scalping` and keep legacy scalping routes active (`/scalping-legacy`, `/scalping-old`, `/scalping-classic`, `/legacy/scalping`).
2. Prefer WebSocket data for live UI and execution decisions. Use REST only for backfill/history or endpoints that have no WS equivalent.
3. Keep frontend broker-agnostic. Do not hardcode `5000/5001/5002` inside React code.
4. Use same-origin requests and let `frontend/src/api/client.ts` handle local port mismatch fallback.
5. Preserve CSRF behavior for non-`/api/v1` POST calls using `webClient`.
6. Keep signal behavior:
   - `ghost` mode: signal + popup only
   - `execute` mode: signal + popup + order placement + virtual TP/SL lifecycle

## Primary Code Map

Use these files first before broad searching:

1. Page shell and panels:
   - `frontend/src/pages/scalping/ScalpingDashboard.tsx`
   - `frontend/src/components/scalping/ChartPanel.tsx`
   - `frontend/src/components/scalping/OptionChainPanel.tsx`
   - `frontend/src/components/scalping/ControlPanel.tsx`
2. Engine and risk:
   - `frontend/src/hooks/useAutoTradeEngine.ts`
   - `frontend/src/lib/autoTradeEngine.ts`
   - `frontend/src/stores/autoTradeStore.ts`
3. Virtual order lifecycle:
   - `frontend/src/hooks/useVirtualTPSL.ts`
   - `frontend/src/hooks/useTrailingMonitor.ts`
   - `frontend/src/lib/scalpingVirtualPosition.ts`
   - `frontend/src/stores/virtualOrderStore.ts`
4. Data feed and options context:
   - `frontend/src/lib/MarketDataManager.ts`
   - `frontend/src/hooks/useMarketData.ts`
   - `frontend/src/hooks/useOptionChainLive.ts`
   - `frontend/src/hooks/useOptionsContext.ts`
5. Route/back-end bridge:
   - `blueprints/react_app.py`
   - `blueprints/scalping.py`
   - `frontend/src/api/client.ts`

## Fast Triage Checklist

1. Reproduce in browser and capture:
   - console errors
   - failing network calls
   - current URL and broker instance
2. Validate route and build:
   - ensure frontend is built and `frontend/dist/index.html` exists
   - ensure `/scalping` serves React app
3. Validate data path:
   - check `/api/websocket/config`
   - verify WS ticks arrive for index + selected CE/PE
   - verify `/api/v1/history` request payload and response
4. Validate order path:
   - market entry behavior
   - immediate virtual TP/SL attach behavior
   - close action from overlay
5. Validate risk and auto mode:
   - `killSwitch`, re-entry gates, side-open gate
   - `ghost` vs `execute` behavior

## Known Error Signatures

1. `Cannot update oldest data...` from lightweight-charts:
   - check candle time monotonic ordering
   - reset series with `setData` on timeframe/symbol resets before incremental `update`
2. `/api/v1/history` 404:
   - verify current broker app exposes endpoint
   - verify same-origin base URL resolution in `frontend/src/api/client.ts`
3. `/oitracker/api/*`, `/gex/api/*`, `/ivchart/api/*`, `/straddle/api/*` 400 CSRF:
   - ensure calls use `webClient` and CSRF token is present
4. P&L stuck at `+0`:
   - compare scalping P&L hook against positions logic
   - verify broker-specific response mapping and symbol normalization

## Implementation Rules

1. Change behavior in source files only; do not patch only built assets.
2. Keep diffs minimal and targeted.
3. Run `npm run build` under `frontend` after frontend changes.
4. Keep a short memory update in `docs/memory/scalping-session-memory.md` after major behavior changes.

## Acceptance Gate Before Handover

1. `npm run build` succeeds.
2. `/scalping` renders with left/center/right panels and resizable handles.
3. Signal popups appear in both `ghost` and `execute` modes.
4. Auto entry in `execute` mode attaches virtual TP/SL immediately.
5. Live P&L updates on the scalping top bar for tested broker instances.
6. No new console crash loops on timeframe switch.
