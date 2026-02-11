# Scalping Next-Session Handover

Date: 2026-02-11
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

## Safe Upstream Merge Setup (Already Prepared)

Use `scripts/safe-merge-upstream.ps1` for future upstream syncs. It was added to protect local work while merging `upstream/main`.

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

`Read docs/skills/scalping-autotrade-copilot/SKILL.md, docs/memory/scalping-session-memory.md, and docs/memory/scalping-next-session-handover.md. Then continue with live-market broker parity testing for scalping (Kotak/Dhan/Zerodha), prioritizing candles, live P&L, and mode behavior (ghost vs execute).`
