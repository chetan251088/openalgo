"""
TOMIC Expiry Specialist — Gamma Capture After 14:00
====================================================
On expiry day, after 14:00, near-ATM options are nearly worthless
but can multiply 5-20× on violent moves.

Generates GAMMA_CAPTURE signals: buy 1-OTM CE + 1-OTM PE.
Max size: ₹5,000 or 0.5% of capital (whichever is lower).
Exit: 15:10 regardless.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from tomic.conflict_router import RoutedSignal, SignalSource
from tomic.config import ExpiryParams, StrategyType, TomicConfig

logger = logging.getLogger(__name__)

# Expiry weekdays per instrument (0=Monday, 3=Thursday, etc.)
_EXPIRY_WEEKDAY = {
    "NIFTY":     3,   # Thursday
    "BANKNIFTY": 2,   # Wednesday
    "SENSEX":    4,   # Friday
    "FINNIFTY":  1,   # Tuesday
}


def is_expiry_day(instrument: str, today: Optional[date] = None) -> bool:
    """Return True if today is the weekly expiry day for the instrument."""
    if today is None:
        today = date.today()
    weekday = _EXPIRY_WEEKDAY.get(instrument.upper(), -1)
    return today.weekday() == weekday


def is_after_gamma_entry_time(now: datetime, entry_hhmm: str) -> bool:
    """Return True if current time is at or after the gamma entry time."""
    try:
        h, m = map(int, entry_hhmm.split(":"))
        return now.hour > h or (now.hour == h and now.minute >= m)
    except ValueError:
        return False


class ExpirySpecialist:
    """
    Generates gamma capture signals on expiry days after 14:00.
    One signal per instrument per day maximum.
    """

    def __init__(self, config: TomicConfig) -> None:
        self._config = config
        self._params: ExpiryParams = config.expiry
        self._lock = threading.Lock()
        self._generated_today: Dict[str, str] = {}  # instrument → date string

    def get_gamma_signals(
        self,
        now: Optional[datetime] = None,
        instruments: Optional[List[str]] = None,
    ) -> List[RoutedSignal]:
        """
        Check each instrument for expiry + time conditions.
        Returns GAMMA_CAPTURE signals for qualifying instruments.
        """
        if now is None:
            now = datetime.now()

        instruments = instruments or list(_EXPIRY_WEEKDAY.keys())
        signals = []
        today_str = now.date().isoformat()

        for instrument in instruments:
            if not is_expiry_day(instrument, now.date()):
                continue
            if not is_after_gamma_entry_time(now, self._params.gamma_entry_hhmm):
                continue

            with self._lock:
                if self._generated_today.get(instrument.upper()) == today_str:
                    continue  # already generated today
                self._generated_today[instrument.upper()] = today_str

            signal = self._make_gamma_signal(instrument)
            signals.append(signal)
            logger.info("ExpirySpecialist: GAMMA_CAPTURE signal for %s", instrument)

        return signals

    def _make_gamma_signal(self, instrument: str) -> RoutedSignal:
        signal_dict: Dict[str, Any] = {
            "instrument": instrument.upper(),
            "strategy_type": StrategyType.GAMMA_CAPTURE.value,
            "direction": "BUY",
            "legs": [
                {"leg_type": "BUY_CALL", "option_type": "CE",
                 "direction": "BUY", "offset": "otm_call"},
                {"leg_type": "BUY_PUT",  "option_type": "PE",
                 "direction": "BUY", "offset": "otm_put"},
            ],
            "max_capital_pct": self._params.max_capital_pct,
            "max_abs_inr": self._params.max_abs_inr,
            "entry_mode": "expiry_gamma",
            "signal_strength": 60.0,
        }
        return RoutedSignal(
            source=SignalSource.VOLATILITY,
            signal_dict=signal_dict,
            priority_score=60.0,
        )

    def reset_for_new_day(self) -> None:
        with self._lock:
            self._generated_today.clear()
