# Scalping Next-Session Handover

Date: 2026-02-13
Commit baseline: `63f5d091` on `main`

## Current State

1. React scalping route `/scalping` is active.
2. Legacy routes are still active: `/scalping-legacy`, `/scalping-old`, `/scalping-classic`, `/legacy/scalping`.
3. Auto engine/risk runtime and trader-grade Auto UI were upgraded.
4. Signals are active in both modes:
   - `ghost`: signal + popup only
   - `execute`: signal + popup + auto execution
5. Frontend build passed after these changes.
6. Trigger/TP-SL parity fixes landed:
   - TRIGGER now enforces legacy action-direction rules (`BUY` above / `SELL` below current LTP)
   - virtual close/trigger execution uses per-order exchange (safe across underlying switches)
   - live reconciliation preserves auto-trailed SL instead of resetting to base SL points
   - execute mode re-checks mode/risk gates before order fire and before virtual attach
7. Pending LIMIT line parity fixes landed:
   - pending LIMIT state now robustly stores broker order id across response shapes
   - drag-to-reprice for pending LIMIT now sends `modify_order` reliably
   - `X` on pending LIMIT now calls broker `cancel_order` before line cleanup
8. Multi-entry line behavior improved:
   - repeated entries on same strike/side are now tracked as fill-anchored virtual entries (no weighted overwrite)
   - line labels can still reflect running average context while anchors stay on fill prices
9. Fill-anchored entry behavior update:
   - same-strike repeated entries now keep per-fill virtual lines (no weighted merge overwrite)
   - virtual lines no longer snap to broker average during live reconciliation
   - pending LIMIT attach resolves fill entry via order id and keeps TP/SL relative to that fill anchor
10. Auto decision-gate/timing update:
   - auto score gate no longer explodes to impossible min-score values in low-sensitivity windows
   - timing is now explicitly gated when hot-zone sensitivity is zero
   - Expiry preset now uses expiry-zone sensitivity schedule for decision scaling
11. Unified multi-broker route is available at `/scalping-unified`:
   - feed selector controls chart/option-chain source
   - exec selector controls order destination + positions/P&L source
   - cross-broker API-key mismatch is handled by proxy-side target-key resolution
12. TOMIC runtime is now bootstrapped in app startup (per instance):
   - `TomicRuntime` is created in `app.py` startup path and injected into `tomic_bp`
   - `/tomic/status`, `/tomic/metrics`, `/tomic/signals/quality` are live
13. TOMIC frontend routes are active:
   - `/tomic/dashboard`
   - `/tomic/agents`
   - `/tomic/risk`
14. TOMIC live signal loop and alerts are wired:
   - market bridge feeds Regime/Sniper/Volatility agents from WS ticks
   - Conflict Router routed signals enqueue into Risk agent with dedupe cooldown
   - operational alerts cover feed disconnect/stale, repeated rejects, dead letters, kill-switch changes
15. TOMIC volatility strategy set expanded and wired end-to-end:
   - new strategy types: `JADE_LIZARD`, `SHORT_STRANGLE`, `SHORT_STRADDLE`
   - these are emitted by Volatility Agent only when regime/volatility filters pass
   - Risk Agent enforces congestion-only + sell-only + VIX flag safety for these strategies
16. Legged execution path now explicitly includes the new strategies:
   - legged orders for `JADE_LIZARD`/`SHORT_STRANGLE`/`SHORT_STRADDLE` route via `/api/v1/optionsmultiorder`
   - order payload validation requires `legs` for these strategies before execution
17. Flow + TOMIC orchestration nodes and templates are now live:
   - nodes in Flow editor: `tomicSnapshot`, `tomicControl`, `tomicSignal`
   - `tomicSignal` supports auto options-selling strategy selection (`autoSelect`)
   - templates:
     - `docs/flow/templates/tomic-observability-flow.json`
     - `docs/flow/templates/tomic-signal-routing-flow.json`
     - `docs/flow/templates/tomic-options-selling-regime-routing-flow.json`
     - `docs/flow/templates/scalping-ws-virtual-tpsl-bridge-flow.json`
   - usage runbook:
     - `docs/flow/README.md`

Flow -> scalping virtual TP/SL bridge details:

1. API endpoints:
   - `POST /api/v1/scalpingbridge`
   - `POST /api/v1/scalpingbridge/pending`
   - `POST /api/v1/scalpingbridge/ack`
2. Queue service:
   - `services/scalping_flow_bridge_service.py`
3. Frontend consumer (shared for both scalping routes):
   - `frontend/src/hooks/useFlowVirtualBridge.ts`
4. Unified realtime chart guard:
   - `/scalping-unified` now forces a clean WS reconnect on first mount and on feed switch
   - candle/chain WS cache lookups now normalize keys to uppercase `EXCHANGE:SYMBOL`
   - prevents silent live-tick misses from casing drift between REST symbols and WS payload symbols

## Unified Ops Runbook

1. Keep all brokers you want selectable running in background.
2. Login once and generate `/apikey` on each target broker instance.
3. Open `/scalping-unified` on any instance and choose feed/exec selectors.
4. Use this checklist for operations and broker additions:
   - `docs/design/scalping-unified-ops-checklist.md`
5. Architecture reference:
   - `docs/design/tomic-unified-architecture.md`
6. Flow orchestration runbook:
   - `docs/flow/README.md`

## TOMIC Control-Instance Runbook

Use this to avoid duplicate autonomous signal loops:

1. Pick one control instance (recommended Zerodha `http://127.0.0.1:5002`).
2. Set `.env` toggles:
   - control instance: `TOMIC_SIGNAL_LOOP_ENABLED='true'`
   - other instances: `TOMIC_SIGNAL_LOOP_ENABLED='false'`
3. Keep all broker instances running if they are needed as execution targets.
4. Ensure every target broker has active login + generated `/apikey`.
5. Use `/tomic/*` pages from control instance for runtime start/pause/resume/monitoring.
6. Use `/scalping-unified` from any instance for feed/exec split trading.

Required TOMIC env keys in `.env.*`:

1. `TOMIC_FEED_PRIMARY_WS`, `TOMIC_FEED_FALLBACK_WS`
2. `TOMIC_FEED_PRIMARY_API_KEY`, `TOMIC_FEED_FALLBACK_API_KEY`
3. `TOMIC_EXECUTION_REST`, `TOMIC_EXECUTION_API_KEY`, `TOMIC_ANALYTICS_REST`
4. `TOMIC_SIGNAL_LOOP_ENABLED` and related loop/alert knobs
5. Advanced premium strategy toggles:
   - `TOMIC_ENABLE_JADE_LIZARD`
   - `TOMIC_ENABLE_SHORT_STRANGLE`
   - `TOMIC_ENABLE_SHORT_STRADDLE`
   - `TOMIC_ALLOW_NAKED_PREMIUM`
   - `TOMIC_SHORT_PREMIUM_IV_RANK_MIN`
   - `TOMIC_SHORT_PREMIUM_IV_HV_MIN`

## Safe Upstream Merge Setup (Already Prepared)

Canonical runbook:

1. `docs/design/safe-upstream-merge-runbook.md`

Default safe command:

1. `pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -BuildFrontendDist`

Use `scripts/safe-merge-upstream.ps1` for upstream syncs. It protects local work while merging `upstream/main`.

What this script does:

1. Requires clean git state before merge (prevents accidental overwrite).
2. Ensures `upstream` remote is set to `https://github.com/marketcalls/openalgo.git`.
3. Fetches both `origin` and `upstream`.
4. Creates and pushes a timestamped backup branch:
   - `backup/main-pre-upstream-YYYYMMDD-HHMMSS`
5. Merges `upstream/main` into current `main` with conflict handling.
6. Auto-resolves generated-file conflicts by preferring local side for:
   - `frontend/dist/*`
   - `static/css/main.css`
7. Optionally rebuilds frontend dist and commits updated bundles.

Recommended usage:

1. `pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -BuildFrontendDist`
2. If many non-critical conflicts and local should win:
   - `pwsh -File .\scripts\safe-merge-upstream.ps1 -Branch main -PreferOursOnRemainingConflicts -BuildFrontendDist`

Recovery path if merge goes wrong:

1. Checkout pushed backup branch and compare.
2. Cherry-pick or re-merge needed commits from backup.

## Frontend Merge Conflict Rules (Important)

When upstream and local both changed frontend:

1. Resolve conflicts in source files first (`frontend/src/**`, APIs, hooks, stores, components).
2. Do not hand-edit hashed bundle conflicts in `frontend/dist/assets/*`.
3. After source conflict resolution, regenerate dist with:
   - `cd frontend`
   - `npm run build`
4. Stage refreshed `frontend/dist` outputs as a whole.
5. Re-test core routes (`/scalping`, `/tools`, `/positions`) before push.

Why this matters:

1. `dist` filenames are hash-based and change across builds.
2. Manual conflict edits in built files often break route chunk loading.
3. Source-first + rebuild keeps upstream features while preserving local logic.

## Known Broker Difference (Important)

1. Dhan and Zerodha support broker history API, so charts can preload candles pre-market.
2. Kotak history adapter currently returns empty data by design (`broker/kotak/api/data.py`), so pre-market candles depend on DB backfill or live ticks.

## What To Test First in Live Market

1. Per broker instance (`5000` Kotak, `5001` Dhan, `5002` Zerodha), open `/scalping`.
2. Verify live ticks on index + selected CE/PE.
3. Change timeframe and verify no black-screen crash.
4. Check top-bar P&L sync against `/positions`.
5. In paper mode, place manual order and confirm immediate virtual TP/SL attach.
6. Check line drag/close behavior.
7. Test mode behavior:
   - Ghost mode: signal popups only
   - Execute mode: signal popups + auto order placement when decision passes

## If Any Failure Appears, Capture These 4 Items

1. Broker and URL (port).
2. Action taken immediately before failure.
3. First console error line.
4. First failed network request (URL + status code).

## Fast Resume Prompt (Copy/Paste)

`Read docs/skills/scalping-autotrade-copilot/SKILL.md, docs/memory/scalping-session-memory.md, docs/memory/scalping-next-session-handover.md, and docs/design/tomic-unified-architecture.md. Then continue from unified feed/exec + TOMIC control-instance runtime state, validating live broker parity and signal-loop health.`

Token-light prompt pack:

1. `docs/memory/quick-resume-prompts.md`
