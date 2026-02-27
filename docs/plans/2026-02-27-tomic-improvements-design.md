# TOMIC Improvements Design
**Date:** 2026-02-27
**Scope:** Dead-letter inspection/retry, command history, audit trail extension, circuit breaker typing, signal quality caching

---

## Problem Statement

The TOMIC dashboard shows `Dead Letters: 25` with no way to inspect or act on the failures. Additionally:
- Audit trail only captures control actions, not risk/execution events
- Circuit breaker status is untyped (`Record<string, unknown>`)
- Signal quality scan has no caching (expensive on every poll)
- No paginated command history endpoint

---

## Approach

Backend-first (Option A): add all missing API endpoints and backend fixes, then build frontend on top.

---

## Backend Design

### 1. CommandStore new methods (`tomic/command_store.py`)

```python
def get_dead_letters(self, limit: int = 50, offset: int = 0) -> List[CommandRow]:
    """Return dead-lettered commands, newest first."""

def requeue_dead_letter(self, row_id: int) -> bool:
    """Reset one DEAD_LETTER row to PENDING with attempt_count=0."""

def requeue_dead_letters_by_class(self, error_class: Optional[str] = None) -> int:
    """Bulk requeue. If error_class given, only rows whose last_error starts with [error_class]."""

def get_commands(self, status: str, limit: int = 50, offset: int = 0) -> List[CommandRow]:
    """General paginated query by status (DONE, FAILED, DEAD_LETTER, etc.)."""
```

### 2. New Blueprint Endpoints (`blueprints/tomic.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tomic/dead-letters` | GET | List dead-letter records (`limit`, `offset` params) |
| `/tomic/dead-letters/<id>/retry` | POST | Requeue one dead letter |
| `/tomic/dead-letters/retry-all` | POST | Bulk requeue (`?error_class=network_timeout`) |
| `/tomic/commands` | GET | Paginated command history (`status`, `limit`, `offset`) |

All write endpoints are auth-protected and audit-logged with `category=DEAD_LETTER`.

### 3. Audit Trail Extension (`blueprints/tomic.py`, `tomic/command_store.py`)

Add `category` column to `audit_log` table:
```sql
ALTER TABLE audit_log ADD COLUMN category TEXT DEFAULT 'CONTROL';
```
Handled via schema migration on startup (only adds column if missing).

Categories:
- `CONTROL` — start/stop/pause/resume (existing)
- `DEAD_LETTER` — retry/retry-all actions
- `EXECUTION` — (future: execution agent audit calls)
- `RISK` — (future: risk agent rejections)
- `SIGNAL` — (future: signal enqueue events)

`GET /tomic/audit?category=DEAD_LETTER&limit=50` filter added.

### 4. Circuit Breaker Status Fix (`tomic/circuit_breakers.py`)

Replace flat `get_status_summary()` dict with per-breaker structured objects:

```python
{
  "capital": 1_000_000,
  "breakers": {
    "DAILY_MAX_LOSS":    {"tripped": False, "threshold_pct": 0.06, "current_pct": 0.012, "message": ""},
    "ORDER_RATE":        {"tripped": False, "threshold": 30, "current": 4, "message": ""},
    "GROSS_NOTIONAL":    {"tripped": False, "threshold_x": 5.0, "current_x": 1.2, "message": ""},
    "PER_UNDERLYING":    {"tripped": False, "threshold_pct": 0.30, "message": ""},
    "UNHEDGED_EXPOSURE": {"tripped": False, "unhedged_count": 0, "message": ""}
  }
}
```

### 5. Signal Quality Caching (`tomic/runtime.py`)

Add 60-second TTL cache to `get_signal_quality()`:
- `run_scan=True` (default): always runs fresh scan, updates cache
- `run_scan=False`: returns cached if age < 60s, runs scan if stale
- Response always includes `cached: bool` and `cache_age_s: float`

---

## Frontend Design

### `frontend/src/api/tomic.ts` — New types

```typescript
export interface TomicDeadLetter {
  id: number
  event_type: string
  source_agent: string
  error_class: string       // parsed from "[error_class] message"
  error_message: string     // parsed message portion
  attempt_count: number
  max_attempts: number
  created_at: string
  processed_at: string | null
  instrument?: string       // extracted from payload if present
}

export interface TomicCircuitBreakerDetail {
  tripped: boolean
  threshold_pct?: number
  current_pct?: number
  threshold?: number
  current?: number
  threshold_x?: number
  current_x?: number
  message: string
}

export interface TomicCircuitBreakersStructured {
  capital: number
  breakers: Record<string, TomicCircuitBreakerDetail>
}
```

New API methods: `getDeadLetters`, `retryDeadLetter`, `retryAllDeadLetters`, `getCommands`.

### `TomicDashboard.tsx` — Dead Letters Panel

Added as expandable section below queue metrics card:
- Auto-expands when `dead_letters > 0`
- Table: ID | Type | Error Class (badge) | Error Message | Attempts | Age | [Retry]
- "Retry All Transient" button — requeues `network_timeout` + `unknown` only (skips permanent broker rejects)
- Per-row Retry button — POST `/tomic/dead-letters/<id>/retry`, optimistically removes from list

Error class badge colors:
- `broker_reject` → red (permanent, no retry button)
- `validation` → red (permanent, no retry button)
- `network_timeout` → amber (transient, retry enabled)
- `broker_rate_limit` → amber (transient, retry enabled)
- `unknown` → gray (transient, retry enabled)

### `TomicRisk.tsx` — Circuit Breakers

Replace raw dict with structured per-breaker cards:
- Card per breaker: name, OPEN (red) / CLOSED (green) badge, threshold vs current

### `TomicAgents.tsx` — Audit Log

- Add category filter dropdown (All | Control | Risk | Execution | Dead Letter | Signal)
- Add category badge column to audit table

---

## File Change Summary

| File | Change |
|------|--------|
| `tomic/command_store.py` | +4 new methods |
| `tomic/circuit_breakers.py` | Update `get_status_summary()` structure |
| `tomic/runtime.py` | Signal quality cache |
| `blueprints/tomic.py` | +4 new endpoints, audit `category` migration, `/audit` filter |
| `frontend/src/api/tomic.ts` | New types + 4 new API methods |
| `frontend/src/pages/tomic/TomicDashboard.tsx` | Dead Letters panel |
| `frontend/src/pages/tomic/TomicRisk.tsx` | Structured circuit breaker display |
| `frontend/src/pages/tomic/TomicAgents.tsx` | Audit category filter + badge |

Total: 8 files, no new files created.
