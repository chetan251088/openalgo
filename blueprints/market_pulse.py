"""
Market Pulse Dashboard — Flask Blueprint
Single endpoint that orchestrates all market pulse services and returns complete dashboard data.
"""

import logging
from datetime import datetime, timedelta

import numpy as np
import pytz
from flask import Blueprint, jsonify, request

from services.market_pulse_analyst import generate_analysis
from services.market_pulse_data import fetch_market_data
from services.market_pulse_execution import compute_execution_window_day, compute_execution_window_swing, track_breakouts
from services.market_pulse_scoring import (
    classify_regime,
    compute_market_quality,
    get_decision,
    score_breadth,
    score_macro,
    score_momentum,
    score_trend,
    score_volatility,
)
from services.market_pulse_screener import screen_equities, select_fno_strategy
from utils.session import check_session_validity

logger = logging.getLogger(__name__)

market_pulse_bp = Blueprint("market_pulse", __name__, url_prefix="/market-pulse")


# ── Authentication ──────────────────────────────────────────────────────


def _require_auth():
    """Verify user session is valid."""
    if not check_session_validity(request.cookies):
        return None
    return True


# ── Helper Functions ────────────────────────────────────────────────────


def _compute_event_proximity(events: list[dict]) -> list[dict]:
    """
    Filter events within 72 hours and compute proximity.

    Args:
        events: List of event dicts from market_events.json

    Returns:
        List of alert dicts with (symbol, name, days_until, severity)
    """
    alerts = []
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    cutoff = now + timedelta(hours=72)

    for event in events:
        try:
            event_dt_str = event.get("date")
            # Parse ISO format or simple date
            if "T" in event_dt_str:
                event_dt = datetime.fromisoformat(event_dt_str).astimezone(pytz.timezone("Asia/Kolkata"))
            else:
                # Simple date format YYYY-MM-DD
                from datetime import datetime as dt

                event_dt = pytz.timezone("Asia/Kolkata").localize(
                    dt.strptime(event_dt_str, "%Y-%m-%d").replace(hour=10, minute=30)
                )

            if now < event_dt <= cutoff:
                days_until = (event_dt - now).days
                severity = "major" if event.get("severity") == "major" else "minor"
                alerts.append(
                    {
                        "name": event.get("name"),
                        "date": event_dt_str,
                        "days_until": days_until,
                        "severity": severity,
                        "impact": event.get("impact", "price"),
                    }
                )
        except Exception as e:
            logger.warning(f"Event parsing error for {event}: {e}")

    # Sort by days_until
    alerts.sort(key=lambda x: x["days_until"])
    return alerts


def _compute_breadth_from_constituents(constituent_data: dict[str, dict], sector_histories: dict[str, object]) -> tuple[int, int, int, int]:
    """
    Compute breadth metrics from constituent histories.

    Returns:
        (pct_above_50d_ma, pct_above_200d_ma, pct_at_20d_highs, new_highs_lows_ratio)
    """
    # Filter out constituents with invalid/missing data
    valid_constituents = {
        symbol: data
        for symbol, data in constituent_data.items()
        if data.get("history") is not None and len(data.get("history", [])) > 0
    }

    if not valid_constituents:
        return 0, 0, 0, 1.0

    above_50d = 0
    above_200d = 0
    at_20d_highs = 0
    at_52w_highs = 0
    at_52w_lows = 0

    for symbol, data in valid_constituents.items():
        try:
            hist = data["history"]
            if len(hist) < 50:
                continue

            closes = [c.get("close", 0) for c in hist]
            if len(closes) < 200:
                # Pad with first value if not enough history
                closes_padded = closes + [closes[-1]] * (200 - len(closes))
            else:
                closes_padded = closes

            current = closes[-1]
            sma_50 = np.mean(closes[-50:]) if len(closes) >= 50 else np.mean(closes)
            sma_200 = np.mean(closes_padded[-200:]) if len(closes) >= 200 else np.mean(closes)

            # 20d high
            high_20d = max([c.get("high", 0) for c in hist[-20:]])

            # 52w high/low
            high_52w = max([c.get("high", 0) for c in hist])
            low_52w = min([c.get("low", 0) for c in hist])

            if current >= sma_50:
                above_50d += 1
            if current >= sma_200:
                above_200d += 1
            if current >= high_20d * 0.99:  # Tolerance for rounding
                at_20d_highs += 1
            if current >= high_52w * 0.99:
                at_52w_highs += 1
            if current <= low_52w * 1.01:
                at_52w_lows += 1
        except Exception as e:
            logger.warning(f"Breadth calc error for {symbol}: {e}")

    n = len(valid_constituents) or 1
    pct_above_50d = int((above_50d / n) * 100)
    pct_above_200d = int((above_200d / n) * 100)
    pct_at_20d_highs = int((at_20d_highs / n) * 100)

    # New highs vs lows ratio
    new_highs_lows_ratio = (at_52w_highs / max(at_52w_lows, 1)) if at_52w_lows > 0 else float(at_52w_highs or 1)

    return pct_above_50d, pct_above_200d, pct_at_20d_highs, new_highs_lows_ratio


def _compute_momentum_data(sector_histories: dict[str, object]) -> tuple[int, float]:
    """
    Compute sector participation and leadership spread from histories.

    Returns:
        (sector_participation_pct, leadership_spread_pct)
    """
    if not sector_histories:
        return 0, 0.0

    sector_returns_5d = {}
    sectors_above_20d_ma = 0

    for sector_name, hist in sector_histories.items():
        try:
            if hist is None or len(hist) < 5:
                continue

            hist_list = list(hist) if not isinstance(hist, list) else hist
            if not hist_list:
                continue

            closes = [c.get("close", 0) for c in hist_list]
            if len(closes) < 5:
                continue

            current = closes[-1]
            sma_20d = np.mean(closes[-20:]) if len(closes) >= 20 else np.mean(closes)

            # 5d return
            ret_5d = ((closes[-1] - closes[-5]) / closes[-5]) * 100 if closes[-5] != 0 else 0
            sector_returns_5d[sector_name] = ret_5d

            # Above 20d MA
            if current >= sma_20d:
                sectors_above_20d_ma += 1
        except Exception as e:
            logger.warning(f"Momentum calc error for {sector_name}: {e}")

    # Sector participation
    total_sectors = len(sector_histories) or 1
    sector_participation = int((sectors_above_20d_ma / total_sectors) * 100)

    # Leadership spread: top 3 vs bottom 3 sectors
    sorted_returns = sorted(sector_returns_5d.values(), reverse=True)
    top_3 = np.mean(sorted_returns[:3]) if len(sorted_returns) >= 3 else (sorted_returns[0] if sorted_returns else 0)
    bottom_3 = np.mean(sorted_returns[-3:]) if len(sorted_returns) >= 3 else (sorted_returns[-1] if sorted_returns else 0)
    leadership_spread = abs(top_3 - bottom_3)

    return sector_participation, leadership_spread


def _direction(score: int) -> str:
    """Classify score direction as text."""
    if score >= 75:
        return "strong"
    elif score >= 60:
        return "moderate"
    elif score >= 45:
        return "weak"
    else:
        return "critical"


# ── Main API Endpoint ──────────────────────────────────────────────────


@market_pulse_bp.route("/api/data", methods=["GET"])
def market_pulse_api():
    """
    Main Market Pulse API endpoint.

    Query parameters:
        mode: "swing" (default) or "day"
        refresh: "1" to force analyst regeneration

    Returns: Complete market pulse dashboard data as JSON
    """
    # Auth check
    if not _require_auth():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    # Get parameters
    mode = request.args.get("mode", "swing").lower()
    if mode not in ("swing", "day"):
        mode = "swing"

    force_refresh = request.args.get("refresh") == "1"

    try:
        # ── Step 1: Fetch all market data ────────────────────────────
        logger.info(f"Fetching market data for mode={mode}")
        market_data = fetch_market_data(mode=mode)

        # ── Step 2: Extract key data structures ──────────────────────
        ticker = market_data.get("ticker", {})
        sectors = market_data.get("sectors", {})
        constituent_data = market_data.get("constituent_data", {})
        sector_histories = market_data.get("sector_histories", {})

        nifty_hist = market_data.get("nifty_history")
        banknifty_hist = market_data.get("banknifty_history")
        vix_hist = market_data.get("vix_history")
        usdinr_hist = market_data.get("usdinr_history")

        # Extract indicators
        nifty_indicators = market_data.get("nifty_indicators", {})
        banknifty_indicators = market_data.get("banknifty_indicators", {})
        vix_indicators = market_data.get("vix_indicators", {})
        usdinr_indicators = market_data.get("usdinr_indicators", {})

        # ── Step 3: Score all 5 categories ──────────────────────────
        logger.info("Computing scores")

        # Extract volatility parameters
        vix_current = vix_indicators.get("current")
        vix_slope_5d = vix_indicators.get("slope_5d")
        vix_percentile = vix_indicators.get("percentile_1y")
        pcr = market_data.get("pcr")
        volatility_score, volatility_rules = score_volatility(vix_current, vix_slope_5d, vix_percentile, pcr)
        volatility_score_data = {"score": volatility_score, "rules": volatility_rules}

        # Extract momentum parameters
        sectors_above_20d, leadership_spread = _compute_momentum_data(sector_histories)
        total_sectors = len(sector_histories) or 1
        pct_above_50d, pct_above_200d, higher_highs_pct, _ = _compute_breadth_from_constituents(constituent_data, sector_histories)
        # Count sectors with positive 5d momentum for rotation diversity
        rotation_diversity = 0
        for sector_hist in sector_histories.values():
            if sector_hist is not None and len(sector_hist) >= 5:
                hist_list = list(sector_hist) if not isinstance(sector_hist, list) else sector_hist
                closes = [c.get("close", 0) for c in hist_list]
                if len(closes) >= 5 and ((closes[-1] - closes[-5]) / closes[-5]) > 0:
                    rotation_diversity += 1
        momentum_score, momentum_rules = score_momentum(sectors_above_20d, total_sectors, leadership_spread, higher_highs_pct, rotation_diversity)
        momentum_score_data = {"score": momentum_score, "rules": momentum_rules}

        # Extract trend parameters
        nifty_ltp = nifty_indicators.get("ltp")
        sma_20 = nifty_indicators.get("sma_20")
        sma_50 = nifty_indicators.get("sma_50")
        sma_200 = nifty_indicators.get("sma_200")
        banknifty_ltp = banknifty_indicators.get("ltp")
        banknifty_sma50 = banknifty_indicators.get("sma_50")
        rsi = nifty_indicators.get("rsi_14")
        slope_50d = nifty_indicators.get("slope_50d")
        slope_200d = nifty_indicators.get("slope_200d")
        trend_score, trend_rules = score_trend(nifty_ltp, sma_20, sma_50, sma_200, banknifty_ltp, banknifty_sma50, rsi, slope_50d, slope_200d)
        trend_score_data = {"score": trend_score, "rules": trend_rules}

        # Extract breadth parameters
        breadth_data = market_data.get("nse_breadth", {})
        ad_ratio = breadth_data.get("ad_ratio") if breadth_data else None
        highs_52w = breadth_data.get("highs_52w") if breadth_data else None
        lows_52w = breadth_data.get("lows_52w") if breadth_data else None
        breadth_score, breadth_rules = score_breadth(ad_ratio, pct_above_50d, pct_above_200d, highs_52w, lows_52w)
        breadth_score_data = {"score": breadth_score, "rules": breadth_rules}

        # Extract macro parameters and find next event
        usdinr_slope_5d = usdinr_indicators.get("slope_5d")
        usdinr_slope_20d = usdinr_indicators.get("slope_20d")
        # Find next event and compute hours away
        events = market_data.get("events", [])
        event_hours_away = 999.0
        event_type = "none"
        if events:
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            for event in events:
                try:
                    event_dt_str = event.get("date")
                    if "T" in event_dt_str:
                        event_dt = datetime.fromisoformat(event_dt_str).astimezone(pytz.timezone("Asia/Kolkata"))
                    else:
                        event_dt = pytz.timezone("Asia/Kolkata").localize(
                            datetime.strptime(event_dt_str, "%Y-%m-%d").replace(hour=10, minute=30)
                        )
                    if event_dt > now:
                        hours = (event_dt - now).total_seconds() / 3600
                        if hours < event_hours_away:
                            event_hours_away = hours
                            event_type = "major" if event.get("severity") == "major" else "minor"
                except Exception:
                    pass
        macro_score, macro_rules = score_macro(usdinr_slope_5d, usdinr_slope_20d, vix_current, event_hours_away, event_type, nifty_correlation=None)
        macro_score_data = {"score": macro_score, "rules": macro_rules}

        # ── Step 4: Compute Market Quality & Decision ───────────────
        scores_dict = {
            "volatility": volatility_score,
            "momentum": momentum_score,
            "trend": trend_score,
            "breadth": breadth_score,
            "macro": macro_score,
        }

        market_quality_score = compute_market_quality(scores_dict)
        decision = get_decision(market_quality_score)
        regime = classify_regime(nifty_ltp, sma_20, sma_50, sma_200, slope_50d, vix_current)

        # ── Step 5: Track Breakouts & Execution Window ───────────────
        logger.info("Computing execution window")
        try:
            track_breakouts(constituent_data)
        except Exception as e:
            logger.warning(f"Breakout tracking failed: {e}")

        if mode == "swing":
            execution_window_score, execution_details = compute_execution_window_swing()
        else:
            execution_details_full = {**market_data, "nifty_indicators": nifty_indicators}
            execution_window_score, execution_details = compute_execution_window_day(execution_details_full)

        # ── Step 6: Screen equities & F&O ideas ──────────────────────
        logger.info("Screening equities and generating F&O ideas")
        equity_ideas = screen_equities(constituent_data, regime, nifty_hist)

        # Generate F&O strategy recommendation
        vix_current_fno = vix_current if vix_current is not None else 15.0
        fno_strategy = select_fno_strategy(vix_current_fno, regime)
        fno_ideas = [
            {
                "instrument": "NIFTY/BANKNIFTY",
                "strategy": fno_strategy.get("type"),
                "description": fno_strategy.get("description"),
                "risk": fno_strategy.get("risk"),
                "reward": fno_strategy.get("reward"),
                "levels": fno_strategy.get("levels", {}),
                "vix_regime": "high" if vix_current_fno > 20 else "low/moderate",
                "rationale": f"VIX={vix_current_fno:.1f}, Regime={regime}",
            }
        ]

        # ── Step 7: Build sectors summary ────────────────────────────
        sectors_summary = []
        for sector_name, hist in sector_histories.items():
            try:
                if hist is None or len(hist) < 20:
                    continue

                hist_list = list(hist) if not isinstance(hist, list) else hist
                closes = [c.get("close", 0) for c in hist_list]

                if len(closes) < 5:
                    continue

                ret_1d = ((closes[-1] - closes[-2]) / closes[-2]) * 100 if closes[-2] != 0 else 0
                ret_5d = ((closes[-1] - closes[-5]) / closes[-5]) * 100 if closes[-5] != 0 else 0
                ret_20d = ((closes[-1] - closes[-20]) / closes[-20]) * 100 if len(closes) >= 20 and closes[-20] != 0 else 0

                sectors_summary.append(
                    {
                        "name": sector_name,
                        "return_1d": round(ret_1d, 2),
                        "return_5d": round(ret_5d, 2),
                        "return_20d": round(ret_20d, 2),
                        "current_price": closes[-1],
                    }
                )
            except Exception as e:
                logger.warning(f"Sector summary error for {sector_name}: {e}")

        # Sort by 5d return descending
        sectors_summary.sort(key=lambda x: x["return_5d"], reverse=True)

        # ── Step 8: Build alerts from events ─────────────────────────
        events = market_data.get("events", [])
        alerts = _compute_event_proximity(events)

        # ── Step 9: Generate AI analyst commentary ───────────────────
        logger.info("Generating analyst commentary")

        # Build pulse_data for analyst
        pulse_data = {
            "decision": decision,
            "market_quality_score": market_quality_score,
            "execution_window_score": execution_window_score,
            "regime": regime,
            "scores": {
                "volatility": volatility_score,
                "momentum": momentum_score,
                "trend": trend_score,
                "breadth": breadth_score,
                "macro": macro_score,
            },
            "ticker": ticker,
            "sectors": sectors_summary,
            "alerts": alerts,
            "equity_ideas": equity_ideas,
            "fno_ideas": fno_ideas,
            "indicators": {
                "vix": vix_indicators,
                "usdinr": usdinr_indicators,
                "nifty": nifty_indicators,
                "banknifty": banknifty_indicators,
            },
        }

        analysis = generate_analysis(pulse_data, mode=mode, force=force_refresh)

        # ── Step 10: Compute correlation (Nifty-USDINR) ──────────────
        nifty_correlation = None
        try:
            if nifty_hist is not None and usdinr_hist is not None:
                nifty_closes = np.array([c.get("close", 0) for c in nifty_hist])
                usdinr_closes = np.array([c.get("close", 0) for c in usdinr_hist])

                # Align to same length (shortest)
                min_len = min(len(nifty_closes), len(usdinr_closes))
                if min_len > 1:
                    nifty_closes = nifty_closes[-min_len:]
                    usdinr_closes = usdinr_closes[-min_len:]

                corr_matrix = np.corrcoef(nifty_closes, usdinr_closes)
                if not np.isnan(corr_matrix[0, 1]):
                    nifty_correlation = round(float(corr_matrix[0, 1]), 3)
        except Exception as e:
            logger.warning(f"Correlation calculation failed: {e}")

        # ── Step 11: Build response ──────────────────────────────────
        ist = pytz.timezone("Asia/Kolkata")
        timestamp = datetime.now(ist)

        pulse_response = {
            "status": "success",
            "data": {
                "decision": decision,
                "market_quality_score": market_quality_score,
                "execution_window_score": execution_window_score,
                "mode": mode,
                "regime": regime,
                "scores": {
                    "volatility": {
                        "score": volatility_score,
                        "weight": 0.25,
                        "direction": _direction(volatility_score),
                        "rules": volatility_score_data.get("rules", []),
                    },
                    "momentum": {
                        "score": momentum_score,
                        "weight": 0.25,
                        "direction": _direction(momentum_score),
                        "rules": momentum_score_data.get("rules", []),
                    },
                    "trend": {
                        "score": trend_score,
                        "weight": 0.20,
                        "direction": _direction(trend_score),
                        "rules": trend_score_data.get("rules", []),
                    },
                    "breadth": {
                        "score": breadth_score,
                        "weight": 0.20,
                        "direction": _direction(breadth_score),
                        "rules": breadth_score_data.get("rules", []),
                    },
                    "macro": {
                        "score": macro_score,
                        "weight": 0.10,
                        "direction": _direction(macro_score),
                        "rules": macro_score_data.get("rules", []),
                    },
                },
                "ticker": ticker,
                "sectors": sectors_summary,
                "alerts": alerts,
                "equity_ideas": equity_ideas,
                "fno_ideas": fno_ideas,
                "analysis": analysis,
                "execution_details": execution_details,
                "nifty_usdinr_correlation": nifty_correlation,
                "updated_at": timestamp.isoformat(),
                "cache_ttl": 30,
            },
        }

        return jsonify(pulse_response), 200

    except Exception as e:
        logger.error(f"Market Pulse API error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
