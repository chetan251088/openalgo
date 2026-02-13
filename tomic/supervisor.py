"""
TOMIC Supervisor — System Watchdog & Lifecycle Manager
=======================================================
Monitors agent health via heartbeats.
Reclaims stale command leases.
Enforces circuit breakers at system level.
Implements kill switch and safe shutdown runbooks.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from tomic.agent_base import AgentBase
from tomic.circuit_breakers import CircuitBreakerEngine, BreakerType
from tomic.command_store import CommandStore
from tomic.config import TomicConfig
from tomic.events import AlertEvent, AlertLevel, EventType
from tomic.event_bus import EventPublisher, EventSubscriber
from tomic.position_book import PositionBook

logger = logging.getLogger(__name__)


class Supervisor:
    """
    System-level watchdog. Not an agent itself — runs in the main process.

    Responsibilities:
        1. Monitor agent heartbeats (detect crashes)
        2. Reclaim stale command leases (PROCESSING past expiry)
        3. Check circuit breakers on every cycle
        4. Execute kill switch / safe shutdown runbooks
        5. Restart crashed agents (up to max retries)

    Usage:
        sup = Supervisor(config)
        sup.register_agent("regime", regime_agent)
        sup.register_agent("execution", execution_agent)
        sup.start()
        # ... running ...
        sup.stop()  # safe shutdown
    """

    def __init__(
        self,
        config: TomicConfig,
        command_store: Optional[CommandStore] = None,
        position_book: Optional[PositionBook] = None,
        circuit_breakers: Optional[CircuitBreakerEngine] = None,
        publisher: Optional[EventPublisher] = None,
        kill_callback: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self._command_store = command_store
        self._position_book = position_book
        self._circuit_breakers = circuit_breakers
        self._publisher = publisher
        self._kill_callback = kill_callback  # external hook for kill switch

        self._agents: Dict[str, AgentBase] = {}
        self._last_heartbeat: Dict[str, float] = {}  # name → monotonic
        self._restart_counts: Dict[str, int] = {}
        self._consecutive_exec_timeouts: int = 0

        self._running = False
        self._killed = False  # kill switch activated
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Heartbeat subscriber
        self._heartbeat_sub: Optional[EventSubscriber] = None

    # -----------------------------------------------------------------------
    # Agent registration
    # -----------------------------------------------------------------------

    def register_agent(self, name: str, agent: AgentBase) -> None:
        """Register an agent for health monitoring."""
        self._agents[name] = agent
        self._last_heartbeat[name] = time.monotonic()
        self._restart_counts[name] = 0

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self, zmq_port: int = 5560) -> None:
        """Start supervisor monitoring loop."""
        self._running = True
        self._killed = False
        now = time.monotonic()

        # Reset heartbeat/restart counters on each start to avoid stale values
        # from app boot time causing immediate false-positive restarts.
        with self._lock:
            for name in self._agents:
                self._last_heartbeat[name] = now
                self._restart_counts[name] = 0

        # Subscribe to heartbeats
        self._heartbeat_sub = EventSubscriber(
            port=zmq_port, topics=["HEARTBEAT"]
        )
        self._heartbeat_sub.start(callback=self._on_heartbeat)

        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="tomic-supervisor"
        )
        self._thread.start()
        logger.info("Supervisor started, monitoring %d agents", len(self._agents))

    def stop(self) -> None:
        """Safe shutdown per runbook."""
        logger.info("Supervisor initiating safe shutdown")
        self._running = False

        # 1. Signal all agents to stop
        for name, agent in self._agents.items():
            logger.info("Stopping agent: %s", name)
            agent.stop()

        # 2. Persist PositionBook
        if self._position_book:
            self._position_book.persist()
            logger.info("PositionBook persisted")

        # 3. Stop heartbeat subscriber
        if self._heartbeat_sub:
            self._heartbeat_sub.stop()

        # 4. Wait for supervisor thread
        if self._thread:
            self._thread.join(timeout=5.0)

        logger.info("Supervisor stopped")

    # -----------------------------------------------------------------------
    # Kill switch
    # -----------------------------------------------------------------------

    def kill_switch(self, reason: str) -> None:
        """
        Emergency kill switch. Per runbook:
        1. System_Pause = True
        2. Cancel all open orders
        3. Reject all queued commands
        4. Stop all signal agents
        5. Critical alert
        """
        if self._killed:
            return

        with self._lock:
            self._killed = True

        logger.critical("KILL SWITCH activated: %s", reason)

        # 1. Pause all agents
        for agent in self._agents.values():
            agent.pause()

        # 2-3. Reject queued commands
        if self._command_store:
            rejected = self._command_store.reject_all_pending(reason=reason)
            logger.critical("Kill switch rejected %d commands", rejected)

        # 4. External kill callback (e.g., call /api/v1/cancelallorder)
        if self._kill_callback:
            try:
                self._kill_callback()
            except Exception as e:
                logger.error("Kill callback failed: %s", e)

        # 5. Alert
        if self._publisher:
            alert = AlertEvent(
                source_agent="supervisor",
                alert_level=AlertLevel.CRITICAL,
                message=f"[CRITICAL] KILL SWITCH: {reason}",
            )
            self._publisher.publish(alert)

    def resume(self) -> None:
        """Resume all paused agents and clear kill state."""
        with self._lock:
            self._killed = False

        for agent in self._agents.values():
            agent.resume()

        logger.info("Supervisor resumed")

    # -----------------------------------------------------------------------
    # Monitor loop
    # -----------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """Main monitoring cycle — runs every 5 seconds."""
        while self._running:
            try:
                self._check_heartbeats()
                self._reclaim_stale_leases()
                self._check_circuit_breakers()
            except Exception as e:
                logger.error("Supervisor monitor error: %s", e, exc_info=True)

            time.sleep(5.0)  # 5-second cycle

    # -----------------------------------------------------------------------
    # Heartbeat monitoring
    # -----------------------------------------------------------------------

    def _on_heartbeat(self, event_data: Dict[str, Any]) -> None:
        """Callback for received heartbeat events."""
        agent_name = event_data.get("source_agent", "")
        if agent_name:
            with self._lock:
                self._last_heartbeat[agent_name] = time.monotonic()

    def _check_heartbeats(self) -> None:
        """Detect agents that haven't sent a heartbeat within the timeout."""
        now = time.monotonic()
        timeout = self.config.supervisor.heartbeat_interval * 2  # 2× interval = dead

        with self._lock:
            for name, last in self._last_heartbeat.items():
                elapsed = now - last
                if elapsed > timeout and name in self._agents:
                    agent = self._agents[name]
                    if agent.is_running:
                        logger.warning(
                            "Agent %s missed heartbeat (%.0fs), attempting restart",
                            name, elapsed,
                        )
                        self._handle_agent_crash(name)

    def _handle_agent_crash(self, name: str) -> None:
        """Agent crash recovery per runbook."""
        retries = self._restart_counts.get(name, 0)
        max_retries = self.config.supervisor.agent_restart_max_retries

        if retries >= max_retries:
            logger.critical(
                "Agent %s exceeded max restarts (%d), activating kill switch",
                name, max_retries,
            )
            self.kill_switch(f"Agent {name} crash — max restarts exceeded")
            return

        # Stop the failed agent
        agent = self._agents[name]
        try:
            agent.stop()
        except Exception as e:
            logger.error("Failed to stop crashed agent %s: %s", name, e)

        # Backoff
        backoff = self.config.supervisor.agent_restart_backoff
        time.sleep(backoff)

        # Restart
        try:
            agent.start()
            self._restart_counts[name] = retries + 1
            self._last_heartbeat[name] = time.monotonic()
            logger.info("Agent %s restarted (attempt %d/%d)", name, retries + 1, max_retries)

            # Reconcile PositionBook after restart if it's the Execution Agent
            if name == "execution" and self._position_book:
                logger.info("Triggering PositionBook reconciliation after %s restart", name)

        except Exception as e:
            logger.error("Failed to restart agent %s: %s", name, e)
            self._restart_counts[name] = retries + 1
            if retries + 1 >= max_retries:
                self.kill_switch(f"Agent {name} restart failed — kill switch")

    # -----------------------------------------------------------------------
    # Lease reclamation
    # -----------------------------------------------------------------------

    def _reclaim_stale_leases(self) -> None:
        """Reclaim commands stuck in PROCESSING past lease expiry."""
        if not self._command_store:
            return

        reclaimed = self._command_store.reclaim_stale_leases()
        if reclaimed > 0:
            logger.warning("Supervisor reclaimed %d stale leases", reclaimed)
            if self._publisher:
                alert = AlertEvent(
                    source_agent="supervisor",
                    alert_level=AlertLevel.RISK,
                    message=f"[RISK] Reclaimed {reclaimed} stale command lease(s)",
                )
                self._publisher.publish(alert)

    # -----------------------------------------------------------------------
    # Circuit breaker enforcement
    # -----------------------------------------------------------------------

    def _check_circuit_breakers(self) -> None:
        """Check system-level circuit breakers."""
        if not self._circuit_breakers or not self._position_book:
            return

        snap = self._position_book.read_snapshot()
        unhedged = self._position_book.has_unhedged_short()

        status = self._circuit_breakers.check_all(
            daily_pnl=snap.total_pnl,
            unhedged_keys=unhedged,
        )

        if not status.all_clear:
            for result in status.tripped_breakers:
                if result.kill_switch:
                    self.kill_switch(result.message)
                    return
                else:
                    logger.warning("Circuit breaker: %s", result.message)

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return supervisor status for API endpoint."""
        now = time.monotonic()
        agents = {}
        with self._lock:
            for name, agent in self._agents.items():
                last_hb = self._last_heartbeat.get(name, 0)
                agents[name] = {
                    "running": agent.is_running,
                    "paused": agent.is_paused,
                    "last_heartbeat_ago_s": round(now - last_hb, 1) if last_hb else -1,
                    "restarts": self._restart_counts.get(name, 0),
                }

        return {
            "running": self._running,
            "killed": self._killed,
            "agents": agents,
            "command_queue_pending": self._command_store.count_pending() if self._command_store else 0,
            "command_dead_letters": self._command_store.count_dead_letters() if self._command_store else 0,
        }
