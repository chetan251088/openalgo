from __future__ import annotations

import time

from tomic.config import TomicConfig
from tomic.supervisor import Supervisor


class _DummyAgent:
    def __init__(self) -> None:
        self.is_running = True
        self.is_paused = False

    def start(self) -> None:
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False


def test_start_resets_heartbeat_and_restart_counters(monkeypatch) -> None:
    config = TomicConfig.load("sandbox")
    sup = Supervisor(config=config)
    agent = _DummyAgent()

    sup.register_agent("execution", agent)  # type: ignore[arg-type]
    sup._last_heartbeat["execution"] = time.monotonic() - 1_000.0
    sup._restart_counts["execution"] = 2

    monkeypatch.setattr("tomic.supervisor.EventSubscriber.start", lambda self, callback: None)
    monkeypatch.setattr("tomic.supervisor.EventSubscriber.stop", lambda self: None)
    monkeypatch.setattr(Supervisor, "_monitor_loop", lambda self: None)

    sup.start(zmq_port=5599)
    try:
        now = time.monotonic()
        assert sup._restart_counts["execution"] == 0
        assert (now - sup._last_heartbeat["execution"]) < 1.0
    finally:
        sup.stop()
