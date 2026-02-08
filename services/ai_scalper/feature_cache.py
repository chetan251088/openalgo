from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
from typing import Deque, Dict, Optional


@dataclass
class SideFeatures:
    last_price: float = 0.0
    last_tick_ts: float = 0.0
    momentum_dir: Optional[str] = None  # "up" | "down"
    momentum_count: int = 0
    prices: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    spread: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None
    imbalance_ratio: Optional[float] = None


class FeatureCache:
    def __init__(self) -> None:
        self.sides: Dict[str, SideFeatures] = {"CE": SideFeatures(), "PE": SideFeatures()}

    def update_tick(self, side: str, price: float, ts: float) -> None:
        features = self.sides[side]
        if features.last_price:
            if price > features.last_price:
                if features.momentum_dir == "up":
                    features.momentum_count += 1
                else:
                    features.momentum_dir = "up"
                    features.momentum_count = 1
            elif price < features.last_price:
                if features.momentum_dir == "down":
                    features.momentum_count += 1
                else:
                    features.momentum_dir = "down"
                    features.momentum_count = 1
        features.last_price = price
        features.last_tick_ts = ts
        features.prices.append(price)

    def update_depth(
        self,
        side: str,
        bid: Optional[float],
        ask: Optional[float],
        bid_qty: Optional[float],
        ask_qty: Optional[float],
    ) -> None:
        features = self.sides[side]
        features.bid = bid
        features.ask = ask
        features.bid_qty = bid_qty
        features.ask_qty = ask_qty
        if bid is not None and ask is not None:
            features.spread = max(0.0, ask - bid)
        else:
            features.spread = None
        if bid_qty and ask_qty and ask_qty > 0:
            features.imbalance_ratio = bid_qty / ask_qty
        else:
            features.imbalance_ratio = None

    def get_momentum(self, side: str) -> tuple[Optional[str], int]:
        feat = self.sides[side]
        return feat.momentum_dir, feat.momentum_count

    def get_spread(self, side: str) -> Optional[float]:
        return self.sides[side].spread

    def get_imbalance_ratio(self, side: str) -> Optional[float]:
        return self.sides[side].imbalance_ratio

    def get_volatility(self, side: str) -> float:
        feat = self.sides[side]
        prices = list(feat.prices)
        if len(prices) < 3:
            return 0.0
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        return math.sqrt(variance)
