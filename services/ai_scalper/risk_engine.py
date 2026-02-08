from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Deque, Optional, Tuple
from collections import deque

from utils.logging import get_logger

from .config import RiskConfig

logger = get_logger(__name__)


@dataclass
class PositionState:
    side: str
    symbol: str
    quantity: int
    entry_price: float
    entry_ts: float


@dataclass
class RiskStatus:
    daily_pnl: float = 0.0
    realized_pnl: float = 0.0
    open_pnl: float = 0.0
    daily_loss_hit: bool = False
    cooldown_until: float = 0.0
    last_exit_reason: str = ""
    trades_today: int = 0


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.position: Optional[PositionState] = None
        self.status = RiskStatus()
        self.trade_timestamps: Deque[float] = deque()
        self.last_entry_ts: float = 0.0

    def reset_session(self) -> None:
        self.position = None
        self.status = RiskStatus()
        self.trade_timestamps.clear()
        self.last_entry_ts = 0.0

    def get_open_pnl(self, ltp: float) -> float:
        if not self.position:
            return 0.0
        return (ltp - self.position.entry_price) * self.position.quantity

    def update_open_pnl(self, ltp: float) -> float:
        pnl = self.get_open_pnl(ltp)
        self.status.open_pnl = pnl
        self.status.daily_pnl = self.status.realized_pnl + pnl
        return pnl

    def record_entry(self, side: str, symbol: str, qty: int, price: float) -> None:
        now = time.time()
        self.position = PositionState(side=side, symbol=symbol, quantity=qty, entry_price=price, entry_ts=now)
        self.last_entry_ts = now
        self.trade_timestamps.append(now)
        self.status.trades_today += 1

    def record_exit(self, ltp: float, reason: str) -> float:
        if not self.position:
            return 0.0
        pnl = self.get_open_pnl(ltp)
        self.status.realized_pnl += pnl
        self.status.open_pnl = 0.0
        self.status.daily_pnl = self.status.realized_pnl
        self.status.last_exit_reason = reason
        self.position = None
        if pnl <= -self.config.per_trade_max_loss:
            self.status.cooldown_until = time.time() + self.config.cooldown_after_loss_s
        return pnl

    def can_enter(self) -> Tuple[bool, str]:
        now = time.time()
        if self.status.daily_loss_hit:
            return False, "Daily loss hit"
        if self.status.cooldown_until and now < self.status.cooldown_until:
            return False, "Cooldown active"
        if self.last_entry_ts and (now - self.last_entry_ts) * 1000 < self.config.min_entry_gap_ms:
            return False, "Min entry gap"
        # trades per minute limit
        cutoff = now - 60
        while self.trade_timestamps and self.trade_timestamps[0] < cutoff:
            self.trade_timestamps.popleft()
        if len(self.trade_timestamps) >= self.config.max_trades_per_min:
            return False, "Max trades per minute"
        return True, ""

    def check_daily_loss(self, ltp: float) -> bool:
        pnl = self.update_open_pnl(ltp)
        if pnl + self.status.realized_pnl <= -self.config.daily_max_loss:
            self.status.daily_loss_hit = True
            return True
        return False

    def should_exit_per_trade(self, ltp: float) -> bool:
        pnl = self.get_open_pnl(ltp)
        return pnl <= -self.config.per_trade_max_loss

    def evaluate_time_guard(self, ltp: float, momentum_ok: bool) -> Tuple[bool, bool]:
        """
        Returns (should_exit, should_tighten_sl)
        """
        if not self.position:
            return False, False
        age = time.time() - self.position.entry_ts
        if age < self.config.max_trade_duration_s:
            return False, False
        if momentum_ok:
            return False, False
        pnl = self.get_open_pnl(ltp)
        if pnl >= 0:
            return True, False
        return False, True
