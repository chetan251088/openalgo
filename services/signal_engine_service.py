"""
Signal Engine Service — Phase 1 (OBSERVE mode)

5-layer decision engine:
  Layer 1: Regime Detection (IV Rank from VIX percentile + classify_regime from Market Pulse)
  Layer 2: Strategy Selection (regime × IV rank × DTE matrix)
  Layer 3: Strike Selection (Max Pain from options chain, OI walls, 1SD range)
  Layer 4: Position Sizing reference (shown only, no execution)
  Layer 5: Exit Rules reference (shown only, no execution)

No order execution in Phase 1. Pure signal generation and display.
"""

import logging
import math
import os
import time
from typing import Any

log = logging.getLogger(__name__)

_SIGNAL_CACHE_TTL = int(os.getenv("SIGNAL_ENGINE_CACHE_TTL", "60"))
_signal_cache: dict[str, Any] = {}
_signal_cache_ts: float = 0.0
_chain_cache: dict[str, list[dict]] = {}  # symbol:exchange:dte → raw options chain

# ---------------------------------------------------------------------------
# Strategy selection matrix (regime, iv_rank_min, iv_rank_max, dte_min, dte_max, name, description)
# ---------------------------------------------------------------------------
_STRATEGY_MATRIX = [
    ("range_bound", 80, 101, 0, 3,   "0DTE Short Strangle",  "High IV + 0-3 DTE range — ultra-fast theta"),
    ("range_bound", 80, 101, 3, 999, "Short Iron Butterfly", "Very high IV + range — maximise premium harvest"),
    ("range_bound", 60,  80, 2, 999, "Short Iron Condor",    "Range-bound + elevated IV — sell wings for credit"),
    ("range_bound",  0,  40, 2, 999, "Long Butterfly",       "Low IV range — buy cheap defined-risk structure"),
    ("trending_up",  60, 101, 2, 999, "Bull Put Spread",     "Uptrend + high IV — sell put spread for credit"),
    ("trending_down",60, 101, 2, 999, "Bear Call Spread",    "Downtrend + high IV — sell call spread for credit"),
    ("pre_event",    0,  40, 0, 999, "Long Straddle",        "Low IV pre-event — buy volatility cheaply"),
    ("volatile",    75, 101, 3, 999, "Short Iron Condor",    "Volatility spike + wide wings — harvest elevated IV"),
]


def _recommend_strategy(regime: str, iv_rank: float | None, dte: int) -> dict:
    if iv_rank is None:
        return {"name": "Insufficient Data", "description": "IV Rank unavailable", "confidence": 0}
    for reg, iv_lo, iv_hi, dte_lo, dte_hi, name, desc in _STRATEGY_MATRIX:
        if regime == reg and iv_lo <= iv_rank < iv_hi and dte_lo <= dte <= dte_hi:
            confidence = 90 if iv_rank >= 70 and regime in ("range_bound", "trending_up", "trending_down") else 70
            return {"name": name, "description": desc, "confidence": confidence}
    # fallback
    if iv_rank >= 60:
        return {"name": "Short Iron Condor", "description": "Default: elevated IV → sell premium", "confidence": 55}
    return {"name": "Wait / No Trade", "description": "Conditions not favorable for a defined-risk setup", "confidence": 0}


def _calc_max_pain(chain: list[dict]) -> float | None:
    """Strike where total intrinsic loss to option buyers is minimised."""
    if not chain:
        return None
    try:
        min_pain = float("inf")
        best_k: float = chain[0]["strike"]
        for k_exp in [row["strike"] for row in chain]:
            pain = 0.0
            for row in chain:
                k = row["strike"]
                ce_oi = (row.get("ce") or {}).get("oi", 0) or 0
                pe_oi = (row.get("pe") or {}).get("oi", 0) or 0
                pain += max(0.0, k - k_exp) * ce_oi  # CE buyer pain
                pain += max(0.0, k_exp - k) * pe_oi  # PE buyer pain
            if pain < min_pain:
                min_pain = pain
                best_k = k_exp
        return best_k
    except Exception as exc:
        log.warning("max_pain calc error: %s", exc)
        return None


def _find_oi_walls(chain: list[dict], top_n: int = 3) -> dict:
    """Highest-OI strikes = resistance (CE) and support (PE) walls."""
    if not chain:
        return {"ce_walls": [], "pe_walls": []}
    try:
        ce_sorted = sorted(chain, key=lambda r: (r.get("ce") or {}).get("oi", 0), reverse=True)
        pe_sorted = sorted(chain, key=lambda r: (r.get("pe") or {}).get("oi", 0), reverse=True)
        return {
            "ce_walls": [r["strike"] for r in ce_sorted[:top_n]],
            "pe_walls": [r["strike"] for r in pe_sorted[:top_n]],
        }
    except Exception:
        return {"ce_walls": [], "pe_walls": []}


def _calc_1sd_range(spot: float, iv_pct: float, dte: int) -> dict:
    """Log-normal 1SD expected spot range: S × exp(±σ√T)."""
    T = max(0, dte) / 365.0
    m = (iv_pct / 100.0) * math.sqrt(T)
    return {
        "lo": round(spot * math.exp(-m)),
        "hi": round(spot * math.exp(m)),
        "dte": dte,
        "iv_used": round(iv_pct, 1),
    }


def _capital_preservation_flags(vix_current: float | None, alerts: list[dict]) -> list[dict]:
    flags: list[dict] = []
    if vix_current and float(vix_current) >= 20:
        flags.append({
            "rule": f"VIX {vix_current:.1f} ≥ 20",
            "action": "No new short premium — flatten book",
            "severity": "high",
        })
    for a in (alerts or []):
        hrs = a.get("hours_away")
        if hrs is not None:
            try:
                if float(hrs) < 24:
                    flags.append({
                        "rule": f"Event in {hrs:.0f}h: {a.get('name', 'Unknown')}",
                        "action": "Flat 1 day before major events",
                        "severity": "medium",
                    })
            except (TypeError, ValueError):
                pass
    try:
        import datetime
        import pytz
        now_ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        if now_ist.weekday() == 0 and (now_ist.hour < 10 or (now_ist.hour == 10 and now_ist.minute < 15)):
            flags.append({
                "rule": "Monday pre-10:15 IST",
                "action": "No new positions until market settles",
                "severity": "medium",
            })
    except Exception:
        pass
    return flags


def get_signal(symbol: str = "NIFTY", exchange: str = "NFO", dte: int = 4) -> dict:
    """
    Generate a complete Phase-1 signal snapshot.

    Returns:
        dict with keys: symbol, exchange, dte, regime, iv_rank, iv_rank_label,
        directional_bias, directional_confidence, spot, vix_current,
        market_quality_score, strategy, max_pain, oi_walls, sd_range_1,
        capital_preservation_flags, favorable_to_trade, no_trade_reasons,
        updated_at, errors
    """
    global _signal_cache, _signal_cache_ts
    cache_key = f"{symbol}:{exchange}:{dte}"
    now = time.monotonic()
    if cache_key in _signal_cache and (now - _signal_cache_ts) < _SIGNAL_CACHE_TTL:
        return _signal_cache[cache_key]

    result: dict[str, Any] = {
        "symbol": symbol,
        "exchange": exchange,
        "dte": dte,
        "regime": "unknown",
        "iv_rank": None,
        "iv_rank_label": "unknown",
        "directional_bias": "NEUTRAL",
        "directional_confidence": 0,
        "spot": None,
        "vix_current": None,
        "market_quality_score": None,
        "strategy": {"name": "Insufficient Data", "description": "", "confidence": 0},
        "max_pain": None,
        "oi_walls": {"ce_walls": [], "pe_walls": []},
        "sd_range_1": None,
        "capital_preservation_flags": [],
        "favorable_to_trade": False,
        "no_trade_reasons": [],
        "updated_at": "",
        "errors": [],
    }

    # -----------------------------------------------------------------------
    # Layer 1 — Regime + IV Rank from Market Pulse data
    # -----------------------------------------------------------------------
    vix_current: float | None = None
    alerts: list[dict] = []
    try:
        from services.market_pulse_data import fetch_market_data
        from services.market_pulse_scoring import (
            classify_regime,
            compute_directional_bias,
            compute_market_quality,
            score_breadth,
            score_macro,
            score_momentum,
            score_trend,
            score_volatility,
        )
        from blueprints.market_pulse import (
            _compute_event_proximity,
            _quote_ltp,
            _quote_change_pct,
        )
        from services.market_pulse_config import SECTOR_INDICES, CATEGORY_WEIGHTS

        data = fetch_market_data(mode="swing")

        ni = data.get("nifty_indicators", {})
        bi = data.get("banknifty_indicators", {})
        vi = data.get("vix_indicators", {})
        ticker = data.get("ticker", {})
        sector_quotes = data.get("sectors", {})

        # IV Rank = VIX 1-year percentile
        iv_rank_raw = vi.get("percentile_1y")
        iv_rank = float(iv_rank_raw) if iv_rank_raw is not None else None
        vix_current = vi.get("current")
        if vix_current is not None:
            vix_current = float(vix_current)

        # Spot from ticker
        nifty_ltp = _quote_ltp(ticker.get(symbol)) or ni.get("ltp")

        # Score all 5 pillars (needed for regime + bias)
        vix_for_scores = _quote_ltp(ticker.get("INDIAVIX")) or vix_current
        nifty_for_scores = nifty_ltp

        # Build momentum data
        sector_above_20d = sum(
            1 for k in SECTOR_INDICES
            if (sector_quotes.get(k) or {}).get("change_pct_20d", 0) > 0
        )
        total_sectors = len(SECTOR_INDICES)

        vol_score, _ = score_volatility(
            vix_current=vix_for_scores,
            vix_slope_5d=vi.get("slope_5d"),
            vix_percentile=iv_rank,
            pcr=data.get("pcr"),
        )
        mom_score, _ = score_momentum(
            sectors_above_20d=sector_above_20d,
            total_sectors=total_sectors,
            leadership_spread=None,
            higher_highs_pct=None,
            rotation_diversity=None,
        )
        trend_score, _ = score_trend(
            nifty_ltp=nifty_for_scores,
            sma_20=ni.get("sma_20"),
            sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"),
            banknifty_ltp=_quote_ltp(ticker.get("BANKNIFTY")) or bi.get("ltp"),
            banknifty_sma50=bi.get("sma_50"),
            rsi=ni.get("rsi_14"),
            slope_50d=ni.get("slope_50d"),
            slope_200d=ni.get("slope_200d"),
        )

        # breadth
        breadth_snap = data.get("breadth_snapshot", {})
        ma_breadth = breadth_snap.get("moving_averages", {})
        ad_data = breadth_snap.get("advance_decline", {})
        hl_data = breadth_snap.get("annual_extremes", {})
        breadth_score, _ = score_breadth(
            ad_ratio=ad_data.get("ad_ratio"),
            pct_above_50d=ma_breadth.get("pct_above_50d"),
            pct_above_200d=ma_breadth.get("pct_above_200d"),
            highs_52w=hl_data.get("highs_52w"),
            lows_52w=hl_data.get("lows_52w"),
            ad_advances=ad_data.get("advances"),
            ad_declines=ad_data.get("declines"),
            ad_unchanged=ad_data.get("unchanged"),
            above_50d_count=ma_breadth.get("above_50d"),
            above_50d_total=ma_breadth.get("eligible_50d"),
            above_200d_count=ma_breadth.get("above_200d"),
            above_200d_total=ma_breadth.get("eligible_200d"),
            highs_total=hl_data.get("eligible_52w"),
            breadth_label=breadth_snap.get("scope", "Nifty 50"),
        )

        event_hours, event_type = _compute_event_proximity(data.get("events", []))
        macro_score, _ = score_macro(
            usdinr_slope=data.get("usdinr_indicators", {}).get("slope_5d"),
            event_proximity_hours=event_hours,
            event_type=event_type,
            nifty_usdinr_corr=None,
            institutional_flows=data.get("institutional_flows"),
        )

        market_quality = compute_market_quality({
            "volatility": vol_score,
            "momentum": mom_score,
            "trend": trend_score,
            "breadth": breadth_score,
            "macro": macro_score,
        })

        regime_str = classify_regime(
            nifty_ltp=nifty_for_scores,
            sma_20=ni.get("sma_20"),
            sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"),
            slope_50d=ni.get("slope_50d"),
            vix_current=vix_for_scores,
        )

        # Map market pulse regime string → our regime keys
        _regime_map = {
            "TRENDING": "trending_up",
            "TRENDING UP": "trending_up",
            "TRENDING DOWN": "trending_down",
            "RANGE": "range_bound",
            "RANGE-BOUND": "range_bound",
            "RANGE_BOUND": "range_bound",
            "VOLATILE": "volatile",
            "CONSOLIDATION": "range_bound",
            "SIDEWAYS": "range_bound",
            "UNKNOWN": "range_bound",
        }
        regime_upper = (regime_str or "").upper()
        regime_key = "range_bound"
        for k, v in _regime_map.items():
            if k in regime_upper:
                regime_key = v
                break

        # Check for pre-event override
        alerts = data.get("alerts") or []
        for a in alerts:
            hrs = a.get("hours_away")
            if hrs is not None:
                try:
                    if float(hrs) < 24:
                        regime_key = "pre_event"
                        break
                except (TypeError, ValueError):
                    pass

        directional_bias = compute_directional_bias(
            regime=regime_str,
            trend_score=trend_score,
            momentum_score=mom_score,
            breadth_score=breadth_score,
            vix_current=vix_for_scores,
            institutional_flows=data.get("institutional_flows"),
            mode="swing",
            intraday_tape=None,
        )

        result["iv_rank"] = round(iv_rank, 1) if iv_rank is not None else None
        result["regime"] = regime_key
        result["directional_bias"] = directional_bias.get("bias", "NEUTRAL")
        result["directional_confidence"] = directional_bias.get("confidence", 0)
        result["market_quality_score"] = market_quality
        result["vix_current"] = round(vix_current, 2) if vix_current else None
        result["spot"] = round(nifty_ltp, 2) if nifty_ltp else None
        result["updated_at"] = data.get("updated_at", "")

        if iv_rank is not None:
            if iv_rank >= 70:
                result["iv_rank_label"] = "SELL IV"
            elif iv_rank <= 30:
                result["iv_rank_label"] = "BUY IV"
            else:
                result["iv_rank_label"] = "SELECTIVE"

    except Exception as exc:
        log.error("Signal Engine market data error: %s", exc, exc_info=True)
        result["errors"].append(f"Market data: {exc}")

    result["capital_preservation_flags"] = _capital_preservation_flags(vix_current, alerts)

    # -----------------------------------------------------------------------
    # Layer 2 — Strategy recommendation
    # -----------------------------------------------------------------------
    result["strategy"] = _recommend_strategy(result["regime"], result["iv_rank"], dte)

    # -----------------------------------------------------------------------
    # Layer 3 — Options chain: Max Pain + OI Walls + 1SD range
    # -----------------------------------------------------------------------
    try:
        from services.market_pulse_greeks import _get_api_key, _get_nearest_weekly_expiry
        from services.option_chain_service import get_option_chain

        api_key = _get_api_key()
        if api_key:
            expiry = _get_nearest_weekly_expiry(symbol, exchange, api_key)
            if expiry:
                ok, chain_data, _ = get_option_chain(symbol, exchange, expiry, 20, api_key)
                if ok and chain_data.get("chain"):
                    chain = chain_data["chain"]
                    _chain_cache[cache_key] = chain  # store for leg building
                    spot_from_chain = chain_data.get("underlying_ltp")
                    if spot_from_chain:
                        result["spot"] = round(float(spot_from_chain), 2)
                    result["max_pain"] = _calc_max_pain(chain)
                    result["oi_walls"] = _find_oi_walls(chain)

                    # Try to get ATM IV from chain
                    atm_iv: float | None = None
                    for row in chain:
                        if (row.get("ce") or {}).get("label") == "ATM":
                            atm_iv = (row.get("ce") or {}).get("iv")
                            if not atm_iv:
                                atm_iv = (row.get("pe") or {}).get("iv")
                            break

                    # Fallback: use VIX as IV proxy
                    if atm_iv is None and vix_current:
                        atm_iv = float(vix_current)

                    if atm_iv and result["spot"]:
                        result["sd_range_1"] = _calc_1sd_range(float(result["spot"]), float(atm_iv), dte)
        else:
            # Attempt 1SD range with VIX as proxy if we have spot
            if result["spot"] and vix_current:
                result["sd_range_1"] = _calc_1sd_range(float(result["spot"]), float(vix_current), dte)
    except Exception as exc:
        log.error("Signal Engine options chain error: %s", exc, exc_info=True)
        result["errors"].append(f"Options chain: {exc}")

    # -----------------------------------------------------------------------
    # Favorable to trade check
    # -----------------------------------------------------------------------
    no_trade: list[str] = []
    for f in result["capital_preservation_flags"]:
        if f["severity"] == "high":
            no_trade.append(f"Capital protection: {f['rule']}")
    if result["strategy"]["confidence"] < 50:
        no_trade.append("No high-confidence strategy match for current regime")
    if result["market_quality_score"] is not None and result["market_quality_score"] < 45:
        no_trade.append(f"Market quality too low ({result['market_quality_score']}/100)")

    result["no_trade_reasons"] = no_trade
    result["favorable_to_trade"] = len(no_trade) == 0 and result["strategy"]["confidence"] >= 50

    _signal_cache[cache_key] = result
    _signal_cache_ts = now
    return result


def get_cached_chain(symbol: str = "NIFTY", exchange: str = "NFO", dte: int = 4) -> list[dict]:
    """Return the most recently fetched options chain for this signal key."""
    return _chain_cache.get(f"{symbol}:{exchange}:{dte}", [])


def invalidate_cache() -> None:
    """Force next call to recompute the signal."""
    global _signal_cache_ts
    _signal_cache_ts = 0.0
