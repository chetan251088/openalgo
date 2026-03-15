"""
TradeGateSnapshot — Precomputed intelligence decisions per symbol/side.

Architecture:
- Refreshed every 60s by a background daemon (never in the tick path)
- Read as in-memory booleans + float multipliers by the scalping engine
- Scalping tick loop does ONE dict lookup (~1μs), not HTTP calls or gate evaluation

This is the bridge between slow intelligence (seconds/minutes) and fast execution (<5ms).

Horizon matching:
- MiroFish → shapes daily bias, allowed side, position size (hours horizon)
- Rotation → chooses the stock universe (weeks horizon)
- Fundamentals → daily eligibility filter (quarters horizon)
- None of these should directly decide a sub-second scalp entry except in extreme conflict
"""

import time
import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Dict
from enum import Enum

logger = logging.getLogger(__name__)

# Confidence half-life: 0.85 confidence at 9:30 decays to ~0.40 by 14:30 (5 hours)
CONFIDENCE_HALF_LIFE_SECONDS = 3600 * 2.5  # 2.5 hours


class SideAllowance(str, Enum):
    ALLOWED = "allowed"
    DISCOURAGED = "discouraged"  # reduced size but not blocked
    BLOCKED = "blocked"          # only for extreme conflict / stale data


@dataclass
class SymbolGate:
    """Precomputed gate decision for ONE symbol on ONE side (CE or PE)."""
    symbol: str
    side: str  # "CE" or "PE"

    # Final decisions (what the tick loop reads)
    allowed: bool = True
    size_multiplier: float = 1.0  # 0x = blocked, 0.5x = discouraged, 1.0x = neutral, 1.25x = boosted
    side_allowance: SideAllowance = SideAllowance.ALLOWED
    reason: str = ""

    # Component scores (for decision attribution / logging)
    mirofish_score: float = 0.0    # -1.0 to +1.0
    rotation_score: float = 0.0    # -1.0 to +1.0
    fundamental_ok: bool = True
    expected_edge_bps: float = 0.0 # expected edge in basis points after costs
    spread_cost_bps: float = 0.0
    min_edge_bps: float = 5.0      # minimum edge to justify a trade

    # Metadata
    computed_at: float = 0.0
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "allowed": self.allowed,
            "size_multiplier": round(self.size_multiplier, 2),
            "side_allowance": self.side_allowance.value,
            "reason": self.reason,
            "mirofish_score": round(self.mirofish_score, 3),
            "rotation_score": round(self.rotation_score, 3),
            "fundamental_ok": self.fundamental_ok,
            "expected_edge_bps": round(self.expected_edge_bps, 1),
            "computed_at": self.computed_at,
            "stale": self.stale,
        }


@dataclass
class TradeGateSnapshot:
    """The full precomputed snapshot. One dict lookup per tick."""
    gates: Dict[str, SymbolGate] = field(default_factory=dict)  # key = "{SYMBOL}_{CE|PE}"
    daily_bias: str = "NEUTRAL"        # BULLISH / BEARISH / NEUTRAL
    daily_confidence: float = 0.0      # decayed confidence
    daily_raw_confidence: float = 0.0  # original confidence before decay
    allowed_universe: list = field(default_factory=list)  # F&O-eligible + rotation-cleared
    kill_switch: bool = False
    computed_at: float = 0.0

    def get_gate(self, symbol: str, side: str) -> Optional[SymbolGate]:
        """O(1) lookup. Returns None if symbol not in universe (= fail-open, size 1.0x)."""
        return self.gates.get(f"{symbol}_{side}")

    def is_allowed(self, symbol: str, side: str) -> bool:
        """Quick boolean check for the tick loop."""
        if self.kill_switch:
            return True  # kill switch = pure technical, everything allowed
        gate = self.get_gate(symbol, side)
        if gate is None:
            return True  # unknown symbol = fail-open
        return gate.allowed

    def get_size_multiplier(self, symbol: str, side: str) -> float:
        """Returns the size multiplier for position sizing. 1.0 = normal."""
        if self.kill_switch:
            return 1.0
        gate = self.get_gate(symbol, side)
        if gate is None:
            return 1.0
        return gate.size_multiplier

    def to_dict(self) -> dict:
        return {
            "daily_bias": self.daily_bias,
            "daily_confidence": round(self.daily_confidence, 3),
            "daily_raw_confidence": round(self.daily_raw_confidence, 3),
            "allowed_universe": self.allowed_universe,
            "kill_switch": self.kill_switch,
            "computed_at": self.computed_at,
            "gate_count": len(self.gates),
            "gates": {k: v.to_dict() for k, v in self.gates.items()},
        }


def decay_confidence(raw_confidence: float, signal_timestamp: float, now: float = None) -> float:
    """Exponential time-decay: halves every CONFIDENCE_HALF_LIFE_SECONDS.
    
    A 0.85 confidence at 9:30 AM decays to ~0.42 by 12:00 PM (2.5hr half-life).
    If the signal is refreshed, timestamp resets and confidence is full again.
    """
    if now is None:
        now = time.time()
    age = max(0, now - signal_timestamp)
    decay_factor = math.exp(-0.693 * age / CONFIDENCE_HALF_LIFE_SECONDS)  # ln(2) ≈ 0.693
    return raw_confidence * decay_factor


def compute_snapshot(
    intelligence_service,
    symbols: list = None,
    spread_estimates: dict = None,
) -> TradeGateSnapshot:
    """Compute a fresh TradeGateSnapshot from current intelligence state.
    
    Called every 60s by a background daemon. The result is stored in-memory
    and read by the tick loop without any computation.
    
    Args:
        intelligence_service: The IntelligenceService singleton
        symbols: List of symbols to compute gates for (default: F&O universe)
        spread_estimates: Dict of {symbol: spread_bps} for edge calculation
    """
    snapshot = TradeGateSnapshot(computed_at=time.time())

    if intelligence_service.kill_switch:
        snapshot.kill_switch = True
        return snapshot

    intel = intelligence_service.get_intelligence()
    if intel is None:
        return snapshot  # no intelligence = all gates open, size 1.0x

    now = time.time()

    # Daily bias from MiroFish (with time decay)
    if intel.mirofish and not intel.mirofish.stale:
        snapshot.daily_raw_confidence = intel.mirofish.confidence
        snapshot.daily_confidence = decay_confidence(
            intel.mirofish.confidence, intel.mirofish.timestamp, now
        )
        snapshot.daily_bias = intel.mirofish.bias.value if hasattr(intel.mirofish.bias, 'value') else str(intel.mirofish.bias)

        # If decayed confidence is too low, treat as NEUTRAL
        if snapshot.daily_confidence < 0.3:
            snapshot.daily_bias = "NEUTRAL"
    else:
        snapshot.daily_bias = "NEUTRAL"
        snapshot.daily_confidence = 0.0

    # Build universe from rotation (if available)
    leading_stocks = set()
    lagging_stocks = set()
    if intel.rotation and not intel.rotation.stale:
        from .rotation_client import SECTOR_TO_STOCKS
        for sector in intel.rotation.leading_sectors:
            leading_stocks.update(SECTOR_TO_STOCKS.get(sector, []))
        for sector in intel.rotation.lagging_sectors:
            lagging_stocks.update(SECTOR_TO_STOCKS.get(sector, []))

    if symbols is None:
        symbols = ["NIFTY", "BANKNIFTY", "SENSEX"]  # at minimum, always include indices

    snapshot.allowed_universe = list(symbols)
    spread_estimates = spread_estimates or {}

    for symbol in symbols:
        for side in ("CE", "PE"):
            gate = _compute_symbol_gate(
                symbol=symbol,
                side=side,
                daily_bias=snapshot.daily_bias,
                daily_confidence=snapshot.daily_confidence,
                leading_stocks=leading_stocks,
                lagging_stocks=lagging_stocks,
                intelligence_service=intelligence_service,
                spread_bps=spread_estimates.get(symbol, 10.0),
            )
            snapshot.gates[f"{symbol}_{side}"] = gate

    return snapshot


def _compute_symbol_gate(
    symbol: str,
    side: str,
    daily_bias: str,
    daily_confidence: float,
    leading_stocks: set,
    lagging_stocks: set,
    intelligence_service,
    spread_bps: float = 10.0,
) -> SymbolGate:
    """Compute a single gate for one symbol+side combination.
    
    Returns SIZE MULTIPLIER instead of binary pass/fail:
    - 0.0x = effectively blocked (only for extreme cases)
    - 0.5x = discouraged (conflicting signals)
    - 1.0x = neutral (no intelligence signal or low confidence)
    - 1.25x = boosted (aligned signals with high confidence)
    """
    gate = SymbolGate(symbol=symbol, side=side, computed_at=time.time())
    multiplier = 1.0
    reasons = []

    index_symbols = {"NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"}
    is_index = symbol in index_symbols

    # 1. MiroFish bias → side allowance + size adjustment
    if daily_confidence >= 0.3:
        bias_aligns = (
            (daily_bias == "BULLISH" and side == "CE") or
            (daily_bias == "BEARISH" and side == "PE")
        )
        bias_conflicts = (
            (daily_bias == "BULLISH" and side == "PE") or
            (daily_bias == "BEARISH" and side == "CE")
        )

        if bias_aligns and daily_confidence >= 0.6:
            multiplier *= 1.25
            gate.mirofish_score = daily_confidence
            reasons.append(f"MiroFish aligned ({daily_bias} + {side}, conf={daily_confidence:.0%})")
        elif bias_conflicts and daily_confidence >= 0.6:
            multiplier *= 0.5
            gate.mirofish_score = -daily_confidence
            gate.side_allowance = SideAllowance.DISCOURAGED
            reasons.append(f"MiroFish conflicts ({daily_bias} vs {side}, conf={daily_confidence:.0%})")
        elif bias_conflicts and daily_confidence >= 0.8:
            multiplier *= 0.0
            gate.mirofish_score = -1.0
            gate.side_allowance = SideAllowance.BLOCKED
            reasons.append(f"MiroFish STRONG conflict ({daily_bias} vs {side}, conf={daily_confidence:.0%})")

    # 2. Rotation → size adjustment for stock options (not indices)
    if not is_index:
        if symbol in leading_stocks:
            if side == "CE":
                multiplier *= 1.15
                gate.rotation_score = 0.5
                reasons.append("Sector Leading + CE = boosted")
            else:
                multiplier *= 0.8
                gate.rotation_score = -0.3
                reasons.append("Sector Leading but PE = reduced")
        elif symbol in lagging_stocks:
            if side == "PE":
                multiplier *= 1.15
                gate.rotation_score = 0.5
                reasons.append("Sector Lagging + PE = boosted")
            else:
                multiplier *= 0.8
                gate.rotation_score = -0.3
                reasons.append("Sector Lagging but CE = reduced")

    # 3. Fundamental gate → hard block only for non-index non-cleared
    if not is_index:
        cleared = intelligence_service.is_fundamentally_cleared(symbol)
        gate.fundamental_ok = cleared
        if not cleared:
            multiplier *= 0.0
            gate.side_allowance = SideAllowance.BLOCKED
            reasons.append("Fundamental gate: not cleared")

    # 4. Expected edge after costs
    estimated_edge_bps = 15.0 * multiplier  # rough: 15bps base edge scaled by multiplier
    gate.expected_edge_bps = estimated_edge_bps - spread_bps
    gate.spread_cost_bps = spread_bps

    if gate.expected_edge_bps < gate.min_edge_bps and multiplier > 0:
        multiplier *= 0.5
        reasons.append(f"Low edge after costs ({gate.expected_edge_bps:.1f}bps < {gate.min_edge_bps}bps)")

    # Final
    gate.size_multiplier = max(0.0, min(2.0, multiplier))
    gate.allowed = gate.size_multiplier > 0.0
    gate.reason = " | ".join(reasons) if reasons else "No intelligence signal (neutral)"

    return gate
