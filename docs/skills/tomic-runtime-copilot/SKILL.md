---
name: tomic-runtime-copilot
description: Build, debug, and evolve the TOMIC multi-agent trading runtime in OpenAlgo. Use when working on regime detection, sniper entries, options strategy routing, risk gates, agent coordination, execution behavior, and live diagnostics dashboards.
---

# TOMIC Runtime Copilot

Use this skill for all TOMIC runtime and strategy-orchestration work.

## Load Context First

Read these files in order:

1. `docs/design/tomic-unified-architecture.md`
2. `docs/memory/scalping-session-memory.md`
3. `docs/memory/scalping-next-session-handover.md`
4. `docs/design/tomic-flow-automation-ideas.md`

Then load:

1. `references/skills.md`

## Core Execution Rules

1. Treat TOMIC as an insurance-underwriting system, not a prediction engine.
2. Block or defer signals when freshness, risk, or execution constraints are invalid.
3. Keep directional entries aligned with regime and impulse filters.
4. Prefer defined-risk options structures for sell-premium logic.
5. Enforce strict position sizing, drawdown limits, and queue staleness handling.
6. Keep decision reasons observable in dashboard and journal outputs.

## Acceptance Gate

1. Runtime loop health is stable (`/tomic/dashboard`, `/tomic/agents`, `/tomic/risk`).
2. No stale or replayed queue entries execute after restart.
3. Signal -> risk -> execution path logs explicit allow/block reasons.
4. Strategy selection follows regime + volatility inputs from the skill reference.
