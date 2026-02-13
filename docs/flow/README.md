# Flow + TOMIC Integration Runbook

Last updated: 2026-02-13

## 1) What This Feature Does

Flow now has direct TOMIC integration nodes so you can:

1. Read live TOMIC state and diagnostics (`tomicSnapshot`)
2. Start/pause/resume/stop runtime (`tomicControl`)
3. Enqueue TOMIC strategy signals from Flow (`tomicSignal`)

This lets you orchestrate TOMIC behavior visually from `/flow` without changing core agent code.

## 2) Where To Access It

1. Open Flow UI at `/flow` on your chosen control instance.
2. Open TOMIC monitoring at:
   - `/tomic/dashboard`
   - `/tomic/agents`
   - `/tomic/risk`

Recommended control instance: Zerodha (`http://127.0.0.1:5002`) with only this instance running `TOMIC_SIGNAL_LOOP_ENABLED='true'`.

## 3) Prerequisites

1. Control broker instance is running and logged in.
2. OpenAlgo API key exists on that instance (`/apikey`).
3. TOMIC runtime routes are live (`/tomic/*` returns JSON/UI).
4. Other broker instances can stay running for execution targets, but should keep signal loop disabled.

## 4) Import Ready Templates

Template files:

1. `docs/flow/templates/tomic-observability-flow.json`
2. `docs/flow/templates/tomic-signal-routing-flow.json`
3. `docs/flow/templates/tomic-options-selling-regime-routing-flow.json`
4. `docs/flow/templates/scalping-unified-feed-exec-bridge-flow.json`
5. `docs/flow/templates/scalping-auto-trade-controller-flow.json`
6. `docs/flow/templates/scalping-ws-virtual-tpsl-bridge-flow.json`

Import steps:

1. Go to `/flow`
2. Click `Import`
3. Upload one JSON file (or paste its JSON)
4. Open the imported workflow
5. Click `Save`
6. Use `Run Now` for manual execution, or `Activate` for scheduled runs

## 5) Template Intent

1. `tomic-observability-flow.json`
   - Pulls runtime status, signal quality, and router diagnostics
   - Best for operational visibility
2. `tomic-signal-routing-flow.json`
   - Demonstrates Flow-to-TOMIC signal enqueue path
   - Good for integration testing
3. `tomic-options-selling-regime-routing-flow.json`
   - Auto-selects between `IRON_CONDOR`, `BULL_PUT_SPREAD`, `BEAR_CALL_SPREAD`
   - Uses live `tomicSnapshot(source=signals)` context and enqueues one sell-side strategy
4. `scalping-unified-feed-exec-bridge-flow.json`
   - Feed/Exec split check using separate base URLs and API keys
   - Reads quote + option chain from feed instance and positions from execution instance
   - Does not place orders
5. `scalping-auto-trade-controller-flow.json`
   - Starts backend auto-trader (`/ai_scalper/start`)
   - Pulls status/logs/analytics for quick run-time visibility
   - Useful as control/monitoring wrapper for backend auto-trade service
6. `scalping-ws-virtual-tpsl-bridge-flow.json`
   - Uses `subscribeQuote` (WebSocket-first) to fetch live option LTP.
   - Enqueues bridge events into `/api/v1/scalpingbridge` for virtual TP/SL attachment.
   - Does not place broker orders by itself.

## 6) Unified/Auto Template Setup

Before first run for templates 4 and 5:

1. Replace `REPLACE_FEED_OPENALGO_API_KEY` and `REPLACE_EXEC_OPENALGO_API_KEY` in template 4.
2. Replace `REPLACE_SCALPING_API_KEY` in template 5.
3. Set broker base URLs as needed (`5000/5001/5002`) via the variable nodes.
4. Update expiry in template 4 (`expiryDate`) to valid current contract.

Before first run for template 6:

1. Replace `REPLACE_SCALPING_API_KEY`.
2. Set `baseUrl` to the same instance where `/scalping` or `/scalping-unified` is open.
3. Update `optionSymbol`, `optionExchange`, and `entryQty`.
4. Keep this flow inactive unless you intentionally want periodic bridge entries.

## 7) Node Configuration Quick Reference

1. `tomicSnapshot`
   - `source`: `status|metrics|signals|positions|journal|analytics|risk|router`
   - `runScan` (for `signals`) to refresh signal-quality snapshot in the same run
   - `outputVariable` for downstream node references
2. `tomicControl`
   - `action`: `start|pause|resume|stop`
   - optional `reason` for pause audit trail
3. `tomicSignal`
   - Base fields: instrument, strategy, direction, exchange, product, confidence, lot size
   - `autoSelect=true` enables regime-driven strategy selection
   - `snapshotVariable` points to prior `tomicSnapshot` output (default `tomicSignals`)
   - `fallbackStrategy` used when no eligible volatility strategy is present

## 8) Auto Options-Selling Selection Logic

When `tomicSignal.autoSelect=true`:

1. Read `top_volatility` and diagnostics from the snapshot variable.
2. Prefer first eligible strategy in this set:
   - `IRON_CONDOR`
   - `BULL_PUT_SPREAD`
   - `BEAR_CALL_SPREAD`
3. If no eligible volatility signal is available, infer from regime:
   - bullish bias -> `BULL_PUT_SPREAD`
   - bearish bias -> `BEAR_CALL_SPREAD`
   - congestion/neutral -> `IRON_CONDOR`
4. If still unresolved, use `fallbackStrategy`.
5. Direction is forced to `SELL` in auto mode.
6. If legs are missing, default legs are generated for spread execution.

## 9) Verify It Is Working

After running a flow:

1. Check Flow execution logs for `queued` status and selected strategy metadata.
2. Check `/tomic/risk` for enqueue counters and pending queue movement.
3. Check `/tomic/dashboard` and `/tomic/agents` for routed/enqueued decision traces.

## 10) Troubleshooting

1. No action in flow:
   - verify WS auth and feed freshness on `/tomic/risk`
   - inspect `no_action_reasons` from `tomicSnapshot(source=signals)`
2. Queue enqueued but no execution:
   - risk gates may block (freshness, invalid direction, position caps)
   - execution broker may reject product/symbol/lot-size constraints
3. Imported flow runs but TOMIC endpoints fail:
   - confirm runtime is bootstrapped in this instance and `/tomic/status` is reachable
4. Bridge events are queued but no TP/SL line appears:
   - keep scalping page open (`/scalping` or `/scalping-unified`) on the same instance
   - verify API key matches that instance
   - confirm symbol/exchange/quantity values are valid
