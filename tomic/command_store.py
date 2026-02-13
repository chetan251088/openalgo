"""
TOMIC Command Store — Durable Command Table
=============================================
SQLite WAL-mode table for order-critical events.
At-least-once delivery + idempotency enforcement.

Features:
  - Retry with exponential/fixed backoff by error class
  - Dead-letter for exhausted retries
  - Lease timeouts with ownership tokens for stale PROCESSING recovery
  - Idempotency via UNIQUE constraint on idempotency_key
  - Broker order ID reconciliation field
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classes for retry policy
# ---------------------------------------------------------------------------

class ErrorClass(str, Enum):
    NETWORK_TIMEOUT = "network_timeout"
    BROKER_RATE_LIMIT = "broker_rate_limit"
    BROKER_REJECT = "broker_reject"     # margin, invalid params
    VALIDATION = "validation"           # local validation failure
    UNKNOWN = "unknown"


# Retry policy: (should_retry, max_attempts, backoff_seconds_fn)
RETRY_POLICY: Dict[ErrorClass, Tuple[bool, int, Any]] = {
    ErrorClass.NETWORK_TIMEOUT:   (True,  3, lambda attempt: 2 ** attempt),      # 1s, 2s, 4s
    ErrorClass.BROKER_RATE_LIMIT: (True,  5, lambda attempt: 1.0),               # fixed 1s (SMART_ORDER_DELAY × 2)
    ErrorClass.BROKER_REJECT:     (False, 1, lambda attempt: 0),                 # fail immediately
    ErrorClass.VALIDATION:        (False, 1, lambda attempt: 0),                 # fail immediately
    ErrorClass.UNKNOWN:           (True,  2, lambda attempt: 5.0),               # fixed 5s, then dead-letter
}


# ---------------------------------------------------------------------------
# Command Row
# ---------------------------------------------------------------------------

@dataclass
class CommandRow:
    """Represents a row in the commands table."""
    id: int
    event_id: str
    correlation_id: str
    idempotency_key: str
    event_type: str
    event_version: int
    source_agent: str
    payload: Dict[str, Any]
    status: str
    attempt_count: int
    max_attempts: int
    last_error: Optional[str]
    next_retry_at: Optional[str]
    owner_token: Optional[str]
    lease_expires: Optional[str]
    broker_order_id: Optional[str]
    created_at: str
    processed_at: Optional[str]


# ---------------------------------------------------------------------------
# Command Store
# ---------------------------------------------------------------------------

class CommandStore:
    """
    Durable command table backed by SQLite WAL mode.

    Usage:
        store = CommandStore(db_path="db/tomic_commands.db")
        store.initialize()

        # Risk Agent enqueues
        cmd_id = store.enqueue(event)

        # Execution Agent dequeues
        row = store.dequeue()
        if row:
            try:
                # ... execute against broker ...
                store.mark_done(row.id, row.owner_token, broker_order_id="123")
            except RetryableError as e:
                store.mark_retry(row.id, row.owner_token, ErrorClass.NETWORK_TIMEOUT, str(e))
            except FatalError as e:
                store.mark_failed(row.id, row.owner_token, str(e))

        # Supervisor reclaims stale leases
        store.reclaim_stale_leases()
    """

    def __init__(self, db_path: str = "db/tomic_commands.db", lease_timeout: float = 30.0):
        self._db_path = Path(db_path)
        self._lease_timeout = lease_timeout
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self):
        """Context manager for SQLite connection with WAL mode."""
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create commands table if it doesn't exist."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS commands (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id        TEXT UNIQUE NOT NULL,
                    correlation_id  TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    event_type      TEXT NOT NULL,
                    event_version   INTEGER DEFAULT 1,
                    source_agent    TEXT NOT NULL,
                    payload         TEXT NOT NULL,
                    status          TEXT DEFAULT 'PENDING',
                    attempt_count   INTEGER DEFAULT 0,
                    max_attempts    INTEGER DEFAULT 3,
                    last_error      TEXT,
                    next_retry_at   TEXT,
                    owner_token     TEXT,
                    lease_expires   TEXT,
                    broker_order_id TEXT,
                    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
                    processed_at    TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_commands_status
                    ON commands(status, next_retry_at);
                CREATE INDEX IF NOT EXISTS idx_commands_idempotency
                    ON commands(idempotency_key);

                CREATE TABLE IF NOT EXISTS schema_version (
                    version  INTEGER PRIMARY KEY,
                    applied  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
                );
            """)

    # -----------------------------------------------------------------------
    # Enqueue
    # -----------------------------------------------------------------------

    def enqueue(
        self,
        event_id: str,
        correlation_id: str,
        idempotency_key: str,
        event_type: str,
        source_agent: str,
        payload: Dict[str, Any],
        event_version: int = 1,
        max_attempts: int = 3,
    ) -> Optional[int]:
        """
        Insert a new command. Returns row ID or None if idempotency_key already exists.
        """
        with self._conn() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO commands
                        (event_id, correlation_id, idempotency_key, event_type,
                         event_version, source_agent, payload, max_attempts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, correlation_id, idempotency_key, event_type,
                        event_version, source_agent, json.dumps(payload),
                        max_attempts,
                    ),
                )
                logger.info(
                    "Command enqueued: event_id=%s type=%s key=%s",
                    event_id, event_type, idempotency_key,
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                logger.warning(
                    "IDEMPOTENCY_SKIP: key=%s already exists, skipping",
                    idempotency_key,
                )
                return None

    # -----------------------------------------------------------------------
    # Dequeue (with lease)
    # -----------------------------------------------------------------------

    def dequeue(self) -> Optional[CommandRow]:
        """
        Atomically pick up the next PENDING command, assign lease.
        Returns None if no work available.
        """
        token = str(uuid.uuid4())
        now = datetime.utcnow()
        lease_exp = (now + timedelta(seconds=self._lease_timeout)).isoformat()
        now_iso = now.isoformat()

        with self._conn() as conn:
            # Find oldest eligible row
            row = conn.execute(
                """
                SELECT id FROM commands
                WHERE status = 'PENDING'
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now_iso,),
            ).fetchone()

            if not row:
                return None

            row_id = row["id"]

            # Claim it
            conn.execute(
                """
                UPDATE commands
                SET status = 'PROCESSING',
                    owner_token = ?,
                    lease_expires = ?,
                    attempt_count = attempt_count + 1
                WHERE id = ? AND status = 'PENDING'
                """,
                (token, lease_exp, row_id),
            )

            # Fetch full row
            full = conn.execute(
                "SELECT * FROM commands WHERE id = ?", (row_id,)
            ).fetchone()

            if not full or full["status"] != "PROCESSING":
                return None  # race condition — another consumer got it

            return self._row_to_command(full)

    # -----------------------------------------------------------------------
    # Mark done / failed / retry
    # -----------------------------------------------------------------------

    def mark_done(
        self,
        row_id: int,
        owner_token: str,
        broker_order_id: str = "",
    ) -> bool:
        """Mark command as successfully processed."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'DONE',
                    broker_order_id = ?,
                    processed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
                    owner_token = NULL,
                    lease_expires = NULL
                WHERE id = ? AND owner_token = ?
                """,
                (broker_order_id, row_id, owner_token),
            )
            if cursor.rowcount == 0:
                logger.error("mark_done failed: row=%d token mismatch or expired", row_id)
                return False
            logger.info("Command DONE: id=%d broker_ref=%s", row_id, broker_order_id)
            return True

    def mark_failed(self, row_id: int, owner_token: str, error: str) -> bool:
        """Mark command as permanently failed (non-retryable)."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'FAILED',
                    last_error = ?,
                    processed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
                    owner_token = NULL,
                    lease_expires = NULL
                WHERE id = ? AND owner_token = ?
                """,
                (error, row_id, owner_token),
            )
            if cursor.rowcount == 0:
                logger.error("mark_failed failed: row=%d token mismatch", row_id)
                return False
            logger.warning("Command FAILED: id=%d error=%s", row_id, error)
            return True

    def mark_retry(
        self,
        row_id: int,
        owner_token: str,
        error_class: ErrorClass,
        error_msg: str,
    ) -> bool:
        """
        Mark command for retry based on error class policy.
        If max attempts exhausted, moves to DEAD_LETTER.
        """
        should_retry, max_att, backoff_fn = RETRY_POLICY.get(
            error_class, RETRY_POLICY[ErrorClass.UNKNOWN]
        )

        with self._conn() as conn:
            row = conn.execute(
                "SELECT attempt_count, max_attempts FROM commands WHERE id = ? AND owner_token = ?",
                (row_id, owner_token),
            ).fetchone()

            if not row:
                logger.error("mark_retry failed: row=%d token mismatch", row_id)
                return False

            attempts = row["attempt_count"]
            effective_max = min(row["max_attempts"], max_att)

            if not should_retry or attempts >= effective_max:
                # Exhausted — dead-letter
                conn.execute(
                    """
                    UPDATE commands
                    SET status = 'DEAD_LETTER',
                        last_error = ?,
                        processed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now'),
                        owner_token = NULL,
                        lease_expires = NULL
                    WHERE id = ? AND owner_token = ?
                    """,
                    (f"[{error_class.value}] {error_msg}", row_id, owner_token),
                )
                logger.critical(
                    "Command DEAD_LETTER: id=%d class=%s after %d attempts: %s",
                    row_id, error_class.value, attempts, error_msg,
                )
                return True

            # Schedule retry
            backoff = backoff_fn(attempts)
            retry_at = (datetime.utcnow() + timedelta(seconds=backoff)).isoformat()

            conn.execute(
                """
                UPDATE commands
                SET status = 'PENDING',
                    last_error = ?,
                    next_retry_at = ?,
                    owner_token = NULL,
                    lease_expires = NULL
                WHERE id = ? AND owner_token = ?
                """,
                (f"[{error_class.value}] {error_msg}", retry_at, row_id, owner_token),
            )
            logger.info(
                "Command RETRY: id=%d class=%s attempt=%d/%d backoff=%.1fs",
                row_id, error_class.value, attempts, effective_max, backoff,
            )
            return True

    def mark_deferred(
        self,
        row_id: int,
        owner_token: str,
        reason: str,
        delay_seconds: float = 1.0,
    ) -> bool:
        """
        Re-queue a command without consuming an execution attempt.
        Used for transient local pre-conditions (e.g., freshness gates).
        """
        delay = max(0.1, float(delay_seconds))
        retry_at = (datetime.utcnow() + timedelta(seconds=delay)).isoformat()

        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'PENDING',
                    last_error = ?,
                    next_retry_at = ?,
                    attempt_count = CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END,
                    owner_token = NULL,
                    lease_expires = NULL
                WHERE id = ? AND owner_token = ?
                """,
                (f"[deferred] {reason}", retry_at, row_id, owner_token),
            )
            if cursor.rowcount == 0:
                logger.error("mark_deferred failed: row=%d token mismatch", row_id)
                return False
            logger.info(
                "Command DEFERRED: id=%d delay=%.1fs reason=%s",
                row_id, delay, reason,
            )
            return True

    # -----------------------------------------------------------------------
    # Lease recovery (called by Supervisor)
    # -----------------------------------------------------------------------

    def reclaim_stale_leases(self) -> int:
        """
        Reclaim commands stuck in PROCESSING past their lease expiry.
        Returns count of reclaimed rows.
        """
        now_iso = datetime.utcnow().isoformat()
        reclaimed = 0

        with self._conn() as conn:
            stale = conn.execute(
                """
                SELECT id, attempt_count, max_attempts, event_id
                FROM commands
                WHERE status = 'PROCESSING' AND lease_expires < ?
                """,
                (now_iso,),
            ).fetchall()

            for row in stale:
                if row["attempt_count"] >= row["max_attempts"]:
                    conn.execute(
                        """
                        UPDATE commands
                        SET status = 'DEAD_LETTER',
                            last_error = 'Lease expired after max attempts',
                            owner_token = NULL,
                            lease_expires = NULL,
                            processed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                    logger.critical(
                        "DEAD_LETTER (lease expired): id=%d event=%s",
                        row["id"], row["event_id"],
                    )
                else:
                    conn.execute(
                        """
                        UPDATE commands
                        SET status = 'PENDING',
                            owner_token = NULL,
                            lease_expires = NULL,
                            last_error = 'Lease expired — reclaimed by supervisor'
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                    logger.warning(
                        "LEASE_RECLAIM: id=%d event=%s attempt=%d",
                        row["id"], row["event_id"], row["attempt_count"],
                    )
                reclaimed += 1

        return reclaimed

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    def get_by_event_id(self, event_id: str) -> Optional[CommandRow]:
        """Look up a command by event_id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM commands WHERE event_id = ?", (event_id,)
            ).fetchone()
            return self._row_to_command(row) if row else None

    def get_done_since(self, since_iso: str) -> List[CommandRow]:
        """Get completed commands since timestamp (for Journaling Agent)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM commands WHERE status = 'DONE' AND processed_at > ? ORDER BY processed_at ASC",
                (since_iso,),
            ).fetchall()
            return [self._row_to_command(r) for r in rows]

    def count_dead_letters(self) -> int:
        """Count dead-lettered commands (for observability)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM commands WHERE status = 'DEAD_LETTER'"
            ).fetchone()
            return row["cnt"] if row else 0

    def count_pending(self) -> int:
        """Count pending commands."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM commands WHERE status = 'PENDING'"
            ).fetchone()
            return row["cnt"] if row else 0

    def reject_all_pending(self, reason: str = "System paused") -> int:
        """Kill switch: reject all queued commands. Returns count rejected."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'FAILED',
                    last_error = ?,
                    processed_at = strftime('%Y-%m-%dT%H:%M:%f', 'now')
                WHERE status IN ('PENDING', 'PROCESSING')
                """,
                (reason,),
            )
            count = cursor.rowcount
            if count > 0:
                logger.critical("KILL_SWITCH: rejected %d pending/processing commands", count)
            return count

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    @staticmethod
    def _row_to_command(row: sqlite3.Row) -> CommandRow:
        """Convert sqlite3.Row to CommandRow dataclass."""
        payload_str = row["payload"]
        try:
            payload = json.loads(payload_str) if payload_str else {}
        except json.JSONDecodeError:
            payload = {"_raw": payload_str}

        return CommandRow(
            id=row["id"],
            event_id=row["event_id"],
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            event_type=row["event_type"],
            event_version=row["event_version"],
            source_agent=row["source_agent"],
            payload=payload,
            status=row["status"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            last_error=row["last_error"],
            next_retry_at=row["next_retry_at"],
            owner_token=row["owner_token"],
            lease_expires=row["lease_expires"],
            broker_order_id=row["broker_order_id"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
        )
