"""
TOMIC Volatility Agent — Options Pricing & Strategy Selection
=============================================================
The "Options Pricer" agent that scans option chains to find
mispriced insurance. Determines the optimal options strategy
based on volatility regime:

  1. IV vs HV analysis — credit or debit bias
  2. IV Rank — percentile within 52-week range
  3. Skew analysis — Put/Call IV skew → Risk Reversals
  4. Term structure — inversion detection → Calendars/Diagonals
  5. Strategy matrix — maps vol state → optimal strategy

Sources: Natenberg, Passarelli, Chen/Sebastian
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from tomic.agents.regime_agent import AtomicRegimeState, RegimeSnapshot
from tomic.config import (
    RegimePhase,
    StrategyType,
    TomicConfig,
    VolatilityParams,
    VIXRules,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Volatility State Enums
# ---------------------------------------------------------------------------

class VolRegime(str, Enum):
    IV_HIGH = "IV_HIGH"        # IV > HV (sell premium)
    IV_LOW = "IV_LOW"          # IV < HV (buy options)
    IV_NORMAL = "IV_NORMAL"    # IV ≈ HV


class SkewState(str, Enum):
    NORMAL = "NORMAL"
    STEEP_PUT = "STEEP_PUT"    # Put IV >> Call IV (fear)
    STEEP_CALL = "STEEP_CALL"  # Call IV >> Put IV (complacency)


class TermStructure(str, Enum):
    NORMAL = "NORMAL"          # front < back (contango)
    INVERTED = "INVERTED"      # front > back (backwardation)
    FLAT = "FLAT"


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class VolSnapshot:
    """Volatility state snapshot for an underlying."""
    underlying: str
    iv: float = 0.0
    hv: float = 0.0
    iv_rank: float = 0.0
    iv_hv_ratio: float = 0.0
    vol_regime: VolRegime = VolRegime.IV_NORMAL
    put_iv_25d: float = 0.0
    call_iv_25d: float = 0.0
    skew_ratio: float = 0.0
    skew_state: SkewState = SkewState.NORMAL
    front_iv: float = 0.0     # near-month IV
    back_iv: float = 0.0      # far-month IV
    term_structure: TermStructure = TermStructure.NORMAL
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "underlying": self.underlying,
            "iv": self.iv,
            "hv": self.hv,
            "iv_rank": self.iv_rank,
            "iv_hv_ratio": self.iv_hv_ratio,
            "vol_regime": self.vol_regime.value,
            "put_iv_25d": self.put_iv_25d,
            "call_iv_25d": self.call_iv_25d,
            "skew_ratio": self.skew_ratio,
            "skew_state": self.skew_state.value,
            "front_iv": self.front_iv,
            "back_iv": self.back_iv,
            "term_structure": self.term_structure.value,
        }


@dataclass
class VolSignal:
    """A scored signal from the Volatility Agent."""
    underlying: str
    strategy_type: StrategyType
    direction: str              # BUY or SELL
    vol_snapshot: VolSnapshot = field(default_factory=lambda: VolSnapshot(""))
    signal_strength: float = 0.0
    reason: str = ""
    legs: List[Dict[str, Any]] = field(default_factory=list)
    dte: int = 0
    timestamp: float = field(default_factory=time.monotonic)

    def to_signal_dict(self) -> Dict[str, Any]:
        return {
            "instrument": self.underlying,
            "strategy_type": self.strategy_type.value,
            "direction": self.direction,
            "vol_snapshot": self.vol_snapshot.to_dict(),
            "signal_strength": self.signal_strength,
            "reason": self.reason,
            "legs": list(self.legs),
            "dte": int(self.dte or 0),
        }


def _signal_strength_with_vix_bias(
    base_strength: float,
    regime: RegimeSnapshot,
    vix_rules: VIXRules,
) -> float:
    strength = float(base_strength)
    vix = float(regime.vix or 0.0)
    if vix_rules.sweet_spot_low <= vix <= vix_rules.sweet_spot_high:
        strength += 8.0
    if vix > vix_rules.half_size_above:
        strength -= 5.0
    if vix < vix_rules.stop_selling_below:
        strength -= 10.0
    return max(0.0, strength)


def _build_credit_spread_legs(strategy_type: StrategyType) -> List[Dict[str, Any]]:
    if strategy_type == StrategyType.BULL_PUT_SPREAD:
        return [
            {"leg_type": "BUY_PUT", "offset": "OTM2", "option_type": "PE", "direction": "BUY"},
            {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL"},
        ]
    if strategy_type == StrategyType.BEAR_CALL_SPREAD:
        return [
            {"leg_type": "BUY_CALL", "offset": "OTM2", "option_type": "CE", "direction": "BUY"},
            {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL"},
        ]
    if strategy_type == StrategyType.IRON_CONDOR:
        return [
            {"leg_type": "BUY_PUT", "offset": "OTM2", "option_type": "PE", "direction": "BUY"},
            {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL"},
            {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL"},
            {"leg_type": "BUY_CALL", "offset": "OTM2", "option_type": "CE", "direction": "BUY"},
        ]
    return []


def _build_collared_risk_reversal_legs() -> List[Dict[str, Any]]:
    # Collared version keeps downside defined in high-vol pockets.
    return [
        {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL"},
        {"leg_type": "BUY_CALL", "offset": "OTM1", "option_type": "CE", "direction": "BUY"},
        {"leg_type": "BUY_PUT", "offset": "OTM3", "option_type": "PE", "direction": "BUY"},
    ]


def _build_jade_lizard_legs() -> List[Dict[str, Any]]:
    # Slightly bullish / neutral structure:
    # short put + short call spread where call-side max loss is capped.
    return [
        {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL"},
        {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL"},
        {"leg_type": "BUY_CALL", "offset": "OTM3", "option_type": "CE", "direction": "BUY"},
    ]


def _build_short_strangle_legs() -> List[Dict[str, Any]]:
    return [
        {"leg_type": "SELL_PUT", "offset": "OTM1", "option_type": "PE", "direction": "SELL"},
        {"leg_type": "SELL_CALL", "offset": "OTM1", "option_type": "CE", "direction": "SELL"},
    ]


def _build_short_straddle_legs() -> List[Dict[str, Any]]:
    return [
        {"leg_type": "SELL_PUT", "offset": "ATM", "option_type": "PE", "direction": "SELL"},
        {"leg_type": "SELL_CALL", "offset": "ATM", "option_type": "CE", "direction": "SELL"},
    ]


def _build_calendar_legs(option_type: str) -> List[Dict[str, Any]]:
    option_u = "PE" if str(option_type or "").strip().upper() == "PE" else "CE"
    return [
        {"leg_type": f"SELL_{option_u}", "offset": "ATM", "option_type": option_u, "direction": "SELL", "expiry_offset": 0},
        {"leg_type": f"BUY_{option_u}", "offset": "ATM", "option_type": option_u, "direction": "BUY", "expiry_offset": 1},
    ]


# ---------------------------------------------------------------------------
# Core Computations
# ---------------------------------------------------------------------------

def compute_iv_rank(
    current_iv: float,
    iv_52w_high: float,
    iv_52w_low: float,
) -> float:
    """
    IV Rank = (Current IV - 52wk Low) / (52wk High - 52wk Low) × 100

    Per doc: calculated at underlying level.
    """
    if iv_52w_high <= iv_52w_low:
        return 0.0
    rank = ((current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low)) * 100
    return max(0.0, min(100.0, rank))


def compute_hv(
    closes: List[float],
    lookback: int = 30,
    annualize: bool = True,
) -> float:
    """
    Historical Volatility (close-to-close).
    HV = std(log returns) × √252

    Uses 30-day lookback by default (Natenberg standard).
    """
    if len(closes) < lookback + 1:
        return 0.0

    recent = closes[-(lookback + 1):]
    log_returns = []
    for i in range(1, len(recent)):
        if recent[i - 1] > 0 and recent[i] > 0:
            log_returns.append(math.log(recent[i] / recent[i - 1]))

    if len(log_returns) < 5:
        return 0.0

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(variance)

    if annualize:
        return daily_vol * math.sqrt(252)
    return daily_vol


def classify_vol_regime(
    iv: float,
    hv: float,
    params: Optional[VolatilityParams] = None,
) -> Tuple[VolRegime, float]:
    """
    Classify IV vs HV relationship.
    Returns (regime, ratio).
    """
    if params is None:
        params = VolatilityParams()

    if hv <= 0:
        return VolRegime.IV_NORMAL, 0.0

    ratio = iv / hv
    if ratio >= params.iv_hv_ratio_trigger:
        return VolRegime.IV_HIGH, ratio
    elif ratio < 1.0 / params.iv_hv_ratio_trigger:
        return VolRegime.IV_LOW, ratio
    else:
        return VolRegime.IV_NORMAL, ratio


def classify_skew(
    put_iv_25d: float,
    call_iv_25d: float,
    params: Optional[VolatilityParams] = None,
) -> Tuple[SkewState, float]:
    """
    Classify the put/call skew.
    Steep put skew (> 1.5× call IV) → Risk Reversal opportunity.
    """
    if params is None:
        params = VolatilityParams()

    if call_iv_25d <= 0:
        return SkewState.NORMAL, 0.0

    ratio = put_iv_25d / call_iv_25d
    if ratio >= params.skew_put_call_ratio:
        return SkewState.STEEP_PUT, ratio
    elif ratio <= 1.0 / params.skew_put_call_ratio:
        return SkewState.STEEP_CALL, ratio
    else:
        return SkewState.NORMAL, ratio


def classify_term_structure(
    front_iv: float,
    back_iv: float,
) -> TermStructure:
    """
    Classify term structure.
    Normal: front < back (time premium increases with duration).
    Inverted: front > back (near-term fear/event).
    """
    if front_iv <= 0 or back_iv <= 0:
        return TermStructure.FLAT

    ratio = front_iv / back_iv
    if ratio > 1.05:
        return TermStructure.INVERTED
    elif ratio < 0.95:
        return TermStructure.NORMAL
    else:
        return TermStructure.FLAT


# ---------------------------------------------------------------------------
# Strategy Selection Matrix
# ---------------------------------------------------------------------------

def select_strategy(
    vol_snap: VolSnapshot,
    regime: RegimeSnapshot,
    params: Optional[VolatilityParams] = None,
    vix_rules: Optional[VIXRules] = None,
    feature_flags: Optional[Dict[str, Any]] = None,
) -> List[VolSignal]:
    """
    Map the volatility state + market regime to optimal strategies.

    Decision matrix:
    ┌──────────────┬──────────────┬──────────────┬──────────────┐
    │ Vol Regime    │ BULLISH      │ CONGESTION   │ BEARISH      │
    ├──────────────┼──────────────┼──────────────┼──────────────┤
    │ IV_HIGH      │ Bull Put     │ Iron Condor  │ Bear Call    │
    │ IV_NORMAL    │ DITM Call    │ Iron Condor  │ —            │
    │ IV_LOW       │ DITM Call    │ Calendar     │ —            │
    ├──────────────┼──────────────┼──────────────┼──────────────┤
    │ STEEP_PUT    │ Risk Rev.    │ Risk Rev.    │ —            │
    │ INVERTED     │ Calendar     │ Calendar     │ Calendar     │
    └──────────────┴──────────────┴──────────────┴──────────────┘
    """
    if params is None:
        params = VolatilityParams()
    if vix_rules is None:
        vix_rules = VIXRules()
    feature_flags = feature_flags or {}

    signals: List[VolSignal] = []

    vix_flags = set(str(flag).strip().upper() for flag in regime.vix_flags)
    premiums_too_low = "PREMIUMS_TOO_LOW" in vix_flags or regime.vix < vix_rules.stop_selling_below
    halt_short_vega = "HALT_SHORT_VEGA" in vix_flags
    defined_risk_only = "DEFINED_RISK_ONLY" in vix_flags or regime.vix > vix_rules.defined_risk_only_above

    credit_allowed = not premiums_too_low and not halt_short_vega and regime.phase != RegimePhase.BLOWOFF
    enable_jade_lizard = bool(feature_flags.get("enable_jade_lizard", True))
    enable_short_strangle = bool(feature_flags.get("enable_short_strangle", True))
    enable_short_straddle = bool(feature_flags.get("enable_short_straddle", True))
    allow_naked_premium = bool(feature_flags.get("allow_naked_premium", True))
    naked_iv_rank_min = float(
        feature_flags.get("naked_iv_rank_min", max(float(params.iv_rank_credit_threshold) + 10.0, 60.0))
    )
    naked_iv_hv_min = float(
        feature_flags.get("naked_iv_hv_min", max(float(params.iv_hv_ratio_trigger) + 0.1, 1.35))
    )

    underlying = vol_snap.underlying

    # --- IV vs HV strategies ---
    if vol_snap.vol_regime == VolRegime.IV_HIGH:
        if vol_snap.iv_rank >= params.iv_rank_credit_threshold and credit_allowed:
            # High IV Rank → sell premium
            if regime.phase == RegimePhase.BULLISH:
                strategy = StrategyType.BULL_PUT_SPREAD
                signals.append(VolSignal(
                    underlying=underlying,
                    strategy_type=strategy,
                    direction="SELL",
                    vol_snapshot=vol_snap,
                    signal_strength=_signal_strength_with_vix_bias(vol_snap.iv_rank, regime, vix_rules),
                    reason=f"IV_HIGH (rank={vol_snap.iv_rank:.0f}%), BULLISH → Bull Put Spread",
                    legs=_build_credit_spread_legs(strategy),
                    dte=params.income_dte_min,
                ))
            elif regime.phase == RegimePhase.CONGESTION:
                strategy = StrategyType.IRON_CONDOR
                signals.append(VolSignal(
                    underlying=underlying,
                    strategy_type=strategy,
                    direction="SELL",
                    vol_snapshot=vol_snap,
                    signal_strength=_signal_strength_with_vix_bias(vol_snap.iv_rank + 4.0, regime, vix_rules),
                    reason=f"IV_HIGH (rank={vol_snap.iv_rank:.0f}%), CONGESTION → Iron Condor",
                    legs=_build_credit_spread_legs(strategy),
                    dte=params.income_dte_min,
                ))
            elif regime.phase == RegimePhase.BEARISH:
                strategy = StrategyType.BEAR_CALL_SPREAD
                signals.append(VolSignal(
                    underlying=underlying,
                    strategy_type=strategy,
                    direction="SELL",
                    vol_snapshot=vol_snap,
                    signal_strength=_signal_strength_with_vix_bias(vol_snap.iv_rank, regime, vix_rules),
                    reason=f"IV_HIGH (rank={vol_snap.iv_rank:.0f}%), BEARISH → Bear Call Spread",
                    legs=_build_credit_spread_legs(strategy),
                    dte=params.income_dte_min,
                ))
        elif halt_short_vega:
            logger.debug("VolAgent: HALT_SHORT_VEGA active, suppressing IV_HIGH credit signals")

    elif vol_snap.vol_regime == VolRegime.IV_LOW:
        if vol_snap.iv_rank < params.iv_rank_debit_threshold:
            # Low IV regime: avoid naked premium selling; prefer calendars.
            if regime.phase in (RegimePhase.CONGESTION, RegimePhase.BEARISH):
                option_type = "PE" if regime.phase == RegimePhase.BEARISH else "CE"
                signals.append(VolSignal(
                    underlying=underlying,
                    strategy_type=StrategyType.CALENDAR_DIAGONAL,
                    direction="SELL",
                    vol_snapshot=vol_snap,
                    signal_strength=max(0.0, 100 - vol_snap.iv_rank),
                    reason=f"IV_LOW (rank={vol_snap.iv_rank:.0f}%), {regime.phase.value} → Calendar Spread",
                    legs=_build_calendar_legs(option_type),
                    dte=params.momentum_dte_max,
                ))
            if regime.phase == RegimePhase.BULLISH:
                signals.append(VolSignal(
                    underlying=underlying,
                    strategy_type=StrategyType.DITM_CALL,
                    direction="BUY",
                    vol_snapshot=vol_snap,
                    signal_strength=100 - vol_snap.iv_rank,  # lower rank = cheaper
                    reason=f"IV_LOW (rank={vol_snap.iv_rank:.0f}%), BULLISH → DITM Call",
                    dte=params.momentum_dte_max,
                ))
            elif regime.phase == RegimePhase.BEARISH:
                signals.append(VolSignal(
                    underlying=underlying,
                    strategy_type=StrategyType.DITM_PUT,
                    direction="BUY",
                    vol_snapshot=vol_snap,
                    signal_strength=100 - vol_snap.iv_rank,
                    reason=f"IV_LOW (rank={vol_snap.iv_rank:.0f}%), BEARISH → DITM Put",
                    dte=params.momentum_dte_max,
                ))

    # --- Skew strategies ---
    if vol_snap.skew_state == SkewState.STEEP_PUT and credit_allowed:
        if regime.phase in (RegimePhase.BULLISH, RegimePhase.CONGESTION) and not defined_risk_only:
            signals.append(VolSignal(
                underlying=underlying,
                strategy_type=StrategyType.RISK_REVERSAL,
                direction="SELL",  # sell expensive put, buy cheap call
                vol_snapshot=vol_snap,
                signal_strength=vol_snap.skew_ratio * 30,
                reason=f"STEEP_PUT skew ({vol_snap.skew_ratio:.2f}×) → Risk Reversal",
                legs=_build_collared_risk_reversal_legs(),
                dte=params.momentum_dte_max,
            ))

    # --- Jade Lizard (slightly bullish / neutral; put-rich skew) ---
    if (
        enable_jade_lizard
        and allow_naked_premium
        and credit_allowed
        and not defined_risk_only
        and regime.phase in (RegimePhase.BULLISH, RegimePhase.CONGESTION)
        and vol_snap.iv_rank >= params.iv_rank_credit_threshold
        and vol_snap.skew_state == SkewState.STEEP_PUT
    ):
        signals.append(
            VolSignal(
                underlying=underlying,
                strategy_type=StrategyType.JADE_LIZARD,
                direction="SELL",
                vol_snapshot=vol_snap,
                signal_strength=_signal_strength_with_vix_bias(
                    max(vol_snap.iv_rank, vol_snap.skew_ratio * 35.0),
                    regime,
                    vix_rules,
                ),
                reason=(
                    f"Steep put skew + elevated IV ({vol_snap.iv_rank:.0f}%) "
                    "→ Jade Lizard (bullish/neutral carry)"
                ),
                legs=_build_jade_lizard_legs(),
                dte=params.income_dte_min,
            )
        )

    # --- Advanced neutral carry: Short Strangle / Straddle ---
    stable_short_vol_env = (
        regime.phase == RegimePhase.CONGESTION
        and credit_allowed
        and not defined_risk_only
        and allow_naked_premium
        and vol_snap.iv_rank >= naked_iv_rank_min
        and vol_snap.iv_hv_ratio >= naked_iv_hv_min
        and (vix_rules.sweet_spot_low <= regime.vix <= min(vix_rules.sweet_spot_high, vix_rules.defined_risk_only_above))
    )
    if stable_short_vol_env and enable_short_strangle:
        signals.append(
            VolSignal(
                underlying=underlying,
                strategy_type=StrategyType.SHORT_STRANGLE,
                direction="SELL",
                vol_snapshot=vol_snap,
                signal_strength=_signal_strength_with_vix_bias(vol_snap.iv_rank + 3.0, regime, vix_rules),
                reason=(
                    f"Range + IV>HV ({vol_snap.iv_hv_ratio:.2f}) + IV rank {vol_snap.iv_rank:.0f}% "
                    "→ Short Strangle"
                ),
                legs=_build_short_strangle_legs(),
                dte=params.momentum_dte_max,
            )
        )
    if stable_short_vol_env and enable_short_straddle and vol_snap.iv_rank >= (naked_iv_rank_min + 10.0):
        signals.append(
            VolSignal(
                underlying=underlying,
                strategy_type=StrategyType.SHORT_STRADDLE,
                direction="SELL",
                vol_snapshot=vol_snap,
                signal_strength=_signal_strength_with_vix_bias(vol_snap.iv_rank, regime, vix_rules),
                reason=(
                    f"Tight range + very elevated IV rank {vol_snap.iv_rank:.0f}% "
                    "→ Short Straddle (advanced)"
                ),
                legs=_build_short_straddle_legs(),
                dte=params.momentum_dte_max,
            )
        )

    # --- Term structure strategies ---
    if vol_snap.term_structure == TermStructure.INVERTED:
        option_type = "PE" if regime.phase == RegimePhase.BEARISH else "CE"
        signals.append(VolSignal(
            underlying=underlying,
            strategy_type=StrategyType.CALENDAR_DIAGONAL,
            direction="SELL",  # sell expensive front, buy cheap back
            vol_snapshot=vol_snap,
            signal_strength=50,
            reason=f"IV INVERTED term structure → Calendar Spread",
            legs=_build_calendar_legs(option_type),
            dte=params.momentum_dte_max,
        ))

    # Deduplicate same strategy per underlying, keep highest strength.
    dedup: Dict[str, VolSignal] = {}
    for sig in signals:
        key = f"{sig.underlying}:{sig.strategy_type.value}:{sig.direction}"
        prev = dedup.get(key)
        if prev is None or sig.signal_strength > prev.signal_strength:
            dedup[key] = sig

    return list(dedup.values())


# ---------------------------------------------------------------------------
# Volatility Agent
# ---------------------------------------------------------------------------

class VolatilityAgent:
    """
    Scans option chains to find volatility edge.

    Workflow per tick:
    1. Compute HV from price data
    2. Ingest IV data (from option chain / analytics API)
    3. Classify vol regime, skew, term structure
    4. Map to optimal strategy via selection matrix
    5. Output signals for Risk Agent

    Phase 3 scope: computation engine + strategy matrix.
    Live chain ingestion deferred to execution integration.
    """

    def __init__(
        self,
        config: TomicConfig,
        regime_state: AtomicRegimeState,
    ):
        self._config = config
        self._params: VolatilityParams = config.volatility
        self._vix_rules: VIXRules = config.vix
        self._regime_state = regime_state
        self._price_cache: Dict[str, List[float]] = {}  # underlying → closes
        self._iv_cache: Dict[str, Dict[str, float]] = {}
        self._snapshots: Dict[str, VolSnapshot] = {}
        self._signals: List[VolSignal] = []
        self._scan_count: int = 0
        self._feature_flags: Dict[str, Any] = {
            "enable_jade_lizard": str(os.getenv("TOMIC_ENABLE_JADE_LIZARD", "true")).strip().lower() in {"1", "true", "yes", "on"},
            "enable_short_strangle": str(os.getenv("TOMIC_ENABLE_SHORT_STRANGLE", "true")).strip().lower() in {"1", "true", "yes", "on"},
            "enable_short_straddle": str(os.getenv("TOMIC_ENABLE_SHORT_STRADDLE", "false")).strip().lower() in {"1", "true", "yes", "on"},
            "allow_naked_premium": str(os.getenv("TOMIC_ALLOW_NAKED_PREMIUM", "true")).strip().lower() in {"1", "true", "yes", "on"},
            "naked_iv_rank_min": float(os.getenv("TOMIC_SHORT_PREMIUM_IV_RANK_MIN", "65") or 65),
            "naked_iv_hv_min": float(os.getenv("TOMIC_SHORT_PREMIUM_IV_HV_MIN", "1.35") or 1.35),
        }

    def feed_price(self, underlying: str, close: float) -> None:
        """Feed daily close for HV calculation."""
        key = underlying.upper()
        if key not in self._price_cache:
            self._price_cache[key] = []
        self._price_cache[key].append(close)
        if len(self._price_cache[key]) > 260:
            self._price_cache[key] = self._price_cache[key][-260:]

    def feed_iv_data(
        self,
        underlying: str,
        current_iv: float,
        iv_52w_high: float,
        iv_52w_low: float,
        put_iv_25d: float = 0.0,
        call_iv_25d: float = 0.0,
        front_iv: float = 0.0,
        back_iv: float = 0.0,
    ) -> None:
        """Feed IV data for an underlying (from option chain / analytics)."""
        key = underlying.upper()
        self._iv_cache[key] = {
            "current_iv": current_iv,
            "iv_52w_high": iv_52w_high,
            "iv_52w_low": iv_52w_low,
            "put_iv_25d": put_iv_25d,
            "call_iv_25d": call_iv_25d,
            "front_iv": front_iv,
            "back_iv": back_iv,
        }

    def scan(self) -> List[VolSignal]:
        """
        Analyze all underlyings with both price and IV data.
        Returns list of strategy signals.
        """
        regime = self._regime_state.read_snapshot()
        self._signals = []
        self._scan_count += 1

        for underlying, iv_data in self._iv_cache.items():
            closes = self._price_cache.get(underlying, [])

            # Compute HV
            hv = compute_hv(closes) if len(closes) > 30 else 0.0

            # IV Rank
            iv_rank = compute_iv_rank(
                iv_data["current_iv"],
                iv_data["iv_52w_high"],
                iv_data["iv_52w_low"],
            )

            # Vol regime
            vol_regime, iv_hv_ratio = classify_vol_regime(
                iv_data["current_iv"], hv, self._params,
            )

            # Skew
            skew_state, skew_ratio = classify_skew(
                iv_data.get("put_iv_25d", 0),
                iv_data.get("call_iv_25d", 0),
                self._params,
            )

            # Term structure
            term = classify_term_structure(
                iv_data.get("front_iv", 0),
                iv_data.get("back_iv", 0),
            )

            # Build snapshot
            snap = VolSnapshot(
                underlying=underlying,
                iv=iv_data["current_iv"],
                hv=hv,
                iv_rank=iv_rank,
                iv_hv_ratio=iv_hv_ratio,
                vol_regime=vol_regime,
                put_iv_25d=iv_data.get("put_iv_25d", 0),
                call_iv_25d=iv_data.get("call_iv_25d", 0),
                skew_ratio=skew_ratio,
                skew_state=skew_state,
                front_iv=iv_data.get("front_iv", 0),
                back_iv=iv_data.get("back_iv", 0),
                term_structure=term,
            )
            self._snapshots[underlying] = snap

            # Select strategies
            new_signals = select_strategy(
                snap,
                regime,
                self._params,
                self._vix_rules,
                feature_flags=self._feature_flags,
            )
            self._signals.extend(new_signals)

        return self._signals

    def get_snapshot(self, underlying: str) -> Optional[VolSnapshot]:
        return self._snapshots.get(underlying.upper())

    @property
    def signals(self) -> List[VolSignal]:
        return self._signals

    @property
    def scan_count(self) -> int:
        return self._scan_count
