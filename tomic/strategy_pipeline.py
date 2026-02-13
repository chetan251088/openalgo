"""
TOMIC Strategy Pipeline — End-to-End Signal Construction & Execution
====================================================================
Wires the Regime Agent → Strategy Constructor → Risk Agent → Command Table
flow for defined-risk strategies (Bull Put Spread, Iron Condor).

This module:
  1. Constructs strategy legs from market data (delta, strikes, DTE)
  2. Applies regime filter (Elder's Triple Screen hierarchy)
  3. Passes through Risk Agent's 8-step sizing chain
  4. Produces ORDER_REQUEST for the durable command table

Phase 2 scope: Bull Put Spread + Iron Condor only.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from tomic.agents.regime_agent import AtomicRegimeState, RegimeSnapshot
from tomic.agents.risk_agent import RiskAgent, run_sizing_chain, SizingResult
from tomic.config import (
    RegimePhase,
    StrategyType,
    TomicConfig,
    VolatilityParams,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy Leg Definition
# ---------------------------------------------------------------------------

class LegType(str, Enum):
    BUY_PUT = "BUY_PUT"
    SELL_PUT = "SELL_PUT"
    BUY_CALL = "BUY_CALL"
    SELL_CALL = "SELL_CALL"


@dataclass
class StrategyLeg:
    """A single leg of a multi-leg option strategy."""
    leg_type: LegType
    strike: float
    expiry: str            # ISO date e.g. "2026-02-20"
    delta: float           # estimated delta at construction
    instrument_symbol: str = ""
    premium: float = 0.0
    direction: str = ""    # BUY / SELL

    def __post_init__(self):
        if not self.direction:
            self.direction = "BUY" if "BUY" in self.leg_type.value else "SELL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg_type": self.leg_type.value,
            "strike": self.strike,
            "expiry": self.expiry,
            "delta": self.delta,
            "instrument_symbol": self.instrument_symbol,
            "premium": self.premium,
            "direction": self.direction,
        }


# ---------------------------------------------------------------------------
# Strategy Templates
# ---------------------------------------------------------------------------

@dataclass
class StrategyTemplate:
    """Constructed multi-leg strategy ready for sizing."""
    strategy_type: StrategyType
    underlying: str
    legs: List[StrategyLeg]
    expiry: str
    dte: int
    expected_credit: float = 0.0    # net premium received (credit spreads)
    expected_debit: float = 0.0     # net premium paid (debit spreads)
    max_loss: float = 0.0           # max risk per lot
    max_profit: float = 0.0         # max gain per lot
    wing_width: float = 0.0         # strike distance between legs

    def to_signal_dict(self, instrument_vol: float = 0.25) -> Dict[str, Any]:
        """Convert to signal dict for Risk Agent."""
        return {
            "instrument": self.underlying,
            "strategy_type": self.strategy_type.value,
            "direction": "SELL" if self.expected_credit > 0 else "BUY",
            "entry_price": self.expected_credit or self.expected_debit,
            "stop_price": self.max_loss,
            "legs": [leg.to_dict() for leg in self.legs],
            "expected_credit": self.expected_credit,
            "expected_debit": self.expected_debit,
            "dte": self.dte,
            "instrument_vol": instrument_vol,
            "lot_size": _get_lot_size(self.underlying),
        }


# ---------------------------------------------------------------------------
# Strategy Constructors
# ---------------------------------------------------------------------------

def construct_bull_put_spread(
    underlying: str,
    spot_price: float,
    short_delta: float,
    short_strike: float,
    long_strike: float,
    expiry: str,
    dte: int,
    short_premium: float,
    long_premium: float,
) -> StrategyTemplate:
    """
    Construct a Bull Put Spread (credit spread).

    Sell higher-strike put (short_delta), Buy lower-strike put (long/hedge).
    Max profit = net credit received.
    Max loss = wing width - net credit.
    """
    net_credit = short_premium - long_premium
    wing_width = abs(short_strike - long_strike)
    max_loss = wing_width - net_credit

    legs = [
        StrategyLeg(
            leg_type=LegType.BUY_PUT,
            strike=long_strike,
            expiry=expiry,
            delta=-abs(short_delta) * 0.5,  # long put is further OTM
            premium=long_premium,
        ),
        StrategyLeg(
            leg_type=LegType.SELL_PUT,
            strike=short_strike,
            expiry=expiry,
            delta=-short_delta,
            premium=short_premium,
        ),
    ]

    return StrategyTemplate(
        strategy_type=StrategyType.BULL_PUT_SPREAD,
        underlying=underlying,
        legs=legs,
        expiry=expiry,
        dte=dte,
        expected_credit=net_credit,
        max_loss=max_loss,
        max_profit=net_credit,
        wing_width=wing_width,
    )


def construct_iron_condor(
    underlying: str,
    spot_price: float,
    put_short_strike: float,
    put_long_strike: float,
    call_short_strike: float,
    call_long_strike: float,
    expiry: str,
    dte: int,
    put_short_premium: float,
    put_long_premium: float,
    call_short_premium: float,
    call_long_premium: float,
    short_delta: float = 0.20,
) -> StrategyTemplate:
    """
    Construct an Iron Condor (credit spread on both sides).

    Put side: Sell OTM put + Buy further OTM put (wing).
    Call side: Sell OTM call + Buy further OTM call (wing).
    Max profit = total net credit.
    Max loss = widest wing - net credit.
    """
    put_credit = put_short_premium - put_long_premium
    call_credit = call_short_premium - call_long_premium
    net_credit = put_credit + call_credit

    put_wing_width = abs(put_short_strike - put_long_strike)
    call_wing_width = abs(call_long_strike - call_short_strike)
    max_wing = max(put_wing_width, call_wing_width)
    max_loss = max_wing - net_credit

    legs = [
        # Put side (protection first per HEDGE_FIRST policy)
        StrategyLeg(
            leg_type=LegType.BUY_PUT,
            strike=put_long_strike,
            expiry=expiry,
            delta=-short_delta * 0.3,
            premium=put_long_premium,
        ),
        StrategyLeg(
            leg_type=LegType.SELL_PUT,
            strike=put_short_strike,
            expiry=expiry,
            delta=-short_delta,
            premium=put_short_premium,
        ),
        # Call side (protection first)
        StrategyLeg(
            leg_type=LegType.BUY_CALL,
            strike=call_long_strike,
            expiry=expiry,
            delta=short_delta * 0.3,
            premium=call_long_premium,
        ),
        StrategyLeg(
            leg_type=LegType.SELL_CALL,
            strike=call_short_strike,
            expiry=expiry,
            delta=short_delta,
            premium=call_short_premium,
        ),
    ]

    return StrategyTemplate(
        strategy_type=StrategyType.IRON_CONDOR,
        underlying=underlying,
        legs=legs,
        expiry=expiry,
        dte=dte,
        expected_credit=net_credit,
        max_loss=max_loss,
        max_profit=net_credit,
        wing_width=max_wing,
    )


# ---------------------------------------------------------------------------
# Strike Selection Helpers
# ---------------------------------------------------------------------------

def select_otm_strike(
    spot_price: float,
    target_delta: float,
    strikes: List[float],
    option_type: str = "PUT",
) -> float:
    """
    Select the nearest OTM strike for a given target delta.
    Prefers the strike slightly further OTM (safer) if exact not available.

    Per doc: "round to the nearest OTM strike. Safer to be
    slightly further away (lower delta) than closer."
    """
    if not strikes:
        raise ValueError("No strikes available")

    if option_type == "PUT":
        # For puts, OTM means strike < spot
        otm_strikes = [s for s in strikes if s < spot_price]
        if not otm_strikes:
            return min(strikes)
        # Target distance from spot based on delta (rough approximation)
        # More OTM = further from spot = safer
        target_dist = spot_price * abs(target_delta)
        target_strike = spot_price - target_dist
        # Pick nearest that's AT or BELOW target (further OTM = safer)
        below_target = [s for s in otm_strikes if s <= target_strike]
        if below_target:
            return max(below_target)  # closest to target while still further OTM
        return min(otm_strikes, key=lambda s: abs(s - target_strike))
    else:
        # For calls, OTM means strike > spot
        otm_strikes = [s for s in strikes if s > spot_price]
        if not otm_strikes:
            return max(strikes)
        target_dist = spot_price * abs(target_delta)
        target_strike = spot_price + target_dist
        # Pick nearest that's AT or ABOVE target (further OTM = safer)
        above_target = [s for s in otm_strikes if s >= target_strike]
        if above_target:
            return min(above_target)
        return min(otm_strikes, key=lambda s: abs(s - target_strike))


def select_wing_strike(
    short_strike: float,
    wing_width: float,
    option_type: str = "PUT",
    strikes: Optional[List[float]] = None,
) -> float:
    """
    Select the wing (long/hedge) strike at the specified distance.
    PUT wing: short_strike - wing_width
    CALL wing: short_strike + wing_width
    """
    if option_type == "PUT":
        target = short_strike - wing_width
    else:
        target = short_strike + wing_width

    if strikes:
        return min(strikes, key=lambda s: abs(s - target))
    return target


def get_wing_width(underlying: str, params: Optional[VolatilityParams] = None) -> float:
    """
    Get wing width for the underlying.
    NIFTY: 200 points, BANKNIFTY: 500 points, others: 5% of spot.
    """
    if params is None:
        params = VolatilityParams()

    underlying_upper = underlying.upper()
    if underlying_upper == "NIFTY":
        return float(params.nifty_wing_width)
    elif underlying_upper == "BANKNIFTY":
        return float(params.banknifty_wing_width)
    else:
        return 0.0  # caller should compute from spot


def validate_dte(dte: int, strategy_type: StrategyType, params: Optional[VolatilityParams] = None) -> bool:
    """
    Validate DTE is within acceptable range for strategy type.
    Credit spreads: 30-45 DTE.
    Momentum/Gamma: 5-10 DTE.
    """
    if params is None:
        params = VolatilityParams()

    credit_strategies = {
        StrategyType.BULL_PUT_SPREAD,
        StrategyType.BEAR_CALL_SPREAD,
        StrategyType.IRON_CONDOR,
    }
    if strategy_type in credit_strategies:
        return params.income_dte_min <= dte <= params.income_dte_max
    else:
        return params.momentum_dte_min <= dte <= params.momentum_dte_max


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class StrategyPipeline:
    """
    End-to-end strategy pipeline orchestrator.

    Workflow:
    1. Receive market data (spot, option chain, VIX)
    2. Check regime state
    3. Construct strategy legs
    4. Submit to Risk Agent for sizing
    5. Risk Agent enqueues to command table if approved

    Phase 2: Bull Put Spread / Iron Condor only.
    """

    def __init__(
        self,
        config: TomicConfig,
        regime_state: AtomicRegimeState,
        risk_agent: RiskAgent,
    ):
        self._config = config
        self._regime_state = regime_state
        self._risk_agent = risk_agent
        self._pipeline_count: int = 0

    def evaluate_bull_put_spread(
        self,
        underlying: str,
        spot_price: float,
        short_strike: float,
        long_strike: float,
        expiry: str,
        dte: int,
        short_premium: float,
        long_premium: float,
        short_delta: float = 0.20,
        instrument_vol: float = 0.25,
    ) -> Optional[StrategyTemplate]:
        """
        Evaluate a Bull Put Spread opportunity.
        Returns the constructed strategy if it passes regime checks, else None.
        """
        # 1. DTE validation
        if not validate_dte(dte, StrategyType.BULL_PUT_SPREAD, self._config.volatility):
            logger.info("Bull Put Spread rejected: DTE=%d outside 30-45 range", dte)
            return None

        # 2. Regime check
        regime = self._regime_state.read_snapshot()
        if regime.phase == RegimePhase.BEARISH:
            logger.info("Bull Put Spread blocked: regime is BEARISH")
            return None
        if "HALT_SHORT_VEGA" in regime.vix_flags:
            logger.info("Bull Put Spread blocked: HALT_SHORT_VEGA flag")
            return None

        # 3. Construct
        strategy = construct_bull_put_spread(
            underlying=underlying,
            spot_price=spot_price,
            short_delta=short_delta,
            short_strike=short_strike,
            long_strike=long_strike,
            expiry=expiry,
            dte=dte,
            short_premium=short_premium,
            long_premium=long_premium,
        )

        # 4. Validate credit received
        if strategy.expected_credit <= 0:
            logger.info("Bull Put Spread rejected: no net credit (%.2f)", strategy.expected_credit)
            return None

        # 5. Submit to Risk Agent
        signal = strategy.to_signal_dict(instrument_vol)
        self._risk_agent.enqueue_signal(signal)
        self._pipeline_count += 1

        logger.info(
            "Bull Put Spread submitted: %s %s/%s credit=%.2f max_loss=%.2f DTE=%d",
            underlying, short_strike, long_strike,
            strategy.expected_credit, strategy.max_loss, dte,
        )
        return strategy

    def evaluate_iron_condor(
        self,
        underlying: str,
        spot_price: float,
        put_short_strike: float,
        put_long_strike: float,
        call_short_strike: float,
        call_long_strike: float,
        expiry: str,
        dte: int,
        put_short_premium: float,
        put_long_premium: float,
        call_short_premium: float,
        call_long_premium: float,
        short_delta: float = 0.20,
        instrument_vol: float = 0.25,
    ) -> Optional[StrategyTemplate]:
        """
        Evaluate an Iron Condor opportunity.
        Returns the constructed strategy if it passes checks, else None.
        """
        # 1. DTE validation
        if not validate_dte(dte, StrategyType.IRON_CONDOR, self._config.volatility):
            logger.info("Iron Condor rejected: DTE=%d outside 30-45 range", dte)
            return None

        # 2. Regime check — Iron Condors excel in congestion
        regime = self._regime_state.read_snapshot()
        if "HALT_SHORT_VEGA" in regime.vix_flags:
            logger.info("Iron Condor blocked: HALT_SHORT_VEGA flag")
            return None

        # 3. Construct
        strategy = construct_iron_condor(
            underlying=underlying,
            spot_price=spot_price,
            put_short_strike=put_short_strike,
            put_long_strike=put_long_strike,
            call_short_strike=call_short_strike,
            call_long_strike=call_long_strike,
            expiry=expiry,
            dte=dte,
            put_short_premium=put_short_premium,
            put_long_premium=put_long_premium,
            call_short_premium=call_short_premium,
            call_long_premium=call_long_premium,
            short_delta=short_delta,
        )

        # 4. Validate credit
        if strategy.expected_credit <= 0:
            logger.info("Iron Condor rejected: no net credit (%.2f)", strategy.expected_credit)
            return None

        # 5. Submit to Risk Agent
        signal = strategy.to_signal_dict(instrument_vol)
        self._risk_agent.enqueue_signal(signal)
        self._pipeline_count += 1

        logger.info(
            "Iron Condor submitted: %s P:%s/%s C:%s/%s credit=%.2f max_loss=%.2f DTE=%d",
            underlying,
            put_short_strike, put_long_strike,
            call_short_strike, call_long_strike,
            strategy.expected_credit, strategy.max_loss, dte,
        )
        return strategy

    @property
    def pipeline_count(self) -> int:
        return self._pipeline_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_lot_size(underlying: str) -> int:
    """Standard lot sizes for Indian index/equity options."""
    lot_sizes = {
        "NIFTY": 50,
        "BANKNIFTY": 15,
        "FINNIFTY": 25,
    }
    return lot_sizes.get(underlying.upper(), 50)
