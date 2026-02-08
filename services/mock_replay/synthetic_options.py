"""
Synthetic CE/PE pricing from index (spot) using Black-Scholes.

Used when Historify has no 1m data for the option symbol: we replay index
and compute option LTP from spot for testing after hours.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from math import log, sqrt, exp
from typing import Any, Optional, Tuple

# Risk-free rate (annual), volatility (annual) - rough defaults for India
DEFAULT_R = 0.07
DEFAULT_SIGMA = 0.18


def _norm_cdf(x: float) -> float:
    """Approximate standard normal CDF (Abramowitz & Stegun)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t = 1.0 / (1.0 + p * abs(x))
    y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * exp(-x * x / 2)
    return y if x >= 0 else 1.0 - y


def black_scholes(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    option_type: str,
    r: float = DEFAULT_R,
    sigma: float = DEFAULT_SIGMA,
) -> float:
    """
    Black-Scholes option price (no dividend).

    spot: current underlying price
    strike: strike price
    time_to_expiry_years: T (fraction of year, e.g. 7/365)
    option_type: "CE" or "PE"
    r: risk-free rate (annual)
    sigma: volatility (annual)
    Returns: option price (premium).
    """
    if time_to_expiry_years <= 0:
        # Expired: intrinsic only
        if option_type.upper() == "CE":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    if sigma <= 0:
        sigma = DEFAULT_SIGMA
    sqrt_t = sqrt(time_to_expiry_years)
    d1 = (log(spot / strike) + (r + 0.5 * sigma * sigma) * time_to_expiry_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if option_type.upper() == "CE":
        return spot * _norm_cdf(d1) - strike * exp(-r * time_to_expiry_years) * _norm_cdf(d2)
    return strike * exp(-r * time_to_expiry_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def parse_option_symbol(symbol: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Parse NFO option symbol to (underlying, expiry_ddmonyy, strike, option_type).

    Examples: NIFTY28OCT2523500CE -> ("NIFTY", "28OCT25", 23500.0, "CE")
              NIFTY24JAN24000CE   -> ("NIFTY", "24JAN24", 24000.0, "CE")
    Returns None if not parseable.
    """
    s = (symbol or "").strip().upper()
    # Pattern: BASE + DDMMMYY + STRIKE (int or float) + CE|PE
    # STRIKE can be 23500 or 292.5
    m = re.match(r"^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$", s)
    if not m:
        return None
    base, exp_str, strike_str, opt_type = m.groups()
    try:
        strike = float(strike_str)
    except ValueError:
        return None
    return (base, exp_str, strike, opt_type)


def expiry_ddmonyy_to_timestamp(expiry_ddmonyy: str) -> Optional[int]:
    """Convert DDMMMYY (e.g. 28OCT25) to epoch seconds at 15:30 IST (expiry day)."""
    try:
        d = datetime.strptime(expiry_ddmonyy.upper(), "%d%b%y")
        # 15:30 IST = 10:00 UTC
        d = d.replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        return int(d.timestamp())
    except ValueError:
        return None


def time_to_expiry_seconds(ts_epoch_sec: int, expiry_ts_epoch_sec: int) -> float:
    """Seconds until expiry from ts_epoch_sec. If past expiry, return 0."""
    diff = expiry_ts_epoch_sec - ts_epoch_sec
    return max(0.0, diff)


def synthetic_option_price(
    spot: float,
    symbol: str,
    ts_epoch_sec: int,
    r: float = DEFAULT_R,
    sigma: float = DEFAULT_SIGMA,
) -> Optional[float]:
    """
    Compute synthetic option price for symbol at given spot and time.

    symbol: e.g. NIFTY28OCT2523500CE
    ts_epoch_sec: current replay timestamp (epoch seconds)
    Returns premium or None if symbol not parseable.
    """
    parsed = parse_option_symbol(symbol)
    if not parsed:
        return None
    underlying, exp_str, strike, option_type = parsed
    exp_ts = expiry_ddmonyy_to_timestamp(exp_str)
    if exp_ts is None:
        return None
    t_sec = time_to_expiry_seconds(ts_epoch_sec, exp_ts)
    t_years = t_sec / (365.25 * 24 * 3600)
    return round(
        black_scholes(spot, strike, t_years, option_type, r=r, sigma=sigma),
        2,
    )
