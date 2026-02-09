# Scalping Auto-Trade Optimization Plan

Last updated: 2026-02-09

## Goal
Build a production-grade, WS-first index-options scalping engine that is robust across trend, range, and volatile regimes, with strict risk controls and broker-safe execution behavior.

## Success Criteria
- Positive expectancy in paper forward tests across at least 20 market sessions.
- Max intraday drawdown within configured hard limits on every session.
- No critical runtime failures (no black screens, no stale-context trading, no route/endpoint mismatches).
- WS-first operation with REST only for mandatory fallback/history.

## Delivery Phases

## P0 (Immediate)
- Wire real indicator snapshots into auto-trade decisions (remove placeholder null indicators).
- Feed index ticks into auto engine for index-bias and regime quality.
- Activate dormant risk gates already present in config:
  - no-trade zone
  - hot-zone sensitivity respect toggle
  - max position size cap (lot-level)
- Improve market-data mode for imbalance filter reliability (Quote when needed).
- Keep options-context WS-first with low-frequency fallback.

## P1 (Next)
- Integrate early-exit logic into auto-managed positions safely (without affecting manual-only flows).
- Side-specific re-entry controls and cooldown tracking.
- Execution quality filters:
  - spread percentile
  - slippage monitor
  - reject-rate guardrails.
- Risk session controls:
  - peak-to-trough lock
  - side-wise kill switches
  - regime-wise trade budget.

## P2 (Advanced)
- Regime router with strategy packs:
  - trend breakout
  - mean reversion
  - vol-expansion.
- Auto parameter adaptation from live outcomes with bounded changes.
- Full audit trail and replay-based validation tooling.

## Validation Checklist
- Build passes cleanly.
- Auto-trade decisions show non-null indicator inputs.
- Index bias reflects live index stream.
- no-trade zone blocks entries in tight ranges.
- respectHotZones toggle changes score threshold behavior.
- maxPositionSize blocks oversize entries.
- imbalance filter has Quote/depth fields available.

## Scope Control
- No backward-incompatible API changes in this phase.
- Keep existing manual trading behavior unchanged unless explicitly flagged.
- Roll out P0 first, validate, then proceed to P1.
