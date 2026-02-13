"""
TOMIC Sniper Agent — Directional Pattern Recognition
=====================================================
Identifies precise entry points using three pattern types:
  1. VCP (Volatility Contraction Pattern) — Minervini
  2. Supply/Demand Zones — Batus/Mansor fresh zone detection
  3. 3-C (Cup Completion Cheat) — Early cup-and-handle entry

Operates on the pre-filtered Active Watchlist from UniverseParams.
Signals are ranked by RS(50d) and submitted to the Risk Agent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from tomic.agents.regime_agent import AtomicRegimeState, RegimeSnapshot
from tomic.config import (
    RegimePhase,
    StrategyType,
    SniperParams,
    TomicConfig,
    UniverseParams,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern Enums
# ---------------------------------------------------------------------------

class PatternType(str, Enum):
    VCP = "VCP"
    SUPPLY_DEMAND = "SUPPLY_DEMAND"
    THREE_C = "THREE_C"


class ZoneType(str, Enum):
    DEMAND = "DEMAND"           # Rally-Base-Drop: bullish entry zone
    SUPPLY = "SUPPLY"           # Drop-Base-Rally: bearish entry zone


class ZoneFreshness(str, Enum):
    FRESH = "FRESH"             # untouched since formation
    TESTED = "TESTED"           # touched once (invalidated)
    BROKEN = "BROKEN"           # price traded through


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class VCPResult:
    """Result of VCP detection on an instrument."""
    detected: bool = False
    contractions: int = 0
    depth_ratios: List[float] = field(default_factory=list)
    volume_ants: bool = False
    pivot_price: float = 0.0
    tightest_range: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SDZone:
    """A Supply/Demand zone with coordinates and freshness tracking."""
    zone_type: ZoneType
    upper: float
    lower: float
    formation_time: float = 0.0   # monotonic timestamp
    touches: int = 0
    freshness: ZoneFreshness = ZoneFreshness.FRESH
    imbalance_strength: float = 0.0  # gap magnitude

    @property
    def midpoint(self) -> float:
        return (self.upper + self.lower) / 2

    @property
    def width(self) -> float:
        return self.upper - self.lower

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_type": self.zone_type.value,
            "upper": self.upper,
            "lower": self.lower,
            "touches": self.touches,
            "freshness": self.freshness.value,
            "imbalance_strength": self.imbalance_strength,
        }


@dataclass
class ThreeCResult:
    """Result of 3-C Cup Completion Cheat detection."""
    detected: bool = False
    cup_depth: float = 0.0
    cup_width_bars: int = 0
    handle_pause: bool = False
    pivot_price: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SniperSignal:
    """A scored signal from the Sniper Agent."""
    instrument: str
    pattern: PatternType
    direction: str              # "BUY" or "SELL"
    entry_price: float
    stop_price: float
    target_price: float = 0.0
    rs_score: float = 0.0       # Relative Strength (50d)
    pattern_data: Dict[str, Any] = field(default_factory=dict)
    signal_score: float = 0.0   # composite scoring for ranking
    timestamp: float = field(default_factory=time.monotonic)

    def to_signal_dict(self) -> Dict[str, Any]:
        """Convert to Risk Agent signal format."""
        # Map directional view to tradable option intent:
        # - bullish sniper -> buy DITM call
        # - bearish sniper -> buy DITM put
        if self.direction.upper() == "SELL":
            strategy_type = StrategyType.DITM_PUT.value
            order_direction = "BUY"
            option_type = "PE"
        else:
            strategy_type = StrategyType.DITM_CALL.value
            order_direction = "BUY"
            option_type = "CE"

        return {
            "instrument": self.instrument,
            "strategy_type": strategy_type,
            "direction": order_direction,
            "signal_direction": self.direction.upper(),
            "option_type": option_type,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "pattern": self.pattern.value,
            "rs_score": self.rs_score,
            "signal_score": self.signal_score,
        }


# ---------------------------------------------------------------------------
# VCP Detection (Minervini)
# ---------------------------------------------------------------------------

def detect_vcp(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    params: Optional[SniperParams] = None,
) -> VCPResult:
    """
    Detect Volatility Contraction Pattern (Minervini).

    Looks for:
    - 2-4 progressively tighter price contractions
    - Each ~50% of prior contraction depth
    - Volume drying up ("ants") < 50% of 50-day average
    - Price near upper end of range (potential breakout pivot)
    """
    if params is None:
        params = SniperParams()

    n = len(closes)
    if n < 30:
        return VCPResult()

    # 1. Find swing highs and swing lows (5-bar pivots)
    swing_highs = []
    swing_lows = []
    for i in range(2, n - 2):
        if highs[i] >= max(highs[i-2:i]) and highs[i] >= max(highs[i+1:i+3]):
            swing_highs.append((i, highs[i]))
        if lows[i] <= min(lows[i-2:i]) and lows[i] <= min(lows[i+1:i+3]):
            swing_lows.append((i, lows[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return VCPResult()

    # 2. Measure contractions (lower highs with roughly constant lows)
    contractions = []
    for i in range(1, len(swing_highs)):
        prev_high = swing_highs[i - 1][1]
        curr_high = swing_highs[i][1]
        if curr_high < prev_high:
            # Find the low between these two highs
            between_lows = [
                lo for idx, lo in swing_lows
                if swing_highs[i - 1][0] < idx < swing_highs[i][0]
            ]
            if between_lows:
                contraction_low = min(between_lows)
                prev_range = prev_high - contraction_low
                curr_range = curr_high - contraction_low
                if prev_range > 0:
                    depth_ratio = curr_range / prev_range
                    contractions.append(depth_ratio)

    if len(contractions) < params.vcp_contractions_min:
        return VCPResult()

    # 3. Check depth ratios (each ~50% of prior)
    valid_ratios = sum(
        1 for r in contractions if r < params.vcp_depth_ratio + 0.2
    )
    if valid_ratios < params.vcp_contractions_min:
        return VCPResult()

    # 4. Volume "ants" check — recent volume < threshold × 50d average
    vol_avg_50 = sum(volumes[-50:]) / min(50, len(volumes[-50:])) if len(volumes) >= 10 else 0
    recent_vol = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    volume_ants = (recent_vol < params.volume_ants_threshold * vol_avg_50) if vol_avg_50 > 0 else False

    # 5. Pivot (breakout) price = highest high of tightest contraction
    pivot_price = max(closes[-5:]) if closes else 0
    tightest = min(contractions) if contractions else 0

    return VCPResult(
        detected=True,
        contractions=len(contractions),
        depth_ratios=contractions[:params.vcp_contractions_max],
        volume_ants=volume_ants,
        pivot_price=pivot_price,
        tightest_range=tightest,
    )


# ---------------------------------------------------------------------------
# Supply/Demand Zone Detection (Batus/Mansor)
# ---------------------------------------------------------------------------

def detect_sd_zones(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    opens: List[float],
    params: Optional[SniperParams] = None,
) -> List[SDZone]:
    """
    Detect fresh Supply/Demand zones.

    Demand Zone (Rally-Base-Drop): Sharp rally → consolidation base → price
    leaves with a gap/imbalance. Bullish re-entry when price returns.

    Supply Zone (Drop-Base-Rally): Sharp drop → consolidation → price
    leaves with a gap upward. Bearish re-entry when price returns.
    """
    if params is None:
        params = SniperParams()

    n = len(closes)
    if n < 10:
        return []

    zones: List[SDZone] = []

    for i in range(3, n - 3):
        # Look for "basing" candles (small body, low range)
        body = abs(closes[i] - opens[i])
        full_range = highs[i] - lows[i]
        if full_range == 0:
            continue
        body_ratio = body / full_range

        # Base candle: small body relative to range
        if body_ratio > 0.5:
            continue

        # Check for "Rally before Base" (Demand) or "Drop before Base" (Supply)
        prior_move = closes[i - 1] - closes[i - 3]
        post_move = closes[min(i + 3, n - 1)] - closes[i + 1] if i + 3 < n else 0

        # Demand Zone: prior rally + subsequent drop (or continuation)
        if prior_move > 0 and abs(post_move) > full_range * 0.5:
            imbalance = abs(post_move)
            zones.append(SDZone(
                zone_type=ZoneType.DEMAND,
                upper=highs[i],
                lower=lows[i],
                formation_time=time.monotonic(),
                imbalance_strength=imbalance,
            ))

        # Supply Zone: prior drop + subsequent rally
        elif prior_move < 0 and abs(post_move) > full_range * 0.5:
            imbalance = abs(post_move)
            zones.append(SDZone(
                zone_type=ZoneType.SUPPLY,
                upper=highs[i],
                lower=lows[i],
                formation_time=time.monotonic(),
                imbalance_strength=imbalance,
            ))

    return zones


def check_zone_entry(
    price: float,
    zones: List[SDZone],
    max_touches: int = 1,
) -> Optional[SDZone]:
    """
    Check if current price is in a fresh zone.
    Returns the best fresh zone if price is within it.
    """
    candidates = []
    for zone in zones:
        if zone.freshness != ZoneFreshness.FRESH:
            continue
        if zone.touches > max_touches:
            continue
        if zone.lower <= price <= zone.upper:
            candidates.append(zone)

    if not candidates:
        return None

    # Best: strongest imbalance
    return max(candidates, key=lambda z: z.imbalance_strength)


def update_zone_freshness(
    zones: List[SDZone],
    price: float,
    max_touches: int = 1,
) -> None:
    """Update zone freshness based on current price touching."""
    for zone in zones:
        if zone.freshness == ZoneFreshness.BROKEN:
            continue
        if zone.lower <= price <= zone.upper:
            zone.touches += 1
            if zone.touches > max_touches:
                zone.freshness = ZoneFreshness.TESTED
        # Price traded through the zone entirely
        if zone.zone_type == ZoneType.DEMAND and price < zone.lower:
            zone.freshness = ZoneFreshness.BROKEN
        if zone.zone_type == ZoneType.SUPPLY and price > zone.upper:
            zone.freshness = ZoneFreshness.BROKEN


# ---------------------------------------------------------------------------
# 3-C Cup Completion Cheat (Minervini/Teo)
# ---------------------------------------------------------------------------

def detect_three_c(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    params: Optional[SniperParams] = None,
) -> ThreeCResult:
    """
    Detect Cup Completion Cheat (3-C) pattern.

    Looks for:
    - Cup formation (3-4 weeks, rounded bottom)
    - Handle pause/pivot (short consolidation near cup rim)
    - Early entry at handle before full breakout
    """
    if params is None:
        params = SniperParams()

    n = len(closes)
    # Need 15-20 bars minimum for 3-4 week cup
    min_bars = params.cup_weeks_min * 5  # trading days per week
    max_bars = params.cup_weeks_max * 5

    if n < min_bars + 5:  # need room for handle
        return ThreeCResult()

    # 1. Find the left rim (highest high in first ~25% of period)
    scan_end = min(n, max_bars + 5)
    left_quarter = scan_end // 4
    left_rim_idx = max(range(left_quarter), key=lambda i: highs[i]) if left_quarter > 0 else 0
    left_rim = highs[left_rim_idx]

    # 2. Find the cup bottom (lowest low between left rim and ~75%)
    search_start = left_rim_idx + 1
    search_end = min(scan_end, int(scan_end * 0.85))
    if search_start >= search_end:
        return ThreeCResult()

    bottom_idx = min(range(search_start, search_end), key=lambda i: lows[i])
    cup_bottom = lows[bottom_idx]

    # 3. Cup depth check
    cup_depth = left_rim - cup_bottom
    if cup_depth <= 0 or cup_depth < left_rim * 0.05:
        return ThreeCResult()

    # 4. Right side recovery: price should be back near left rim level
    right_side = closes[bottom_idx:]
    if not right_side:
        return ThreeCResult()

    max_recovery = max(right_side)
    recovery_pct = (max_recovery - cup_bottom) / cup_depth if cup_depth > 0 else 0
    if recovery_pct < 0.65:  # not enough recovery
        return ThreeCResult()

    # 5. Handle detection: short consolidation near rim
    cup_width = bottom_idx - left_rim_idx
    if cup_width < min_bars:
        return ThreeCResult()

    # Last few bars should be a tight range (handle)
    handle_bars = min(5, len(closes) - bottom_idx)
    if handle_bars < 2:
        return ThreeCResult()

    recent_closes = closes[-handle_bars:]
    handle_range = max(recent_closes) - min(recent_closes)
    handle_pause = handle_range < cup_depth * 0.3

    # Pivot = top of handle
    pivot_price = max(highs[-handle_bars:])

    return ThreeCResult(
        detected=handle_pause and recovery_pct >= 0.65,
        cup_depth=cup_depth,
        cup_width_bars=cup_width,
        handle_pause=handle_pause,
        pivot_price=pivot_price,
    )


# ---------------------------------------------------------------------------
# Relative Strength Ranking (Minervini)
# ---------------------------------------------------------------------------

def compute_relative_strength(
    closes: List[float],
    benchmark_closes: List[float],
    lookback: int = 50,
) -> float:
    """
    Compute RS score (Minervini's 50-day Relative Strength).
    RS = (Stock % change / Benchmark % change) × 100

    Higher RS = stronger than benchmark.
    """
    if len(closes) < lookback + 1 or len(benchmark_closes) < lookback + 1:
        return 0.0

    stock_return = (closes[-1] - closes[-lookback]) / closes[-lookback] if closes[-lookback] != 0 else 0
    bench_return = (benchmark_closes[-1] - benchmark_closes[-lookback]) / benchmark_closes[-lookback] if benchmark_closes[-lookback] != 0 else 0

    if bench_return == 0:
        return 100.0 if stock_return > 0 else 0.0

    return (stock_return / bench_return) * 100


# ---------------------------------------------------------------------------
# Signal Ranking
# ---------------------------------------------------------------------------

def rank_signals(signals: List[SniperSignal]) -> List[SniperSignal]:
    """
    Rank signals using Carver's Subsystem Allocation + Minervini RS.

    Priority:
    1. RS score (higher = better)
    2. Pattern completeness (VCP with ants > VCP without)
    3. Zone imbalance strength (for S/D)
    """
    for sig in signals:
        score = sig.rs_score

        # VCP bonus for volume ants
        if sig.pattern == PatternType.VCP and sig.pattern_data.get("volume_ants"):
            score += 5

        # S/D bonus for fresh zone strength
        if sig.pattern == PatternType.SUPPLY_DEMAND:
            score += sig.pattern_data.get("imbalance_strength", 0) * 0.01

        sig.signal_score = score

    return sorted(signals, key=lambda s: s.signal_score, reverse=True)


# ---------------------------------------------------------------------------
# Sniper Agent
# ---------------------------------------------------------------------------

class SniperAgent:
    """
    Scans the Active Watchlist for directional entry signals.

    On each tick:
    1. Filters instruments by regime (BEARISH → only short setups)
    2. Runs pattern detectors (VCP, S/D, 3-C)
    3. Ranks signals by RS
    4. Submits top signals to Risk Agent
    """

    def __init__(
        self,
        config: TomicConfig,
        regime_state: AtomicRegimeState,
    ):
        self._config = config
        self._params: SniperParams = config.sniper
        self._universe: UniverseParams = config.universe
        self._regime_state = regime_state
        self._ohlcv_cache: Dict[str, Dict[str, list]] = {}  # instrument → {H,L,C,O,V}
        self._sd_zones: Dict[str, List[SDZone]] = {}
        self._benchmark_closes: List[float] = []
        self._signals: List[SniperSignal] = []
        self._scan_count: int = 0

    def feed_candle(
        self,
        instrument: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        """Feed OHLCV data for an instrument."""
        key = instrument.upper()
        if key not in self._ohlcv_cache:
            self._ohlcv_cache[key] = {"O": [], "H": [], "L": [], "C": [], "V": []}
        cache = self._ohlcv_cache[key]
        cache["O"].append(open_)
        cache["H"].append(high)
        cache["L"].append(low)
        cache["C"].append(close)
        cache["V"].append(volume)

        # Keep last 200 bars
        max_bars = 200
        for k in cache:
            if len(cache[k]) > max_bars:
                cache[k] = cache[k][-max_bars:]

    def feed_benchmark(self, close: float) -> None:
        """Feed benchmark (e.g., NIFTY) close for RS calculation."""
        self._benchmark_closes.append(close)
        if len(self._benchmark_closes) > 200:
            self._benchmark_closes = self._benchmark_closes[-200:]

    def scan(self) -> List[SniperSignal]:
        """
        Scan all instruments in cache for patterns.
        Returns ranked signals list.
        """
        regime = self._regime_state.read_snapshot()
        self._signals = []
        self._scan_count += 1

        for instrument, data in self._ohlcv_cache.items():
            highs = data["H"]
            lows = data["L"]
            closes = data["C"]
            opens = data["O"]
            volumes = data["V"]

            if len(closes) < 30:
                continue

            # RS score
            rs = compute_relative_strength(
                closes, self._benchmark_closes, self._params.rs_lookback_days,
            )

            # --- Pattern detection ---

            # 1. VCP
            vcp = detect_vcp(highs, lows, closes, volumes, self._params)
            if vcp.detected:
                direction = self._allowed_direction(regime, "BUY")
                if direction:
                    # Stop: below tightest contraction low
                    stop = min(lows[-10:]) if lows else 0
                    self._signals.append(SniperSignal(
                        instrument=instrument,
                        pattern=PatternType.VCP,
                        direction=direction,
                        entry_price=vcp.pivot_price,
                        stop_price=stop,
                        target_price=vcp.pivot_price * 1.10,  # 10% target
                        rs_score=rs,
                        pattern_data=vcp.to_dict(),
                    ))

            # 2. S/D Zones
            new_zones = detect_sd_zones(highs, lows, closes, opens, self._params)
            if instrument not in self._sd_zones:
                self._sd_zones[instrument] = []
            self._sd_zones[instrument].extend(new_zones)

            # Update freshness
            current_price = closes[-1]
            update_zone_freshness(
                self._sd_zones[instrument], current_price,
                self._params.sd_zone_max_touches,
            )

            # Check for zone entry
            zone = check_zone_entry(
                current_price, self._sd_zones[instrument],
                self._params.sd_zone_max_touches,
            )
            if zone:
                if zone.zone_type == ZoneType.DEMAND:
                    direction = self._allowed_direction(regime, "BUY")
                    if direction:
                        self._signals.append(SniperSignal(
                            instrument=instrument,
                            pattern=PatternType.SUPPLY_DEMAND,
                            direction="BUY",
                            entry_price=zone.midpoint,
                            stop_price=zone.lower * 0.995,
                            target_price=zone.upper * 1.05,
                            rs_score=rs,
                            pattern_data=zone.to_dict(),
                        ))
                elif zone.zone_type == ZoneType.SUPPLY:
                    direction = self._allowed_direction(regime, "SELL")
                    if direction:
                        self._signals.append(SniperSignal(
                            instrument=instrument,
                            pattern=PatternType.SUPPLY_DEMAND,
                            direction="SELL",
                            entry_price=zone.midpoint,
                            stop_price=zone.upper * 1.005,
                            target_price=zone.lower * 0.95,
                            rs_score=rs,
                            pattern_data=zone.to_dict(),
                        ))

            # 3. 3-C Cup
            three_c = detect_three_c(highs, lows, closes, self._params)
            if three_c.detected:
                direction = self._allowed_direction(regime, "BUY")
                if direction:
                    stop = min(lows[-three_c.cup_width_bars:]) if lows else 0
                    self._signals.append(SniperSignal(
                        instrument=instrument,
                        pattern=PatternType.THREE_C,
                        direction=direction,
                        entry_price=three_c.pivot_price,
                        stop_price=stop,
                        target_price=three_c.pivot_price * 1.15,  # 15% target
                        rs_score=rs,
                        pattern_data=three_c.to_dict(),
                    ))

        # Rank signals
        self._signals = rank_signals(self._signals)
        return self._signals

    def _allowed_direction(
        self,
        regime: RegimeSnapshot,
        desired: str,
    ) -> Optional[str]:
        """
        Check if the desired direction is allowed by the regime.
        Regime Agent = Master Filter (Elder's Triple Screen).

        BEARISH: block BUY directional signals, allow SELL/short setups.
        CONGESTION: block directional (Sniper), allow non-directional.
        BULLISH: allow BUY directional signals.
        """
        desired_u = str(desired or "").strip().upper()
        if regime.phase == RegimePhase.BEARISH:
            return desired_u if desired_u == "SELL" else None
        if regime.phase == RegimePhase.BULLISH:
            return desired_u if desired_u == "BUY" else None
        if regime.phase == RegimePhase.CONGESTION:
            # Congestion → Volatility Agent takes priority, not Sniper
            return None
        # BLOWOFF: do not allow fresh directional entries.
        return None

    @property
    def signals(self) -> List[SniperSignal]:
        return self._signals

    @property
    def scan_count(self) -> int:
        return self._scan_count
