"""
TOMIC Leg Resolver — Delta-Based Strike Resolution
===================================================
Translates abstract leg specs (short_delta=0.25, option_type=CE) into
real NFO option symbols and strikes using the Greeks Engine.

Used by ExecutionAgent to resolve multi-leg strategies before order placement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tomic.greeks_engine import GreeksEngine

logger = logging.getLogger(__name__)


@dataclass
class LegSpec:
    """Abstract leg specification from strategy signals."""
    leg_type: str           # e.g. "SELL_PUT", "BUY_CALL"
    option_type: str        # "CE" or "PE"
    direction: str          # "BUY" or "SELL"
    offset: str             # "ATM", "OTM1", "OTM2" (fallback if no delta)
    delta_target: float = 0.0
    expiry_offset: int = 0  # 0 = front, 1 = next expiry


@dataclass
class LegResolution:
    """A resolved leg with real strike and symbol info."""
    leg_type: str
    option_type: str
    direction: str
    strike: float
    symbol: str = ""        # e.g. "NIFTY25JAN25000CE" (filled by ExecutionAgent)
    actual_delta: float = 0.0
    actual_iv: float = 0.0
    estimated_price: float = 0.0


class LegResolver:
    """
    Resolves abstract leg specs into strikes using delta targeting.

    Requires:
    - Available strikes for the underlying/expiry (from option chain service)
    - Current option prices (LTP) for each strike
    - Underlying spot price
    - Days to expiry (DTE)
    """

    def __init__(self, greeks_engine: Optional[GreeksEngine] = None):
        self._greeks_engine = greeks_engine or GreeksEngine()

    def find_strike_by_delta(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        option_type: str,        # "c" or "p"
        target_delta: float,     # absolute value (0.25 = 25Δ)
    ) -> Optional[float]:
        """
        Find the strike whose delta is closest to target_delta.
        Skips strikes with no price (illiquid).
        Returns the strike float or None if not resolvable.
        """
        best_strike = None
        best_diff = float("inf")
        opt = option_type.lower()[0]  # 'c' or 'p'

        valid_strikes = [s for s in strikes if prices.get(s, 0.0) > 0.0]
        if not valid_strikes:
            return None

        for strike in valid_strikes:
            price = prices[strike]
            try:
                result = self._greeks_engine.compute(
                    spot=spot,
                    strike=strike,
                    expiry_days=max(dte, 0.1),
                    option_price=price,
                    option_type=opt,
                )
                actual_delta = abs(result.delta)
                diff = abs(actual_delta - target_delta)
                if diff < best_diff:
                    best_diff = diff
                    best_strike = strike
            except Exception as exc:
                logger.debug("Delta computation failed for strike %.0f: %s", strike, exc)
                continue

        return best_strike

    def resolve_iron_condor(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        short_delta: float,
        wing_delta: float,
    ) -> List[LegResolution]:
        """
        Resolve 4-leg Iron Condor:
          BUY  PE (wing)  → wing_delta
          SELL PE (short) → short_delta
          BUY  CE (wing)  → wing_delta
          SELL CE (short) → short_delta
        """
        legs = []

        # Put side
        short_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", short_delta)
        wing_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", wing_delta)
        if short_put and wing_put and wing_put < short_put:
            legs.append(LegResolution(
                leg_type="BUY_PUT", option_type="PE", direction="BUY",
                strike=wing_put, estimated_price=prices.get(wing_put, 0.0),
            ))
            legs.append(LegResolution(
                leg_type="SELL_PUT", option_type="PE", direction="SELL",
                strike=short_put, estimated_price=prices.get(short_put, 0.0),
            ))

        # Call side
        short_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", short_delta)
        wing_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", wing_delta)
        if short_call and wing_call and wing_call > short_call:
            legs.append(LegResolution(
                leg_type="BUY_CALL", option_type="CE", direction="BUY",
                strike=wing_call, estimated_price=prices.get(wing_call, 0.0),
            ))
            legs.append(LegResolution(
                leg_type="SELL_CALL", option_type="CE", direction="SELL",
                strike=short_call, estimated_price=prices.get(short_call, 0.0),
            ))

        return legs

    def resolve_bull_put_spread(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        short_delta: float,
        wing_delta: float,
    ) -> List[LegResolution]:
        """
        Resolve 2-leg Bull Put Spread (sell OTM put, buy further OTM put).
        Hedge (BUY) first per LEGGING_POLICY.
        """
        short_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", short_delta)
        wing_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", wing_delta)

        if not short_put or not wing_put or wing_put >= short_put:
            logger.warning(
                "Bull Put Spread: could not resolve strikes (short=%.0f, wing=%.0f)",
                short_put or 0, wing_put or 0,
            )
            return []

        return [
            LegResolution(
                leg_type="BUY_PUT", option_type="PE", direction="BUY",
                strike=wing_put, estimated_price=prices.get(wing_put, 0.0),
            ),
            LegResolution(
                leg_type="SELL_PUT", option_type="PE", direction="SELL",
                strike=short_put, estimated_price=prices.get(short_put, 0.0),
            ),
        ]

    def resolve_bear_call_spread(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        short_delta: float,
        wing_delta: float,
    ) -> List[LegResolution]:
        """
        Resolve 2-leg Bear Call Spread (sell OTM call, buy further OTM call).
        Hedge (BUY) first per LEGGING_POLICY.
        """
        short_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", short_delta)
        wing_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", wing_delta)

        if not short_call or not wing_call or wing_call <= short_call:
            logger.warning(
                "Bear Call Spread: could not resolve strikes (short=%.0f, wing=%.0f)",
                short_call or 0, wing_call or 0,
            )
            return []

        return [
            LegResolution(
                leg_type="BUY_CALL", option_type="CE", direction="BUY",
                strike=wing_call, estimated_price=prices.get(wing_call, 0.0),
            ),
            LegResolution(
                leg_type="SELL_CALL", option_type="CE", direction="SELL",
                strike=short_call, estimated_price=prices.get(short_call, 0.0),
            ),
        ]

    def resolve_gamma_capture(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        max_price: float = 10.0,
    ) -> List[LegResolution]:
        """
        Expiry day gamma capture: buy cheap near-ATM CE + PE.
        Selects 1-strike OTM on each side with price < max_price.
        """
        # Find ATM strike
        if not strikes:
            return []
        atm = min(strikes, key=lambda s: abs(s - spot))
        atm_idx = strikes.index(atm)

        # 1 strike OTM call
        ce_idx = min(atm_idx + 1, len(strikes) - 1)
        pe_idx = max(atm_idx - 1, 0)

        legs = []
        ce_strike = strikes[ce_idx]
        pe_strike = strikes[pe_idx]

        ce_price = prices.get(ce_strike, 0.0)
        pe_price = prices.get(pe_strike, 0.0)

        if 0 < ce_price <= max_price:
            legs.append(LegResolution(
                leg_type="BUY_CALL", option_type="CE", direction="BUY",
                strike=ce_strike, estimated_price=ce_price,
            ))
        if 0 < pe_price <= max_price:
            legs.append(LegResolution(
                leg_type="BUY_PUT", option_type="PE", direction="BUY",
                strike=pe_strike, estimated_price=pe_price,
            ))

        return legs
