"""
ExpirySpecialist — Generates GAMMA_CAPTURE signals after 14:00 on expiry days.
Buys cheap near-ATM CE+PE (lottery tickets) with strict capital limits.
"""

import logging
import time
from datetime import datetime
from typing import List, Optional

from .config import ExpiryParams, StrategyType

logger = logging.getLogger(__name__)


EXPIRY_WEEKDAYS = {
    "NIFTY": 3,       # Thursday
    "BANKNIFTY": 2,    # Wednesday
    "SENSEX": 4,       # Friday
    "FINNIFTY": 1,     # Tuesday
    "MIDCPNIFTY": 0,   # Monday
}


class ExpirySpecialist:
    """Detects expiry day conditions and generates gamma capture signals."""

    def __init__(self, config: Optional[ExpiryParams] = None, intelligence_service=None):
        self.config = config or ExpiryParams()
        self.intelligence = intelligence_service
        self._signals_today: dict = {}  # instrument -> bool (fired today)

    def is_expiry_day(self, instrument: str) -> bool:
        """Check if today is the weekly expiry day for the given instrument."""
        target_weekday = EXPIRY_WEEKDAYS.get(instrument, -1)
        return datetime.now().weekday() == target_weekday

    def is_gamma_window(self) -> bool:
        """Check if current time is within the gamma capture window."""
        now = datetime.now()
        entry_h, entry_m = map(int, self.config.gamma_entry_hhmm.split(":"))
        exit_h, exit_m = map(int, self.config.gamma_exit_hhmm.split(":"))
        
        current_minutes = now.hour * 60 + now.minute
        entry_minutes = entry_h * 60 + entry_m
        exit_minutes = exit_h * 60 + exit_m
        
        return entry_minutes <= current_minutes <= exit_minutes

    def check_gamma_entry(self, market_context) -> list:
        """Check if gamma capture conditions are met and generate signals.

        Conditions:
        1. Today is expiry day for the instrument
        2. Current time is within gamma window (after 14:00)
        3. Haven't already fired a gamma signal for this instrument today
        4. MiroFish confidence is above minimum (if available)
        """
        signals = []

        for instrument in EXPIRY_WEEKDAYS:
            if not self.is_expiry_day(instrument):
                continue
            if not self.is_gamma_window():
                continue
            if self._signals_today.get(instrument):
                continue

            # Skip if MiroFish confidence is too low (too uncertain for gamma play)
            if (
                self.intelligence
                and market_context.mirofish_confidence > 0
                and market_context.mirofish_confidence < 0.3
            ):
                logger.info("Skipping gamma capture for %s: MiroFish confidence %.2f too low",
                          instrument, market_context.mirofish_confidence)
                continue

            from .strategy_engine import RoutedSignal
            signal = RoutedSignal(
                instrument=instrument,
                strategy_type=StrategyType.GAMMA_CAPTURE.value,
                direction="BUY",
                entry_mode="expiry_gamma",
                lots=1,
                rationale=f"Expiry day gamma capture for {instrument}",
                confidence=0.5,
                timestamp=time.time(),
            )
            signals.append(signal)
            self._signals_today[instrument] = True
            logger.info("Gamma capture signal generated for %s", instrument)

        return signals

    def get_max_capital(self, total_capital: float) -> float:
        """Return the maximum capital allowed for gamma capture."""
        pct_limit = total_capital * self.config.max_capital_pct
        return min(pct_limit, self.config.max_abs_inr)

    def reset_daily(self) -> None:
        """Reset daily signal tracking."""
        self._signals_today.clear()
        logger.info("ExpirySpecialist daily counters reset")
