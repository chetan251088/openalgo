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
    _enqueue(store, "1")
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
