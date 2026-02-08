from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from utils.logging import get_logger

from .config import PlaybookConfig

logger = get_logger(__name__)


@dataclass
class Playbook:
    name: str
    config: PlaybookConfig


def parse_expiry_date(expiry: str) -> Optional[datetime]:
    if not expiry:
        return None
    try:
        # Expected format: DDMMMYY (e.g., 03FEB26)
        return datetime.strptime(expiry.upper(), "%d%b%y")
    except ValueError:
        return None


def is_expiry_day(expiry: str) -> bool:
    expiry_date = parse_expiry_date(expiry)
    if not expiry_date:
        return False
    now = datetime.now()
    return now.date() == expiry_date.date()


class PlaybookManager:
    def __init__(self, base: PlaybookConfig) -> None:
        self.base = base
        self.current = Playbook(name="baseline", config=base)

    def select_playbook(
        self, volatility: float, expiry: str, after_1400: bool, after_1430: bool, after_1500: bool
    ) -> Playbook:
        if is_expiry_day(expiry) and after_1400:
            cfg = PlaybookConfig(
                momentum_ticks=max(2, self.base.momentum_ticks - 1),
                tp_points=3.0 if not after_1500 else 2.5,
                sl_points=5.0 if not after_1500 else 4.0,
                trail_distance=self.base.trail_distance,
                trail_step=self.base.trail_step,
                trailing_enabled=self.base.trailing_enabled,
                trailing_override_tp=self.base.trailing_override_tp,
            )
            return Playbook(name="expiry_gamma", config=cfg)

        if volatility >= 1.2:
            return Playbook(name="trend", config=self.base)

        cfg = PlaybookConfig(
            momentum_ticks=min(6, self.base.momentum_ticks + 1),
            tp_points=self.base.tp_points,
            sl_points=self.base.sl_points,
            trail_distance=self.base.trail_distance,
            trail_step=self.base.trail_step,
            trailing_enabled=self.base.trailing_enabled,
            trailing_override_tp=self.base.trailing_override_tp,
        )
        return Playbook(name="chop", config=cfg)

    def update(self, volatility: float, expiry: str) -> Playbook:
        now = datetime.now()
        after_1400 = now.hour >= 14
        after_1430 = now.hour > 14 or (now.hour == 14 and now.minute >= 30)
        after_1500 = now.hour >= 15
        self.current = self.select_playbook(volatility, expiry, after_1400, after_1430, after_1500)
        return self.current

    def apply_adjustments(self, adjustments: dict) -> None:
        for key, value in adjustments.items():
            if hasattr(self.base, key):
                setattr(self.base, key, value)
