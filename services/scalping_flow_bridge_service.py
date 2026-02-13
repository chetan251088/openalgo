from __future__ import annotations

import threading
import time
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()
_NEXT_EVENT_ID = 1
_EVENTS_BY_USER: dict[str, list[dict[str, Any]]] = {}

# Keep queue bounded per user to avoid unbounded growth in long-running sessions.
_MAX_EVENTS_PER_USER = 500
# Keep acked events briefly for diagnostics, then prune.
_ACK_RETENTION_MS = 10 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clone_event(event: dict[str, Any]) -> dict[str, Any]:
    return dict(event)


def _prune_locked(user_id: str) -> None:
    events = _EVENTS_BY_USER.get(user_id, [])
    if not events:
        return

    now_ms = _now_ms()
    retained: list[dict[str, Any]] = []
    for event in events:
        status = str(event.get("status", "pending")).strip().lower()
        if status == "acked":
            acked_at = int(event.get("acked_at", event.get("updated_at", 0)) or 0)
            if acked_at > 0 and (now_ms - acked_at) > _ACK_RETENTION_MS:
                continue
        retained.append(event)

    if len(retained) > _MAX_EVENTS_PER_USER:
        # Drop oldest events first.
        retained = retained[-_MAX_EVENTS_PER_USER:]

    _EVENTS_BY_USER[user_id] = retained


def enqueue_flow_virtual_entry(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Enqueue a flow-triggered entry for scalping virtual TP/SL attachment.
    """
    global _NEXT_EVENT_ID
    now_ms = _now_ms()

    with _LOCK:
        event_id = _NEXT_EVENT_ID
        _NEXT_EVENT_ID += 1

        event = {
            "id": event_id,
            "status": "pending",
            "created_at": now_ms,
            "updated_at": now_ms,
            **payload,
        }

        user_key = str(user_id).strip()
        events = _EVENTS_BY_USER.setdefault(user_key, [])
        events.append(event)
        _prune_locked(user_key)

    logger.debug(
        "Scalping flow bridge enqueue: user=%s event=%s symbol=%s action=%s qty=%s",
        user_id,
        event_id,
        event.get("symbol"),
        event.get("action"),
        event.get("quantity"),
    )
    return _clone_event(event)


def get_pending_flow_virtual_entries(
    user_id: str,
    *,
    limit: int = 50,
    after_id: int = 0,
) -> list[dict[str, Any]]:
    """
    Read pending bridge entries for a user.
    """
    normalized_limit = max(1, min(int(limit), 200))
    normalized_after_id = max(0, int(after_id))
    user_key = str(user_id).strip()

    with _LOCK:
        _prune_locked(user_key)
        events = _EVENTS_BY_USER.get(user_key, [])
        pending = [
            _clone_event(event)
            for event in events
            if str(event.get("status", "pending")).strip().lower() == "pending"
            and int(event.get("id", 0) or 0) > normalized_after_id
        ]

    return pending[:normalized_limit]


def acknowledge_flow_virtual_entries(user_id: str, ids: list[int]) -> int:
    """
    Mark entries as acknowledged for a user.
    Returns number of newly acked entries.
    """
    user_key = str(user_id).strip()
    now_ms = _now_ms()

    valid_ids = {
        int(raw_id)
        for raw_id in ids
        if isinstance(raw_id, (int, float, str)) and str(raw_id).strip().isdigit()
    }
    if not valid_ids:
        return 0

    acked_count = 0
    with _LOCK:
        events = _EVENTS_BY_USER.get(user_key, [])
        for event in events:
            event_id = int(event.get("id", 0) or 0)
            if event_id not in valid_ids:
                continue
            status = str(event.get("status", "pending")).strip().lower()
            if status == "pending":
                event["status"] = "acked"
                event["acked_at"] = now_ms
                event["updated_at"] = now_ms
                acked_count += 1
        _prune_locked(user_key)

    if acked_count:
        logger.debug(
            "Scalping flow bridge ack: user=%s count=%s ids=%s",
            user_id,
            acked_count,
            sorted(valid_ids),
        )
    return acked_count
