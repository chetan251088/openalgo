"""
TOMIC Position Manager — Real-Time P&L Monitoring & Adjustments
================================================================
Polls PositionBook snapshot every 5 seconds.
Evaluates each open options position against P&L thresholds.
Enqueues close commands via CommandStore when thresholds are hit.

P&L is measured as a fraction of max credit received:
  - entry_credit: premium collected at entry (positive number)
  - current_value: current cost to close the position (positive number)
  - profit = entry_credit - current_value
  - profit_pct = profit / entry_credit
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from tomic.config import PositionManagerParams, TomicConfig

logger = logging.getLogger(__name__)


class PositionAction(str, Enum):
    HOLD = "HOLD"
    ACTIVATE_TRAIL = "ACTIVATE_TRAIL"
    CLOSE_PROFIT = "CLOSE_PROFIT"
    CLOSE_LOSS = "CLOSE_LOSS"
    CLOSE_TRAIL = "CLOSE_TRAIL"
    CLOSE_TIME = "CLOSE_TIME"
    ADJUST_DELTA = "ADJUST_DELTA"


def evaluate_position(
    entry_credit: float,
    current_value: float,
    trail_stop_activated: bool,
    params: Optional[PositionManagerParams] = None,
) -> PositionAction:
    """
    Evaluate a position and return the recommended action.
    Pure function — no side effects.

    entry_credit: premium collected (e.g. 100 rupees per lot)
    current_value: cost to close now (e.g. 50 = 50% profit)
    """
    if params is None:
        params = PositionManagerParams()

    profit = entry_credit - current_value
    profit_pct = profit / entry_credit if entry_credit > 0 else 0.0

    # Loss stop: current cost > 2× credit
    if current_value >= entry_credit * params.stop_loss_multiple:
        return PositionAction.CLOSE_LOSS

    # Trail stop triggered: position reversed back to breakeven or loss
    if trail_stop_activated and profit_pct <= 0:
        return PositionAction.CLOSE_TRAIL

    # Profit target: 50% of max credit
    if profit_pct >= params.profit_target_pct:
        return PositionAction.CLOSE_PROFIT

    # Trail stop activation: 30% profit
    if profit_pct >= params.trail_stop_activate_pct and not trail_stop_activated:
        return PositionAction.ACTIVATE_TRAIL

    return PositionAction.HOLD


@dataclass
class PositionState:
    """Tracks the lifecycle state of a single options position."""
    instrument: str
    strategy_tag: str
    entry_credit: float
    lots: int
    trail_stop_activated: bool = False
    open_time_mono: float = field(default_factory=time.monotonic)

    def pnl_pct(self, current_value: float) -> float:
        """Profit as fraction of entry credit. Positive = profit."""
        if self.entry_credit <= 0:
            return 0.0
        return (self.entry_credit - current_value) / self.entry_credit

    def action(
        self, current_value: float, params: Optional[PositionManagerParams] = None
    ) -> PositionAction:
        return evaluate_position(
            self.entry_credit, current_value,
            self.trail_stop_activated, params,
        )


class PositionManager:
    """
    Monitors open positions and emits close/adjust signals.
    Runs as a background thread, checking every `check_interval_s`.
    """

    def __init__(
        self,
        config: TomicConfig,
        position_book,
        command_store=None,
    ) -> None:
        self._config = config
        self._params: PositionManagerParams = config.position_manager
        self._position_book = position_book
        self._command_store = command_store
        self._states: Dict[str, PositionState] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def register_position(
        self,
        strategy_tag: str,
        instrument: str,
        entry_credit: float,
        lots: int,
    ) -> None:
        """Called by ExecutionAgent after a position is opened."""
        with self._lock:
            self._states[strategy_tag] = PositionState(
                instrument=instrument,
                strategy_tag=strategy_tag,
                entry_credit=entry_credit,
                lots=lots,
            )
        logger.info(
            "PositionManager: registered %s credit=%.2f lots=%d",
            strategy_tag, entry_credit, lots,
        )

    def unregister_position(self, strategy_tag: str) -> None:
        """Called by ExecutionAgent after a position is closed."""
        with self._lock:
            self._states.pop(strategy_tag, None)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tomic-position-manager"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                self._check_positions()
            except Exception as exc:
                logger.error("PositionManager error: %s", exc)
            time.sleep(self._params.check_interval_s)

    def _check_positions(self) -> None:
        snapshot = self._position_book.read_snapshot()
        with self._lock:
            states = dict(self._states)

        for strategy_tag, state in states.items():
            pos = snapshot.positions.get(strategy_tag)
            if pos is None:
                continue

            current_pnl = pos.pnl
            current_value = max(0.0, state.entry_credit - current_pnl)

            action = state.action(current_value, self._params)

            if action == PositionAction.ACTIVATE_TRAIL:
                with self._lock:
                    if strategy_tag in self._states:
                        self._states[strategy_tag].trail_stop_activated = True
                logger.info("PositionManager: trail stop activated for %s", strategy_tag)

            elif action in (
                PositionAction.CLOSE_PROFIT,
                PositionAction.CLOSE_LOSS,
                PositionAction.CLOSE_TRAIL,
            ):
                logger.info(
                    "PositionManager: %s → %s (credit=%.2f value=%.2f)",
                    strategy_tag, action.value, state.entry_credit, current_value,
                )
                self._enqueue_close(strategy_tag, state.instrument, action.value)

    def _enqueue_close(self, strategy_tag: str, instrument: str, reason: str) -> None:
        """Enqueue a close command. Executed by ExecutionAgent."""
        if self._command_store is None:
            logger.warning(
                "PositionManager: no command_store — cannot enqueue close for %s", strategy_tag
            )
            return

        payload = {
            "action": "CLOSE_POSITION",
            "strategy_tag": strategy_tag,
            "instrument": instrument,
            "reason": reason,
        }
        self._command_store.enqueue(
            event_id=str(uuid.uuid4()),
            event_type="CLOSE_REQUEST",
            source_agent="position_manager",
            payload=payload,
            idempotency_key=f"{strategy_tag}:close:{reason}:{int(time.time())}",
            correlation_id=str(uuid.uuid4()),
        )

    def get_states(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                tag: {
                    "instrument": s.instrument,
                    "entry_credit": s.entry_credit,
                    "trail_stop_activated": s.trail_stop_activated,
                    "lots": s.lots,
                }
                for tag, s in self._states.items()
            }
