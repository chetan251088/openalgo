"""
Intelligence Service - Aggregates MiroFish predictions, Sector Rotation data,
and OpenScreener fundamentals into a unified market intelligence feed.

Key components:
- IntelligenceService: central aggregator with kill switch
- TradeGateSnapshot: precomputed per-symbol/side decisions (read by tick loop)
- DecisionLogger: full attribution logging for every trade decision
"""

from .service import IntelligenceService
from .models import (
    MarketIntelligence,
    MiroFishSignal,
    RotationSignal,
    FundamentalSignal,
    SectorRotation,
    FundamentalProfile,
    MarketBias,
    VIXExpectation,
    RRGQuadrant,
    GatePolicy,
    SourceHealth,
)
from .gate_snapshot import TradeGateSnapshot, SymbolGate, compute_snapshot, decay_confidence
from .decision_logger import DecisionLogger

__all__ = [
    "IntelligenceService",
    "MarketIntelligence",
    "MiroFishSignal",
    "RotationSignal",
    "FundamentalSignal",
    "SectorRotation",
    "FundamentalProfile",
    "MarketBias",
    "VIXExpectation",
    "RRGQuadrant",
    "GatePolicy",
    "SourceHealth",
    "TradeGateSnapshot",
    "SymbolGate",
    "compute_snapshot",
    "decay_confidence",
    "DecisionLogger",
]
