"""
TOMIC Greeks Engine — Options Pricing & Greeks Calculator
==========================================================
Uses py_vollib_vectorized for fast vectorized calculations.
Black-Scholes for European (NIFTY/BANKNIFTY options).
API fallback when library unavailable or data stale.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Try to import py_vollib_vectorized; fall back gracefully
try:
    from py_vollib_vectorized import vectorized_implied_volatility as vec_iv
    from py_vollib_vectorized import vectorized_greeks as vec_greeks
    HAS_VOLLIB = True
except ImportError:
    HAS_VOLLIB = False
    logger.warning("py_vollib_vectorized not available — using API fallback for Greeks")


# ---------------------------------------------------------------------------
# Greeks result
# ---------------------------------------------------------------------------

@dataclass
class GreeksResult:
    """Computed Greeks for a single option."""
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    computed_at_mono: float = 0.0   # monotonic timestamp
    source: str = "vollib"          # vollib | api_fallback | stale


# ---------------------------------------------------------------------------
# Greeks Engine
# ---------------------------------------------------------------------------

class GreeksEngine:
    """
    Compute implied volatility and Greeks for options positions.

    Usage:
        engine = GreeksEngine(risk_free_rate=0.065)

        result = engine.compute(
            spot=22000, strike=21800, expiry_days=30,
            option_price=250, option_type='p',
        )
        print(result.delta, result.iv)

        # Batch computation
        results = engine.compute_batch(options_data)
    """

    def __init__(
        self,
        risk_free_rate: float = 0.065,     # RBI repo rate as proxy
        dividend_yield: float = 0.012,      # ~1.2% NIFTY dividend yield
    ):
        self._r = risk_free_rate
        self._q = dividend_yield

    def compute(
        self,
        spot: float,
        strike: float,
        expiry_days: float,
        option_price: float,
        option_type: str = "c",   # 'c' or 'p'
    ) -> GreeksResult:
        """
        Compute IV and Greeks for a single option.
        Falls back to simplified model if py_vollib unavailable.
        """
        t = max(expiry_days / 365.0, 1e-6)  # time to expiry in years

        if HAS_VOLLIB:
            return self._compute_vollib(spot, strike, t, option_price, option_type)
        else:
            return self._compute_simplified(spot, strike, t, option_price, option_type)

    def compute_batch(
        self,
        options: List[Dict],
    ) -> List[GreeksResult]:
        """
        Batch compute Greeks for multiple options.

        Each dict requires: spot, strike, expiry_days, option_price, option_type
        """
        if HAS_VOLLIB and len(options) > 1:
            return self._compute_batch_vollib(options)
        else:
            return [
                self.compute(
                    spot=o["spot"],
                    strike=o["strike"],
                    expiry_days=o["expiry_days"],
                    option_price=o["option_price"],
                    option_type=o.get("option_type", "c"),
                )
                for o in options
            ]

    # -----------------------------------------------------------------------
    # py_vollib_vectorized
    # -----------------------------------------------------------------------

    def _compute_vollib(
        self, spot: float, strike: float, t: float,
        price: float, opt_type: str,
    ) -> GreeksResult:
        try:
            flag = opt_type.lower()  # 'c' or 'p'

            # Vectorized IV (single-element arrays)
            iv_arr = vec_iv(
                price=np.array([price]),
                S=np.array([spot]),
                K=np.array([strike]),
                t=np.array([t]),
                r=self._r,
                flag=np.array([flag]),
                q=self._q,
                model="black_scholes",
                return_as="numpy",
            )
            iv = float(iv_arr[0]) if not np.isnan(iv_arr[0]) else 0.0

            if iv <= 0:
                return GreeksResult(
                    iv=0.0, source="vollib_iv_failed",
                    computed_at_mono=time.monotonic(),
                )

            # Vectorized Greeks
            greeks = vec_greeks(
                flag=np.array([flag]),
                S=np.array([spot]),
                K=np.array([strike]),
                t=np.array([t]),
                r=self._r,
                sigma=np.array([iv]),
                q=self._q,
                model="black_scholes",
                return_as="dict",
            )

            return GreeksResult(
                iv=iv,
                delta=float(greeks["delta"][0]),
                gamma=float(greeks["gamma"][0]),
                theta=float(greeks["theta"][0]),
                vega=float(greeks["vega"][0]),
                rho=float(greeks.get("rho", [0.0])[0]),
                computed_at_mono=time.monotonic(),
                source="vollib",
            )
        except Exception as e:
            logger.warning("Vollib computation failed: %s. Using simplified.", e)
            return self._compute_simplified(spot, strike, t, price, opt_type)

    def _compute_batch_vollib(self, options: List[Dict]) -> List[GreeksResult]:
        """Batch vectorized computation."""
        n = len(options)
        try:
            spots = np.array([o["spot"] for o in options])
            strikes = np.array([o["strike"] for o in options])
            times = np.array([max(o["expiry_days"] / 365.0, 1e-6) for o in options])
            prices = np.array([o["option_price"] for o in options])
            flags = np.array([o.get("option_type", "c").lower() for o in options])

            ivs = vec_iv(
                price=prices, S=spots, K=strikes, t=times,
                r=self._r, flag=flags, q=self._q,
                model="black_scholes", return_as="numpy",
            )
            ivs = np.nan_to_num(ivs, nan=0.0)

            greeks = vec_greeks(
                flag=flags, S=spots, K=strikes, t=times,
                r=self._r, sigma=ivs, q=self._q,
                model="black_scholes", return_as="dict",
            )

            mono = time.monotonic()
            results = []
            for i in range(n):
                results.append(GreeksResult(
                    iv=float(ivs[i]),
                    delta=float(greeks["delta"][i]),
                    gamma=float(greeks["gamma"][i]),
                    theta=float(greeks["theta"][i]),
                    vega=float(greeks["vega"][i]),
                    rho=float(greeks.get("rho", np.zeros(n))[i]),
                    computed_at_mono=mono,
                    source="vollib_batch",
                ))
            return results

        except Exception as e:
            logger.warning("Batch vollib failed: %s. Falling back to individual.", e)
            return [
                self.compute(
                    o["spot"], o["strike"], o["expiry_days"],
                    o["option_price"], o.get("option_type", "c"),
                )
                for o in options
            ]

    # -----------------------------------------------------------------------
    # Simplified BS (fallback when py_vollib unavailable)
    # -----------------------------------------------------------------------

    def _compute_simplified(
        self, spot: float, strike: float, t: float,
        price: float, opt_type: str,
    ) -> GreeksResult:
        """
        Simplified Black-Scholes Greeks computation (no external deps).
        Used as fallback only.
        """
        from math import log, sqrt, exp, pi
        from statistics import NormalDist

        norm = NormalDist()

        try:
            # Newton-Raphson IV estimation
            iv = self._newton_iv(spot, strike, t, price, opt_type, norm)
            if iv <= 0:
                return GreeksResult(iv=0.0, source="simplified_iv_failed",
                                    computed_at_mono=time.monotonic())

            # d1, d2
            d1 = (log(spot / strike) + (self._r - self._q + 0.5 * iv**2) * t) / (iv * sqrt(t))
            d2 = d1 - iv * sqrt(t)

            # Greeks
            nd1 = norm.cdf(d1)
            nd2 = norm.cdf(d2)
            nd1_neg = norm.cdf(-d1)
            nd2_neg = norm.cdf(-d2)
            npd1 = (1.0 / sqrt(2 * pi)) * exp(-0.5 * d1**2)

            eq = exp(-self._q * t)
            er = exp(-self._r * t)

            if opt_type.lower() == "c":
                delta = eq * nd1
                theta = (
                    -(spot * npd1 * iv * eq) / (2 * sqrt(t))
                    + self._q * spot * nd1 * eq
                    - self._r * strike * er * nd2
                ) / 365.0
            else:
                delta = eq * (nd1 - 1)
                theta = (
                    -(spot * npd1 * iv * eq) / (2 * sqrt(t))
                    - self._q * spot * nd1_neg * eq
                    + self._r * strike * er * nd2_neg
                ) / 365.0

            gamma = (npd1 * eq) / (spot * iv * sqrt(t))
            vega = spot * eq * npd1 * sqrt(t) / 100.0

            return GreeksResult(
                iv=iv, delta=delta, gamma=gamma, theta=theta, vega=vega,
                computed_at_mono=time.monotonic(), source="simplified_bs",
            )

        except Exception as e:
            logger.error("Simplified BS failed: %s", e)
            return GreeksResult(source="error", computed_at_mono=time.monotonic())

    def _newton_iv(
        self, spot: float, strike: float, t: float,
        market_price: float, opt_type: str,
        norm,
        max_iters: int = 50, tol: float = 1e-6,
    ) -> float:
        """Newton-Raphson IV solver."""
        from math import log, sqrt, exp, pi

        sigma = 0.3  # initial guess
        for _ in range(max_iters):
            d1 = (log(spot / strike) + (self._r - self._q + 0.5 * sigma**2) * t) / (sigma * sqrt(t))
            d2 = d1 - sigma * sqrt(t)

            eq = exp(-self._q * t)
            er = exp(-self._r * t)

            if opt_type.lower() == "c":
                bs_price = spot * eq * norm.cdf(d1) - strike * er * norm.cdf(d2)
            else:
                bs_price = strike * er * norm.cdf(-d2) - spot * eq * norm.cdf(-d1)

            npd1 = (1.0 / sqrt(2 * pi)) * exp(-0.5 * d1**2)
            vega = spot * eq * npd1 * sqrt(t)

            if vega < 1e-10:
                break

            sigma = sigma - (bs_price - market_price) / vega
            if abs(bs_price - market_price) < tol:
                return max(sigma, 0.01)

        return max(sigma, 0.0) if sigma > 0 else 0.0
