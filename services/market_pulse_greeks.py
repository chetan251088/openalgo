"""
Market Pulse — Options Greeks Dashboard Service (Phase 3).

Thin orchestrator that reuses existing OpenAlgo services:
  - gex_service.py  → GEX profile
  - option_greeks_service.py → IV, delta, gamma, theta, vega
  - option_chain_service.py  → chain data
  - expiry_service.py  → nearest expiry auto-detect

Provides: GEX profile, ATM IV percentile, put-call skew,
          dealer gamma positioning.
"""

import logging
import os
import time
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── Cache ───────────────────────────────────────────────────────
_greeks_cache: dict[str, dict[str, Any]] = {}
_greeks_cache_ts: dict[str, float] = {}
_GREEKS_CACHE_TTL = max(15, int(os.getenv("MARKET_PULSE_GREEKS_CACHE_TTL", "60")))


def _get_api_key() -> str | None:
    return os.getenv("APP_KEY") or os.getenv("OPENALGO_API_KEY")


def _get_nearest_weekly_expiry(underlying: str, exchange: str = "NFO") -> str | None:
    """Auto-detect nearest weekly/monthly expiry."""
    try:
        from services.expiry_service import get_expiry_dates
        success, resp, _ = get_expiry_dates(underlying, exchange, "options", api_key=_get_api_key())
        if success and resp.get("status") == "success":
            expiries = resp.get("data", [])
            if expiries:
                return str(expiries[0]).replace("-", "").upper()  # Normalize to DDMMMYY
    except Exception as e:
        logger.warning("Failed to get expiry for %s: %s", underlying, e)
    return None


def fetch_gex_profile(
    underlying: str = "NIFTY",
    exchange: str = "NFO",
    expiry_date: str | None = None,
) -> dict[str, Any] | None:
    """Fetch GEX profile using existing gex_service."""
    cache_key = f"gex:{underlying}"
    now = time.time()
    if cache_key in _greeks_cache and (now - _greeks_cache_ts.get(cache_key, 0)) < _GREEKS_CACHE_TTL:
        return _greeks_cache[cache_key]

    try:
        from services.gex_service import get_gex_data

        if not expiry_date:
            expiry_date = _get_nearest_weekly_expiry(underlying, exchange)
        if not expiry_date:
            logger.warning("No expiry found for GEX computation: %s", underlying)
            return None

        api_key = _get_api_key()
        if not api_key:
            return None

        success, data, _ = get_gex_data(underlying, exchange, expiry_date, api_key)
        if not success:
            logger.warning("GEX fetch failed for %s: %s", underlying, data.get("message"))
            return None

        # Compute summary
        chain = data.get("chain", [])
        total_net_gex = data.get("total_net_gex", 0)

        # Dealer gamma positioning
        gamma_positioning = "neutral"
        if total_net_gex > 0:
            gamma_positioning = "long_gamma"  # Mean-reverting market
        elif total_net_gex < 0:
            gamma_positioning = "short_gamma"  # Trending market

        # Find GEX flip point (where net_gex changes sign)
        flip_strike = None
        for i in range(1, len(chain)):
            if chain[i - 1]["net_gex"] * chain[i]["net_gex"] < 0:
                flip_strike = chain[i]["strike"]
                break

        # Top 3 call/put GEX strikes
        sorted_by_ce = sorted(chain, key=lambda x: x.get("ce_gex", 0), reverse=True)
        sorted_by_pe = sorted(chain, key=lambda x: x.get("pe_gex", 0), reverse=True)

        result = {
            "underlying": underlying,
            "expiry_date": expiry_date,
            "spot_price": data.get("spot_price"),
            "futures_price": data.get("futures_price"),
            "atm_strike": data.get("atm_strike"),
            "pcr_oi": data.get("pcr_oi"),
            "total_ce_gex": data.get("total_ce_gex"),
            "total_pe_gex": data.get("total_pe_gex"),
            "total_net_gex": total_net_gex,
            "gamma_positioning": gamma_positioning,
            "gamma_label": "Mean-Reverting" if gamma_positioning == "long_gamma" else (
                "Trending" if gamma_positioning == "short_gamma" else "Neutral"
            ),
            "flip_strike": flip_strike,
            "top_call_gex_strikes": [
                {"strike": s["strike"], "gex": s["ce_gex"]}
                for s in sorted_by_ce[:3]
            ],
            "top_put_gex_strikes": [
                {"strike": s["strike"], "gex": s["pe_gex"]}
                for s in sorted_by_pe[:3]
            ],
            "chain_summary": [
                {
                    "strike": item["strike"],
                    "ce_oi": item["ce_oi"],
                    "pe_oi": item["pe_oi"],
                    "net_gex": item["net_gex"],
                }
                for item in chain
            ],
        }

        _greeks_cache[cache_key] = result
        _greeks_cache_ts[cache_key] = now
        return result

    except ImportError:
        logger.warning("gex_service not available")
        return None
    except Exception as e:
        logger.exception("Error fetching GEX profile for %s: %s", underlying, e)
        return None


def fetch_atm_iv_data(
    underlying: str = "NIFTY",
    exchange: str = "NFO",
    expiry_date: str | None = None,
) -> dict[str, Any] | None:
    """Fetch ATM IV and compute IV percentile using existing services."""
    cache_key = f"iv:{underlying}"
    now = time.time()
    if cache_key in _greeks_cache and (now - _greeks_cache_ts.get(cache_key, 0)) < _GREEKS_CACHE_TTL:
        return _greeks_cache[cache_key]

    try:
        from services.option_chain_service import get_option_chain

        if not expiry_date:
            expiry_date = _get_nearest_weekly_expiry(underlying, exchange)
        if not expiry_date:
            return None

        api_key = _get_api_key()
        if not api_key:
            return None

        success, chain_data, _ = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=10,
            api_key=api_key,
        )
        if not success:
            return None

        chain = chain_data.get("chain", [])
        atm_strike = chain_data.get("atm_strike")
        spot_price = chain_data.get("underlying_ltp")

        if not chain or not spot_price:
            return None

        # Find ATM CE and PE
        atm_ce_iv = None
        atm_pe_iv = None
        skew_25d_call_iv = None
        skew_25d_put_iv = None

        from services.option_greeks_service import calculate_greeks

        for item in chain:
            strike = item["strike"]
            ce = item.get("ce", {})
            pe = item.get("pe", {})

            if strike == atm_strike:
                # ATM CE IV
                if ce and ce.get("symbol") and ce.get("ltp", 0) > 0:
                    try:
                        ok, resp, _ = calculate_greeks(
                            ce["symbol"], exchange, spot_price, ce["ltp"]
                        )
                        if ok:
                            atm_ce_iv = resp.get("implied_volatility")
                    except Exception:
                        pass

                # ATM PE IV
                if pe and pe.get("symbol") and pe.get("ltp", 0) > 0:
                    try:
                        ok, resp, _ = calculate_greeks(
                            pe["symbol"], exchange, spot_price, pe["ltp"]
                        )
                        if ok:
                            atm_pe_iv = resp.get("implied_volatility")
                    except Exception:
                        pass

        # Compute ATM IV (average of CE/PE)
        atm_iv = None
        if atm_ce_iv is not None and atm_pe_iv is not None:
            atm_iv = round((atm_ce_iv + atm_pe_iv) / 2, 2)
        elif atm_ce_iv is not None:
            atm_iv = round(atm_ce_iv, 2)
        elif atm_pe_iv is not None:
            atm_iv = round(atm_pe_iv, 2)

        # Put-Call skew (ATM put IV - ATM call IV)
        pc_skew = None
        if atm_ce_iv is not None and atm_pe_iv is not None:
            pc_skew = round(atm_pe_iv - atm_ce_iv, 2)

        result = {
            "underlying": underlying,
            "expiry_date": expiry_date,
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "atm_ce_iv": round(atm_ce_iv, 2) if atm_ce_iv else None,
            "atm_pe_iv": round(atm_pe_iv, 2) if atm_pe_iv else None,
            "atm_iv": atm_iv,
            "pc_skew": pc_skew,
            "skew_interpretation": _interpret_skew(pc_skew),
        }

        _greeks_cache[cache_key] = result
        _greeks_cache_ts[cache_key] = now
        return result

    except ImportError:
        logger.warning("option_chain_service or option_greeks_service not available")
        return None
    except Exception as e:
        logger.exception("Error fetching ATM IV data for %s: %s", underlying, e)
        return None


def _interpret_skew(skew: float | None) -> str:
    """Interpret put-call skew."""
    if skew is None:
        return "unavailable"
    if skew > 3:
        return "heavy_fear"
    if skew > 1:
        return "mild_fear"
    if skew < -3:
        return "complacency"
    if skew < -1:
        return "mild_complacency"
    return "neutral"


def fetch_options_dashboard(
    underlyings: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch complete options dashboard for all underlyings."""
    if underlyings is None:
        underlyings = ["NIFTY", "BANKNIFTY"]

    cache_key = "options_dashboard"
    now = time.time()
    if cache_key in _greeks_cache and (now - _greeks_cache_ts.get(cache_key, 0)) < _GREEKS_CACHE_TTL:
        return _greeks_cache[cache_key]

    result: dict[str, Any] = {}

    for underlying in underlyings:
        gex = fetch_gex_profile(underlying)
        iv_data = fetch_atm_iv_data(underlying)

        result[underlying] = {
            "gex": gex,
            "iv": iv_data,
        }

    _greeks_cache[cache_key] = result
    _greeks_cache_ts[cache_key] = now
    return result
