"""
TOMIC Event Bus — ZeroMQ Pub/Sub for Telemetry
================================================
Fire-and-forget telemetry channel for non-critical events.
Order-critical events go through the durable command table instead.

Channels (topics):
  - REGIME_UPDATE  — Regime Agent publishes phase changes
  - HEARTBEAT      — All agents publish every 60s
  - ALERT          — Any agent publishes operational alerts
  - POSITION_UPDATE — Execution Agent publishes position changes
  - SIGNAL         — Sniper / Volatility agents publish trade signals

Events optionally persisted to SQLite for replay debugging.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import zmq

from tomic.events import TomicEvent, EventType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------

class EventPublisher:
    """
    ZeroMQ PUB socket for publishing telemetry events.

    Usage:
        pub = EventPublisher(port=5560)
        pub.start()
        pub.publish(RegimeUpdateEvent(phase="BULLISH", score=12))
        pub.stop()
    """

    def __init__(self, port: int = 5560):
        self._port = port
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._lock = threading.Lock()
        self._running = False
        # Optional event persistence
        self._persist_db: Optional[str] = None

    def start(self) -> None:
        """Bind PUB socket."""
        self._context = zmq.Context.instance()
        self._socket = self._context.socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, 1000)      # high-water mark
        self._socket.setsockopt(zmq.LINGER, 0)           # don't block on close
        self._socket.bind(f"tcp://127.0.0.1:{self._port}")
        self._running = True
        logger.info("EventPublisher bound on tcp://127.0.0.1:%d", self._port)

    def stop(self) -> None:
        """Close PUB socket."""
        self._running = False
        if self._socket:
            with suppress(Exception):
                self._socket.close()
        logger.info("EventPublisher stopped")

    def publish(self, event: TomicEvent) -> bool:
        """
        Publish an event to all subscribers.
        Returns True if sent, False if failed (non-blocking, best-effort).
        """
        if not self._running or not self._socket:
            return False

        topic = event.event_type.value.encode("utf-8")
        payload = event.model_dump_json().encode("utf-8")

        try:
            with self._lock:
                self._socket.send_multipart([topic, payload], zmq.NOBLOCK)

            # Optional persistence for replay
            if self._persist_db:
                self._persist_event(event)

            return True
        except zmq.ZMQError as e:
            logger.warning("EventPublisher send failed: %s", e)
            return False

    def enable_persistence(self, db_path: str = "db/tomic_telemetry.db") -> None:
        """Enable event persistence to SQLite for replay debugging."""
        self._persist_db = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source     TEXT NOT NULL,
                payload    TEXT NOT NULL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Telemetry persistence enabled: %s", db_path)

    def _persist_event(self, event: TomicEvent) -> None:
        """Write event to persistence DB (best effort)."""
        try:
            conn = sqlite3.connect(self._persist_db, timeout=2.0)
            conn.execute(
                "INSERT INTO telemetry_events (event_id, event_type, source, payload) VALUES (?,?,?,?)",
                (event.event_id, event.event_type.value, event.source_agent,
                 event.model_dump_json()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("Telemetry persist failed (non-critical): %s", e)


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------

class EventSubscriber:
    """
    ZeroMQ SUB socket for receiving telemetry events.

    Usage:
        sub = EventSubscriber(port=5560, topics=["REGIME_UPDATE", "ALERT"])
        sub.start(callback=my_handler)
        # ... runs in background thread ...
        sub.stop()
    """

    def __init__(self, port: int = 5560, topics: Optional[List[str]] = None):
        self._port = port
        self._topics = topics or []
        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def start(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Connect SUB socket and start receive loop in background thread."""
        self._callback = callback
        self._context = zmq.Context.instance()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.RCVHWM, 1000)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(f"tcp://127.0.0.1:{self._port}")

        # Subscribe to requested topics, or all if none specified
        if self._topics:
            for topic in self._topics:
                self._socket.subscribe(topic.encode("utf-8"))
        else:
            self._socket.subscribe(b"")  # all topics

        self._running = True
        self._thread = threading.Thread(
            target=self._receive_loop, daemon=True, name="tomic-sub"
        )
        self._thread.start()
        logger.info(
            "EventSubscriber connected to tcp://127.0.0.1:%d, topics=%s",
            self._port, self._topics or ["ALL"],
        )

    def stop(self) -> None:
        """Stop receive loop and close socket."""
        self._running = False
        if self._socket:
            with suppress(Exception):
                self._socket.close()
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("EventSubscriber stopped")

    def _receive_loop(self) -> None:
        """Background thread: receive and dispatch events."""
        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)

        while self._running:
            try:
                socks = dict(poller.poll(timeout=500))  # 500ms poll
                if self._socket in socks:
                    parts = self._socket.recv_multipart(zmq.NOBLOCK)
                    if len(parts) == 2:
                        topic = parts[0].decode("utf-8")
                        payload = json.loads(parts[1].decode("utf-8"))
                        payload["_topic"] = topic

                        if self._callback:
                            try:
                                self._callback(payload)
                            except Exception as e:
                                logger.error(
                                    "Subscriber callback error on %s: %s",
                                    topic, e,
                                )
            except zmq.ZMQError:
                if self._running:
                    time.sleep(0.1)
            except Exception as e:
                logger.error("EventSubscriber error: %s", e)
                if self._running:
                    time.sleep(1.0)
