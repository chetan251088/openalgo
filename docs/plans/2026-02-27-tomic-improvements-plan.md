# TOMIC Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add dead-letter inspection/retry UI, command history endpoint, extended audit trail with categories, structured circuit breaker status, and signal quality TTL caching to the TOMIC system.

**Architecture:** Backend-first (Option A) — all Python changes committed before touching frontend. 8 files total, no new files. TDD for backend Python; UI-level verification for frontend.

**Tech Stack:** Python/Flask/SQLite (backend), React 19/TypeScript/shadcn-ui (frontend), `uv run pytest` for Python tests, `npm run build` for frontend.

---

## Task 1: CommandStore — dead-letter queries and requeue methods

**Files:**
- Modify: `tomic/command_store.py` (append after `count_pending` at line ~521)
- Create: `test/test_tomic_command_store.py`

**Step 1: Write failing tests**

```python
# test/test_tomic_command_store.py
import pytest
from tomic.command_store import CommandStore, ErrorClass

@pytest.fixture
def store(tmp_path):
    s = CommandStore(db_path=str(tmp_path / "commands.db"))
    s.initialize()
    return s

def _enqueue(store, key_suffix="1"):
    return store.enqueue(
        event_id=f"evt-{key_suffix}",
        correlation_id=f"corr-{key_suffix}",
        idempotency_key=f"idem-{key_suffix}",
        event_type="ORDER_REQUEST",
        source_agent="risk_agent",
        payload={"instrument": "NIFTY24JAN24000CE", "strategy": "iron_condor"},
        max_attempts=1,
    )

def test_get_dead_letters_empty(store):
    rows = store.get_dead_letters()
    assert rows == []

def test_requeue_dead_letter(store):
    row_id = _enqueue(store, "1")
    row = store.dequeue()
    assert row is not None
    store.mark_retry(row.id, row.owner_token, ErrorClass.NETWORK_TIMEOUT, "timeout")
    # max_attempts=1 so it goes straight to DEAD_LETTER
    dead = store.get_dead_letters()
    assert len(dead) == 1
    assert dead[0].status == "DEAD_LETTER"

    ok = store.requeue_dead_letter(dead[0].id)
    assert ok is True
    # Should now be PENDING with attempt_count reset
    refetched = store.get_commands("PENDING")
    assert len(refetched) == 1
    assert refetched[0].attempt_count == 0

def test_requeue_nonexistent_returns_false(store):
    assert store.requeue_dead_letter(999) is False

def test_requeue_dead_letters_by_class(store):
    _enqueue(store, "a")
    _enqueue(store, "b")
    for _ in range(2):
        row = store.dequeue()
        store.mark_retry(row.id, row.owner_token, ErrorClass.NETWORK_TIMEOUT, "timeout")
    assert len(store.get_dead_letters()) == 2
    count = store.requeue_dead_letters_by_class("network_timeout")
    assert count == 2
    assert store.get_dead_letters() == []

def test_get_commands_by_status(store):
    _enqueue(store, "x")
    _enqueue(store, "y")
    pending = store.get_commands("PENDING")
    assert len(pending) == 2
    assert all(r.status == "PENDING" for r in pending)
```

**Step 2: Run tests to verify they fail**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo
uv run pytest test/test_tomic_command_store.py -v
```
Expected: FAIL — `AttributeError: 'CommandStore' object has no attribute 'get_dead_letters'`

**Step 3: Implement the four new methods in `tomic/command_store.py`**

Append after `count_pending` (around line 521):

```python
def get_dead_letters(self, limit: int = 50, offset: int = 0) -> List[CommandRow]:
    """Return dead-lettered commands, newest first."""
    with self._conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM commands
            WHERE status = 'DEAD_LETTER'
            ORDER BY processed_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [self._row_to_command(r) for r in rows]

def requeue_dead_letter(self, row_id: int) -> bool:
    """
    Reset a single DEAD_LETTER row back to PENDING with a fresh attempt count.
    Returns True if a row was updated, False if not found or wrong status.
    """
    with self._conn() as conn:
        cursor = conn.execute(
            """
            UPDATE commands
            SET status = 'PENDING',
                attempt_count = 0,
                next_retry_at = NULL,
                last_error = 'Manual retry',
                processed_at = NULL,
                owner_token = NULL,
                lease_expires = NULL
            WHERE id = ? AND status = 'DEAD_LETTER'
            """,
            (row_id,),
        )
        if cursor.rowcount == 0:
            return False
        logger.info("Command REQUEUED (manual): id=%d", row_id)
        return True

def requeue_dead_letters_by_class(self, error_class: Optional[str] = None) -> int:
    """
    Bulk-requeue dead-lettered commands.
    If error_class is given (e.g. 'network_timeout'), only requeue rows whose
    last_error starts with '[error_class]'. Returns count requeued.
    """
    with self._conn() as conn:
        if error_class:
            prefix = f"[{error_class}]"
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'PENDING',
                    attempt_count = 0,
                    next_retry_at = NULL,
                    last_error = 'Manual retry (bulk)',
                    processed_at = NULL,
                    owner_token = NULL,
                    lease_expires = NULL
                WHERE status = 'DEAD_LETTER' AND last_error LIKE ?
                """,
                (f"{prefix}%",),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'PENDING',
                    attempt_count = 0,
                    next_retry_at = NULL,
                    last_error = 'Manual retry (bulk)',
                    processed_at = NULL,
                    owner_token = NULL,
                    lease_expires = NULL
                WHERE status = 'DEAD_LETTER'
                """,
            )
        count = cursor.rowcount
        if count > 0:
            logger.info("Bulk REQUEUE: %d dead-letter(s) restored, class_filter=%s", count, error_class)
        return count

def get_commands(self, status: str, limit: int = 50, offset: int = 0) -> List[CommandRow]:
    """Paginated command query by status."""
    with self._conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM commands
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (status, limit, offset),
        ).fetchall()
        return [self._row_to_command(r) for r in rows]
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_tomic_command_store.py -v
```
Expected: all 6 tests PASS

**Step 5: Commit**

```bash
git add tomic/command_store.py test/test_tomic_command_store.py
git commit -m "feat(tomic): add dead-letter query, requeue, and command history methods to CommandStore"
```

---

## Task 2: CircuitBreakerEngine — structured status summary

**Files:**
- Modify: `tomic/circuit_breakers.py` (replace `get_status_summary` at line ~273)
- Create: `test/test_tomic_circuit_breakers.py`

**Step 1: Write failing tests**

```python
# test/test_tomic_circuit_breakers.py
import pytest
from tomic.circuit_breakers import CircuitBreakerEngine, BreakerType
from tomic.config import CircuitBreakerThresholds

@pytest.fixture
def engine():
    th = CircuitBreakerThresholds()
    return CircuitBreakerEngine(thresholds=th, capital=1_000_000)

def test_status_summary_has_breakers_key(engine):
    summary = engine.get_status_summary()
    assert "breakers" in summary
    assert "capital" in summary

def test_status_summary_each_breaker_has_tripped(engine):
    summary = engine.get_status_summary()
    for name in ["DAILY_MAX_LOSS", "ORDER_RATE", "GROSS_NOTIONAL",
                 "PER_UNDERLYING", "UNHEDGED_EXPOSURE"]:
        assert name in summary["breakers"], f"Missing breaker: {name}"
        assert "tripped" in summary["breakers"][name]

def test_order_rate_current_updates(engine):
    engine.record_order()
    engine.record_order()
    summary = engine.get_status_summary()
    assert summary["breakers"]["ORDER_RATE"]["current"] == 2

def test_not_tripped_when_no_orders(engine):
    summary = engine.get_status_summary()
    assert summary["breakers"]["ORDER_RATE"]["tripped"] is False
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_tomic_circuit_breakers.py -v
```
Expected: FAIL — `AssertionError: 'breakers' not in ...`

**Step 3: Replace `get_status_summary` in `tomic/circuit_breakers.py`**

Find and replace the entire `get_status_summary` method (lines ~273–288):

```python
def get_status_summary(self) -> Dict[str, object]:
    """Structured diagnostic summary per breaker for observability endpoint."""
    now = time.monotonic()
    with self._lock:
        # Prune for accurate order rate count
        while self._order_timestamps and (now - self._order_timestamps[0]) > 60.0:
            self._order_timestamps.popleft()
        order_count = len(self._order_timestamps)

        return {
            "capital": self._capital,
            "breakers": {
                "DAILY_MAX_LOSS": {
                    "tripped": False,  # live check requires current PnL; static here
                    "threshold_pct": self._th.daily_max_loss_pct,
                    "description": f"Trip if daily PnL < -{self._th.daily_max_loss_pct:.0%} of capital",
                    "message": "",
                },
                "ORDER_RATE": {
                    "tripped": order_count >= self._th.max_orders_per_minute,
                    "threshold": self._th.max_orders_per_minute,
                    "current": order_count,
                    "message": (
                        f"{order_count} orders/min exceeds limit {self._th.max_orders_per_minute}"
                        if order_count >= self._th.max_orders_per_minute else ""
                    ),
                },
                "GROSS_NOTIONAL": {
                    "tripped": False,  # live check requires current notional; static here
                    "threshold_x": self._th.max_gross_notional_multiple,
                    "description": f"Trip if gross notional > {self._th.max_gross_notional_multiple}× capital",
                    "message": "",
                },
                "PER_UNDERLYING": {
                    "tripped": False,
                    "threshold_pct": self._th.per_underlying_margin_cap,
                    "description": f"Trip if single underlying > {self._th.per_underlying_margin_cap:.0%} of margin",
                    "message": "",
                },
                "UNHEDGED_EXPOSURE": {
                    "tripped": bool(self._unhedged_since),
                    "unhedged_count": len(self._unhedged_since),
                    "timeout_s": self._th.unhedged_timeout_seconds,
                    "message": (
                        f"{len(self._unhedged_since)} unhedged position(s) tracked"
                        if self._unhedged_since else ""
                    ),
                },
            },
        }
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_tomic_circuit_breakers.py -v
```
Expected: all 4 tests PASS

**Step 5: Commit**

```bash
git add tomic/circuit_breakers.py test/test_tomic_circuit_breakers.py
git commit -m "feat(tomic): structured circuit breaker status with per-breaker threshold+current fields"
```

---

## Task 3: Runtime — signal quality TTL cache (60 s)

**Files:**
- Modify: `tomic/runtime.py` — `get_signal_quality` method only

**Context:** `get_signal_quality` already caches in `_last_signal_quality`. The gap is that `run_scan=False` serves stale data forever. Fix: add 60 s TTL — if cache age > 60 s, run scan even when `run_scan=False`.

**Step 1: Find the method** (`get_signal_quality` starts around line 359)

**Step 2: Edit `get_signal_quality` in `tomic/runtime.py`**

Find this block (inside `get_signal_quality`, after `if not run_scan:` branch opens):

```python
        if not run_scan:
            with self._quality_lock:
                if self._last_signal_quality:
                    cached = self._last_signal_quality.copy()
                    cached["cached"] = True
                    cached["cached_age_s"] = round(
                        max(0.0, time.time() - float(cached.get("timestamp_epoch", time.time()))), 2
                    )
                    return cached
```

Replace with:

```python
        _QUALITY_TTL_S = 60.0
        if not run_scan:
            with self._quality_lock:
                if self._last_signal_quality:
                    age_s = max(0.0, time.time() - float(
                        self._last_signal_quality.get("timestamp_epoch", 0.0)
                    ))
                    if age_s <= _QUALITY_TTL_S:
                        cached = self._last_signal_quality.copy()
                        cached["cached"] = True
                        cached["cached_age_s"] = round(age_s, 2)
                        return cached
                    # Cache stale — fall through to run fresh scan below
```

**Step 3: Verify no import needed** — `time` is already imported in `runtime.py`.

**Step 4: Smoke test manually**

Start app with `uv run app.py`, call `/tomic/signals/quality?run_scan=false` twice within 60 s. Second call should return `"cached": true, "cached_age_s": <N>`. After 60 s, it should run a fresh scan.

**Step 5: Commit**

```bash
git add tomic/runtime.py
git commit -m "feat(tomic): add 60s TTL to signal quality cache (run_scan=false respects staleness)"
```

---

## Task 4: Blueprint — 4 new endpoints, audit category migration, audit filter

**Files:**
- Modify: `blueprints/tomic.py` (append new endpoints + update `_audit_log` + update `get_audit_log`)

**Step 1: Add `category` column migration helper and update `_audit_log`**

Find `_audit_log` function (around line 59). Update it to:
1. Add `category TEXT DEFAULT 'CONTROL'` column via migration on first use
2. Accept an optional `category` parameter

```python
def _ensure_audit_category_column() -> None:
    """One-time migration: add category column to audit_log if missing."""
    try:
        conn = sqlite3.connect(_audit_db_path, timeout=5.0)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
        if "category" not in cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN category TEXT DEFAULT 'CONTROL'")
            conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Audit category migration skipped: %s", e)


def _audit_log(action: str, details: str = "", category: str = "CONTROL") -> None:
    """Log control action to audit table."""
    try:
        Path(_audit_db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_audit_db_path, timeout=5.0)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                user_id    TEXT,
                action     TEXT NOT NULL,
                details    TEXT,
                ip_address TEXT,
                category   TEXT DEFAULT 'CONTROL'
            )
        """)
        _ensure_audit_category_column()
        conn.execute(
            "INSERT INTO audit_log (user_id, action, details, ip_address, category) VALUES (?,?,?,?,?)",
            (
                session.get("user", "unknown"),
                action,
                details,
                request.remote_addr or "unknown",
                category,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Audit log failed: %s", e)
```

**Step 2: Update `get_audit_log` endpoint to support `?category=` filter**

Find `get_audit_log` (around line 438). Replace the query section:

```python
@tomic_bp.route("/audit", methods=["GET"])
def get_audit_log():
    """Get recent audit log entries. Optional ?category= filter."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    limit = request.args.get("limit", 100, type=int)
    category = request.args.get("category", "").strip().upper() or None
    try:
        conn = sqlite3.connect(_audit_db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        if category:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE UPPER(category) = ? ORDER BY timestamp DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return jsonify({
            "status": "success",
            "entries": [dict(r) for r in rows],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
```

**Step 3: Add helper to serialize a `CommandRow` for API responses**

Add this helper function near the other helpers in `blueprints/tomic.py`:

```python
def _parse_error_class(last_error: str | None) -> tuple[str, str]:
    """Parse '[error_class] message' format into (error_class, message)."""
    if not last_error:
        return ("unknown", "")
    if last_error.startswith("[") and "]" in last_error:
        bracket_end = last_error.index("]")
        cls = last_error[1:bracket_end].strip()
        msg = last_error[bracket_end + 1:].strip()
        return (cls, msg)
    return ("unknown", last_error)


def _serialize_command_row(row) -> dict:
    """Serialize a CommandRow for API response, with parsed error class."""
    error_class, error_message = _parse_error_class(row.last_error)
    instrument = str(row.payload.get("instrument", "") or row.payload.get("underlying", "") or "")
    return {
        "id": row.id,
        "event_id": row.event_id,
        "event_type": row.event_type,
        "source_agent": row.source_agent,
        "status": row.status,
        "error_class": error_class,
        "error_message": error_message,
        "attempt_count": row.attempt_count,
        "max_attempts": row.max_attempts,
        "instrument": instrument,
        "created_at": row.created_at,
        "processed_at": row.processed_at,
    }
```

**Step 4: Add the 4 new endpoints**

Append to the end of `blueprints/tomic.py`:

```python
# ---------------------------------------------------------------------------
# Dead-letter endpoints
# ---------------------------------------------------------------------------

@tomic_bp.route("/dead-letters", methods=["GET"])
def get_dead_letters():
    """List dead-lettered commands with full error details."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    try:
        rows = runtime.command_store.get_dead_letters(limit=limit, offset=offset)
        return jsonify({
            "status": "success",
            "total": runtime.command_store.count_dead_letters(),
            "limit": limit,
            "offset": offset,
            "items": [_serialize_command_row(r) for r in rows],
        })
    except Exception as e:
        logger.error("get_dead_letters failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/dead-letters/<int:row_id>/retry", methods=["POST"])
def retry_dead_letter(row_id: int):
    """Requeue a single dead-lettered command."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    try:
        ok = runtime.command_store.requeue_dead_letter(row_id)
        if not ok:
            return jsonify({"status": "error", "message": f"Command {row_id} not found or not a dead letter"}), 404
        _audit_log(f"DEAD_LETTER_RETRY:{row_id}", f"Manual retry of dead-letter id={row_id}", category="DEAD_LETTER")
        return jsonify({"status": "success", "message": f"Command {row_id} requeued"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/dead-letters/retry-all", methods=["POST"])
def retry_all_dead_letters():
    """Bulk-requeue dead-lettered commands. Optional ?error_class= filter."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    error_class = request.args.get("error_class", "").strip() or None
    try:
        count = runtime.command_store.requeue_dead_letters_by_class(error_class=error_class)
        detail = f"class_filter={error_class or 'all'} count={count}"
        _audit_log("DEAD_LETTER_RETRY_ALL", detail, category="DEAD_LETTER")
        return jsonify({"status": "success", "requeued": count, "error_class": error_class})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tomic_bp.route("/commands", methods=["GET"])
def get_commands():
    """Paginated command history by status."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    runtime = _get_runtime()
    if not runtime:
        return jsonify({"status": "error", "message": "Offline"}), 503

    status_filter = request.args.get("status", "DONE").strip().upper()
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    valid_statuses = {"PENDING", "PROCESSING", "DONE", "FAILED", "DEAD_LETTER", "DEFERRED"}
    if status_filter not in valid_statuses:
        return jsonify({"status": "error", "message": f"Invalid status. Valid: {sorted(valid_statuses)}"}), 400

    try:
        rows = runtime.command_store.get_commands(status=status_filter, limit=limit, offset=offset)
        return jsonify({
            "status": "success",
            "status_filter": status_filter,
            "limit": limit,
            "offset": offset,
            "items": [_serialize_command_row(r) for r in rows],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
```

**Step 5: Verify `runtime.command_store` is accessible**

Check that `TomicRuntime` exposes `command_store` as a public attribute. Search in `tomic/runtime.py`:

```bash
grep -n "command_store" C:/algo/openalgov2/openalgov2/openalgo/tomic/runtime.py | head -10
```

If it's named differently (e.g. `_command_store`), update the blueprint calls accordingly.

**Step 6: Run app and smoke test new endpoints**

```bash
uv run app.py
# In another terminal:
curl -s http://127.0.0.1:5002/tomic/dead-letters  # expect 401 (need session auth) or list
curl -s "http://127.0.0.1:5002/tomic/audit?category=DEAD_LETTER"
curl -s "http://127.0.0.1:5002/tomic/commands?status=DONE"
```

**Step 7: Commit**

```bash
git add blueprints/tomic.py
git commit -m "feat(tomic): add dead-letters list/retry endpoints, command history endpoint, audit category filter"
```

---

## Task 5: Frontend API client — new types and methods

**Files:**
- Modify: `frontend/src/api/tomic.ts`

**Step 1: Add new TypeScript interfaces** (after `TomicAuditEntry` at line ~140)

```typescript
// Dead letter record with parsed error class
export interface TomicDeadLetter {
  id: number
  event_id: string
  event_type: string
  source_agent: string
  status: string
  error_class: string
  error_message: string
  attempt_count: number
  max_attempts: number
  instrument: string
  created_at: string
  processed_at: string | null
}

export interface TomicDeadLettersResponse {
  status: string
  total: number
  limit: number
  offset: number
  items: TomicDeadLetter[]
  message?: string
}

export interface TomicCommandsResponse {
  status: string
  status_filter: string
  limit: number
  offset: number
  items: TomicDeadLetter[]
  message?: string
}

// Structured per-breaker details
export interface TomicCircuitBreakerDetail {
  tripped: boolean
  threshold_pct?: number
  threshold?: number
  threshold_x?: number
  current?: number
  current_x?: number
  unhedged_count?: number
  timeout_s?: number
  description?: string
  message: string
}

export interface TomicCircuitBreakersStructured {
  capital: number
  breakers: Record<string, TomicCircuitBreakerDetail>
}
```

**Step 2: Update `TomicMetricsResponse`** — change `circuit_breakers` from `Record<string, unknown>` to `TomicCircuitBreakersStructured | Record<string, unknown>`:

```typescript
export interface TomicMetricsResponse {
  status: string
  data?: {
    circuit_breakers?: TomicCircuitBreakersStructured | Record<string, unknown>
    freshness?: Record<string, unknown>
    ws_data?: Record<string, unknown>
    market_bridge?: Record<string, unknown>
  }
  message?: string
}
```

**Step 3: Update `TomicAuditEntry`** — add optional `category` field:

```typescript
export interface TomicAuditEntry {
  id: number
  timestamp: string
  user_id: string
  action: string
  details?: string
  ip_address?: string
  category?: string
}
```

**Step 4: Add new API methods** to the `tomicApi` object (after `getAudit`):

```typescript
  getDeadLetters: async (limit = 50, offset = 0): Promise<TomicDeadLettersResponse> => {
    const response = await webClient.get<TomicDeadLettersResponse>('/tomic/dead-letters', {
      params: { limit, offset },
    })
    return response.data
  },

  retryDeadLetter: async (id: number): Promise<TomicActionResponse> => {
    const response = await webClient.post<TomicActionResponse>(`/tomic/dead-letters/${id}/retry`)
    return response.data
  },

  retryAllDeadLetters: async (errorClass?: string): Promise<{ status: string; requeued: number; error_class: string | null }> => {
    const params = errorClass ? { error_class: errorClass } : {}
    const response = await webClient.post<{ status: string; requeued: number; error_class: string | null }>(
      '/tomic/dead-letters/retry-all',
      {},
      { params },
    )
    return response.data
  },

  getCommands: async (status = 'DONE', limit = 50, offset = 0): Promise<TomicCommandsResponse> => {
    const response = await webClient.get<TomicCommandsResponse>('/tomic/commands', {
      params: { status, limit, offset },
    })
    return response.data
  },

  getAuditByCategory: async (category: string, limit = 100): Promise<TomicAuditResponse> => {
    const response = await webClient.get<TomicAuditResponse>('/tomic/audit', {
      params: { category, limit },
    })
    return response.data
  },
```

**Step 5: Build to verify types**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo/frontend && npm run build 2>&1 | grep -E "error|warning" | head -20
```
Expected: no TypeScript errors relating to tomic.ts

**Step 6: Commit**

```bash
git add frontend/src/api/tomic.ts
git commit -m "feat(tomic): add TypeScript types and API methods for dead-letters, commands, structured circuit breakers"
```

---

## Task 6: TomicDashboard — Dead Letters inspection panel

**Files:**
- Modify: `frontend/src/pages/tomic/TomicDashboard.tsx`

**Step 1: Add imports**

Add to the existing import block at the top:
- `RotateCcw` from `lucide-react` (retry icon)
- `TomicDeadLetter`, `TomicDeadLettersResponse` from `@/api/tomic`

```typescript
import { Activity, PauseCircle, PlayCircle, RefreshCw, RotateCcw, ShieldAlert, Square } from 'lucide-react'
// add to tomic import:
import {
  tomicApi,
  type TomicAnalyticsResponse,
  type TomicDeadLetter,
  type TomicDeadLettersResponse,
  type TomicPositionsResponse,
  type TomicSignalQualityResponse,
  type TomicStatusResponse,
} from '@/api/tomic'
```

**Step 2: Add state and load dead letters**

After existing state declarations (`const [quality, setQuality] = ...`):

```typescript
const [deadLetters, setDeadLetters] = useState<TomicDeadLettersResponse | null>(null)
const [retryingId, setRetryingId] = useState<number | null>(null)
const [retryingAll, setRetryingAll] = useState(false)
```

In `loadData`, add `tomicApi.getDeadLetters()` to the `Promise.allSettled` array:

```typescript
const [statusResp, positionsResp, analyticsResp, qualityResp, deadLettersResp] = await Promise.allSettled([
  tomicApi.getStatus(),
  tomicApi.getPositions(),
  tomicApi.getAnalytics(),
  tomicApi.getSignalQuality(false),
  tomicApi.getDeadLetters(50, 0),
])
// ...after existing setters:
if (deadLettersResp.status === 'fulfilled') setDeadLetters(deadLettersResp.value)
```

**Step 3: Add helper to determine if an error class is retryable**

```typescript
const TRANSIENT_ERROR_CLASSES = new Set(['network_timeout', 'broker_rate_limit', 'unknown'])

function isRetryable(errorClass: string): boolean {
  return TRANSIENT_ERROR_CLASSES.has(errorClass)
}

function errorClassVariant(errorClass: string): 'destructive' | 'secondary' | 'outline' {
  if (errorClass === 'broker_reject' || errorClass === 'validation') return 'destructive'
  if (TRANSIENT_ERROR_CLASSES.has(errorClass)) return 'secondary'
  return 'outline'
}
```

**Step 4: Add retry handlers**

```typescript
const retryOne = useCallback(async (id: number) => {
  setRetryingId(id)
  try {
    await tomicApi.retryDeadLetter(id)
    showToast.success(`Command ${id} requeued`, 'monitoring')
    await loadData(true)
  } catch {
    showToast.error(`Failed to retry command ${id}`, 'monitoring')
  } finally {
    setRetryingId(null)
  }
}, [loadData])

const retryAllTransient = useCallback(async () => {
  setRetryingAll(true)
  try {
    // Retry each transient class separately
    let total = 0
    for (const cls of ['network_timeout', 'broker_rate_limit', 'unknown']) {
      const res = await tomicApi.retryAllDeadLetters(cls)
      total += res.requeued ?? 0
    }
    showToast.success(`${total} command(s) requeued`, 'monitoring')
    await loadData(true)
  } catch {
    showToast.error('Failed to bulk retry', 'monitoring')
  } finally {
    setRetryingAll(false)
  }
}, [loadData])
```

**Step 5: Add Dead Letters panel after the "Dead Letters" metric card**

After the 4-column metric grid (after `</div>` closing the `xl:grid-cols-4` div around line ~267), insert:

```tsx
{/* Dead Letters Panel — auto-expands when count > 0 */}
{(deadLetters?.total ?? 0) > 0 && (
  <Card>
    <CardHeader>
      <div className="flex items-center justify-between">
        <div>
          <CardTitle className="flex items-center gap-2 text-red-500">
            <ShieldAlert className="h-5 w-5" />
            Dead Letters ({deadLetters?.total ?? 0})
          </CardTitle>
          <CardDescription>
            Commands that exhausted all retry attempts. Transient failures can be requeued.
          </CardDescription>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void retryAllTransient()}
          disabled={retryingAll}
        >
          <RotateCcw className={`h-4 w-4 mr-2 ${retryingAll ? 'animate-spin' : ''}`} />
          Retry All Transient
        </Button>
      </div>
    </CardHeader>
    <CardContent>
      <div className="border rounded-md">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Instrument</TableHead>
              <TableHead>Error Class</TableHead>
              <TableHead>Error Message</TableHead>
              <TableHead>Attempts</TableHead>
              <TableHead>Age</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(deadLetters?.items ?? []).map((dl: TomicDeadLetter) => {
              const createdAt = dl.created_at ? new Date(dl.created_at) : null
              const ageMs = createdAt ? Date.now() - createdAt.getTime() : null
              const ageStr = ageMs != null
                ? ageMs > 3_600_000
                  ? `${Math.floor(ageMs / 3_600_000)}h ago`
                  : ageMs > 60_000
                  ? `${Math.floor(ageMs / 60_000)}m ago`
                  : `${Math.floor(ageMs / 1000)}s ago`
                : '—'
              return (
                <TableRow key={dl.id}>
                  <TableCell className="font-mono text-xs">{dl.id}</TableCell>
                  <TableCell className="text-xs">{dl.event_type}</TableCell>
                  <TableCell className="font-medium text-xs">{dl.instrument || '—'}</TableCell>
                  <TableCell>
                    <Badge variant={errorClassVariant(dl.error_class)}>
                      {dl.error_class}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-w-[240px] truncate text-xs text-muted-foreground">
                    {dl.error_message || '—'}
                  </TableCell>
                  <TableCell className="text-xs">{dl.attempt_count}/{dl.max_attempts}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{ageStr}</TableCell>
                  <TableCell>
                    {isRetryable(dl.error_class) && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void retryOne(dl.id)}
                        disabled={retryingId === dl.id}
                      >
                        <RotateCcw className={`h-3.5 w-3.5 mr-1 ${retryingId === dl.id ? 'animate-spin' : ''}`} />
                        Retry
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </CardContent>
  </Card>
)}
```

**Step 6: Build to check for TypeScript errors**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo/frontend && npm run build 2>&1 | grep -i error | head -20
```
Expected: no errors

**Step 7: Commit**

```bash
git add frontend/src/pages/tomic/TomicDashboard.tsx
git commit -m "feat(tomic): add dead-letter inspection panel with retry/retry-all to dashboard"
```

---

## Task 7: TomicRisk — structured circuit breakers; TomicAgents — audit category filter

**Files:**
- Modify: `frontend/src/pages/tomic/TomicRisk.tsx`
- Modify: `frontend/src/pages/tomic/TomicAgents.tsx`

### TomicRisk.tsx

**Step 1: Import new type and update `circuitBreakers` access**

Add to import:
```typescript
import { type TomicCircuitBreakersStructured } from '@/api/tomic'
```

**Step 2: Replace the Circuit Breakers card** (lines ~224–260)

Find the card with `<CardTitle>Circuit Breakers</CardTitle>` and replace its `<CardContent>` section:

```tsx
<Card>
  <CardHeader>
    <CardTitle className="flex items-center gap-2">
      <AlertTriangle className="h-5 w-5" />
      Circuit Breakers
    </CardTitle>
    <CardDescription>Supervisor-level hard stops and thresholds.</CardDescription>
  </CardHeader>
  <CardContent>
    {(() => {
      const structured = metrics?.data?.circuit_breakers as TomicCircuitBreakersStructured | undefined
      const breakers = structured?.breakers ?? {}
      const breakerEntries = Object.entries(breakers)
      if (breakerEntries.length === 0) {
        return <p className="text-sm text-muted-foreground">No circuit breaker metrics available.</p>
      }
      return (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {breakerEntries.map(([name, detail]) => (
            <div
              key={name}
              className={`rounded-md border p-3 ${detail.tripped ? 'border-red-500 bg-red-50 dark:bg-red-950' : ''}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium">{name.replace(/_/g, ' ')}</span>
                <Badge variant={detail.tripped ? 'destructive' : 'secondary'} className="text-xs">
                  {detail.tripped ? 'OPEN' : 'CLOSED'}
                </Badge>
              </div>
              {detail.threshold != null && detail.current != null && (
                <div className="text-xs text-muted-foreground">
                  {detail.current} / {detail.threshold} limit
                </div>
              )}
              {detail.threshold_pct != null && (
                <div className="text-xs text-muted-foreground">
                  threshold: {(detail.threshold_pct * 100).toFixed(0)}%
                </div>
              )}
              {detail.threshold_x != null && (
                <div className="text-xs text-muted-foreground">
                  threshold: {detail.threshold_x}×
                </div>
              )}
              {detail.unhedged_count != null && (
                <div className="text-xs text-muted-foreground">
                  unhedged: {detail.unhedged_count} (timeout: {detail.timeout_s}s)
                </div>
              )}
              {detail.message && (
                <div className="text-xs text-red-500 mt-1">{detail.message}</div>
              )}
              {detail.description && !detail.message && (
                <div className="text-xs text-muted-foreground mt-1">{detail.description}</div>
              )}
            </div>
          ))}
        </div>
      )
    })()}
  </CardContent>
</Card>
```

### TomicAgents.tsx

**Step 1: Add category filter state**

After existing `useState` declarations (around line 50):
```typescript
const [auditCategory, setAuditCategory] = useState<string>('')
```

**Step 2: Update `loadData` to pass category filter**

In `loadData`, change the audit fetch to use `auditCategory`:
```typescript
tomicApi.getAudit(80),   // existing
// change to:
auditCategory
  ? tomicApi.getAuditByCategory(auditCategory, 80)
  : tomicApi.getAudit(80),
```

Also add `auditCategory` to `loadData`'s `useCallback` dependency array.

**Step 3: Update `useEffect` to reload when `auditCategory` changes**

The existing `useEffect` already calls `loadData` on mount and on interval. Since `loadData` is memoized with `auditCategory` in its deps, changing `auditCategory` automatically creates a new `loadData` function, which triggers the `useEffect` to re-run. No change needed to `useEffect`.

**Step 4: Add category filter above the audit table**

Find `<Card>` with `<CardTitle>Control Audit Trail</CardTitle>` (around line 433). Add a filter row inside `<CardHeader>` after `<CardDescription>`:

```tsx
<CardHeader>
  <div className="flex items-center justify-between">
    <div>
      <CardTitle>Control Audit Trail</CardTitle>
      <CardDescription>Operator actions and control-plane history.</CardDescription>
    </div>
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">Category:</span>
      {(['', 'CONTROL', 'DEAD_LETTER', 'RISK', 'EXECUTION', 'SIGNAL'] as const).map((cat) => (
        <Button
          key={cat || 'all'}
          size="sm"
          variant={auditCategory === cat ? 'default' : 'outline'}
          className="text-xs h-7 px-2"
          onClick={() => setAuditCategory(cat)}
        >
          {cat || 'All'}
        </Button>
      ))}
    </div>
  </div>
</CardHeader>
```

**Step 5: Add `category` badge column to audit table**

In the `<TableHeader>` of the audit table, add `<TableHead>Category</TableHead>` after `<TableHead>Action</TableHead>`.

In the `<TableBody>` rows (after the Action cell):
```tsx
<TableCell>
  {entry.category && (
    <Badge variant="outline" className="text-xs">
      {entry.category}
    </Badge>
  )}
</TableCell>
```

**Step 6: Build to check for TypeScript errors**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo/frontend && npm run build 2>&1 | grep -i error | head -20
```
Expected: no errors

**Step 7: Commit**

```bash
git add frontend/src/pages/tomic/TomicRisk.tsx frontend/src/pages/tomic/TomicAgents.tsx
git commit -m "feat(tomic): structured circuit breaker cards in risk page; audit category filter in agents page"
```

---

## Task 8: Final verification build

**Step 1: Full frontend build**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo/frontend && npm run build
```
Expected: exit code 0, `✓ built in ...`

**Step 2: Run Python tests**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo
uv run pytest test/test_tomic_command_store.py test/test_tomic_circuit_breakers.py -v
```
Expected: all tests PASS

**Step 3: Smoke test new endpoints in browser**

1. Open `http://127.0.0.1:5002/tomic/dashboard` — verify Dead Letters panel visible with table
2. Open `http://127.0.0.1:5002/tomic/risk` — verify circuit breaker cards with threshold/current values
3. Open `http://127.0.0.1:5002/tomic/agents` — verify audit category filter buttons
4. Check JSON: `http://127.0.0.1:5002/tomic/dead-letters` — verify structured response
5. Check JSON: `http://127.0.0.1:5002/tomic/commands?status=DONE` — verify pagination

**Step 4: Final commit (if any stray changes)**

```bash
git status
git add -A
git commit -m "chore(tomic): final cleanup and verification"
```
