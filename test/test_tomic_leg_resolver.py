"""Tests for LegResolver — delta-based strike selection."""
import pytest
from unittest.mock import MagicMock, patch
from tomic.leg_resolver import LegResolver, LegSpec, LegResolution


def make_resolver():
    engine = MagicMock()
    # engine.compute returns a result with .delta
    return LegResolver(greeks_engine=engine)


def test_find_strike_by_delta_call():
    """Should find the strike closest to target delta for a call."""
    resolver = make_resolver()
    # Strikes: 24800, 24900, 25000 (ATM), 25100, 25200
    strikes = [24800.0, 24900.0, 25000.0, 25100.0, 25200.0]
    spot = 25000.0
    dte = 7

    # Mock greeks: delta decreases as strike goes OTM for calls
    delta_map = {
        24800.0: 0.70,
        24900.0: 0.55,
        25000.0: 0.50,
        25100.0: 0.30,  # closest to 0.25
        25200.0: 0.18,
    }
    prices = {k: 100.0 for k in strikes}

    resolver._greeks_engine.compute.side_effect = lambda spot, strike, expiry_days, option_price, option_type: (
        MagicMock(delta=delta_map[strike], iv=0.15)
    )

    result = resolver.find_strike_by_delta(
        strikes=strikes,
        prices=prices,
        spot=spot,
        dte=dte,
        option_type="c",
        target_delta=0.25,
    )
    assert result == 25100.0


def test_find_strike_by_delta_put():
    """Put delta is negative; target is abs value."""
    resolver = make_resolver()
    strikes = [24800.0, 24900.0, 25000.0, 25100.0, 25200.0]
    spot = 25000.0

    delta_map = {
        24800.0: -0.35,
        24900.0: -0.28,  # closest to -0.25
        25000.0: -0.50,
        25100.0: -0.18,
        25200.0: -0.10,
    }
    prices = {k: 50.0 for k in strikes}

    resolver._greeks_engine.compute.side_effect = lambda spot, strike, expiry_days, option_price, option_type: (
        MagicMock(delta=delta_map[strike], iv=0.15)
    )

    result = resolver.find_strike_by_delta(
        strikes=strikes,
        prices=prices,
        spot=spot,
        dte=7,
        option_type="p",
        target_delta=0.25,  # abs value
    )
    assert result == 24900.0


def test_resolve_iron_condor_legs():
    """Should return 4 resolved legs for an Iron Condor."""
    resolver = make_resolver()
    strikes = [24700.0, 24800.0, 24900.0, 25000.0, 25100.0, 25200.0, 25300.0]
    prices = {k: 80.0 for k in strikes}
    spot = 25000.0

    # Delta mock: put side
    def mock_compute(spot, strike, expiry_days, option_price, option_type):
        if option_type == "c":
            deltas = {24700: 0.80, 24800: 0.65, 24900: 0.55,
                      25000: 0.50, 25100: 0.30, 25200: 0.20, 25300: 0.12}
        else:
            deltas = {24700: -0.12, 24800: -0.20, 24900: -0.30,
                      25000: -0.50, 25100: -0.55, 25200: -0.65, 25300: -0.80}
        return MagicMock(delta=deltas.get(int(strike), 0.0), iv=0.15)

    resolver._greeks_engine.compute.side_effect = mock_compute

    legs = resolver.resolve_iron_condor(
        strikes=strikes, prices=prices, spot=spot, dte=7,
        short_delta=0.25, wing_delta=0.10,
    )
    assert len(legs) == 4
    directions = [l.direction for l in legs]
    assert "BUY" in directions
    assert "SELL" in directions
    option_types = {l.option_type for l in legs}
    assert "CE" in option_types
    assert "PE" in option_types


def test_no_option_price_skips_strike():
    """Strike with price=0 should be skipped in delta search."""
    resolver = make_resolver()
    strikes = [25000.0, 25100.0, 25200.0]
    prices = {25000.0: 0.0, 25100.0: 0.0, 25200.0: 50.0}

    resolver._greeks_engine.compute.return_value = MagicMock(delta=0.25, iv=0.15)

    result = resolver.find_strike_by_delta(
        strikes=strikes, prices=prices, spot=25000.0, dte=7,
        option_type="c", target_delta=0.25,
    )
    # Only 25200 has a price, so it's selected
    assert result == 25200.0
