# TOMIC + Unified Scalping Architecture

Last updated: 2026-02-13
Scope: `/scalping-unified` and `/tomic/*` runtime model across Kotak, Dhan, Zerodha instances.

## 1) Purpose

This document defines the current production-style local setup where:

1. Scalping UI can read market data from one broker and execute on another broker.
2. TOMIC multi-agent runtime can run with live market feed and risk-aware signal loop.
3. Multiple broker instances stay online, but autonomous loop control is centralized to one instance.

## 2) Instance Topology

Session-standard local mapping:

1. Kotak: Flask `5000`, WS `8765`, env `.env.kotak`
2. Dhan: Flask `5001`, WS `8766`, env `.env.dhan`
3. Zerodha: Flask `5002`, WS `8767`, env `.env.zerodha`

All three can run in parallel. Cookies must remain unique per instance.

## 3) Route Model

Scalping:

1. `/scalping`: single-instance broker-local behavior.
2. `/scalping-unified`: feed/exec split behavior through multi-broker proxy.

TOMIC:

1. `/tomic/dashboard`
2. `/tomic/agents`
3. `/tomic/risk`
4. Backend control/status APIs under `/tomic/*`.

## 4) Unified Data and Execution Split

Backend bridge: `blueprints/multi_broker.py`

1. `POST /api/multibroker/ws-config`
   - returns feed WS targets plus per-target API key.
   - `auto` feed sequence is Zerodha primary, Dhan fallback.
2. `POST /api/multibroker/v1`
   - proxies selected `/api/v1/*` calls to target broker instance.
   - rewrites `payload.apikey` to target broker API key when present.

Frontend routing behavior:

1. Feed selector controls:
   - WS chart ticks (via `/api/multibroker/ws-config`)
   - feed-side REST reads (option chain, expiries, history, multiquotes)
2. Execution selector controls:
   - place/modify/cancel orders
   - positions/orders/trades/funds
   - scalping P&L source
3. Market order path is direct to execution broker. No cross-broker quote pre-check.

## 5) TOMIC Runtime Wiring

Bootstrap:

1. `app.py` registers `tomic_bp`.
2. `setup_environment()` constructs `TomicRuntime()` and stores it in `app.extensions`.
3. runtime stop is registered on process exit.

Runtime graph:

1. `WSDataManager`
2. `TomicMarketBridge`
3. `RegimeAgent`, `SniperAgent`, `VolatilityAgent`
4. `ConflictRouter`
5. `RiskAgent.enqueue_signal(...)`
6. `ExecutionAgent` and `JournalingAgent`
7. `Supervisor` + circuit breakers

Operational observability:

1. `/tomic/status` includes signal loop and market bridge status.
2. `/tomic/metrics` includes freshness, WS, circuit breakers, market bridge.
3. `/tomic/signals/quality` returns scan quality and routed decision summary.

## 6) TOMIC Environment Keys

Configured in each `.env.*`:

1. Feed endpoints:
   - `TOMIC_FEED_PRIMARY_WS`
   - `TOMIC_FEED_FALLBACK_WS`
2. Feed auth:
   - `TOMIC_FEED_PRIMARY_API_KEY`
   - `TOMIC_FEED_FALLBACK_API_KEY`
3. Execution/analytics:
   - `TOMIC_EXECUTION_REST`
   - `TOMIC_EXECUTION_API_KEY`
   - `TOMIC_ANALYTICS_REST`
4. Loop and alert controls:
   - `TOMIC_SIGNAL_LOOP_ENABLED`
   - `TOMIC_SIGNAL_LOOP_INTERVAL_S`
   - `TOMIC_SIGNAL_ENQUEUE_COOLDOWN_S`
   - `TOMIC_SIGNAL_REJECT_ALERT_THRESHOLD`
   - `TOMIC_FEED_STALE_ALERT_AFTER_S`
   - `TOMIC_ALERT_COOLDOWN_S`
   - `TOMIC_TELEGRAM_ALERTS`

Additional strategy controls (volatility agent):

1. `TOMIC_ENABLE_JADE_LIZARD` (default: `true`)
2. `TOMIC_ENABLE_SHORT_STRANGLE` (default: `true`)
3. `TOMIC_ENABLE_SHORT_STRADDLE` (default: `false`)
4. `TOMIC_ALLOW_NAKED_PREMIUM` (default: `true`)
5. `TOMIC_SHORT_PREMIUM_IV_RANK_MIN` (default: `65`)
6. `TOMIC_SHORT_PREMIUM_IV_HV_MIN` (default: `1.35`)

## 7) Regime-Matched Strategy Routing (TOMIC)

Implemented strategy expansion and routing:

1. New strategy types:
   - `JADE_LIZARD`
   - `SHORT_STRANGLE`
   - `SHORT_STRADDLE`
2. Volatility strategy selector now emits these only when volatility and regime criteria match.
3. Risk agent enforces regime gates before enqueue:
   - `JADE_LIZARD`, `SHORT_STRANGLE`, `SHORT_STRADDLE` are allowed only in `CONGESTION`
   - blocked on `PREMIUMS_TOO_LOW`, `DEFINED_RISK_ONLY`, or `HALT_SHORT_VEGA`
   - only `SELL` direction is valid for these strategies
4. Execution agent routes all legged structures through `/api/v1/optionsmultiorder` when legs are present, including:
   - `IRON_CONDOR`
   - `BULL_PUT_SPREAD`
   - `BEAR_CALL_SPREAD`
   - `JADE_LIZARD`
   - `SHORT_STRANGLE`
   - `SHORT_STRADDLE`
   - `RISK_REVERSAL`
   - `CALENDAR_DIAGONAL`
5. Calendar legs continue to support near/far expiry resolution via `expiry_offset`.

## 8) Flow + TOMIC Orchestration

Flow entry points:

1. `/flow` (workflow list/import)
2. `/flow/editor/:id` (visual editor/execution logs)

TOMIC-aware Flow nodes:

1. `tomicSnapshot` (read runtime diagnostics/signal quality/risk/router)
2. `tomicControl` (`start|pause|resume|stop`)
3. `tomicSignal` (enqueue strategy signal into TOMIC risk queue)

Ready templates:

1. `docs/flow/templates/tomic-observability-flow.json`
2. `docs/flow/templates/tomic-signal-routing-flow.json`
3. `docs/flow/templates/tomic-options-selling-regime-routing-flow.json`

Access model:

1. Open `/flow` on the control instance (recommended `:5002`).
2. Import template JSON from `docs/flow/templates/`.
3. Save and run manually first, then activate schedule.
4. Validate impact in `/tomic/dashboard`, `/tomic/agents`, `/tomic/risk`.

## 9) Recommended Daily Operating Pattern

1. Start all broker instances needed for execution targets.
2. Login once on each instance and generate `/apikey`.
3. Choose one control instance for TOMIC (recommended Zerodha `:5002`).
4. Set signal loop flags:
   - control instance: `TOMIC_SIGNAL_LOOP_ENABLED='true'`
   - other instances: `TOMIC_SIGNAL_LOOP_ENABLED='false'`
5. Use `/scalping-unified` for manual/auto trading with feed/exec selectors.
6. Use `/tomic/*` pages from control instance for runtime operations.

## 10) Failure Playbook

1. `404 /api/multibroker/*`
   - stale server process; restart with updated code.
2. `Invalid openalgo apikey` on unified order
   - target broker not logged in or API key missing; regenerate `/apikey` on target.
3. No option chain after refresh on unified page
   - check feed selector, target broker availability, and `/api/multibroker/v1` response.
4. TOMIC duplicate autonomous behavior
   - more than one instance has signal loop enabled; disable loops on non-control instances.
5. TOMIC feed stale/disconnected alerts
   - verify `TOMIC_FEED_PRIMARY_WS`/fallback endpoints and corresponding feed API keys.

## 11) Related Docs

1. `docs/memory/scalping-session-memory.md`
2. `docs/memory/scalping-next-session-handover.md`
3. `docs/design/scalping-unified-ops-checklist.md`
4. `docs/design/unified-scalping-dashboard-plan.md`
5. `docs/flow/README.md`
6. `docs/design/tomic-flow-automation-ideas.md`
