"""
PositionManager — Real-time P&L monitoring for options selling positions.
Monitors every position and applies trail stops, profit targets, and mandatory stop-outs.
"""

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List

from .config import PositionManagerParams

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    strategy_tag: str
    instrument: str
    strategy_type: str
    legs: list = field(default_factory=list)
    entry_credit: float = 0.0
    current_pnl: float = 0.0
    current_pnl_pct: float = 0.0
    peak_pnl: float = 0.0
    peak_pnl_pct: float = 0.0
    trail_active: bool = False
    trail_stop_level: float = 0.0
    entry_time: float = 0.0
    last_check: float = 0.0
    status: str = "OPEN"  # OPEN, TRAILING, CLOSING, CLOSED
    close_reason: str = ""
    reentry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "strategy_tag": self.strategy_tag,
            "instrument": self.instrument,
            "strategy_type": self.strategy_type,
            "entry_credit": self.entry_credit,
            "current_pnl": round(self.current_pnl, 2),
            "current_pnl_pct": round(self.current_pnl_pct * 100, 1),
            "peak_pnl": round(self.peak_pnl, 2),
            "peak_pnl_pct": round(self.peak_pnl_pct * 100, 1),
            "trail_active": self.trail_active,
            "trail_stop_level": round(self.trail_stop_level, 2),
            "status": self.status,
            "close_reason": self.close_reason,
            "reentry_count": self.reentry_count,
            "age_minutes": round((time.time() - self.entry_time) / 60, 1) if self.entry_time else 0,
        }


class PositionManager:
    """Monitors and manages all active options selling positions."""

    def __init__(self, config: Optional[PositionManagerParams] = None, intelligence_service=None):
        self.config = config or PositionManagerParams()
        self.intelligence = intelligence_service
        self._positions: Dict[str, PositionState] = {}
        self._lock = threading.Lock()
        self._close_commands: List[dict] = []

    def register_position(
        self,
        strategy_tag: str,
        instrument: str,
        strategy_type: str,
        entry_credit: float,
        legs: list = None,
    ) -> None:
        """Register a new position for monitoring."""
        with self._lock:
            self._positions[strategy_tag] = PositionState(
                strategy_tag=strategy_tag,
                instrument=instrument,
                strategy_type=strategy_type,
                legs=legs or [],
                entry_credit=entry_credit,
                entry_time=time.time(),
            )
            logger.info("Position registered: %s %s credit=%.2f", strategy_tag, strategy_type, entry_credit)

    def update_pnl(self, strategy_tag: str, current_pnl: float) -> Optional[dict]:
        """Update P&L for a position and check exit conditions.
        
        Returns a close command dict if exit is triggered, else None.
        """
        with self._lock:
            pos = self._positions.get(strategy_tag)
            if not pos or pos.status == "CLOSED":
                return None

            pos.current_pnl = current_pnl
            if pos.entry_credit > 0:
                pos.current_pnl_pct = current_pnl / pos.entry_credit
            pos.last_check = time.time()

            if current_pnl > pos.peak_pnl:
                pos.peak_pnl = current_pnl
                pos.peak_pnl_pct = pos.current_pnl_pct

            return self._check_exit_conditions(pos)

    def check_all_positions(self) -> List[dict]:
        """Check all positions for exit conditions. Called periodically."""
        commands = []
        with self._lock:
            for tag, pos in list(self._positions.items()):
                if pos.status == "CLOSED":
                    continue
                cmd = self._check_exit_conditions(pos)
                if cmd:
                    commands.append(cmd)
        return commands

    def get_positions(self) -> List[dict]:
        """Return all position states as dicts."""
        with self._lock:
            return [pos.to_dict() for pos in self._positions.values()]

    def get_active_count(self) -> int:
        with self._lock:
            return sum(1 for p in self._positions.values() if p.status != "CLOSED")

    def mark_closed(self, strategy_tag: str, reason: str) -> None:
        with self._lock:
            pos = self._positions.get(strategy_tag)
            if pos:
                pos.status = "CLOSED"
                pos.close_reason = reason

    def _check_exit_conditions(self, pos: PositionState) -> Optional[dict]:
        """Check all exit conditions for a position.
        
        Exit priority:
        1. Mandatory stop-out (loss >= 2x credit)
        2. Profit target (>= 50% of credit)
        3. Trail stop (if active and P&L drops below trail level)
        4. Time exit (after intraday_exit_hhmm)
        5. Rotation exit (sector transition from Leading to Weakening)
        """
        # 1. Mandatory stop-out
        if pos.entry_credit > 0 and pos.current_pnl <= -(pos.entry_credit * self.config.stop_loss_multiple):
            pos.status = "CLOSING"
            pos.close_reason = f"STOP_OUT: loss {pos.current_pnl:.0f} >= {self.config.stop_loss_multiple}x credit"
            return self._build_close_command(pos)

        # 2. Profit target
        if pos.current_pnl_pct >= self.config.profit_target_pct:
            pos.status = "CLOSING"
            pos.close_reason = f"PROFIT_TARGET: {pos.current_pnl_pct*100:.0f}% >= {self.config.profit_target_pct*100:.0f}%"
            return self._build_close_command(pos)

        # 3. Trail stop activation and check
        if pos.peak_pnl_pct >= self.config.trail_stop_activate_pct:
            if not pos.trail_active:
                pos.trail_active = True
                pos.trail_stop_level = pos.peak_pnl * 0.5
                pos.status = "TRAILING"
                logger.info("Trail activated for %s at %.0f%% profit", pos.strategy_tag, pos.peak_pnl_pct * 100)

        if pos.trail_active:
            pos.trail_stop_level = max(pos.trail_stop_level, pos.peak_pnl * 0.5)
            if pos.current_pnl <= pos.trail_stop_level:
                pos.status = "CLOSING"
                pos.close_reason = f"TRAIL_STOP: P&L {pos.current_pnl:.0f} <= trail {pos.trail_stop_level:.0f}"
                return self._build_close_command(pos)

        # 4. Time exit
        now = time.localtime()
        exit_h, exit_m = map(int, self.config.intraday_exit_hhmm.split(":"))
        if now.tm_hour > exit_h or (now.tm_hour == exit_h and now.tm_min >= exit_m):
            pos.status = "CLOSING"
            pos.close_reason = f"TIME_EXIT: past {self.config.intraday_exit_hhmm}"
            return self._build_close_command(pos)

        # 5. Rotation exit (intelligence-driven)
        if self.intelligence and self._check_rotation_exit(pos):
            pos.status = "CLOSING"
            pos.close_reason = "ROTATION_EXIT: sector transition detected"
            return self._build_close_command(pos)

        return None

    def _check_rotation_exit(self, pos: PositionState) -> bool:
        """Check if the sector of this position's underlying has weakened."""
        if not self.intelligence:
            return False
        intel = self.intelligence.get_intelligence()
        if not intel or not intel.rotation:
            return False
        for transition in intel.rotation.transitions:
            if transition.get("to_quadrant") in ("Lagging", "Weakening"):
                from .rotation_client import STOCK_TO_SECTOR
                sector = STOCK_TO_SECTOR.get(pos.instrument)
                if sector and transition.get("symbol") == sector:
                    return True
        return False

    def _build_close_command(self, pos: PositionState) -> dict:
        """Build a close command for the execution agent."""
        logger.info("Close command: %s reason=%s", pos.strategy_tag, pos.close_reason)
        return {
            "action": "CLOSE",
            "strategy_tag": pos.strategy_tag,
            "instrument": pos.instrument,
            "reason": pos.close_reason,
            "legs": pos.legs,
            "timestamp": time.time(),
        }
