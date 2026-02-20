# Scalping Session Memory

Last updated: 2026-02-19
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
25. Chart price-scale readability on load/refresh:
   - CE/PE option charts now use stepped autoscale rounding with 10-point ladders and a minimum vertical span
   - index chart now uses stepped autoscale rounding with 50-point ladders and a wider minimum vertical span
   - helps avoid over-zoomed tick-level Y-axis at startup and preserves big-picture candle readability
26. Auto-entry volume influence added (index + option side):
   - auto engine now consumes WS volume for index spot and CE/PE option symbols
   - decision path adds three volume checks:
     - index volume flow ratio
     - selected side option volume flow ratio
     - selected side vs opposite-side volume dominance
   - weak option participation can block entry when both side-flow and dominance are weak
   - new config knobs exposed in Auto config:
     - enable/disable volume influence
     - lookback ticks
     - min ratios for index flow, option flow, and side dominance
     - volume score weight
   - persisted auto config now merges with `DEFAULT_CONFIG` on hydrate to avoid missing new fields
27. Scalping hotkeys expanded for direct side entries:
   - `ArrowUp` now places CE `BUY` as forced `MARKET`
   - `ArrowDown` now places PE `BUY` as forced `MARKET`
   - both shortcuts use the same post-fill virtual TP/SL attach pipeline as regular hotkey buys
   - bottom-bar and hotkey-help labels updated to reflect new mappings
28. Dhan index exchange normalization hardening:
   - Dhan WS adapter now normalizes known index symbols to the correct index exchange before token lookup
   - `SENSEX` / `BANKEX` / `SENSEX50` are auto-routed to `BSE_INDEX` if a caller accidentally sends `NSE_INDEX`
   - unsubscribe path applies the same normalization for parity with subscribe
29. Auto execute transparency hardening:
   - auto engine now fetches API key on-demand before live auto-entry (same pattern as manual hotkeys)
   - missing API key no longer fails silently; execution sample records `Missing API key` and a throttled toast is shown
   - Auto tab status now shows `SIM (REPLAY)` when execute is selected but replay mode blocks live order placement
   - Execution panel now displays an explicit execution gate state (`OPEN`, `BLOCKED (Replay)`, or `Ghost Mode`)
30. Scalping lot-size parity hardening for SENSEX:
   - SENSEX fallback lot size in scalping store/types updated from `10` to `20`
   - option-chain lot-size hydration now scans chain rows for first valid positive `lotsize` instead of trusting only row 0
   - avoids stale/wrong lot sizing when first row is missing/incomplete and improves Dhan/Kotak parity on underlying switch
31. Refresh-rehydrate underlying parity fix:
   - on persisted scalping state rehydrate, derived fields are now recomputed from restored underlying (`optionExchange`, `indexExchange`, `lotSize`)
   - fixes refresh case where underlying restored as `SENSEX` but exchange stayed at default `NFO`, causing empty/failed expiry + option-chain fetch until manual toggle
32. Unified multi-broker scalping route introduced (non-breaking):
   - new React route `/scalping-unified` wraps existing scalping dashboard without changing `/scalping`
   - UI adds feed/execution selectors in top bar when unified mode is active:
     - feed: `Auto (Zerodha -> Dhan)`, `Zerodha`, `Dhan`
     - execution: `Kotak`, `Dhan`, `Zerodha`
   - unified mode is enabled only on `/scalping-unified` mount and disabled on unmount
33. Multi-broker feed/execute routing bridge added:
   - backend proxy blueprint `/api/multibroker` routes selected `/api/v1/*` requests to chosen broker instance
   - frontend unified routing sends:
     - option-chain + chart/market-data calls to feed broker role
     - order placement/modify/cancel + positions/orders/trades/funds/holdings to execution broker role
   - WebSocket feed bootstrap supports Auto failover sequence `Zerodha -> Dhan`
   - MarketDataManager REST fallback now also respects unified feed role (no hardcoded local `/api/v1/multiquotes` path in unified mode)
   - scalping position/P&L path in unified mode is execution-broker sourced from execution positionbook polling (without feed-side WS quote overlay)
34. Cross-broker OpenAlgo API-key handling in unified mode:
   - proxy now auto-resolves target broker OpenAlgo API key per request (instead of reusing the current instance key)
   - `/api/multibroker/v1` rewrites `payload.apikey` to the selected target broker key before forwarding
   - `/api/multibroker/ws-config` now returns per-target WS `api_key` for feed failover targets
   - requirement: user must be logged in on each target broker instance once and API key must exist on that instance (`/apikey`)
35. Re-entry toggle/state hardening:
   - auto-trade config persistence now sanitizes config types against defaults (boolean and number coercion)
   - fixes stale persisted boolean-like strings making toggles appear stuck (including Re-Entry)
   - Re-entry gate now reports explicit `ON/OFF` decision check and enforces `OFF => first entry only` deterministically
36. TOMIC runtime bootstrap is now live in app startup:
   - `app.py` registers `tomic_bp` and constructs `TomicRuntime()` in `setup_environment()`
   - runtime is attached once per instance (`app.extensions["tomic_runtime"]`) with graceful stop on process exit
   - React routes are live for:
     - `/tomic/dashboard`
     - `/tomic/agents`
     - `/tomic/risk`
37. TOMIC live signal pipeline is wired end-to-end:
   - WS tick flow: `WSDataManager -> TomicMarketBridge -> Regime/Sniper/Volatility`
   - routed decisions: `ConflictRouter -> RiskAgent.enqueue_signal()` with dedupe cooldown
   - observability endpoints include:
     - `/tomic/status` (includes `signal_loop` + `market_bridge` status)
     - `/tomic/signals/quality` (scans + top candidates + decision breakdown)
38. TOMIC operational alerts are now active:
   - alert conditions: feed disconnect/stale, repeated rejects, dead-letter growth, kill-switch transitions
   - alerts publish via `AlertEvent`; risk/critical alerts optionally broadcast to Telegram
   - key env controls:
     - `TOMIC_SIGNAL_LOOP_ENABLED`
     - `TOMIC_SIGNAL_LOOP_INTERVAL_S`
     - `TOMIC_SIGNAL_ENQUEUE_COOLDOWN_S`
     - `TOMIC_SIGNAL_REJECT_ALERT_THRESHOLD`
     - `TOMIC_FEED_STALE_ALERT_AFTER_S`
     - `TOMIC_ALERT_COOLDOWN_S`
     - `TOMIC_TELEGRAM_ALERTS`
39. `.env.*` broker files now include TOMIC endpoint config:
   - feed endpoints:
     - `TOMIC_FEED_PRIMARY_WS`
     - `TOMIC_FEED_FALLBACK_WS`
     - `TOMIC_FEED_PRIMARY_API_KEY`
     - `TOMIC_FEED_FALLBACK_API_KEY`
   - execution/analytics endpoints:
     - `TOMIC_EXECUTION_REST`
     - `TOMIC_EXECUTION_API_KEY`
     - `TOMIC_ANALYTICS_REST`
40. Unified route behavior is now explicit and stable:
   - chart ticks use feed WS targets from `/api/multibroker/ws-config`
   - option chain/expiry/history/multiquotes use feed broker via `/api/multibroker/v1`
   - order placement/modify/cancel + positions/P&L use execution broker via `/api/multibroker/v1`
   - no cross-broker quote pre-check is required before market order fire
41. Cross-broker API-key flow clarification:
   - each broker instance must have valid login session + generated `/apikey`
   - proxy rewrites per-target `apikey` server-side for forwarded `/api/v1/*` calls
42. Control-instance recommendation for TOMIC:
   - run TOMIC loop on exactly one instance (recommended: Zerodha `:5002`)
   - set non-control instances to `TOMIC_SIGNAL_LOOP_ENABLED='false'` to avoid duplicate autonomous loops
43. TOMIC observability upgrade for real-time decision introspection:
   - `/tomic/signals/quality` now includes:
     - router decision trace with per-instrument action + reason
     - aggregated no-action reasons (feed auth, zero signals, router blocks, risk outcomes)
     - risk-agent evaluation telemetry (blocked/sizing reject/enqueued/duplicate)
     - sniper/volatility readiness snapshots (bars, IV/HV/IV-rank state)
   - WS status now exposes auth/error diagnostics (`last_auth_message`, `last_error`, message/error ages)
   - TOMIC UI pages (`/tomic/dashboard`, `/tomic/agents`, `/tomic/risk`) render these diagnostics live
44. TOMIC volatility strategy expansion (regime-matched):
   - added `JADE_LIZARD`, `SHORT_STRANGLE`, `SHORT_STRADDLE` strategy types
   - volatility agent emits these only when volatility/regime criteria are satisfied
45. TOMIC risk gating for new premium-selling strategies:
   - `JADE_LIZARD`/`SHORT_STRANGLE`/`SHORT_STRADDLE` are allowed only in `CONGESTION`
   - blocked when VIX flags indicate `PREMIUMS_TOO_LOW`, `DEFINED_RISK_ONLY`, or `HALT_SHORT_VEGA`
   - enforced as `SELL`-only signals
46. TOMIC execution routing parity for legged options:
   - new legged strategies are routed via `/api/v1/optionsmultiorder` (same legged execution path as spread strategies)
   - validation now requires legs for these strategies before execution
47. New TOMIC env toggles for advanced premium strategies:
   - `TOMIC_ENABLE_JADE_LIZARD`
   - `TOMIC_ENABLE_SHORT_STRANGLE`
   - `TOMIC_ENABLE_SHORT_STRADDLE`
   - `TOMIC_ALLOW_NAKED_PREMIUM`
   - `TOMIC_SHORT_PREMIUM_IV_RANK_MIN`
   - `TOMIC_SHORT_PREMIUM_IV_HV_MIN`
48. Flow + TOMIC visual automation bridge is now available:
   - new Flow nodes: `tomicSnapshot`, `tomicControl`, `tomicSignal`
   - `tomicSignal` supports `autoSelect=true` using `snapshotVariable` context and `fallbackStrategy`
   - ready templates:
     - `docs/flow/templates/tomic-observability-flow.json`
     - `docs/flow/templates/tomic-signal-routing-flow.json`
     - `docs/flow/templates/tomic-options-selling-regime-routing-flow.json`
   - runbook: `docs/flow/README.md`
49. Flow -> scalping virtual TP/SL bridge is now wired:
   - new API namespace: `/api/v1/scalpingbridge` (`enqueue`, `pending`, `ack`)
   - backend in-memory queue service: `services/scalping_flow_bridge_service.py`
   - frontend consumer hook: `frontend/src/hooks/useFlowVirtualBridge.ts`
   - shared dashboard integration means both `/scalping` and `/scalping-unified` consume bridge events
   - new template: `docs/flow/templates/scalping-ws-virtual-tpsl-bridge-flow.json`
50. Unified realtime chart robustness hardening:
   - `/scalping-unified` now forces a clean MarketDataManager reconnect on first mount (and on feed switch) so chart ticks cannot stay pinned to a stale socket target
   - candle-builder and option-chain WS merge paths now normalize lookup keys to uppercase (`EXCHANGE:SYMBOL`) before reading from shared WS cache
   - this prevents silent live-tick misses when symbol/exchange casing differs across REST vs WS payloads
51. New aggressive auto preset added for scalpers:
   - `Adaptive Scalper` preset (`id: adaptive-scalper`) added in `frontend/src/lib/scalpingPresets.ts`
   - keeps existing `Auto-Adaptive` preset unchanged for conservative/context-aware users
   - lowers entry gate and momentum thresholds, relaxes timing/no-trade constraints, and speeds re-entry cadence
   - intended for users who want higher auto-entry frequency in active scalping windows
52. Adaptive-scalper execution/trailing behavior hardening:
   - adaptive-scalper preset now disables IV-spike early-exit to prevent immediate enter->exit churn
   - virtual TP/SL attach for auto entries now guarantees fallback TP/SL points from auto config when UI TP/SL are zero
   - virtual options-context early-exit checks now wait `3s` after entry before triggering
   - trailing monitor adds adaptive-scalper mode: once LTP crosses entry, SL trails continuously at `2` points from live price with monotonic updates
53. Kill-switch + adaptive pyramiding behavior update:
   - lock-profit trigger no longer force-sets `killSwitch`; kill-switch now remains manual/daily-loss driven
   - adaptive-scalper can add to winning positions after trailing starts (profit + trailing stage required)
   - add-on cap is `+2 lots` beyond configured entry lots for that side while profit persists
   - side-open gate now shows explicit pyramiding allowance in decision checks when eligible
54. Manual order trailing parity update:
   - trailing monitor now includes manual/hotkey/trigger-managed virtual TP/SL orders (not only auto-managed)
   - manual `MARKET`, filled `LIMIT`, and triggered `TRIGGER` entries now use cross-entry trailing logic
   - once LTP crosses filled entry price, SL starts trailing continuously at `2` points with monotonic updates
   - auto runtime trail/high store fields remain auto-only to avoid manual-trade noise in auto diagnostics
55. Trailing TP sync enhancement:
   - while SL trails, virtual TP now shifts by the same delta as SL (BUY: upward, SELL: downward)
   - keeps TP and SL moving together once trailing starts, preserving TP-SL spread
   - applies to both cross-entry trailing path and stage-based trailing path when trailing SL updates
56. CE/PE order-flow overlay view (toggleable):
   - chart toolbar now includes a `FLOW` toggle to turn CE/PE order-flow overlay on/off on demand
   - option charts subscribe to quote/depth feed in flow mode and compute 30s buy/sell flow, delta, cumulative delta, spread, and depth imbalance
   - attribution uses volume-delta + price direction when possible (`VOL`) with fallback estimation (`EST`) when exact aggressor mapping is unavailable in feed
   - designed as a lightweight overlay so base chart/entry interactions remain unchanged
57. Order-flow trade-bias hint added:
   - FLOW card now derives a bias label from delta + cumulative-delta alignment and imbalance strength
   - outputs one of: `BUY CE NOW`, `BUY PE NOW`, or `HOLD FOR NOW`
   - low activity or weak/mixed flow automatically resolves to `HOLD FOR NOW`
58. Candle latency parity tuning for scalping charts:
   - `useCandleBuilder` now supports feed mode selection and chart views use `Quote` mode instead of `LTP`
   - aligns candle feed behavior with option-chain quote feed path for faster visual parity
   - tick de-dup key now includes timestamp + LTP + volume to avoid dropping valid same-ms price updates
59. FLOW overlay live-update regression fix:
   - option chart FLOW now consumes both `Quote` and `LTP` streams, then merges payload fields per symbol
   - update cadence uses freshest tick timestamp, while preferring quote fields for volume/bid/ask/depth
   - restores FLOW responsiveness after candle feed-mode changes without reverting chart latency tuning
60. Manual execution visibility + failure feedback:
   - successful `/api/v1/placeorder` now records `lastOrderAck` (order-id, broker, symbol, action, timestamp) in scalping store
   - scalping top bar shows `ACK: <order-id>` badge in live mode, alongside execution broker badge
   - manual tab, floating widget, and hotkey placement now surface on-screen toast errors for missing symbol/API key, order rejections, and close failures
61. FLOW zero-value resiliency enhancement:
   - FLOW parser now accepts alternate tick field names (`last_price`, `lp`, `bid/ask`, broker-specific size aliases) instead of only strict `ltp/volume/bid_price/ask_price`
   - when volume delta is unavailable, FLOW infers pressure from bid/ask size consumption + replenishment and LTP drift before final 1-tick fallback
   - flow state now tracks prior bid/ask sizes to keep 30s flow active on sparse-volume feeds where raw volume often stays flat
62. FLOW display + flat-tick heartbeat refinement:
   - FLOW number formatting now shows decimal values for small activity, preventing weak but real flow from rendering as `0`
   - removed `ltq` from cumulative-volume inference to avoid false `VOL` classification on non-cumulative fields
   - when fresh ticks arrive but price/depth/volume provide no directional signal, FLOW adds a tiny balanced heartbeat (`EST`) so overlay does not appear stalled
63. Unified hotkey redirect hardening for SENSEX/error paths:
   - frontend auth interceptors now treat `/api/multibroker/*` `401` responses as non-redirecting unless message indicates true session auth failure (`Not authenticated` / `Authentication required` / `Session expired`)
   - prevents `/scalping-unified` from force-redirecting to login/dashboard on broker-target API-key/permission failures during hotkey/manual trade requests
   - hotkey error parser now surfaces backend `message`/`error` text so unified-order failures show actionable toasts instead of generic Axios status text
64. Kotak option FLOW depth-parity fix:
   - option chart FLOW now subscribes to `Depth` mode in addition to `Quote` and `LTP` while FLOW is enabled
   - FLOW parser now accepts Kotak depth totals (`totalbuyqty` / `totalsellqty`) and bid/ask aliases (`bp` / `sp`)
   - spread fallback now uses top-of-book depth prices when quote bid/ask are absent, reducing false `No L2 quote` states on Kotak options
65. Chart-focus mode for bigger chart workspace:
   - top bar now has a `Chart Focus` toggle that hides both left Option Chain and right Control Panel
   - when enabled, center `ChartPanel` expands to full dashboard width; toggle changes to `Show Panels`
   - implemented in shared `ScalpingDashboard`, so it works in both `/scalping` and `/scalping-unified`
66. Unified close-all reliability hardening for fast scalping exits:
   - unified `tradingApi.closeAllPositions()` now calls execution-broker `/api/v1/closeposition` first (broker-native flatten path)
   - after broker close-all, a short verification sweep checks remaining open positions and force-closes leftovers with parallel MARKET reverse orders
   - unified `tradingApi.closePosition()` now resolves targets with resilient symbol/exchange/product matching (fallback to symbol-level) and closes all matched legs in parallel
   - hotkey/dashboard and manual-tab `Close All` now run `cancelAllOrders` alongside close-all and only clear virtual lines after confirmed close response
   - dashboard hotkey close/close-all/reversal handlers now validate API status and show error toasts instead of silently treating non-success responses as success
67. Manual trailing distance is now configurable from scalping UI:
   - new persisted store field `trailDistancePoints` (default `2`) added to scalping state
   - Manual tab now shows `Trail SL` input next to TP/SL, and floating trade widget also exposes compact `TR` input
   - new manual/hotkey/trigger virtual entries carry `trailDistancePoints` snapshot per position
   - pending LIMIT attach path also preserves the snapshot so post-fill virtual lines trail with the intended distance
   - trailing monitor now uses per-position `trailDistancePoints` for manual/hotkey/trigger cross-entry trailing instead of fixed 2-point distance
68. Super-fast scalping mode hardwired for lightning response:
   - fill-resolution now bypasses broker orderbook lookups in fast mode and anchors from preferred/cache/fallback local price immediately
   - manual, hotkey, and floating widget MARKET entry paths now feed local market-price hints into virtual-line attach
   - scalping positionbook poll interval reduced from `2500ms` to `1000ms` for quicker post-trade and post-close reconciliation

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

`Read docs/skills/scalping-autotrade-copilot/SKILL.md, docs/memory/scalping-session-memory.md, docs/memory/scalping-next-session-handover.md, and docs/design/tomic-unified-architecture.md, then continue from the latest unified scalping + TOMIC control-instance state.`

Token-light alternatives:

1. `docs/memory/quick-resume-prompts.md`

## 12) Unified Route Ops (Day-To-Day)

Use this model for `/scalping-unified`:

1. Keep all target broker instances running in background:
   - Kotak `:5000`
   - Dhan `:5001`
   - Zerodha `:5002`
2. Open `/scalping-unified` from any one instance.
3. Use `Feed` selector for chart/option-chain market data source.
4. Use `Exec` selector for order placement + positions + P&L source.
5. Ensure each target instance has:
   - active login session
   - API key generated on `/apikey`

Quick sanity checks:

1. `/api/multibroker/config` should return `401` when not logged in and `200` when logged in.
2. Feed switch should update option chain and chart stream without page reload.
3. Exec switch should move order destination and P&L/positions source.

Reference checklist:

1. `docs/design/scalping-unified-ops-checklist.md`
2. `docs/design/tomic-unified-architecture.md`
3. `docs/flow/README.md`

## 13) Unified + TOMIC Runtime (Recommended Daily Model)

Use this model to avoid duplicate loops and routing ambiguity:

1. Keep all broker instances running in background if they are needed as selectable execution targets.
2. Use one control instance for TOMIC dashboards and loop control (recommended Zerodha `:5002`).
3. Set env for control/worker roles:
   - control instance: `TOMIC_SIGNAL_LOOP_ENABLED='true'`
   - non-control instances: `TOMIC_SIGNAL_LOOP_ENABLED='false'`
4. On every running instance, login once and generate `/apikey`.
5. Open `/scalping-unified` from any instance for manual/auto scalping feed-exec split.
6. Open `/tomic/dashboard` only on control instance for agent/loop operations.

If unified orders fail with `Invalid openalgo apikey`:

1. Verify target execution broker session is active.
2. Re-generate `/apikey` on that target broker instance.
3. Retry order from `/scalping-unified`.

## 14) Safe Upstream Merge (Repeatable Process)

When upstream sync is requested, always follow:

1. `docs/design/safe-upstream-merge-runbook.md`

Default command:

1. `pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -BuildFrontendDist`

Notes:

1. Script creates and pushes backup branch before merge.
2. Script prefers local side for generated `frontend/dist/*` conflicts.
3. Use `-NoPush` if you want to inspect merge result before pushing.
