"""
LegResolver — Translates abstract strategy leg specifications (delta targets)
into concrete NFO option symbols using option chain data and Greeks.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class LegSpec:
    """Abstract leg specification from a strategy."""
    option_type: str  # "CE" or "PE"
    delta_target: float  # e.g. 0.25
    action: str  # "BUY" or "SELL"
    lots: int = 1
    role: str = ""  # "short_put", "long_put", "short_call", "long_call"


@dataclass
class ResolvedLeg:
    """Concrete leg with real trading symbol and strike."""
    trading_symbol: str  # e.g. "NIFTY27MAR2522000CE"
    exchange: str  # "NFO"
    strike: float
    option_type: str  # "CE" or "PE"
    action: str  # "BUY" or "SELL"
    lots: int
    lot_size: int
    quantity: int  # lots * lot_size
    delta: float  # actual delta found
    premium: float  # LTP of the option
    role: str


@dataclass
class ResolvedStrategy:
    """Fully resolved multi-leg strategy ready for execution."""
    legs: List[ResolvedLeg]
    underlying: str
    expiry: str
    strategy_type: str
    net_credit: float = 0.0
    max_loss: float = 0.0
    resolution_time: str = ""


INSTRUMENT_CONFIG = {
    "NIFTY": {"lot_size": 25, "strike_step": 50, "exchange": "NFO", "expiry_weekday": 3},
    "BANKNIFTY": {"lot_size": 15, "strike_step": 100, "exchange": "NFO", "expiry_weekday": 2},
    "SENSEX": {"lot_size": 10, "strike_step": 100, "exchange": "BFO", "expiry_weekday": 4},
    "FINNIFTY": {"lot_size": 25, "strike_step": 50, "exchange": "NFO", "expiry_weekday": 1},
    "MIDCPNIFTY": {"lot_size": 50, "strike_step": 25, "exchange": "NFO", "expiry_weekday": 0},
}


class LegResolver:
    """Resolves abstract delta-based leg specs into real tradeable symbols."""

    def __init__(self, option_chain_fetcher=None, greeks_engine=None):
        self._fetch_chain = option_chain_fetcher
        self._greeks = greeks_engine

    def resolve_strategy(
        self,
        underlying: str,
        strategy_type: str,
        leg_specs: List[LegSpec],
        spot_price: float,
        expiry_date: Optional[str] = None,
    ) -> Optional[ResolvedStrategy]:
        """Resolve all legs of a strategy into concrete symbols.

        Args:
            underlying: e.g. "NIFTY"
            strategy_type: e.g. "IRON_CONDOR"
            leg_specs: List of abstract leg specifications
            spot_price: Current spot price of the underlying
            expiry_date: Target expiry date (YYYY-MM-DD), or None for nearest
        """
        config = INSTRUMENT_CONFIG.get(underlying)
        if not config:
            logger.warning("Unknown underlying: %s", underlying)
            return None

        if not expiry_date:
            expiry_date = self._find_nearest_expiry(underlying)

        resolved_legs = []
        for spec in leg_specs:
            leg = self._resolve_single_leg(
                underlying=underlying,
                spec=spec,
                spot_price=spot_price,
                expiry_date=expiry_date,
                config=config,
            )
            if leg is None:
                logger.warning("Failed to resolve leg: %s %s delta=%.2f", underlying, spec.option_type, spec.delta_target)
                return None
            resolved_legs.append(leg)

        net_credit = sum(
            leg.premium * leg.quantity * (1 if leg.action == "SELL" else -1)
            for leg in resolved_legs
        )

        return ResolvedStrategy(
            legs=resolved_legs,
            underlying=underlying,
            expiry=expiry_date,
            strategy_type=strategy_type,
            net_credit=net_credit,
            resolution_time=datetime.now().isoformat(),
        )

    def build_iron_condor_specs(
        self, short_delta: float = 0.25, wing_delta: float = 0.10, lots: int = 1
    ) -> List[LegSpec]:
        """Generate leg specs for an Iron Condor."""
        return [
            LegSpec(option_type="PE", delta_target=wing_delta, action="BUY", lots=lots, role="long_put"),
            LegSpec(option_type="PE", delta_target=short_delta, action="SELL", lots=lots, role="short_put"),
            LegSpec(option_type="CE", delta_target=short_delta, action="SELL", lots=lots, role="short_call"),
            LegSpec(option_type="CE", delta_target=wing_delta, action="BUY", lots=lots, role="long_call"),
        ]

    def build_bull_put_specs(
        self, short_delta: float = 0.25, wing_delta: float = 0.10, lots: int = 1
    ) -> List[LegSpec]:
        """Generate leg specs for a Bull Put Spread."""
        return [
            LegSpec(option_type="PE", delta_target=wing_delta, action="BUY", lots=lots, role="long_put"),
            LegSpec(option_type="PE", delta_target=short_delta, action="SELL", lots=lots, role="short_put"),
        ]

    def build_bear_call_specs(
        self, short_delta: float = 0.25, wing_delta: float = 0.10, lots: int = 1
    ) -> List[LegSpec]:
        """Generate leg specs for a Bear Call Spread."""
        return [
            LegSpec(option_type="CE", delta_target=short_delta, action="SELL", lots=lots, role="short_call"),
            LegSpec(option_type="CE", delta_target=wing_delta, action="BUY", lots=lots, role="long_call"),
        ]

    def build_gamma_capture_specs(self, lots: int = 1) -> List[LegSpec]:
        """Generate leg specs for Gamma Capture (buy ATM straddle)."""
        return [
            LegSpec(option_type="CE", delta_target=0.50, action="BUY", lots=lots, role="long_call"),
            LegSpec(option_type="PE", delta_target=0.50, action="BUY", lots=lots, role="long_put"),
        ]

    def _resolve_single_leg(
        self,
        underlying: str,
        spec: LegSpec,
        spot_price: float,
        expiry_date: str,
        config: dict,
    ) -> Optional[ResolvedLeg]:
        """Resolve a single leg by finding the strike closest to the target delta."""
        strike_step = config["strike_step"]
        lot_size = config["lot_size"]
        exchange = config["exchange"]

        # Estimate strike from delta using a simplified approach:
        # For puts: lower strikes have lower (absolute) delta
        # For calls: higher strikes have lower delta
        # delta ~0.50 is ATM, delta ~0.25 is ~1 std dev OTM

        atm_strike = round(spot_price / strike_step) * strike_step

        # Rough delta-to-distance mapping (simplified; real impl uses Greeks engine)
        # delta 0.50 -> ATM, 0.25 -> ~1 strike_step * 4-6 OTM, 0.10 -> ~8-12 OTM
        distance_factor = max(0, (0.50 - abs(spec.delta_target)) / 0.50)
        num_steps = int(distance_factor * 10)

        if spec.option_type == "CE":
            target_strike = atm_strike + (num_steps * strike_step)
        else:
            target_strike = atm_strike - (num_steps * strike_step)

        # Build the trading symbol
        try:
            exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            exp_dt = datetime.now()

        trading_symbol = self._build_symbol(underlying, exp_dt, target_strike, spec.option_type)

        return ResolvedLeg(
            trading_symbol=trading_symbol,
            exchange=exchange,
            strike=target_strike,
            option_type=spec.option_type,
            action=spec.action,
            lots=spec.lots,
            lot_size=lot_size,
            quantity=spec.lots * lot_size,
            delta=spec.delta_target,  # approximate; real impl uses Greeks
            premium=0.0,  # filled at execution time
            role=spec.role,
        )

    def _build_symbol(self, underlying: str, expiry: datetime, strike: float, option_type: str) -> str:
        """Build an OpenAlgo-format option symbol like NIFTY27MAR2522000CE."""
        day = expiry.strftime("%d")
        month = expiry.strftime("%b").upper()
        year = expiry.strftime("%y")
        strike_str = str(int(strike)) if strike == int(strike) else str(strike)
        return f"{underlying}{day}{month}{year}{strike_str}{option_type}"

    def _find_nearest_expiry(self, underlying: str) -> str:
        """Find the nearest weekly expiry for the underlying."""
        config = INSTRUMENT_CONFIG.get(underlying, {})
        target_weekday = config.get("expiry_weekday", 3)

        today = datetime.now()
        days_ahead = target_weekday - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        nearest = today + timedelta(days=days_ahead)
        return nearest.strftime("%Y-%m-%d")
