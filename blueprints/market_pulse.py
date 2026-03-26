"""
Market Pulse blueprint — "Should I Be Trading India?"
Serves the React page and provides the API endpoint.
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request, session
import pandas as pd

from services.market_pulse_config import CACHE_TTL_SECONDS, CATEGORY_WEIGHTS, RESPONSE_CACHE_TTL_SECONDS, SECTOR_INDICES

logger = logging.getLogger(__name__)

market_pulse_bp = Blueprint("market_pulse", __name__, url_prefix="/market-pulse")

# ── Response-level cache (avoids re-scoring when data hasn't changed) ──
_response_cache: dict[str, Any] = {}
_response_cache_ts: dict[str, float] = {}
_RESPONSE_CACHE_TTL = RESPONSE_CACHE_TTL_SECONDS


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_DAY_INTRADAY_LIMIT_PER_SIDE = max(
    2,
    _int_env("MARKET_PULSE_DAY_INTRADAY_LIMIT_PER_SIDE", 4),
)
_DAY_INTRADAY_BAR_CONTEXT_ENABLED = _env_flag(
    "MARKET_PULSE_DAY_INTRADAY_BAR_CONTEXT",
    False,
)


def _require_auth():
    """Check session-based authentication."""
    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    return None


def _compute_event_proximity(events: list[dict]) -> tuple[float | None, str | None]:
    """Find nearest upcoming event and hours away."""
    now = datetime.now()
    nearest_hours = None
    nearest_type = None

    for event in events:
        try:
            event_dt = datetime.strptime(
                f"{event['date']} {event.get('time', '09:15')}",
                "%Y-%m-%d %H:%M",
            )
            hours_away = (event_dt - now).total_seconds() / 3600
            if 0 <= hours_away <= 168:  # within 7 days
                if nearest_hours is None or hours_away < nearest_hours:
                    nearest_hours = hours_away
                    nearest_type = event.get("type", "minor")
        except (ValueError, KeyError):
            continue

    return nearest_hours, nearest_type


def _quote_ltp(quote: dict | None) -> float | None:
    if isinstance(quote, dict) and isinstance(quote.get("ltp"), (int, float)):
        return float(quote["ltp"])
    return None


def _quote_change_pct(quote: dict | None) -> float | None:
    if isinstance(quote, dict) and isinstance(quote.get("change_pct"), (int, float)):
        return float(quote["change_pct"])
    if isinstance(quote, dict):
        ltp = quote.get("ltp")
        prev_close = quote.get("prev_close")
        if isinstance(ltp, (int, float)) and isinstance(prev_close, (int, float)) and prev_close:
            return round(((float(ltp) - float(prev_close)) / float(prev_close)) * 100, 2)
    return None


def _quote_prev_close(quote: dict | None) -> float | None:
    if isinstance(quote, dict) and isinstance(quote.get("prev_close"), (int, float)):
        return float(quote["prev_close"])
    return None


def _build_quote_tape_context(quote: dict | None) -> dict[str, Any]:
    """Derive a cheap VWAP-style tape read from the live quote payload."""
    if not isinstance(quote, dict):
        return {}

    current = _quote_ltp(quote)
    average_price = None
    for key in ("average_price", "avg_price", "vwap"):
        value = quote.get(key)
        if isinstance(value, (int, float)) and value > 0:
            average_price = float(value)
            break

    if current is None or average_price is None or average_price <= 0:
        return {}

    vwap_distance_pct = round(((current - average_price) / average_price) * 100, 2)
    return {
        "vwap": round(average_price, 2),
        "vwap_distance_pct": vwap_distance_pct,
        "above_vwap": bool(current > average_price),
        "below_vwap": bool(current < average_price),
    }


def _resolve_live_underlying_ltp(
    ticker: dict[str, dict] | None,
    key: str,
    fallback: float | None,
) -> float | None:
    """Use the live ticker price for execution ideas; fall back only if missing."""
    live_ltp = _quote_ltp((ticker or {}).get(key))
    if live_ltp is not None:
        return live_ltp
    if isinstance(fallback, (int, float)):
        return float(fallback)
    return None


def _resolve_previous_day_levels(
    history,
    live_quote: dict | None = None,
) -> dict[str, Any] | None:
    """Derive PDH/PDL/PDC and current state from daily history plus live quote."""
    if history is None or any(col not in history.columns for col in ("high", "low", "close")):
        return None
    hist = history.copy()
    if "timestamp" in hist.columns:
        timestamps = hist["timestamp"]
        if pd.api.types.is_numeric_dtype(timestamps):
            parsed = pd.to_datetime(timestamps, unit="s", errors="coerce")
        else:
            parsed = pd.to_datetime(timestamps, errors="coerce")
        hist = hist.assign(_timestamp=parsed).sort_values(
            by=["_timestamp"],
            kind="stable",
            na_position="last",
        )
        hist = hist.drop_duplicates(subset=["_timestamp"], keep="last")
        hist = hist.drop(columns=["_timestamp"])
    for col in ("high", "low", "close"):
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    if len(hist) == 0:
        return None

    prev_close = _quote_prev_close(live_quote)
    current = _quote_ltp(live_quote)
    if current is None and len(hist) > 0:
        current = float(hist["close"].iloc[-1])

    if (
        isinstance(prev_close, (int, float))
        and len(hist) > 0
        and float(prev_close) > 0
    ):
        latest_close = float(hist["close"].iloc[-1])
        divergence_pct = abs((latest_close - float(prev_close)) / float(prev_close)) * 100
        if divergence_pct > 8:
            logger.warning(
                "Skipping previous-day level resolution due to stale history mismatch: "
                "latest_close=%s prev_close=%s divergence_pct=%.2f",
                latest_close,
                prev_close,
                divergence_pct,
            )
            return None

    if isinstance(prev_close, (int, float)):
        tolerance = max(0.5, abs(prev_close) * 0.001)
        close_diff = (hist["close"] - float(prev_close)).abs()
        matching_rows = hist.loc[close_diff <= tolerance]
        if not matching_rows.empty:
            previous_bar = matching_rows.iloc[-1]
        elif len(hist) >= 2:
            previous_bar = hist.iloc[-2]
        else:
            previous_bar = hist.iloc[-1]
    else:
        previous_bar = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]

    pdc = float(prev_close) if isinstance(prev_close, (int, float)) else float(previous_bar["close"])
    pdh = float(previous_bar["high"])
    pdl = float(previous_bar["low"])

    state = "unknown"
    if isinstance(current, (int, float)):
        if current > pdh:
            state = "above_pdh"
        elif current < pdl:
            state = "below_pdl"
        else:
            state = "inside_prior_range"

    gap_pct = None
    if isinstance(current, (int, float)) and pdc:
        gap_pct = round(((current - pdc) / pdc) * 100, 2)

    return {
        "pdh": round(pdh, 2),
        "pdl": round(pdl, 2),
        "pdc": round(pdc, 2),
        "current": round(float(current), 2) if isinstance(current, (int, float)) else None,
        "state": state,
        "gap_pct": gap_pct,
    }


def _shortlist_intraday_equities(
    constituent_data: dict[str, dict],
    live_quotes: dict[str, dict] | None,
    benchmark_change_pct: float | None,
    limit_per_side: int = _DAY_INTRADAY_LIMIT_PER_SIDE,
) -> list[dict[str, Any]]:
    """Pick the strongest and weakest live tapes for VWAP/RVOL confirmation."""
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []

    for symbol, payload in constituent_data.items():
        exchange = payload.get("exchange")
        quote = (live_quotes or {}).get(symbol) or {}
        current_price = _quote_ltp(quote)
        live_change_pct = _quote_change_pct(quote)
        if not exchange or current_price is None or live_change_pct is None:
            continue

        rs_vs_nifty = (
            float(live_change_pct) - float(benchmark_change_pct)
            if isinstance(benchmark_change_pct, (int, float))
            else float(live_change_pct)
        )
        row = {
            "key": symbol,
            "symbol": symbol,
            "exchange": exchange,
            "current_price": current_price,
            "rs_vs_nifty": rs_vs_nifty,
            "change_pct": float(live_change_pct),
        }
        if rs_vs_nifty >= 0:
            positive.append(row)
        else:
            negative.append(row)

    positive.sort(key=lambda item: (item["rs_vs_nifty"], item["change_pct"]), reverse=True)
    negative.sort(key=lambda item: (item["rs_vs_nifty"], item["change_pct"]))

    selected = positive[:limit_per_side] + negative[:limit_per_side]
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        key = item["key"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _compute_momentum_data(
    sector_histories: dict,
    constituent_data: dict,
    sector_quotes: dict[str, dict] | None = None,
    constituent_quotes: dict[str, dict] | None = None,
    use_live_inputs: bool = False,
) -> dict:
    """Compute momentum sub-scores from sector histories."""
    sectors_above_20d = 0
    sector_returns_5d = {}

    for key, hist in sector_histories.items():
        if hist is None or "close" not in hist.columns or len(hist) < 20:
            continue
        closes = hist["close"]
        sma_20 = closes.tail(20).mean()
        latest_close = closes.iloc[-1]
        if use_live_inputs and sector_quotes:
            live_ltp = _quote_ltp(sector_quotes.get(key))
            if live_ltp is not None:
                latest_close = live_ltp
        if latest_close > sma_20:
            sectors_above_20d += 1
        live_change = _quote_change_pct((sector_quotes or {}).get(key))
        if use_live_inputs and live_change is not None:
            sector_returns_5d[key] = round(live_change, 2)
        elif len(closes) >= 5:
            ret = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100
            sector_returns_5d[key] = round(ret, 2)

    # Leadership spread
    sorted_returns = sorted(sector_returns_5d.values())
    if len(sorted_returns) >= 6:
        top3 = sum(sorted_returns[-3:]) / 3
        bot3 = sum(sorted_returns[:3]) / 3
        spread = top3 - bot3
    else:
        spread = 3.0

    # Higher highs % (Nifty 50 at 20d highs)
    highs_count = 0
    total_const = 0
    for sym, data in constituent_data.items():
        hist = data.get("history")
        if hist is None or len(hist) < 21:
            continue
        closes = hist["close"]
        total_const += 1
        high_20d = closes.iloc[-21:-1].max()
        latest_close = closes.iloc[-1]
        if use_live_inputs and constituent_quotes:
            live_ltp = _quote_ltp(constituent_quotes.get(sym))
            if live_ltp is not None:
                latest_close = live_ltp
        if latest_close >= high_20d:
            highs_count += 1

    higher_highs_pct = (highs_count / max(total_const, 1)) * 100

    # Rotation diversity (how many sectors have positive 5d return)
    positive_sectors = sum(1 for v in sector_returns_5d.values() if v > 0)

    return {
        "sectors_above_20d": sectors_above_20d,
        "total_sectors": len(sector_histories),
        "leadership_spread": round(spread, 2),
        "higher_highs_pct": round(higher_highs_pct, 1),
        "rotation_diversity": positive_sectors,
        "sector_returns_5d": sector_returns_5d,
    }


@market_pulse_bp.route("/api/data", methods=["GET"])
def market_pulse_api():
    """Main API endpoint — returns full market pulse data."""
    # Market Pulse is a system-level dashboard using APP_KEY from environment
    # No session authentication required
    mode = request.args.get("mode", "swing")
    force_refresh = request.args.get("refresh") == "1"
    if mode not in ("swing", "day"):
        mode = "swing"

    # Check response cache first (skip all scoring if we have a fresh response)
    if not force_refresh:
        cached_resp = _response_cache.get(mode)
        cached_ts = _response_cache_ts.get(mode, 0)
        if cached_resp and (time.time() - cached_ts) < _RESPONSE_CACHE_TTL:
            return jsonify({"status": "success", "data": cached_resp})

    try:
        from services.market_pulse_data import (
            compute_constituent_breadth_snapshot,
            fetch_intraday_trade_context,
            fetch_market_data,
        )
        from services.market_pulse_scoring import (
            apply_intraday_tape_overrides,
            assess_intraday_tape,
            classify_regime,
            compute_directional_bias,
            compute_market_quality,
            get_decision,
            get_trade_decision,
            resolve_execution_regime,
            score_breadth,
            score_macro,
            score_momentum,
            score_trend,
            score_volatility,
        )
        from services.market_pulse_execution import (
            compute_execution_window_day,
            compute_execution_window_swing,
            track_breakouts,
        )
        from services.market_pulse_screener import (
            generate_fno_ideas,
            screen_equities,
        )
        from services.market_pulse_analyst import generate_analysis

        # 1. Fetch all data (30s cached)
        data = fetch_market_data(mode=mode, force_refresh=force_refresh)

        # 2. Extract indicators
        ni = data.get("nifty_indicators", {})
        bi = data.get("banknifty_indicators", {})
        vi = data.get("vix_indicators", {})
        ui = data.get("usdinr_indicators", {})
        ticker = data.get("ticker", {})
        sector_quotes = data.get("sectors", {})
        constituent_quotes = data.get("constituent_quotes", {})
        use_live_day_scores = mode == "day"

        nifty_ltp_for_scores = (
            _quote_ltp(ticker.get("NIFTY")) if use_live_day_scores else None
        ) or ni.get("ltp")
        banknifty_ltp_for_scores = (
            _quote_ltp(ticker.get("BANKNIFTY")) if use_live_day_scores else None
        ) or bi.get("ltp")
        vix_current_for_scores = (
            _quote_ltp(ticker.get("INDIAVIX")) if use_live_day_scores else None
        ) or vi.get("current")
        nifty_change_pct = _quote_change_pct(ticker.get("NIFTY"))
        banknifty_change_pct = _quote_change_pct(ticker.get("BANKNIFTY"))
        vix_change_pct = _quote_change_pct(ticker.get("INDIAVIX"))

        # 3. Compute breadth from constituents
        breadth_snapshot = compute_constituent_breadth_snapshot(
            data.get("constituent_data", {}),
            constituent_quotes=constituent_quotes,
            use_live_ltp=use_live_day_scores,
        )
        ma_breadth = breadth_snapshot.get("moving_averages", {})
        ad_data = breadth_snapshot.get("advance_decline", {})
        hl_data = breadth_snapshot.get("annual_extremes", {})

        # 4. Compute momentum data
        momentum_data = _compute_momentum_data(
            data.get("sector_histories", {}),
            data.get("constituent_data", {}),
            sector_quotes=sector_quotes,
            constituent_quotes=constituent_quotes,
            use_live_inputs=use_live_day_scores,
        )

        # 5. Event proximity
        event_hours, event_type = _compute_event_proximity(data.get("events", []))

        # 6. Score all categories
        vol_score, vol_rules = score_volatility(
            vix_current=vix_current_for_scores,
            vix_slope_5d=vi.get("slope_5d"),
            vix_percentile=vi.get("percentile_1y"),
            pcr=data.get("pcr"),
        )

        mom_score, mom_rules = score_momentum(
            sectors_above_20d=momentum_data["sectors_above_20d"],
            total_sectors=momentum_data["total_sectors"],
            leadership_spread=momentum_data["leadership_spread"],
            higher_highs_pct=momentum_data["higher_highs_pct"],
            rotation_diversity=momentum_data["rotation_diversity"],
        )

        trend_score, trend_rules = score_trend(
            nifty_ltp=nifty_ltp_for_scores,
            sma_20=ni.get("sma_20"),
            sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"),
            banknifty_ltp=banknifty_ltp_for_scores,
            banknifty_sma50=bi.get("sma_50"),
            rsi=ni.get("rsi_14"),
            slope_50d=ni.get("slope_50d"),
            slope_200d=ni.get("slope_200d"),
        )

        breadth_score, breadth_rules = score_breadth(
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
            breadth_label=breadth_snapshot.get("scope", "Nifty 50"),
        )

        sector_returns_1d = [
            _quote_change_pct(sector_quotes.get(key))
            for key in SECTOR_INDICES
        ]
        intraday_tape = assess_intraday_tape(
            nifty_change_pct=nifty_change_pct,
            banknifty_change_pct=banknifty_change_pct,
            vix_change_pct=vix_change_pct,
            sector_returns_1d=sector_returns_1d,
            advances=ad_data.get("advances"),
            declines=ad_data.get("declines"),
            unchanged=ad_data.get("unchanged"),
        )

        tape_overrides = apply_intraday_tape_overrides(
            mode=mode,
            trend_score=trend_score,
            trend_rules=trend_rules,
            momentum_score=mom_score,
            momentum_rules=mom_rules,
            breadth_score=breadth_score,
            breadth_rules=breadth_rules,
            tape=intraday_tape,
        )
        trend_score = tape_overrides["trend_score"]
        trend_rules = tape_overrides["trend_rules"]
        mom_score = tape_overrides["momentum_score"]
        mom_rules = tape_overrides["momentum_rules"]
        breadth_score = tape_overrides["breadth_score"]
        breadth_rules = tape_overrides["breadth_rules"]

        nifty_usdinr_corr = None
        nifty_hist = data.get("nifty_history")
        usdinr_hist = data.get("usdinr_history")
        if (
            nifty_hist is not None
            and usdinr_hist is not None
            and "close" in nifty_hist.columns
            and "close" in usdinr_hist.columns
            and len(nifty_hist) >= 20
            and len(usdinr_hist) >= 20
        ):
            import numpy as np

            nifty_returns = nifty_hist["close"].pct_change().dropna().tail(20).values
            usdinr_returns = usdinr_hist["close"].pct_change().dropna().tail(20).values
            min_len = min(len(nifty_returns), len(usdinr_returns))
            if min_len >= 15:
                corr_matrix = np.corrcoef(
                    nifty_returns[-min_len:],
                    usdinr_returns[-min_len:],
                )
                corr = float(corr_matrix[0, 1])
                if corr == corr:
                    nifty_usdinr_corr = round(corr, 3)

        macro_score, macro_rules = score_macro(
            usdinr_slope_5d=ui.get("slope_5d"),
            usdinr_slope_20d=ui.get("slope_20d"),
            vix_current=vix_current_for_scores,
            event_hours_away=event_hours,
            event_type=event_type,
            nifty_usdinr_corr=nifty_usdinr_corr,
            institutional_flows=data.get("institutional_flows"),
        )

        # 7. Market Quality Score
        category_scores = {
            "volatility": vol_score,
            "momentum": mom_score,
            "trend": trend_score,
            "breadth": breadth_score,
            "macro": macro_score,
        }
        market_quality = compute_market_quality(category_scores)
        quality_decision = get_decision(market_quality)

        # 8. Regime
        regime = classify_regime(
            nifty_ltp=nifty_ltp_for_scores,
            sma_20=ni.get("sma_20"),
            sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"),
            slope_50d=ni.get("slope_50d"),
            vix_current=vix_current_for_scores,
        )

        # 9. Execution Window
        try:
            track_breakouts(data.get("constituent_data", {}))
        except Exception as e:
            logger.warning("Breakout tracking failed: %s", e)

        exec_score, exec_details = compute_execution_window_swing()
        if mode == "day":
            day_score, day_details = compute_execution_window_day(data)
            exec_score = int((exec_score + day_score) / 2)
            exec_details.update(day_details)

        directional_bias = compute_directional_bias(
            regime=regime,
            trend_score=trend_score,
            momentum_score=mom_score,
            breadth_score=breadth_score,
            vix_current=vix_current_for_scores,
            institutional_flows=data.get("institutional_flows"),
            mode=mode,
            intraday_tape=intraday_tape,
        )
        execution_regime = resolve_execution_regime(
            mode=mode,
            structural_regime=regime,
            directional_bias=str(directional_bias["bias"]),
            bias_confidence=int(directional_bias["confidence"]),
        )
        decision = get_trade_decision(
            market_quality_score=market_quality,
            execution_score=exec_score,
            directional_bias=str(directional_bias["bias"]),
            bias_confidence=int(directional_bias["confidence"]),
        )

        # 10. Screener
        intraday_equity_context: dict[str, dict[str, Any]] = {}
        if mode == "day" and _DAY_INTRADAY_BAR_CONTEXT_ENABLED:
            intraday_symbols = _shortlist_intraday_equities(
                data.get("constituent_data", {}),
                constituent_quotes,
                nifty_change_pct,
            )
            intraday_equity_context = fetch_intraday_trade_context(
                intraday_symbols,
                force_refresh=force_refresh,
            )

        equity_ideas = screen_equities(
            data.get("constituent_data", {}),
            execution_regime,
            data.get("nifty_history"),
            mode=mode,
            live_quotes=constituent_quotes,
            benchmark_change_pct=nifty_change_pct,
            intraday_context=intraday_equity_context,
        )

        vix_current = _resolve_live_underlying_ltp(
            ticker,
            "INDIAVIX",
            vix_current_for_scores,
        ) or 15
        nifty_ltp = _resolve_live_underlying_ltp(
            ticker,
            "NIFTY",
            nifty_ltp_for_scores,
        )
        banknifty_ltp = _resolve_live_underlying_ltp(
            ticker,
            "BANKNIFTY",
            banknifty_ltp_for_scores,
        )
        market_levels = {
            "NIFTY": _resolve_previous_day_levels(
                data.get("nifty_history"),
                ticker.get("NIFTY"),
            ),
            "SENSEX": _resolve_previous_day_levels(
                data.get("sensex_history"),
                ticker.get("SENSEX"),
            ),
            "BANKNIFTY": _resolve_previous_day_levels(
                data.get("banknifty_history"),
                ticker.get("BANKNIFTY"),
            ),
        }
        intraday_index_context: dict[str, dict[str, Any]] = {}
        if mode == "day":
            for key in ("NIFTY", "BANKNIFTY"):
                quote_context = _build_quote_tape_context(ticker.get(key))
                if quote_context:
                    intraday_index_context[key] = quote_context

            if _DAY_INTRADAY_BAR_CONTEXT_ENABLED:
                enriched_intraday_index_context = fetch_intraday_trade_context(
                    [
                        {
                            "key": "NIFTY",
                            "symbol": "NIFTY",
                            "exchange": "NSE_INDEX",
                            "current_price": nifty_ltp,
                        },
                        {
                            "key": "BANKNIFTY",
                            "symbol": "BANKNIFTY",
                            "exchange": "NSE_INDEX",
                            "current_price": banknifty_ltp,
                        },
                    ],
                    force_refresh=force_refresh,
                )
                for key, payload in enriched_intraday_index_context.items():
                    intraday_index_context[key] = {
                        **intraday_index_context.get(key, {}),
                        **payload,
                    }
        fno_ideas = generate_fno_ideas(
            execution_regime,
            vix_current,
            nifty_ltp,
            banknifty_ltp,
            mode=mode,
            directional_bias=str(directional_bias["bias"]),
            bias_confidence=int(directional_bias["confidence"]),
            options_context=data.get("options_context"),
            market_levels=market_levels,
            intraday_context=intraday_index_context,
        )

        # 11. Sector summary for heatmap
        sectors_summary = []
        for key, returns_5d in sorted(
            momentum_data["sector_returns_5d"].items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            info = SECTOR_INDICES.get(key, {})
            quote = data.get("sectors", {}).get(key, {})
            sector_hist = data.get("sector_histories", {}).get(key)
            return_20d = None
            if sector_hist is not None and len(sector_hist) >= 20:
                closes_20d = sector_hist["close"]
                return_20d = round(
                    (closes_20d.iloc[-1] - closes_20d.iloc[-20])
                    / closes_20d.iloc[-20]
                    * 100,
                    2,
                )
            sectors_summary.append({
                "key": key,
                "name": info.get("symbol", key),
                "ltp": quote.get("ltp"),
                "return_5d": returns_5d,
                "return_1d": quote.get("change_pct") if isinstance(quote.get("change_pct"), (int, float)) else None,
                "return_20d": return_20d,
            })

        # 12. Alerts
        alerts = []
        if event_hours is not None and event_hours <= 72:
            for event in data.get("events", []):
                try:
                    time_str = event.get("time", "09:15")
                    event_dt = datetime.strptime(
                        f"{event['date']} {time_str}",
                        "%Y-%m-%d %H:%M",
                    )
                    hours = (event_dt - datetime.now()).total_seconds() / 3600
                    if 0 <= hours <= 72:
                        alerts.append({
                            "type": event.get("type", "minor"),
                            "name": event.get("name", "Unknown event"),
                            "date": event["date"],
                            "time": time_str,
                            "hours_away": round(hours, 1),
                        })
                except (ValueError, KeyError):
                    continue

        snapshot_ts = data.get("updated_at")
        if isinstance(snapshot_ts, (int, float)):
            snapshot_updated_at = datetime.fromtimestamp(snapshot_ts).isoformat()
        else:
            snapshot_updated_at = datetime.now().isoformat()

        # 13. Build response
        pulse_response = {
            "decision": decision,
            "quality_decision": quality_decision,
            "market_quality_score": market_quality,
            "execution_window_score": exec_score,
            "mode": mode,
            "regime": regime,
            "execution_regime": execution_regime,
            "directional_bias": directional_bias,
            "scores": {
                "volatility": {"score": vol_score, "weight": CATEGORY_WEIGHTS["volatility"], "direction": _direction(vol_rules), "rules": vol_rules},
                "momentum": {"score": mom_score, "weight": CATEGORY_WEIGHTS["momentum"], "direction": _direction(mom_rules), "rules": mom_rules},
                "trend": {"score": trend_score, "weight": CATEGORY_WEIGHTS["trend"], "direction": _direction(trend_rules), "rules": trend_rules},
                "breadth": {"score": breadth_score, "weight": CATEGORY_WEIGHTS["breadth"], "direction": _direction(breadth_rules), "rules": breadth_rules},
                "macro": {"score": macro_score, "weight": CATEGORY_WEIGHTS["macro"], "direction": _direction(macro_rules), "rules": macro_rules},
            },
            "ticker": data.get("ticker", {}),
            "sectors": sectors_summary,
            "options_context": data.get("options_context", {}),
            "market_levels": market_levels,
            "institutional_flows": data.get("institutional_flows"),
            "alerts": alerts,
            "equity_ideas": equity_ideas,
            "fno_ideas": fno_ideas,
            "execution_details": exec_details,
            "errors": data.get("errors", []),
            "updated_at": snapshot_updated_at,
            "cache_ttl": CACHE_TTL_SECONDS,
            "breadth_snapshot": breadth_snapshot,
            "intraday_tape": intraday_tape,
            "intraday_equity_context": intraday_equity_context,
        }

        # 14. AI Analysis (separate cache, non-blocking)
        try:
            pulse_response["sectors_summary"] = sectors_summary
            analysis = generate_analysis(
                pulse_response,
                mode=mode,
                force=force_refresh,
            )
            pulse_response["analysis"] = analysis
        except Exception as e:
            logger.warning("Analysis generation failed: %s", e)
            pulse_response["analysis"] = None

        # 15. Alert evaluation (non-blocking, best-effort)
        try:
            from services.market_pulse_alerts import evaluate_alerts
            fired_alerts = evaluate_alerts(pulse_response)
            if fired_alerts:
                pulse_response["fired_alerts"] = fired_alerts
        except Exception as e:
            logger.debug("Alert evaluation skipped: %s", e)

        # 16. Auto-log HIGH conviction signals (non-blocking)
        try:
            from services.market_pulse_journal import auto_log_ideas
            auto_log_ideas(
                equity_ideas,
                regime=regime,
                quality_score=market_quality,
                execution_score=exec_score,
                mode=mode,
            )
        except Exception as e:
            logger.debug("Journal auto-log skipped: %s", e)

        # Cache the full scored response
        _response_cache[mode] = pulse_response
        _response_cache_ts[mode] = time.time()

        return jsonify({"status": "success", "data": pulse_response})

    except Exception as e:
        logger.exception("Market pulse API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


def _direction(rules: list[dict]) -> str:
    """Derive overall direction from rules."""
    positive = sum(1 for r in rules if r.get("impact") == "positive")
    negative = sum(1 for r in rules if r.get("impact") == "negative")
    if positive > negative:
        return "healthy" if positive >= 3 else "improving"
    elif negative > positive:
        return "weakening" if negative >= 3 else "risk-off"
    return "neutral"


# ══════════════════════════════════════════════════════════════════
# Phase 1: Progressive API Endpoints (split for fast loading)
# ══════════════════════════════════════════════════════════════════

_prewarm_done = False


def prewarm_market_pulse():
    """Pre-warm the data cache on app startup. Called once."""
    global _prewarm_done
    if _prewarm_done:
        return
    _prewarm_done = True
    import threading

    def _warm():
        try:
            from services.market_pulse_data import fetch_market_data
            logger.info("Market Pulse: pre-warming cache...")
            fetch_market_data(mode="swing", force_refresh=False)
            logger.info("Market Pulse: cache pre-warmed successfully")
        except Exception as e:
            logger.warning("Market Pulse: pre-warm failed: %s", e)

    threading.Thread(target=_warm, daemon=True, name="mp-prewarm").start()


@market_pulse_bp.record_once
def _on_register(state):
    """Trigger pre-warm when blueprint is registered with the app."""
    app = state.app

    @app.before_request
    def _first_request_prewarm():
        prewarm_market_pulse()
        # Remove this handler after first invocation to avoid re-running
        app.before_request_funcs[None].remove(_first_request_prewarm)


@market_pulse_bp.route("/api/core", methods=["GET"])
def market_pulse_core_api():
    """Fast-path endpoint — returns scores, decision, regime, ticker only.

    This is the first thing the frontend should fetch. The hero card,
    score panels, and ticker bar can render immediately from this data.
    """
    mode = request.args.get("mode", "swing")
    if mode not in ("swing", "day"):
        mode = "swing"

    # Check response cache first
    core_cache_key = f"core:{mode}"
    cached_core = _response_cache.get(core_cache_key)
    cached_core_ts = _response_cache_ts.get(core_cache_key, 0)
    if cached_core and (time.time() - cached_core_ts) < _RESPONSE_CACHE_TTL:
        return jsonify({"status": "success", "data": cached_core})

    try:
        from services.market_pulse_data import (
            compute_constituent_breadth_snapshot,
            fetch_market_data,
        )
        from services.market_pulse_scoring import (
            apply_intraday_tape_overrides,
            assess_intraday_tape,
            classify_regime,
            compute_directional_bias,
            compute_market_quality,
            get_decision,
            get_trade_decision,
            resolve_execution_regime,
            score_breadth,
            score_macro,
            score_momentum,
            score_trend,
            score_volatility,
        )
        from services.market_pulse_execution import (
            compute_execution_window_day,
            compute_execution_window_swing,
        )

        data = fetch_market_data(mode=mode, force_refresh=False)

        ni = data.get("nifty_indicators", {})
        bi = data.get("banknifty_indicators", {})
        vi = data.get("vix_indicators", {})
        ui = data.get("usdinr_indicators", {})
        ticker = data.get("ticker", {})
        sector_quotes = data.get("sectors", {})
        constituent_quotes = data.get("constituent_quotes", {})
        use_live = mode == "day"

        nifty_ltp = (_quote_ltp(ticker.get("NIFTY")) if use_live else None) or ni.get("ltp")
        bn_ltp = (_quote_ltp(ticker.get("BANKNIFTY")) if use_live else None) or bi.get("ltp")
        vix_current = (_quote_ltp(ticker.get("INDIAVIX")) if use_live else None) or vi.get("current")
        nifty_change = _quote_change_pct(ticker.get("NIFTY"))
        bn_change = _quote_change_pct(ticker.get("BANKNIFTY"))
        vix_change = _quote_change_pct(ticker.get("INDIAVIX"))

        breadth_snapshot = compute_constituent_breadth_snapshot(
            data.get("constituent_data", {}),
            constituent_quotes=constituent_quotes,
            use_live_ltp=use_live,
        )
        ma_breadth = breadth_snapshot.get("moving_averages", {})
        ad_data = breadth_snapshot.get("advance_decline", {})
        hl_data = breadth_snapshot.get("annual_extremes", {})

        momentum_data = _compute_momentum_data(
            data.get("sector_histories", {}),
            data.get("constituent_data", {}),
            sector_quotes=sector_quotes,
            constituent_quotes=constituent_quotes,
            use_live_inputs=use_live,
        )

        event_hours, event_type = _compute_event_proximity(data.get("events", []))

        vol_score, vol_rules = score_volatility(
            vix_current=vix_current, vix_slope_5d=vi.get("slope_5d"),
            vix_percentile=vi.get("percentile_1y"), pcr=data.get("pcr"),
        )
        mom_score, mom_rules = score_momentum(
            sectors_above_20d=momentum_data["sectors_above_20d"],
            total_sectors=momentum_data["total_sectors"],
            leadership_spread=momentum_data["leadership_spread"],
            higher_highs_pct=momentum_data["higher_highs_pct"],
            rotation_diversity=momentum_data["rotation_diversity"],
        )
        trend_score, trend_rules = score_trend(
            nifty_ltp=nifty_ltp, sma_20=ni.get("sma_20"), sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"), banknifty_ltp=bn_ltp,
            banknifty_sma50=bi.get("sma_50"), rsi=ni.get("rsi_14"),
            slope_50d=ni.get("slope_50d"), slope_200d=ni.get("slope_200d"),
        )
        breadth_score, breadth_rules = score_breadth(
            ad_ratio=ad_data.get("ad_ratio"),
            pct_above_50d=ma_breadth.get("pct_above_50d"),
            pct_above_200d=ma_breadth.get("pct_above_200d"),
            highs_52w=hl_data.get("highs_52w"), lows_52w=hl_data.get("lows_52w"),
            ad_advances=ad_data.get("advances"), ad_declines=ad_data.get("declines"),
            ad_unchanged=ad_data.get("unchanged"),
            above_50d_count=ma_breadth.get("above_50d"),
            above_50d_total=ma_breadth.get("eligible_50d"),
            above_200d_count=ma_breadth.get("above_200d"),
            above_200d_total=ma_breadth.get("eligible_200d"),
            highs_total=hl_data.get("eligible_52w"),
            breadth_label=breadth_snapshot.get("scope", "Nifty 50"),
        )

        sector_returns_1d = [
            _quote_change_pct(sector_quotes.get(key)) for key in SECTOR_INDICES
        ]
        tape = assess_intraday_tape(
            nifty_change_pct=nifty_change, banknifty_change_pct=bn_change,
            vix_change_pct=vix_change, sector_returns_1d=sector_returns_1d,
            advances=ad_data.get("advances"), declines=ad_data.get("declines"),
            unchanged=ad_data.get("unchanged"),
        )
        overrides = apply_intraday_tape_overrides(
            mode=mode, trend_score=trend_score, trend_rules=trend_rules,
            momentum_score=mom_score, momentum_rules=mom_rules,
            breadth_score=breadth_score, breadth_rules=breadth_rules, tape=tape,
        )
        trend_score = overrides["trend_score"]
        trend_rules = overrides["trend_rules"]
        mom_score = overrides["momentum_score"]
        mom_rules = overrides["momentum_rules"]
        breadth_score = overrides["breadth_score"]
        breadth_rules = overrides["breadth_rules"]

        # Macro (simplified — skip USDINR correlation for speed)
        macro_score, macro_rules = score_macro(
            usdinr_slope_5d=ui.get("slope_5d"), usdinr_slope_20d=ui.get("slope_20d"),
            vix_current=vix_current, event_hours_away=event_hours,
            event_type=event_type, nifty_usdinr_corr=None,
            institutional_flows=data.get("institutional_flows"),
        )

        scores_dict = {
            "volatility": vol_score, "momentum": mom_score,
            "trend": trend_score, "breadth": breadth_score, "macro": macro_score,
        }
        quality = compute_market_quality(scores_dict)
        quality_decision = get_decision(quality)
        regime = classify_regime(
            nifty_ltp=nifty_ltp, sma_20=ni.get("sma_20"), sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"), slope_50d=ni.get("slope_50d"),
            vix_current=vix_current,
        )

        exec_score, exec_details = compute_execution_window_swing()
        if mode == "day":
            day_score, day_details = compute_execution_window_day(data)
            exec_score = int((exec_score + day_score) / 2)

        dir_bias = compute_directional_bias(
            regime=regime, trend_score=trend_score, momentum_score=mom_score,
            breadth_score=breadth_score, vix_current=vix_current,
            institutional_flows=data.get("institutional_flows"),
            mode=mode, intraday_tape=tape,
        )
        exec_regime = resolve_execution_regime(
            mode=mode, structural_regime=regime,
            directional_bias=str(dir_bias["bias"]),
            bias_confidence=int(dir_bias["confidence"]),
        )
        decision = get_trade_decision(
            market_quality_score=quality, execution_score=exec_score,
            directional_bias=str(dir_bias["bias"]),
            bias_confidence=int(dir_bias["confidence"]),
        )

        # Multi-timeframe confluence (Phase 5)
        confluence = _compute_confluence(regime, exec_regime, dir_bias, quality, mode)

        # Position sizing (Phase 8)
        try:
            from services.market_pulse_risk import compute_position_sizing
            risk_ctx = compute_position_sizing(
                quality_score=quality, execution_score=exec_score,
                vix_current=vix_current, regime=regime,
                directional_bias=str(dir_bias["bias"]),
                bias_confidence=int(dir_bias["confidence"]),
            )
        except Exception:
            risk_ctx = None

        # Market levels
        market_levels = {
            "NIFTY": _resolve_previous_day_levels(data.get("nifty_history"), ticker.get("NIFTY")),
            "SENSEX": _resolve_previous_day_levels(data.get("sensex_history"), ticker.get("SENSEX")),
            "BANKNIFTY": _resolve_previous_day_levels(data.get("banknifty_history"), ticker.get("BANKNIFTY")),
        }

        snapshot_ts = data.get("updated_at")
        updated_at = (
            datetime.fromtimestamp(snapshot_ts).isoformat()
            if isinstance(snapshot_ts, (int, float))
            else datetime.now().isoformat()
        )

        response = {
            "decision": decision,
            "quality_decision": quality_decision,
            "market_quality_score": quality,
            "execution_window_score": exec_score,
            "mode": mode,
            "regime": regime,
            "execution_regime": exec_regime,
            "directional_bias": dir_bias,
            "confluence": confluence,
            "scores": {
                "volatility": {"score": vol_score, "weight": CATEGORY_WEIGHTS["volatility"], "direction": _direction(vol_rules), "rules": vol_rules},
                "momentum": {"score": mom_score, "weight": CATEGORY_WEIGHTS["momentum"], "direction": _direction(mom_rules), "rules": mom_rules},
                "trend": {"score": trend_score, "weight": CATEGORY_WEIGHTS["trend"], "direction": _direction(trend_rules), "rules": trend_rules},
                "breadth": {"score": breadth_score, "weight": CATEGORY_WEIGHTS["breadth"], "direction": _direction(breadth_rules), "rules": breadth_rules},
                "macro": {"score": macro_score, "weight": CATEGORY_WEIGHTS["macro"], "direction": _direction(macro_rules), "rules": macro_rules},
            },
            "ticker": ticker,
            "market_levels": market_levels,
            "risk_context": risk_ctx,
            "intraday_tape": tape,
            "updated_at": updated_at,
            "cache_ttl": CACHE_TTL_SECONDS,
        }

        # Cache the full core response
        _response_cache[core_cache_key] = response
        _response_cache_ts[core_cache_key] = time.time()

        return jsonify({"status": "success", "data": response})
    except Exception as e:
        logger.exception("Market Pulse core API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


def _compute_confluence(
    regime: str,
    exec_regime: str,
    dir_bias: dict,
    quality: int,
    mode: str,
) -> dict[str, Any]:
    """Phase 5: Multi-timeframe confluence matrix."""
    structural = regime
    intraday = exec_regime
    bias = dir_bias.get("bias", "NEUTRAL")
    confidence = dir_bias.get("confidence", 50)

    # 2×2 matrix: structural direction × intraday direction
    struct_bullish = structural == "uptrend"
    struct_bearish = structural == "downtrend"
    intra_bullish = intraday == "uptrend" or bias == "LONG"
    intra_bearish = intraday == "downtrend" or bias == "SHORT"

    if struct_bullish and intra_bullish:
        level = "HIGH"
        action = "Aggressive"
        label = "SWING ↑ + DAY ↑"
        color = "green"
    elif struct_bearish and intra_bearish:
        level = "HIGH"
        action = "Aggressive Short"
        label = "SWING ↓ + DAY ↓"
        color = "red"
    elif struct_bullish and intra_bearish:
        level = "LOW"
        action = "Wait / Reduce"
        label = "SWING ↑ + DAY ↓"
        color = "amber"
    elif struct_bearish and intra_bullish:
        level = "LOW"
        action = "Counter-Trend / Caution"
        label = "SWING ↓ + DAY ↑"
        color = "amber"
    else:
        level = "MEDIUM"
        action = "Selective"
        label = "Mixed signals"
        color = "cyan"

    # Adjust with quality
    if quality < 40 and level == "HIGH":
        level = "MEDIUM"
        action = "Cautious"

    return {
        "level": level,
        "action": action,
        "label": label,
        "color": color,
        "structural_regime": structural,
        "intraday_regime": intraday,
        "bias": bias,
        "confidence": confidence,
    }


# ══════════════════════════════════════════════════════════════════
# Phase 2: Intraday Context Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/intraday", methods=["GET"])
def market_pulse_intraday_api():
    """Intraday trading context — OR, IB, VWAP, ADR, session phase."""
    try:
        from services.market_pulse_data import fetch_market_data
        from services.market_pulse_intraday import compute_intraday_context

        mode = request.args.get("mode", "day")
        data = fetch_market_data(mode=mode, force_refresh=False)

        ticker = data.get("ticker", {})
        results: dict[str, Any] = {}

        for symbol in ("NIFTY", "BANKNIFTY"):
            quote = ticker.get(symbol, {})
            intraday_hist = data.get(f"intraday_{symbol.lower()}")

            # Also try from intraday history cache if main data doesn't have it
            current_high = quote.get("high") if isinstance(quote.get("high"), (int, float)) else None
            current_low = quote.get("low") if isinstance(quote.get("low"), (int, float)) else None

            daily_hist = data.get(
                f"{'nifty' if symbol == 'NIFTY' else 'banknifty'}_history"
            )

            results[symbol] = compute_intraday_context(
                symbol=symbol,
                intraday_bars=intraday_hist,
                daily_history=daily_hist,
                current_high=current_high,
                current_low=current_low,
            )

        return jsonify({"status": "success", "data": results})
    except Exception as e:
        logger.exception("Intraday context API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 3: Options Greeks Dashboard Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/greeks", methods=["GET"])
def market_pulse_greeks_api():
    """Options Greeks dashboard — GEX, IV, skew, dealer positioning."""
    try:
        from services.market_pulse_greeks import fetch_options_dashboard
        result = fetch_options_dashboard(["NIFTY", "BANKNIFTY"])
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger.exception("Greeks API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 4: Global Correlation Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/global", methods=["GET"])
def market_pulse_global_api():
    """Global/cross-market context — correlations, gap analysis."""
    try:
        from services.market_pulse_data import fetch_market_data
        from services.market_pulse_global import fetch_global_context

        data = fetch_market_data(mode="swing", force_refresh=False)
        result = fetch_global_context(
            ticker=data.get("ticker"),
            nifty_history=data.get("nifty_history"),
        )
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger.exception("Global context API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 6: Alerts Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/alerts", methods=["GET"])
def market_pulse_alerts_api():
    """Get alert history and current rules."""
    try:
        from services.market_pulse_alerts import get_alert_history, get_alert_rules
        return jsonify({
            "status": "success",
            "data": {
                "history": get_alert_history(),
                "rules": get_alert_rules(),
            },
        })
    except Exception as e:
        logger.exception("Alerts API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 7: Trade Journal Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/journal", methods=["GET"])
def market_pulse_journal_api():
    """Signal history and win-rate stats."""
    try:
        from services.market_pulse_journal import get_signal_history, get_win_rate_stats

        days = int(request.args.get("days", 30))
        return jsonify({
            "status": "success",
            "data": {
                "signals": get_signal_history(days=days),
                "stats": get_win_rate_stats(days=days),
            },
        })
    except Exception as e:
        logger.exception("Journal API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 8: Risk Context Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/risk", methods=["GET"])
def market_pulse_risk_api():
    """Position sizing and risk context."""
    try:
        from services.market_pulse_data import fetch_market_data
        from services.market_pulse_risk import compute_position_sizing
        from services.market_pulse_scoring import (
            classify_regime, compute_directional_bias, compute_market_quality,
        )

        data = fetch_market_data(mode="swing", force_refresh=False)
        ni = data.get("nifty_indicators", {})
        vi = data.get("vix_indicators", {})
        ticker = data.get("ticker", {})
        vix = _quote_ltp(ticker.get("INDIAVIX")) or vi.get("current") or 15

        result = compute_position_sizing(
            quality_score=50,  # Default; full computation done by /api/core
            execution_score=50,
            vix_current=vix,
            regime="chop",
            directional_bias="NEUTRAL",
        )
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger.exception("Risk API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 9: Institutional Flow Intelligence
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/institutional", methods=["GET"])
def market_pulse_institutional_api():
    """FII/DII daily flows, streaks, F&O participant OI, and heatmap."""
    try:
        from services.market_pulse_institutional import fetch_institutional_context
        result = fetch_institutional_context()
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger.exception("Institutional API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 9: Fundamental Quality Scoring
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/fundamentals", methods=["GET"])
def market_pulse_fundamentals_api():
    """Quality scores, key ratios, and shareholding for symbols."""
    try:
        from services.market_pulse_fundamentals import batch_fundamentals

        symbols_param = request.args.get("symbols", "")
        if not symbols_param:
            return jsonify({"status": "error", "message": "symbols parameter required"}), 400

        symbols = [
            {"symbol": s.strip().upper(), "exchange": "NSE"}
            for s in symbols_param.split(",")
            if s.strip()
        ]

        if not symbols:
            return jsonify({"status": "error", "message": "No valid symbols provided"}), 400

        result = batch_fundamentals(symbols)
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger.exception("Fundamentals API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Phase 9: Sector Performance & Rotation
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/sectors", methods=["GET"])
def market_pulse_sectors_api():
    """Sector heatmap, relative strength, and rotation signals."""
    try:
        from services.market_pulse_sectors import fetch_sector_context
        result = fetch_sector_context()
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger.exception("Sectors API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# Health Endpoint
# ══════════════════════════════════════════════════════════════════

@market_pulse_bp.route("/api/health", methods=["GET"])
def market_pulse_health_api():
    """Health check — cache age, data freshness, component status."""
    try:
        from services.market_pulse_data import _cache_ts, _cache

        now = time.time()
        cache_age = round(now - _cache_ts, 1) if _cache_ts else None
        has_data = bool(_cache)

        components = {
            "data_cache": "warm" if has_data and cache_age and cache_age < 120 else "cold",
            "cache_age_seconds": cache_age,
            "has_nifty_history": "nifty_history" in _cache if _cache else False,
            "has_ticker": bool(_cache.get("ticker")) if _cache else False,
            "prewarm_done": _prewarm_done,
        }

        return jsonify({"status": "success", "data": components})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
