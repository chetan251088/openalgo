# Scalping Next-Session Handover

Date: 2026-02-10
Commit baseline: `5e401de6` on `main`

## Current State

1. React scalping route `/scalping` is active.
2. Legacy routes are still active: `/scalping-legacy`, `/scalping-old`, `/scalping-classic`, `/legacy/scalping`.
3. Auto engine/risk runtime and trader-grade Auto UI were upgraded.
4. Signals are active in both modes:
   - `ghost`: signal + popup only
   - `execute`: signal + popup + auto execution
5. Frontend build passed after these changes.

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
