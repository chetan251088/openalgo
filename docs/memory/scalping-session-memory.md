# Scalping Session Memory

Last updated: 2026-02-11
Scope: OpenAlgo React scalping stack and related auto-trade/risk work.

## 1) What This Memory Is For

Use this document at the start of future sessions to avoid re-discovery.
It captures:

1. Current architecture and route model
2. Broker deployment conventions
3. Recently implemented behavior
4. Known gaps and pending work
5. Debug and validation runbook

## 2) Deployment Conventions Used in Current Setup

User's local broker mapping (session convention):

1. Kotak: `http://127.0.0.1:5000`
2. Dhan: `http://127.0.0.1:5001`
3. Zerodha: `http://127.0.0.1:5002`

WebSocket mapping noted in project/session:

1. `5000 -> 8765`
2. `5001 -> 8766`
3. `5002 -> 8767`

Note: keep frontend same-origin and broker-agnostic; do not hardcode these ports in React logic.

## 3) Route and UI Model

Current scalping route model:

1. React scalping route: `/scalping` (and `/scalping-v2`) from `blueprints/react_app.py`
2. Legacy scalping routes preserved in `blueprints/scalping.py`:
   - `/scalping-legacy`
   - `/scalping-old`
   - `/scalping-classic`
   - `/legacy/scalping`

Core page layout:

1. Left panel: Option chain
2. Center panel: Index + CE/PE charts
3. Right panel: Control panel (Manual/Auto/Risk/Depth/Orders)

## 4) Data Path Decisions

Data strategy currently targeted:

1. WebSocket-first for live price/decisioning
2. REST for historical candles and endpoints with no WS equivalent
3. Same-origin HTTP client behavior from `frontend/src/api/client.ts`
4. CSRF enforcement for non-`/api/v1` POST endpoints via `webClient`

Important endpoint classes:

1. History candles: `/api/v1/history`
2. Tools APIs:
   - `/oitracker/api/oi-data`
   - `/oitracker/api/maxpain`
   - `/gex/api/gex-data`
   - `/ivchart/api/iv-data`
   - `/straddle/api/straddle-data`

## 5) Key Files for Scalping Work

Frontend execution and state:

1. `frontend/src/pages/scalping/ScalpingDashboard.tsx`
2. `frontend/src/hooks/useAutoTradeEngine.ts`
3. `frontend/src/lib/autoTradeEngine.ts`
4. `frontend/src/stores/autoTradeStore.ts`
5. `frontend/src/hooks/useVirtualTPSL.ts`
6. `frontend/src/hooks/useTrailingMonitor.ts`
7. `frontend/src/lib/scalpingVirtualPosition.ts`
8. `frontend/src/components/scalping/AutoTradeTab.tsx`
9. `frontend/src/components/scalping/LLMAdvisorPanel.tsx`
10. `frontend/src/hooks/useOptionsContext.ts`
11. `frontend/src/api/client.ts`

Backend route bridge:

1. `blueprints/react_app.py`
2. `blueprints/scalping.py`
3. `blueprints/oitracker.py`
4. `blueprints/gex.py`
5. `blueprints/ivchart.py`
6. `blueprints/straddle_chart.py`

## 6) Implemented Changes in Recent Sessions

Major implemented behavior (high level):

1. Auto-trade runtime store expanded with risk and telemetry state:
   - replay mode, kill switch, lock-profit, side counters, decision history, execution samples
2. Entry decision gates activated to avoid no-op settings:
   - side-open block, re-entry controls, per-trade guard usage, kill switch path
3. Auto execution flow attaches virtual TP/SL immediately on entry and tags order management metadata
4. Per-trade max loss and options early-exit integrated in virtual TP/SL monitor
5. Trailing monitor upgraded for multiple active virtual positions (not single-active only)
6. LLM tuning apply logic expanded beyond numeric-only values to include boolean/categorical fields
7. Trader-grade Auto tab panels added:
   - Why Trade
   - Regime Router
   - Execution quality snapshot
   - Risk cockpit
8. Live risk state update wired from scalping live P&L updates
9. Signals now active in both modes with popup behavior:
   - ghost mode: signal only
   - execute mode: signal + execute
10. Virtual line lifecycle hardened:
   - symbol-level dedupe/replacement when creating virtual TP/SL (prevents duplicate/stuck lines)
   - live broker-position reconciliation clears stale virtual lines after closes and aligns qty/action/entry
11. TP/SL trigger close path hardened:
   - virtual TP/SL still fires MARKET close first
   - fallback to `closePosition` if MARKET close call fails
12. Trailing monitor tightened for auto mode:
   - only auto-managed virtual orders are trailed
   - trailing SL updates are monotonic (never loosen SL)
13. LIMIT lifecycle hardened for live brokers:
   - live LIMIT now stays in `pendingLimitPlacement` (entry/TP/SL lines visible on chart)
   - virtual TP/SL is attached only after real fill appears in positionbook reconciliation
   - avoids immediate TP/SL market exits before LIMIT fill
14. Live position-sync grace for virtual lines:
   - reconciliation now waits briefly before pruning virtual lines with no live position
   - prevents immediate disappearance of MARKET/TRIGGER lines while broker positionbook lags
15. Trigger order parity with legacy chart window:
   - trigger direction is now action-driven (`BUY -> above`, `SELL -> below`)
   - trigger placement/drag now blocks immediate-fire levels relative to current LTP
16. Virtual TP/SL close path made exchange-safe:
   - close and trigger-entry MARKET calls now use each order’s own exchange instead of mutable UI exchange
   - prevents wrong-exchange closes when underlying/exchange is switched mid-session
17. Auto trailing SL preservation in live reconciliation:
   - positionbook sync no longer resets auto-managed trailed SL/TP back to static point offsets
   - if broker entry price shifts, existing auto-managed TP/SL are shifted by entry delta instead of reset
18. Auto mode safety gating:
   - execute path now re-checks runtime mode/risk gates before order fire and before virtual attach
   - avoids stray execute fills when toggling from `execute` to `ghost` (or when kill-switch trips) mid-decision
19. LIMIT modify/cancel parity fixes:
   - pending LIMIT placements now capture broker order id from both `data.orderid` and root `orderid` response shapes
   - dragging a pending LIMIT line now consistently calls `modifyOrder` (order id no longer lost)
   - clicking `X` on a pending LIMIT line now attempts broker `cancelOrder` before clearing local lines
20. Same-strike multi-entry behavior (superseded):
   - earlier build used weighted-merge virtual lines for repeated entries on same symbol/side
   - this has now been replaced by fill-anchored per-entry tracking (see items 21-23)
21. Fill-anchored multi-entry behavior (latest update):
   - virtual entry lines now anchor to per-order fill price (orderbook-first), not broker net average
   - repeated entries on same strike now create separate virtual fill records instead of weighted merge
   - chart line title now shows fill anchor plus running weighted average context label
22. Live reconciliation no longer average-snaps lines:
   - live sync keeps fill entry anchors intact and only prunes stale/mismatched lines
   - when broker net qty drops below tracked qty, newest virtual fills are trimmed first (LIFO) to avoid TP/SL over-close
23. LIMIT fill attach now uses pending-order identity:
   - pending LIMIT virtual attach resolves entry from pending order id (with fallback), then creates a fill-specific TP/SL line
24. Auto-engine gating sanity + timing fix:
   - adjusted score-gate now clamps to realistic bounds (prevents impossible `minScore` inflation like `50`)
   - when hot-zone timing is respected and sensitivity is zero, decision now blocks explicitly as timing gate
   - market-clock sensitivity now uses expiry-zone schedule when Expiry preset is active

## 7) Still Important Gaps / Follow-ups

These need explicit follow-up and validation:

1. Indicator depth:
   - some indicator inputs still rely on lightweight snapshots; full indicator parity from old system may still be incomplete
2. Option context WS migration:
   - current context uses REST-backed aggregation; move to WS snapshots where possible for lower latency and lower polling load
3. Broker parity:
   - confirm Kotak candle + P&L behavior during market hours under real live ticks
4. Tools page CSRF / payload hardening:
   - ensure POST payloads and CSRF token handling are valid for all broker instances
5. Timeframe switch robustness:
   - watch for chart series ordering/reset regressions

## 8) Common Failure Signatures and Likely Cause

1. `Cannot update oldest data...` (lightweight-charts):
   - out-of-order candle timestamps or bad update path after timeframe/symbol reset
2. `/api/v1/history` 404:
   - wrong host/port base URL or missing endpoint on current broker instance
3. 400 + `CSRF token is missing` on tools endpoints:
   - request bypassed `webClient`, token fetch failed, or cookies/session not available
4. P&L stuck at `+0` on scalping only:
   - mismatch between scalping position mapping and broker response shape/symbol mapping

## 9) Fast Validation Checklist

Run this after scalping changes:

1. Frontend build:
   - `cd frontend`
   - `npm run build`
2. Route smoke:
   - open `/scalping` and `/scalping-legacy`
3. Data smoke:
   - confirm index + CE/PE candles load
   - switch timeframe and verify no black-screen crash
4. Trading smoke:
   - manual buy/sell and verify immediate virtual TP/SL line attach
   - drag and close lines from chart overlay
5. Auto smoke:
   - ghost mode shows signals + popup without placing order
   - execute mode shows signals and places orders when criteria match
6. P&L smoke:
   - verify top-bar scalping P&L changes in sync with positions

## 10) Guardrails for Future Sessions

1. Prefer source edits over dist bundle edits.
2. Keep route compatibility and broker agnosticism intact.
3. Keep changes incremental and test each behavior slice.
4. Update this file when fixing a major defect or changing core data-flow assumptions.

## 11) Reuse Prompt Template

Use this in a new session to bootstrap context quickly:

`Read docs/skills/scalping-autotrade-copilot/SKILL.md and docs/memory/scalping-session-memory.md, then continue from the latest scalping/autotrade state.`
