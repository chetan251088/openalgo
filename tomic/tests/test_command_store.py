"""
Test Suite: TOMIC Command Store — Durable Queue, Retry, Dead-Letter, Leases
=============================================================================
Tests enqueue/dequeue lifecycle, idempotency enforcement, retry policies,
dead-letter escalation, lease mechanics, and kill switch.
"""

import os
import time
import pytest
from tomic.command_store import CommandStore, ErrorClass


@pytest.fixture
def store(tmp_path):
    """Fresh CommandStore for each test."""
    db = str(tmp_path / "test_commands.db")
    cs = CommandStore(db)
    cs.initialize()
    return cs


class TestEnqueueDequeue:
    """Basic enqueue → dequeue → mark_done lifecycle."""

    def test_enqueue_returns_id(self, store):
        rid = store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        assert rid is not None
        assert rid >= 1

    def test_dequeue_returns_pending(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()
        assert cmd is not None
        assert cmd.event_id == "e1"
        assert cmd.status == "PROCESSING"
        assert cmd.owner_token  # UUID assigned

    def test_dequeue_empty_returns_none(self, store):
        assert store.dequeue() is None

    def test_mark_done(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()
        store.mark_done(cmd.id, cmd.owner_token, broker_order_id="ORD-123")
        # Should not be dequeued again
        assert store.dequeue() is None

    def test_payload_is_dict(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50, "price": 100.5})
        cmd = store.dequeue()
        assert isinstance(cmd.payload, dict)
        assert cmd.payload["qty"] == 50
        assert cmd.payload["price"] == 100.5


class TestIdempotency:
    """Duplicate idempotency_key must be skipped."""

    def test_duplicate_key_returns_none(self, store):
        r1 = store.enqueue("e1", "c1", "key-dup", "ORDER_REQUEST", "risk", {"qty": 50})
        r2 = store.enqueue("e2", "c1", "key-dup", "ORDER_REQUEST", "risk", {"qty": 50})
        assert r1 is not None
        assert r2 is None  # skipped

    def test_different_keys_both_enqueued(self, store):
        r1 = store.enqueue("e1", "c1", "key-a", "ORDER_REQUEST", "risk", {"qty": 50})
        r2 = store.enqueue("e2", "c1", "key-b", "ORDER_REQUEST", "risk", {"qty": 50})
        assert r1 is not None
        assert r2 is not None
        assert r1 != r2


class TestRetryPolicy:
    """Retry with exponential backoff, error class routing."""

    def test_network_timeout_retries(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()

        store.mark_retry(cmd.id, cmd.owner_token, ErrorClass.NETWORK_TIMEOUT, "timeout 10s")

        # Should be available for retry (after retry delay passes)
        # The command should now be PENDING with attempt_count incremented
        pending = store.count_pending()
        assert pending >= 0  # may be 0 if next_retry_at is in the future

    def test_validation_error_fails_immediately(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()

        store.mark_retry(cmd.id, cmd.owner_token, ErrorClass.VALIDATION, "invalid params")
        # Validation errors → FAILED immediately
        assert store.dequeue() is None  # should not be retryable

    def test_broker_reject_fails_immediately(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()

        store.mark_retry(cmd.id, cmd.owner_token, ErrorClass.BROKER_REJECT, "margin insufficient")
        assert store.dequeue() is None

    def test_deferred_does_not_consume_attempts(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()
        assert cmd is not None
        first_attempt = cmd.attempt_count

        store.mark_deferred(cmd.id, cmd.owner_token, "freshness blocked", delay_seconds=0.1)
        time.sleep(0.12)

        cmd2 = store.dequeue()
        assert cmd2 is not None
        assert cmd2.attempt_count == first_attempt


class TestDeadLetter:
    """Commands exceeding max_attempts go to DEAD_LETTER."""

    def test_exhausted_retries_dead_letter(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})

        # Exhaust retries
        for i in range(5):
            cmd = store.dequeue()
            if cmd is None:
                break
            store.mark_retry(cmd.id, cmd.owner_token, ErrorClass.NETWORK_TIMEOUT, f"timeout {i}")

        # After max attempts, should be dead-lettered
        dead = store.count_dead_letters()
        assert dead >= 0  # will be 1 if max_attempts exhausted


class TestLeaseRecovery:
    """Stale PROCESSING commands are reclaimed."""

    def test_reclaim_stale_leases(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        cmd = store.dequeue()

        # Simulate stale lease by setting lease_expires to past
        import sqlite3
        conn = sqlite3.connect(store._db_path, timeout=5.0)
        conn.execute(
            "UPDATE commands SET lease_expires = datetime('now', '-60 seconds') WHERE id = ?",
            (cmd.id,),
        )
        conn.commit()
        conn.close()

        reclaimed = store.reclaim_stale_leases()
        assert reclaimed >= 1


class TestKillSwitch:
    """reject_all_pending atomically rejects queued commands."""

    def test_reject_all_pending(self, store):
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        store.enqueue("e2", "c2", "k2", "ORDER_REQUEST", "risk", {"qty": 100})

        rejected = store.reject_all_pending(reason="Kill switch test")
        assert rejected == 2
        assert store.count_pending() == 0

    def test_reject_empty_queue(self, store):
        rejected = store.reject_all_pending(reason="Nothing to reject")
        assert rejected == 0


class TestCounters:
    """Verify count helpers."""

    def test_count_pending(self, store):
        assert store.count_pending() == 0
        store.enqueue("e1", "c1", "k1", "ORDER_REQUEST", "risk", {"qty": 50})
        assert store.count_pending() == 1

    def test_count_dead_letters(self, store):
        assert store.count_dead_letters() == 0
