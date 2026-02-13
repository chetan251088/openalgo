# TOMIC Flow Automation Ideas (n8n-Inspired)

This note captures practical ideas from:

- `https://www.marketcalls.in/openalgo/n8n-algo-trading-workflow-automation-using-openalgo.html`
- `https://docs.openalgo.in/trading-platform/n8n`
- `https://n8n.io/workflows/5711-automated-stock-trading-with-ai-integrating-alpaca-and-google-sheets/`
- `https://fyers.in/community/blogs-gdppin8d/post/automate-build-and-execute-rule-based-strategies-QTdBsgwu5XvXnS7`

## What to Reuse in OpenAlgo Flow

1. Trigger -> Filter -> Action graph
- Keep flows explicit: event source, validation layer, execution layer.
- Use `Start/Webhook/PriceAlert` only for trigger.
- Put all risk checks before order/signal actions.

2. Route-based control paths
- Split success/failure branches for every critical step.
- Send alerts/logs on failures instead of silently skipping.

3. Paper-first + promotion workflow
- Build in paper mode / ghost mode.
- Promote to execute mode only after stability checks.

4. Guard rails before execution
- Account/funds checks
- Time-window checks
- Stale-data checks
- Max-position and daily-loss checks

5. Full observability
- Every run should emit reason-rich logs.
- Keep a compact snapshot node for runtime and routing health.

6. Deterministic payload shaping
- Build a canonical signal payload before enqueue.
- Avoid ad-hoc field names per strategy.

## Implemented in This Repo

New Flow nodes were added:

- `tomicSnapshot` (data): reads runtime diagnostics
  - sources: `status`, `metrics`, `signals`, `positions`, `journal`, `analytics`, `risk`, `router`
- `tomicControl` (action): `start|pause|resume|stop`
- `tomicSignal` (action): enqueue synthetic signal into TOMIC risk queue
  - supports auto strategy selection using snapshot context:
    - `autoSelect=true`
    - `snapshotVariable=tomicSignals`
    - `fallbackStrategy=IRON_CONDOR|BULL_PUT_SPREAD|BEAR_CALL_SPREAD`

Files:

- `services/flow_executor_service.py`
- `frontend/src/components/flow/nodes/TomicSnapshotNode.tsx`
- `frontend/src/components/flow/nodes/TomicControlNode.tsx`
- `frontend/src/components/flow/nodes/TomicSignalNode.tsx`
- `frontend/src/components/flow/panels/NodePalette.tsx`
- `frontend/src/components/flow/panels/ConfigPanel.tsx`
- `frontend/src/components/flow/nodes/index.ts`
- `frontend/src/lib/flow/constants.ts`
- `frontend/src/types/flow.ts`

## Ready-to-Import Flow Templates

- `docs/flow/templates/tomic-observability-flow.json`
- `docs/flow/templates/tomic-signal-routing-flow.json`
- `docs/flow/templates/tomic-options-selling-regime-routing-flow.json`
- `docs/flow/templates/scalping-unified-feed-exec-bridge-flow.json`
- `docs/flow/templates/scalping-auto-trade-controller-flow.json`
- `docs/flow/templates/scalping-ws-virtual-tpsl-bridge-flow.json`

Import from Flow UI -> Import JSON.

## Access and Usage Quick Start

1. Open `/flow` on your control instance (recommended: Zerodha `:5002`).
2. Import one of the templates from `docs/flow/templates/`.
3. Save and run once manually (`Run Now`) to validate output variables and logs.
4. Check `/tomic/dashboard`, `/tomic/agents`, `/tomic/risk` for runtime impact.
5. Activate schedule only after manual run succeeds.

Full runbook:

- `docs/flow/README.md`

## Scalping Virtual TP/SL Bridge

Flow can now enqueue virtual TP/SL attach events for scalping via:

- `POST /api/v1/scalpingbridge`
- `POST /api/v1/scalpingbridge/pending`
- `POST /api/v1/scalpingbridge/ack`

Backend queue service:

- `services/scalping_flow_bridge_service.py`

Frontend consumer hook (shared by `/scalping` and `/scalping-unified`):

- `frontend/src/hooks/useFlowVirtualBridge.ts`
