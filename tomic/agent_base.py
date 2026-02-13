"""
TOMIC Agent Base — Abstract Base Class for All Agents
======================================================
Provides lifecycle management, heartbeat emission, and
structured event handling. All internal timers use monotonic clock.

Every agent must implement:
  - _setup()    — initialization logic
  - _tick()     — main processing loop body
  - _teardown() — cleanup logic
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from tomic.config import TomicConfig
from tomic.events import HeartbeatEvent, AlertEvent, AlertLevel
from tomic.event_bus import EventPublisher, EventSubscriber

logger = logging.getLogger(__name__)


class AgentBase(abc.ABC):
    """
    Abstract base class for TOMIC agents.

    Lifecycle:
        agent = MyAgent(config, publisher)
        agent.start()     # spawns background thread, calls _setup()
        # ... runs _tick() in loop ...
        agent.stop()      # signals stop, calls _teardown()

    Heartbeat:
        Automatically publishes HeartbeatEvent every `heartbeat_interval` seconds.
    """

    def __init__(
        self,
        name: str,
        config: TomicConfig,
        publisher: EventPublisher,
    ):
        self.name = name
        self.config = config
        self._publisher = publisher
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._start_mono: float = 0.0
        self._last_heartbeat_mono: float = 0.0
        self._tick_count: int = 0
        self._heartbeat_interval: float = config.supervisor.heartbeat_interval

        # Subscriber for receiving telemetry (optional, set in subclass)
        self._subscriber: Optional[EventSubscriber] = None

        self.logger = logging.getLogger(f"tomic.{name}")

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the agent in a background thread."""
        if self._running:
            self.logger.warning("Agent %s already running, skipping start", self.name)
            return

        self._running = True
        self._start_mono = time.monotonic()
        self._last_heartbeat_mono = self._start_mono

        self.logger.info("Agent %s starting", self.name)
        self._setup()

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name=f"tomic-{self.name}"
        )
        self._thread.start()
        self.logger.info("Agent %s started", self.name)

    def stop(self) -> None:
        """Signal the agent to stop and wait for thread to finish."""
        if not self._running:
            return

        self.logger.info("Agent %s stopping", self.name)
        self._running = False

        if self._subscriber:
            self._subscriber.stop()

        if self._thread:
            self._thread.join(timeout=self.config.supervisor.safe_shutdown_timeout)
            if self._thread.is_alive():
                self.logger.warning("Agent %s thread did not stop in time", self.name)

        self._teardown()
        self.logger.info("Agent %s stopped after %d ticks", self.name, self._tick_count)

    def pause(self) -> None:
        """Pause the agent (stops ticking, still sends heartbeats)."""
        self._paused = True
        self.logger.info("Agent %s paused", self.name)

    def resume(self) -> None:
        """Resume a paused agent."""
        self._paused = False
        self.logger.info("Agent %s resumed", self.name)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def uptime_seconds(self) -> float:
        if self._start_mono == 0:
            return 0.0
        return time.monotonic() - self._start_mono

    # -----------------------------------------------------------------------
    # Abstract methods
    # -----------------------------------------------------------------------

    @abc.abstractmethod
    def _setup(self) -> None:
        """Called once on start. Initialize resources, subscribe to events."""
        ...

    @abc.abstractmethod
    def _tick(self) -> None:
        """
        Called repeatedly in the main loop.
        Should be non-blocking; use sleep in the loop for pacing.
        """
        ...

    @abc.abstractmethod
    def _teardown(self) -> None:
        """Called once on stop. Release resources, flush state."""
        ...

    def _get_tick_interval(self) -> float:
        """Override to control tick pacing. Default: 1.0 second."""
        return 1.0

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Background thread main loop."""
        while self._running:
            now = time.monotonic()

            # Heartbeat
            if (now - self._last_heartbeat_mono) >= self._heartbeat_interval:
                self._emit_heartbeat()
                self._last_heartbeat_mono = now

            # Tick (unless paused)
            if not self._paused:
                try:
                    self._tick()
                    self._tick_count += 1
                except Exception as e:
                    self.logger.error("Agent %s tick error: %s", self.name, e, exc_info=True)
                    self._publish_alert(
                        AlertLevel.RISK,
                        f"Tick error in {self.name}: {e}",
                    )

            # Pace
            time.sleep(self._get_tick_interval())

    # -----------------------------------------------------------------------
    # Heartbeat
    # -----------------------------------------------------------------------

    def _emit_heartbeat(self) -> None:
        """Publish heartbeat event via telemetry bus."""
        event = HeartbeatEvent(
            source_agent=self.name,
            agent_status="paused" if self._paused else "healthy",
            uptime_seconds=self.uptime_seconds,
        )
        self._publisher.publish(event)

    # -----------------------------------------------------------------------
    # Alerting
    # -----------------------------------------------------------------------

    def _publish_alert(self, level: AlertLevel, message: str) -> None:
        """Publish an operational alert via telemetry bus."""
        prefix = f"[{level.value}]"
        prefixed_message = message if message.startswith(prefix) else f"{prefix} {message}"
        event = AlertEvent(
            source_agent=self.name,
            alert_level=level,
            message=prefixed_message,
        )
        self._publisher.publish(event)
        self.logger.log(
            logging.CRITICAL if level in (AlertLevel.CRITICAL, AlertLevel.RISK) else logging.INFO,
            "%s %s: %s", prefix, self.name, prefixed_message,
        )

    # -----------------------------------------------------------------------
    # Telemetry helpers
    # -----------------------------------------------------------------------

    def _publish_event(self, event: Any) -> bool:
        """Shorthand: publish any TomicEvent."""
        return self._publisher.publish(event)

    def _subscribe(
        self,
        port: int,
        topics: list,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Start a subscriber for this agent."""
        self._subscriber = EventSubscriber(port=port, topics=topics)
        self._subscriber.start(callback=callback)
