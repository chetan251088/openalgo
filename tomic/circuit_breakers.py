"""
TOMIC Circuit Breakers — System-Level Hard Stops
==================================================
These sit ABOVE all strategy logic. Checked by Supervisor before
any command is executed. Non-bypassable.

Per plan §6:
  - Daily max loss: realized + unrealized < -6% → kill switch
  - Max order rate: > 30/min → throttle
  - Max notional: gross > 5× capital → reject
  - Per-underlying cap: single underlying > 30% of margin → reject
  - Unhedged exposure: any short unhedged > 5s → force close
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, Optional

from tomic.config import CircuitBreakerThresholds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Breaker types
# ---------------------------------------------------------------------------

class BreakerType(str, Enum):
    DAILY_MAX_LOSS = "DAILY_MAX_LOSS"
    ORDER_RATE = "ORDER_RATE"
    GROSS_NOTIONAL = "GROSS_NOTIONAL"
    PER_UNDERLYING = "PER_UNDERLYING"
    UNHEDGED_EXPOSURE = "UNHEDGED_EXPOSURE"


@dataclass
class BreakerResult:
    """Result of a circuit breaker check."""
    tripped: bool = False
    breaker: Optional[BreakerType] = None
    message: str = ""
    kill_switch: bool = False   # True → system-wide halt


@dataclass
class BreakerStatus:
    """Aggregate status of all breakers."""
    all_clear: bool = True
    tripped_breakers: list = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Circuit Breaker Engine
# ---------------------------------------------------------------------------

class CircuitBreakerEngine:
    """
    System-level circuit breakers checked before every order execution.

    Usage:
        breakers = CircuitBreakerEngine(thresholds, capital=1_000_000)

        # Before each order:
        status = breakers.check_all(
            daily_pnl=-65000,
            gross_notional=4_500_000,
            underlying="NIFTY",
            underlying_margin_pct=0.25,
            unhedged_keys=[],
        )
        if not status.all_clear:
            # Block order / trigger kill switch
    """

    def __init__(
        self,
        thresholds: Optional[CircuitBreakerThresholds] = None,
        capital: float = 0.0,
    ):
        self._th = thresholds or CircuitBreakerThresholds()
        self._capital = capital
        self._lock = threading.Lock()

        # Order rate tracking (rolling 1-minute window using monotonic clock)
        self._order_timestamps: Deque[float] = deque()

        # Unhedged exposure tracking: key → monotonic time first detected
        self._unhedged_since: Dict[str, float] = {}

    def set_capital(self, capital: float) -> None:
        """Update trading capital (e.g., from /api/v1/funds)."""
        with self._lock:
            self._capital = capital

    # -----------------------------------------------------------------------
    # Core check
    # -----------------------------------------------------------------------

    def check_all(
        self,
        daily_pnl: float = 0.0,
        gross_notional: float = 0.0,
        underlying: str = "",
        underlying_margin_pct: float = 0.0,
        unhedged_keys: Optional[list] = None,
    ) -> BreakerStatus:
        """
        Run all circuit breaker checks. Returns aggregate status.
        """
        status = BreakerStatus()
        now = time.monotonic()

        with self._lock:
            # 1. Daily max loss
            result = self._check_daily_loss(daily_pnl)
            if result.tripped:
                status.all_clear = False
                status.tripped_breakers.append(result)
                status.details[result.breaker.value] = result.message

            # 2. Order rate
            result = self._check_order_rate(now)
            if result.tripped:
                status.all_clear = False
                status.tripped_breakers.append(result)
                status.details[result.breaker.value] = result.message

            # 3. Gross notional
            result = self._check_gross_notional(gross_notional)
            if result.tripped:
                status.all_clear = False
                status.tripped_breakers.append(result)
                status.details[result.breaker.value] = result.message

            # 4. Per-underlying
            if underlying:
                result = self._check_per_underlying(underlying, underlying_margin_pct)
                if result.tripped:
                    status.all_clear = False
                    status.tripped_breakers.append(result)
                    status.details[result.breaker.value] = result.message

            # 5. Unhedged exposure
            if unhedged_keys:
                result = self._check_unhedged(unhedged_keys, now)
                if result.tripped:
                    status.all_clear = False
                    status.tripped_breakers.append(result)
                    status.details[result.breaker.value] = result.message

        if not status.all_clear:
            logger.warning(
                "CIRCUIT_BREAKER tripped: %s",
                [b.breaker.value for b in status.tripped_breakers],
            )

        return status

    # -----------------------------------------------------------------------
    # Individual checks
    # -----------------------------------------------------------------------

    def _check_daily_loss(self, daily_pnl: float) -> BreakerResult:
        """Daily max loss: realized + unrealized P&L."""
        if self._capital <= 0:
            return BreakerResult()

        loss_pct = daily_pnl / self._capital  # negative when losing
        if loss_pct < -self._th.daily_max_loss_pct:
            return BreakerResult(
                tripped=True,
                breaker=BreakerType.DAILY_MAX_LOSS,
                message=f"Daily PnL {loss_pct:.2%} exceeds -{self._th.daily_max_loss_pct:.0%} limit",
                kill_switch=True,  # system-wide halt
            )
        return BreakerResult()

    def _check_order_rate(self, now: float) -> BreakerResult:
        """Max orders per minute (rolling window)."""
        # Prune orders older than 60s
        while self._order_timestamps and (now - self._order_timestamps[0]) > 60.0:
            self._order_timestamps.popleft()

        count = len(self._order_timestamps)
        if count >= self._th.max_orders_per_minute:
            return BreakerResult(
                tripped=True,
                breaker=BreakerType.ORDER_RATE,
                message=f"{count} orders in last 60s, limit={self._th.max_orders_per_minute}",
            )
        return BreakerResult()

    def _check_gross_notional(self, gross_notional: float) -> BreakerResult:
        """Max gross notional exposure."""
        if self._capital <= 0:
            return BreakerResult()

        ratio = gross_notional / self._capital
        if ratio > self._th.max_gross_notional_multiple:
            return BreakerResult(
                tripped=True,
                breaker=BreakerType.GROSS_NOTIONAL,
                message=f"Gross notional {ratio:.1f}× capital, limit={self._th.max_gross_notional_multiple}×",
            )
        return BreakerResult()

    def _check_per_underlying(self, underlying: str, margin_pct: float) -> BreakerResult:
        """Per-underlying margin concentration."""
        if margin_pct > self._th.per_underlying_margin_cap:
            return BreakerResult(
                tripped=True,
                breaker=BreakerType.PER_UNDERLYING,
                message=f"{underlying} uses {margin_pct:.1%} of margin, cap={self._th.per_underlying_margin_cap:.0%}",
            )
        return BreakerResult()

    def _check_unhedged(self, unhedged_keys: list, now: float) -> BreakerResult:
        """Unhedged short option exposure timer."""
        timeout = self._th.unhedged_timeout_seconds

        for key in unhedged_keys:
            if key not in self._unhedged_since:
                self._unhedged_since[key] = now

            elapsed = now - self._unhedged_since[key]
            if elapsed > timeout:
                return BreakerResult(
                    tripped=True,
                    breaker=BreakerType.UNHEDGED_EXPOSURE,
                    message=f"{key} unhedged for {elapsed:.1f}s > {timeout}s",
                    kill_switch=False,  # force close, not full halt
                )

        # Clean up keys no longer unhedged
        current_keys = set(unhedged_keys)
        stale_keys = set(self._unhedged_since.keys()) - current_keys
        for k in stale_keys:
            del self._unhedged_since[k]

        return BreakerResult()

    # -----------------------------------------------------------------------
    # Order rate recording
    # -----------------------------------------------------------------------

    def record_order(self) -> None:
        """Record that an order was placed (for rate tracking)."""
        with self._lock:
            self._order_timestamps.append(time.monotonic())

    # -----------------------------------------------------------------------
    # Reset (e.g., start of new trading day)
    # -----------------------------------------------------------------------

    def reset_daily(self) -> None:
        """Reset daily counters at start of trading session."""
        with self._lock:
            self._order_timestamps.clear()
            self._unhedged_since.clear()
        logger.info("Circuit breakers reset for new trading day")

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def get_status_summary(self) -> Dict[str, object]:
        """Diagnostic summary for observability endpoint."""
        now = time.monotonic()
        with self._lock:
            # Prune for accurate count
            while self._order_timestamps and (now - self._order_timestamps[0]) > 60.0:
                self._order_timestamps.popleft()

            return {
                "capital": self._capital,
                "orders_last_minute": len(self._order_timestamps),
                "max_orders_per_minute": self._th.max_orders_per_minute,
                "daily_max_loss_pct": self._th.daily_max_loss_pct,
                "max_notional_multiple": self._th.max_gross_notional_multiple,
                "unhedged_keys": list(self._unhedged_since.keys()),
            }
