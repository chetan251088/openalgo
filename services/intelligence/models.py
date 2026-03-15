"""
Intelligence data models shared across all intelligence sub-services.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class MarketBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class VIXExpectation(str, Enum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"


class RRGQuadrant(str, Enum):
    LEADING = "Leading"
    WEAKENING = "Weakening"
    IMPROVING = "Improving"
    LAGGING = "Lagging"


@dataclass
class MiroFishSignal:
    bias: MarketBias
    confidence: float
    vix_expectation: VIXExpectation
    narrative_summary: str
    scenarios: list = field(default_factory=list)
    key_risks: list = field(default_factory=list)
    sector_outlook: dict = field(default_factory=dict)
    timestamp: float = 0.0
    stale: bool = False
    mode: str = "quick"

    def to_dict(self) -> dict:
        return {
            "bias": self.bias.value if isinstance(self.bias, MarketBias) else self.bias,
            "confidence": self.confidence,
            "vix_expectation": self.vix_expectation.value if isinstance(self.vix_expectation, VIXExpectation) else self.vix_expectation,
            "narrative_summary": self.narrative_summary,
            "scenarios": self.scenarios,
            "key_risks": self.key_risks,
            "sector_outlook": self.sector_outlook,
            "timestamp": self.timestamp,
            "stale": self.stale,
            "mode": self.mode,
        }


@dataclass
class SectorRotation:
    symbol: str
    name: str
    quadrant: RRGQuadrant
    rs_ratio: float
    rs_momentum: float
    date: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "quadrant": self.quadrant.value if isinstance(self.quadrant, RRGQuadrant) else self.quadrant,
            "rs_ratio": self.rs_ratio,
            "rs_momentum": self.rs_momentum,
            "date": self.date,
        }


@dataclass
class RotationSignal:
    sectors: list = field(default_factory=list)
    transitions: list = field(default_factory=list)
    leading_sectors: list = field(default_factory=list)
    lagging_sectors: list = field(default_factory=list)
    improving_sectors: list = field(default_factory=list)
    weakening_sectors: list = field(default_factory=list)
    benchmark: str = "NIFTY"
    timestamp: float = 0.0
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "sectors": [s.to_dict() if hasattr(s, "to_dict") else s for s in self.sectors],
            "transitions": self.transitions,
            "leading_sectors": self.leading_sectors,
            "lagging_sectors": self.lagging_sectors,
            "improving_sectors": self.improving_sectors,
            "weakening_sectors": self.weakening_sectors,
            "benchmark": self.benchmark,
            "timestamp": self.timestamp,
            "stale": self.stale,
        }


@dataclass
class FundamentalProfile:
    symbol: str
    roce: Optional[float] = None
    pe: Optional[float] = None
    debt_equity: Optional[float] = None
    promoter_holding: Optional[float] = None
    fii_change_qoq: Optional[float] = None
    market_cap: Optional[float] = None
    quarterly_profit_growth: Optional[float] = None
    cleared: bool = False
    block_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "roce": self.roce,
            "pe": self.pe,
            "debt_equity": self.debt_equity,
            "promoter_holding": self.promoter_holding,
            "fii_change_qoq": self.fii_change_qoq,
            "market_cap": self.market_cap,
            "quarterly_profit_growth": self.quarterly_profit_growth,
            "cleared": self.cleared,
            "block_reason": self.block_reason,
        }


@dataclass
class FundamentalSignal:
    profiles: dict = field(default_factory=dict)
    cleared_symbols: set = field(default_factory=set)
    blocked_symbols: dict = field(default_factory=dict)
    timestamp: float = 0.0
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "profiles": {k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in self.profiles.items()},
            "cleared_symbols": sorted(self.cleared_symbols),
            "blocked_symbols": self.blocked_symbols,
            "timestamp": self.timestamp,
            "stale": self.stale,
        }


class GatePolicy(str, Enum):
    """How to handle a stale or disconnected intelligence source."""
    FAIL_OPEN = "fail_open"    # treat as neutral / allow trade
    FAIL_CLOSED = "fail_closed"  # block trade / skip signal


# Default gate policies: what happens when each source is unavailable
DEFAULT_GATE_POLICIES = {
    "mirofish": GatePolicy.FAIL_OPEN,       # if no prediction, treat as neutral
    "rotation": GatePolicy.FAIL_OPEN,       # if no rotation data, allow trading
    "fundamentals": GatePolicy.FAIL_OPEN,   # if no fundamentals, allow trading
}


@dataclass
class SourceHealth:
    """Per-source staleness and health tracking."""
    status: str = "disconnected"  # connected, stale, disconnected, error
    staleness_seconds: float = 0.0
    last_success: float = 0.0
    last_error: str = ""
    gate_policy: GatePolicy = GatePolicy.FAIL_OPEN

    def is_usable(self) -> bool:
        """Can this source's data be used for trade decisions?"""
        if self.status == "connected":
            return True
        if self.status == "stale" and self.gate_policy == GatePolicy.FAIL_OPEN:
            return True
        return self.gate_policy == GatePolicy.FAIL_OPEN

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "staleness_seconds": round(self.staleness_seconds, 1),
            "last_success": self.last_success,
            "last_error": self.last_error,
            "gate_policy": self.gate_policy.value,
            "usable": self.is_usable(),
        }


@dataclass
class MarketIntelligence:
    mirofish: Optional[MiroFishSignal] = None
    rotation: Optional[RotationSignal] = None
    fundamentals: Optional[FundamentalSignal] = None
    timestamp: float = 0.0
    staleness_seconds: float = 0.0
    source_health: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.source_health:
            self.source_health = {
                "mirofish": SourceHealth(gate_policy=DEFAULT_GATE_POLICIES["mirofish"]),
                "rotation": SourceHealth(gate_policy=DEFAULT_GATE_POLICIES["rotation"]),
                "fundamentals": SourceHealth(gate_policy=DEFAULT_GATE_POLICIES["fundamentals"]),
            }

    def update_source_health(self) -> None:
        """Recompute per-source health from actual signal state."""
        now = time.time()

        for name, signal in [("mirofish", self.mirofish), ("rotation", self.rotation), ("fundamentals", self.fundamentals)]:
            health = self.source_health.get(name, SourceHealth())
            if signal is None:
                health.status = "disconnected"
                health.staleness_seconds = 0
            elif signal.stale:
                health.status = "stale"
                health.staleness_seconds = round(now - signal.timestamp, 1) if signal.timestamp else 0
            else:
                health.status = "connected"
                health.staleness_seconds = round(now - signal.timestamp, 1) if signal.timestamp else 0
                health.last_success = signal.timestamp
            self.source_health[name] = health

    def is_source_usable(self, source_name: str) -> bool:
        """Check if a specific source is usable for trade decisions."""
        health = self.source_health.get(source_name)
        if health is None:
            return True  # unknown source -> fail open
        return health.is_usable()

    def to_dict(self) -> dict:
        now = time.time()
        self.update_source_health()
        return {
            "mirofish": self.mirofish.to_dict() if self.mirofish else None,
            "rotation": self.rotation.to_dict() if self.rotation else None,
            "fundamentals": self.fundamentals.to_dict() if self.fundamentals else None,
            "timestamp": self.timestamp,
            "staleness_seconds": round(now - self.timestamp, 1) if self.timestamp else 0,
            "sources": {
                name: health.to_dict()
                for name, health in self.source_health.items()
            },
        }
