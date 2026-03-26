"""
Rule-based equity and F&O screener.
Screens Nifty 50 constituents based on market regime.
"""

import logging
import math
from typing import Any

import pandas as pd

from services.market_pulse_config import EXECUTION_SWING

logger = logging.getLogger(__name__)

# Screening thresholds
MIN_DATA_POINTS = 25        # Minimum history length required
ATR_PERIOD = 14             # ATR calculation period
SMA_20_PERIOD = 20          # 20-day moving average
SMA_50_PERIOD = 50          # 50-day moving average
SMA_200_PERIOD = 200        # 200-day moving average
HIGH_20D_LOOKBACK = 20      # 20-day high lookback
RS_OUTPERFORMANCE_THRESHOLD = 2.0  # RS % to consider strong
ATR_MULTIPLIER_SL = 1.5     # ATR multiplier for stop loss (uptrend)
ATR_MULTIPLIER_TARGET = 2.5  # ATR multiplier for target (uptrend)
WEAK_RS_THRESHOLD = -2.0    # Threshold for weak relative strength
CHOP_SMA_THRESHOLD = 0.01   # Within 1% of 20d SMA for consolidation
CHOP_RETURN_THRESHOLD = 2.0  # Low volatility for consolidation
MIN_RISK_REWARD = 1.25
DAY_CONFIRM_RVOL = 1.15
DAY_HIGH_CONVICTION_RVOL = 1.5


def _format_rs_label(value: float) -> str:
    return f"{value:+.2f}% vs NIFTY"


def _format_liquidity_note(
    *,
    mode: str,
    volume_vs_10d_avg: float | None,
    rvol: float | None,
    vwap_distance_pct: float | None,
    delivery_pct: float | None = None,
    avg_delivery_pct_10d: float | None = None,
    delivery_vs_10d_avg: float | None = None,
) -> str | None:
    parts: list[str] = []
    if isinstance(volume_vs_10d_avg, (int, float)):
        parts.append(f"Vol {volume_vs_10d_avg:.2f}x 10D avg")
    if mode == "day":
        if isinstance(vwap_distance_pct, (int, float)):
            parts.append(f"VWAP {vwap_distance_pct:+.2f}%")
        if isinstance(rvol, (int, float)):
            parts.append(f"RVOL {rvol:.2f}x")
    else:
        if isinstance(delivery_pct, (int, float)):
            parts.append(f"Delivery {delivery_pct:.1f}%")
        if isinstance(delivery_vs_10d_avg, (int, float)):
            parts.append(f"{delivery_vs_10d_avg:.2f}x 10D deliv")
        elif isinstance(avg_delivery_pct_10d, (int, float)):
            parts.append(f"10D deliv avg {avg_delivery_pct_10d:.1f}%")
        else:
            parts.append("Delivery unavailable")
    return " | ".join(parts) if parts else None


def _has_delivery_confirmation(
    delivery_pct: float | None,
    avg_delivery_pct_10d: float | None,
    delivery_vs_10d_avg: float | None,
) -> bool:
    """Strong cash delivery confirms a swing move is more than intraday noise."""
    if not isinstance(delivery_pct, (int, float)):
        return False
    if isinstance(delivery_vs_10d_avg, (int, float)):
        return delivery_vs_10d_avg >= 1.05 and delivery_pct >= 45
    if isinstance(avg_delivery_pct_10d, (int, float)):
        return delivery_pct >= max(avg_delivery_pct_10d + 2.0, 45)
    return delivery_pct >= 50


def _passes_intraday_confirmation(signal: str, intraday: dict[str, Any] | None) -> bool:
    """Validate day-mode entries with session VWAP + RVOL."""
    if not intraday:
        return False

    rvol = intraday.get("rvol")
    if not isinstance(rvol, (int, float)) or rvol < DAY_CONFIRM_RVOL:
        return False

    if signal == "BUY":
        return bool(intraday.get("above_vwap"))
    if signal == "SELL":
        return bool(intraday.get("below_vwap"))
    return True


def _quote_average_price(quote: dict[str, Any] | None) -> float | None:
    """Read a broker quote's session average price as a VWAP-style fallback."""
    if not isinstance(quote, dict):
        return None
    for key in ("average_price", "avg_price", "vwap"):
        value = quote.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _compute_risk_reward(
    signal: str,
    entry: float | None,
    stop_loss: float | None,
    target: float | None,
) -> float | None:
    """Compute reward-to-risk ratio for actionable setups."""
    if None in (entry, stop_loss, target):
        return None

    if signal == "BUY":
        risk = entry - stop_loss
        reward = target - entry
    elif signal == "SELL":
        risk = stop_loss - entry
        reward = entry - target
    else:
        return None

    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 2)


def _enforce_min_risk_reward(
    signal: str,
    entry: float,
    stop_loss: float,
    target: float,
) -> tuple[float, float | None]:
    """Adjust target so actionable setups maintain a sane reward-to-risk ratio."""
    rr = _compute_risk_reward(signal, entry, stop_loss, target)
    if rr is not None and rr >= MIN_RISK_REWARD:
        return round(target, 2), rr

    if signal == "BUY":
        risk = entry - stop_loss
        adjusted_target = entry + max(risk * MIN_RISK_REWARD, 0)
    else:
        risk = stop_loss - entry
        adjusted_target = max(entry - risk * MIN_RISK_REWARD, 0)

    rr = _compute_risk_reward(signal, entry, stop_loss, adjusted_target)
    return round(adjusted_target, 2), rr


def _round_to_step(price: float, step: int) -> int:
    """Round a price to the nearest option strike step."""
    return int(round(price / step) * step)


def _round_down_to_step(price: float, step: int) -> int:
    """Round down to the nearest listed option strike."""
    return int(math.floor(price / step) * step)


def _round_up_to_step(price: float, step: int) -> int:
    """Round up to the nearest listed option strike."""
    return int(math.ceil(price / step) * step)


def _spread_width(step: int, mode: str) -> int:
    """Return a practical hedge width for day vs swing structures."""
    if step == 50:
        return 100 if mode == "day" else 200
    return 200 if mode == "day" else 500


def _credit_offset_pct(mode: str, vix: float | None) -> float:
    """Choose a sensible OTM distance for short-vol structures."""
    pct = 0.008 if mode == "day" else 0.015
    if vix is None:
        return pct
    if vix >= 25:
        return pct + (0.005 if mode == "day" else 0.010)
    if vix >= 20:
        return pct + (0.003 if mode == "day" else 0.006)
    if vix <= 14:
        return max(pct - (0.002 if mode == "day" else 0.004), 0.004)
    return pct


def _offset_steps(ltp: float, step: int, pct: float) -> int:
    """Convert an OTM percentage buffer into exchange strike steps."""
    return max(1, int(math.ceil((ltp * pct) / step)))


def _nifty_swing_condor_pct(vix: float | None) -> float:
    """Use a wider expected-move buffer for swing NIFTY condors."""
    if vix is None or vix <= 0:
        return 0.035

    weekly_expected_move = (vix / 100) * math.sqrt(7 / 365)
    return max(0.025, weekly_expected_move * 1.15)


def _validate_fno_structure(
    strategy: str,
    ltp: float,
    short_put: int | None = None,
    long_put: int | None = None,
    short_call: int | None = None,
    long_call: int | None = None,
    buy_put: int | None = None,
    sell_put: int | None = None,
    buy_call: int | None = None,
    sell_call: int | None = None,
) -> bool:
    """Sanity-check that generated strikes make structural sense around spot."""
    if strategy == "Bull Call Spread":
        return (
            buy_call is not None
            and sell_call is not None
            and buy_call <= ltp
            and sell_call > buy_call
        )
    if strategy == "Bear Put Spread":
        return (
            buy_put is not None
            and sell_put is not None
            and buy_put >= ltp
            and sell_put < buy_put
        )
    if strategy == "Put Credit Spread":
        return (
            short_put is not None
            and long_put is not None
            and short_put < ltp
            and long_put < short_put
        )
    if strategy == "Call Credit Spread":
        return (
            short_call is not None
            and long_call is not None
            and short_call > ltp
            and long_call > short_call
        )
    if strategy == "Iron Condor":
        return (
            short_put is not None
            and long_put is not None
            and short_call is not None
            and long_call is not None
            and long_put < short_put < ltp < short_call < long_call
        )
    return True


def _format_fno_strikes(
    strategy: str,
    ltp: float,
    step: int,
    *,
    underlying: str = "",
    mode: str = "swing",
    vix: float | None = None,
) -> str:
    """Generate trader-sane strike strings for the chosen options structure."""
    atm = _round_to_step(ltp, step)
    call_atm = max(_round_down_to_step(ltp, step), step)
    put_atm = max(_round_up_to_step(ltp, step), step)
    width = _spread_width(step, mode)
    credit_steps = _offset_steps(ltp, step, _credit_offset_pct(mode, vix))
    short_put = max(atm - (credit_steps * step), step)
    long_put = max(short_put - width, step)
    short_call = atm + (credit_steps * step)
    long_call = short_call + width

    if strategy == "Iron Condor" and underlying == "NIFTY" and mode == "swing":
        condor_steps = max(1, int(round((ltp * _nifty_swing_condor_pct(vix)) / step)))
        short_put = max(atm - (condor_steps * step), step)
        long_put = max(short_put - width, step)
        short_call = atm + (condor_steps * step)
        long_call = short_call + width

    if strategy == "Buy Calls":
        return f"Buy {call_atm}CE"
    if strategy == "Buy Puts":
        return f"Buy {put_atm}PE"
    if strategy == "Bull Call Spread":
        if not _validate_fno_structure(
            strategy,
            ltp,
            buy_call=call_atm,
            sell_call=call_atm + width,
        ):
            safe_buy_call = max(_round_down_to_step(ltp, step), step)
            return f"Buy {safe_buy_call}CE / Sell {safe_buy_call + width}CE"
        return f"Buy {call_atm}CE / Sell {call_atm + width}CE"
    if strategy == "Bear Put Spread":
        if not _validate_fno_structure(
            strategy,
            ltp,
            buy_put=put_atm,
            sell_put=max(put_atm - width, step),
        ):
            safe_buy_put = max(_round_up_to_step(ltp, step), step)
            return f"Buy {safe_buy_put}PE / Sell {max(safe_buy_put - width, step)}PE"
        return f"Buy {put_atm}PE / Sell {max(put_atm - width, step)}PE"
    if strategy == "Put Credit Spread":
        if not _validate_fno_structure(strategy, ltp, short_put=short_put, long_put=long_put):
            short_put = max(_round_down_to_step(ltp * 0.97, step), step)
            long_put = max(short_put - width, step)
        return f"Sell {short_put}PE / Buy {long_put}PE"
    if strategy == "Call Credit Spread":
        if not _validate_fno_structure(strategy, ltp, short_call=short_call, long_call=long_call):
            short_call = _round_up_to_step(ltp * 1.03, step)
            long_call = short_call + width
        return f"Sell {short_call}CE / Buy {long_call}CE"
    if strategy == "Iron Condor":
        if not _validate_fno_structure(
            strategy,
            ltp,
            short_put=short_put,
            long_put=long_put,
            short_call=short_call,
            long_call=long_call,
        ):
            fallback_steps = max(1, int(round((ltp * 0.04) / step)))
            short_put = max(atm - (fallback_steps * step), step)
            long_put = max(short_put - width, step)
            short_call = atm + (fallback_steps * step)
            long_call = short_call + width
        return (
            f"Buy {long_put}PE / Sell {short_put}PE / "
            f"Sell {short_call}CE / Buy {long_call}CE"
        )
    return f"{atm}"


def screen_equities(
    constituent_data: dict[str, dict],
    regime: str,
    nifty_history: pd.DataFrame | None,
    *,
    mode: str = "swing",
    live_quotes: dict[str, dict] | None = None,
    benchmark_change_pct: float | None = None,
    intraday_context: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """Screen Nifty 50 stocks for trade ideas based on regime.

    Args:
        constituent_data: {symbol: {"history": DataFrame, "sector": str}}
        regime: "uptrend" | "downtrend" | "chop"
        nifty_history: Nifty 50 OHLCV for relative strength calc

    Returns: List of equity ideas sorted by conviction.
    """
    ideas = []

    nifty_return_5d = None
    if nifty_history is not None and len(nifty_history) >= 5:
        nc = nifty_history["close"]
        nifty_return_5d = (nc.iloc[-1] - nc.iloc[-5]) / nc.iloc[-5] * 100

    for symbol, data in constituent_data.items():
        hist = data.get("history")
        sector = data.get("sector", "")
        if hist is None or len(hist) < MIN_DATA_POINTS:
            continue

        closes = hist["close"]
        quote = (live_quotes or {}).get(symbol) or {}
        live_ltp = quote.get("ltp") if isinstance(quote.get("ltp"), (int, float)) else None
        live_change_pct = (
            quote.get("change_pct")
            if isinstance(quote.get("change_pct"), (int, float))
            else None
        )
        live_volume = quote.get("volume") if isinstance(quote.get("volume"), (int, float)) else None
        delivery_pct = data.get("delivery_pct")
        avg_delivery_pct_10d = data.get("avg_delivery_pct_10d")
        delivery_vs_10d_avg = data.get("delivery_vs_10d_avg")
        delivery_confirmed = _has_delivery_confirmation(
            delivery_pct,
            avg_delivery_pct_10d,
            delivery_vs_10d_avg,
        )
        ltp = float(live_ltp) if mode == "day" and live_ltp is not None else float(closes.iloc[-1])
        intraday = dict((intraday_context or {}).get(symbol) or {})

        # Compute indicators
        sma_20 = closes.tail(SMA_20_PERIOD).mean()
        sma_50 = closes.tail(SMA_50_PERIOD).mean() if len(closes) >= SMA_50_PERIOD else None
        sma_200 = closes.tail(SMA_200_PERIOD).mean() if len(closes) >= SMA_200_PERIOD else None
        high_20d = closes.iloc[-HIGH_20D_LOOKBACK-1:-1].max() if len(closes) >= HIGH_20D_LOOKBACK + 1 else closes.max()
        avg_volume_10d = None
        volume_vs_10d_avg = None
        if "volume" in hist.columns:
            volumes = pd.to_numeric(hist["volume"], errors="coerce").dropna()
            if len(volumes) >= 10:
                avg_volume_10d = float(volumes.tail(10).mean())
                ref_volume = float(live_volume) if mode == "day" and isinstance(live_volume, (int, float)) else float(volumes.iloc[-1])
                if avg_volume_10d > 0:
                    volume_vs_10d_avg = round(ref_volume / avg_volume_10d, 2)

        intraday_rvol = intraday.get("rvol")
        if not isinstance(intraday_rvol, (int, float)) and isinstance(volume_vs_10d_avg, (int, float)):
            intraday_rvol = volume_vs_10d_avg

        intraday_vwap_distance_pct = intraday.get("vwap_distance_pct")
        if not isinstance(intraday_vwap_distance_pct, (int, float)):
            average_price = _quote_average_price(quote)
            if isinstance(average_price, (int, float)) and average_price > 0 and live_ltp is not None:
                intraday_vwap_distance_pct = round(
                    ((float(live_ltp) - average_price) / average_price) * 100,
                    2,
                )
        if isinstance(intraday_rvol, (int, float)):
            intraday["rvol"] = intraday_rvol
        if isinstance(intraday_vwap_distance_pct, (int, float)):
            intraday["vwap_distance_pct"] = intraday_vwap_distance_pct
            intraday.setdefault("above_vwap", intraday_vwap_distance_pct > 0)
            intraday.setdefault("below_vwap", intraday_vwap_distance_pct < 0)

        # 5d return for relative strength
        stock_return_5d = (ltp - closes.iloc[-5]) / closes.iloc[-5] * 100 if len(closes) >= 5 else 0
        if mode == "day" and live_change_pct is not None and benchmark_change_pct is not None:
            rs_vs_nifty = float(live_change_pct) - float(benchmark_change_pct)
        else:
            rs_vs_nifty = stock_return_5d - (nifty_return_5d or 0)

        # ATR for stop loss
        if len(hist) >= ATR_PERIOD:
            tr = pd.concat([
                hist["high"] - hist["low"],
                (hist["high"] - hist["close"].shift(1)).abs(),
                (hist["low"] - hist["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = tr.tail(ATR_PERIOD).mean()
        else:
            atr = (hist["high"] - hist["low"]).tail(5).mean()

        idea = {
            "symbol": symbol,
            "sector": sector,
            "ltp": round(ltp, 2),
            "rs_vs_nifty": round(rs_vs_nifty, 2),
            "rs_label": _format_rs_label(round(rs_vs_nifty, 2)),
            "risk_reward": None,
            "volume_vs_10d_avg": volume_vs_10d_avg,
            "rvol": intraday_rvol,
            "vwap_distance_pct": intraday_vwap_distance_pct,
            "delivery_pct": delivery_pct,
            "avg_delivery_pct_10d": avg_delivery_pct_10d,
            "delivery_vs_10d_avg": delivery_vs_10d_avg,
            "liquidity_note": _format_liquidity_note(
                mode=mode,
                volume_vs_10d_avg=volume_vs_10d_avg,
                rvol=intraday_rvol,
                vwap_distance_pct=intraday_vwap_distance_pct,
                delivery_pct=delivery_pct,
                avg_delivery_pct_10d=avg_delivery_pct_10d,
                delivery_vs_10d_avg=delivery_vs_10d_avg,
            ),
        }

        day_leadership = (
            mode == "day"
            and live_change_pct is not None
            and benchmark_change_pct is not None
            and live_change_pct > 0
            and rs_vs_nifty > 0.25
        )
        day_breakdown = (
            mode == "day"
            and live_change_pct is not None
            and benchmark_change_pct is not None
            and live_change_pct < 0
            and rs_vs_nifty < -0.25
        )

        if regime == "uptrend":
            # BUY: breakouts + pullbacks to MA + strong RS
            if ltp > high_20d and rs_vs_nifty > 0:
                entry = round(ltp, 2)
                stop_loss = round(ltp - ATR_MULTIPLIER_SL * atr, 2)
                target = round(ltp + ATR_MULTIPLIER_TARGET * atr, 2)
                target, risk_reward = _enforce_min_risk_reward(
                    "BUY", entry, stop_loss, target
                )
                if mode == "day" and not _passes_intraday_confirmation("BUY", intraday):
                    continue
                idea["signal"] = "BUY"
                idea["reason"] = "20d breakout + relative strength"
                idea["conviction"] = (
                    "HIGH"
                    if (
                        rs_vs_nifty > RS_OUTPERFORMANCE_THRESHOLD
                        and (intraday.get("rvol") or 0) >= DAY_HIGH_CONVICTION_RVOL
                    )
                    else (
                        "HIGH"
                        if mode != "day"
                        and (rs_vs_nifty > RS_OUTPERFORMANCE_THRESHOLD or delivery_confirmed)
                        else "MED"
                    )
                )
                idea["entry"] = entry
                idea["stop_loss"] = stop_loss
                idea["target"] = target
                idea["risk_reward"] = risk_reward
                if mode == "day" and intraday:
                    idea["reason"] = "20d breakout + above VWAP on expanding volume"
                elif delivery_confirmed:
                    idea["reason"] = "20d breakout + relative strength + delivery-backed"
                ideas.append(idea)
            elif day_leadership and (
                ltp >= sma_20 * 0.95
                or (
                    live_change_pct is not None
                    and live_change_pct >= 2.5
                    and rs_vs_nifty > 1.0
                )
            ):
                entry = round(ltp, 2)
                stop_candidates = [ltp - ATR_MULTIPLIER_SL * atr, closes.iloc[-1] * 0.985]
                if sma_20 and sma_20 < ltp:
                    stop_candidates.append(sma_20 * 0.985)
                valid_stops = [candidate for candidate in stop_candidates if candidate < ltp]
                stop_loss = round(max(valid_stops) if valid_stops else ltp - atr, 2)
                target = round(ltp + 2 * atr, 2)
                target, risk_reward = _enforce_min_risk_reward(
                    "BUY", entry, stop_loss, target
                )
                if not _passes_intraday_confirmation("BUY", intraday):
                    continue
                idea["signal"] = "BUY"
                idea["reason"] = "Intraday leadership + above VWAP + RVOL confirmation"
                idea["conviction"] = (
                    "HIGH"
                    if rs_vs_nifty > 1.0 and (intraday.get("rvol") or 0) >= DAY_HIGH_CONVICTION_RVOL
                    else "MED"
                )
                idea["entry"] = entry
                idea["stop_loss"] = stop_loss
                idea["target"] = target
                idea["risk_reward"] = risk_reward
                ideas.append(idea)
            elif sma_20 and abs(ltp - sma_20) / sma_20 < CHOP_SMA_THRESHOLD and ltp > (sma_50 or 0):
                entry = round(ltp, 2)
                stop_loss = round(sma_20 - atr, 2)
                target = round(ltp + 2 * atr, 2)
                target, risk_reward = _enforce_min_risk_reward(
                    "BUY", entry, stop_loss, target
                )
                if mode == "day" and not _passes_intraday_confirmation("BUY", intraday):
                    continue
                idea["signal"] = "BUY"
                idea["reason"] = "Pullback to 20d MA support"
                idea["conviction"] = "HIGH" if delivery_confirmed else "MED"
                idea["entry"] = entry
                idea["stop_loss"] = stop_loss
                idea["target"] = target
                idea["risk_reward"] = risk_reward
                if delivery_confirmed:
                    idea["reason"] = "Pullback to 20d MA support with delivery support"
                ideas.append(idea)

        elif regime == "downtrend":
            below_short_mas = ltp < sma_20 and (sma_50 is None or ltp < sma_50)
            if rs_vs_nifty < WEAK_RS_THRESHOLD and below_short_mas:
                stop_candidates = [ltp + ATR_MULTIPLIER_SL * atr]
                if sma_20 and sma_20 > ltp:
                    stop_candidates.append(sma_20 * 1.005)
                if sma_50 and sma_50 > ltp:
                    stop_candidates.append(sma_50 * 1.005)
                entry = round(ltp, 2)
                stop_loss = round(min(stop_candidates), 2)
                target = round(max(ltp - ATR_MULTIPLIER_TARGET * atr, 0), 2)
                target, risk_reward = _enforce_min_risk_reward(
                    "SELL", entry, stop_loss, target
                )
                if risk_reward is None or risk_reward < MIN_RISK_REWARD:
                    continue
                if mode == "day" and not _passes_intraday_confirmation("SELL", intraday):
                    continue
                idea["signal"] = "SELL"
                idea["reason"] = "Relative weakness + below 20d/50d breakdown"
                idea["conviction"] = (
                    "HIGH"
                    if (
                        sma_200 and ltp < sma_200
                        and (intraday.get("rvol") or 0) >= DAY_HIGH_CONVICTION_RVOL
                    )
                    else (
                        "HIGH"
                        if mode != "day"
                        and (((sma_200 is not None) and ltp < sma_200) or delivery_confirmed)
                        else "MED"
                    )
                )
                idea["entry"] = entry
                idea["stop_loss"] = stop_loss
                idea["target"] = target
                idea["risk_reward"] = risk_reward
                idea["sort_score"] = round(-rs_vs_nifty, 2)
                if mode == "day" and intraday:
                    idea["reason"] = "Breakdown + below VWAP on expanding volume"
                elif delivery_confirmed:
                    idea["reason"] = "Relative weakness + breakdown with delivery-backed distribution"
                ideas.append(idea)
            elif day_breakdown and ltp <= sma_20 * 1.03:
                stop_candidates = [ltp + ATR_MULTIPLIER_SL * atr]
                if sma_20 and sma_20 > ltp:
                    stop_candidates.append(sma_20 * 1.01)
                entry = round(ltp, 2)
                stop_loss = round(min(stop_candidates), 2)
                target = round(max(ltp - 2 * atr, 0), 2)
                target, risk_reward = _enforce_min_risk_reward(
                    "SELL", entry, stop_loss, target
                )
                if risk_reward is None or risk_reward < MIN_RISK_REWARD:
                    continue
                if not _passes_intraday_confirmation("SELL", intraday):
                    continue
                idea["signal"] = "SELL"
                idea["reason"] = "Intraday breakdown + below VWAP + RVOL confirmation"
                idea["conviction"] = (
                    "HIGH"
                    if rs_vs_nifty < -1.0 and (intraday.get("rvol") or 0) >= DAY_HIGH_CONVICTION_RVOL
                    else "MED"
                )
                idea["entry"] = entry
                idea["stop_loss"] = stop_loss
                idea["target"] = target
                idea["risk_reward"] = risk_reward
                idea["sort_score"] = round(-rs_vs_nifty, 2)
                ideas.append(idea)
            elif rs_vs_nifty < WEAK_RS_THRESHOLD and ltp < sma_20:
                idea["signal"] = "AVOID"
                idea["reason"] = "Weak RS + below 20d MA"
                idea["conviction"] = "HIGH" if ltp < (sma_50 or ltp + 1) else "MED"
                idea["entry"] = None
                idea["stop_loss"] = None
                idea["target"] = None
                idea["sort_score"] = round(-rs_vs_nifty, 2)
                ideas.append(idea)

        else:  # chop
            if sma_200 and ltp > sma_200 and abs(stock_return_5d) < CHOP_RETURN_THRESHOLD:
                idea["signal"] = "HOLD"
                idea["reason"] = "Above 200d MA, low volatility"
                idea["conviction"] = "LOW"
                idea["entry"] = None
                idea["stop_loss"] = round(sma_200 * 0.98, 2)
                idea["target"] = None
                idea["sort_score"] = abs(rs_vs_nifty)
                ideas.append(idea)

        if "sort_score" not in idea and idea.get("signal") == "BUY":
            idea["sort_score"] = round(rs_vs_nifty, 2)

    # Sort by conviction then RS
    conv_order = {"HIGH": 0, "MED": 1, "LOW": 2}
    ideas.sort(
        key=lambda x: (
            conv_order.get(x.get("conviction", "LOW"), 3),
            -x.get("sort_score", 0),
        )
    )

    for idea in ideas:
        idea.pop("sort_score", None)

    return ideas[:10]  # Top 10 ideas


def select_fno_strategy(vix: float, regime: str) -> dict[str, str]:
    """Select F&O strategy type based on VIX regime + trend.

    Args:
        vix: Current VIX level (must be >= 0)
        regime: "uptrend" | "downtrend" | "chop"

    Returns: Strategy recommendation with type and bias.

    Raises:
        ValueError: If VIX is negative or regime is invalid.
    """
    if vix < 0:
        raise ValueError("VIX cannot be negative")
    if regime not in ("uptrend", "downtrend", "chop"):
        raise ValueError(f"Invalid regime '{regime}'. Must be 'uptrend', 'downtrend', or 'chop'.")

    if vix < 15:
        if regime == "uptrend":
            return {"type": "Buy Calls", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Bear Put Spread", "bias": "bearish"}
        else:
            return {"type": "Bull Call Spread", "bias": "neutral-bullish"}
    elif vix > 20:
        if regime == "uptrend":
            return {"type": "Put Credit Spread", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Call Credit Spread", "bias": "bearish"}
        return {"type": "Iron Condor", "bias": "neutral"}
    else:
        if regime == "uptrend":
            return {"type": "Bull Call Spread", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Bear Put Spread", "bias": "bearish"}
        else:
            return {"type": "Iron Condor", "bias": "neutral"}


def _build_fno_rationale(
    instrument: str,
    strategy_regime: str,
    strategy_bias: str,
    vix: float,
    mode: str,
    directional_bias: str | None,
    bias_confidence: int | None,
    options_context: dict[str, Any] | None,
    market_levels: dict[str, Any] | None,
    intraday_context: dict[str, Any] | None,
) -> str:
    parts: list[str] = []
    if mode == "day" and directional_bias and (bias_confidence or 0) >= 65:
        parts.append(f"Day bias {directional_bias.lower()} {bias_confidence}/100")
    elif strategy_bias != "neutral":
        parts.append(f"VIX {vix:.1f} + {strategy_regime} regime")
    else:
        parts.append(f"High VIX {vix:.1f} with range-bound tape")

    if intraday_context:
        vwap_distance_pct = intraday_context.get("vwap_distance_pct")
        rvol = intraday_context.get("rvol")
        if isinstance(vwap_distance_pct, (int, float)):
            vwap_side = "above VWAP" if vwap_distance_pct >= 0 else "below VWAP"
            parts.append(f"{vwap_side} {abs(vwap_distance_pct):.2f}%")
        if isinstance(rvol, (int, float)):
            parts.append(f"RVOL {rvol:.2f}x")

    if market_levels:
        state = market_levels.get("state")
        if state == "above_pdh":
            parts.append("trading above PDH")
        elif state == "below_pdl":
            parts.append("trading below PDL")
        elif state == "inside_prior_range":
            parts.append("inside prior range")

    if options_context:
        call_wall = (options_context.get("call_wall") or {}).get("strike")
        put_wall = (options_context.get("put_wall") or {}).get("strike")
        max_pain = options_context.get("max_pain")
        wall_bits = []
        if put_wall:
            wall_bits.append(f"put wall {int(put_wall)}")
        if call_wall:
            wall_bits.append(f"call wall {int(call_wall)}")
        if max_pain:
            wall_bits.append(f"max pain {int(max_pain)}")
        if wall_bits:
            parts.append(", ".join(wall_bits))

    return " | ".join(parts)


def generate_fno_ideas(
    regime: str,
    vix: float,
    nifty_ltp: float | None,
    banknifty_ltp: float | None,
    *,
    mode: str = "swing",
    directional_bias: str | None = None,
    bias_confidence: int | None = None,
    options_context: dict[str, Any] | None = None,
    market_levels: dict[str, Any] | None = None,
    intraday_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate F&O trade ideas."""
    ideas = []
    strategy_regime = regime
    if mode == "day" and directional_bias in {"LONG", "SHORT"} and (bias_confidence or 0) >= 65:
        strategy_regime = "uptrend" if directional_bias == "LONG" else "downtrend"
    strategy = select_fno_strategy(vix, strategy_regime)

    if nifty_ltp:
        ideas.append({
            "instrument": "NIFTY",
            "strategy": strategy["type"],
            "strikes": _format_fno_strikes(
                strategy["type"],
                nifty_ltp,
                50,
                underlying="NIFTY",
                mode=mode,
                vix=vix,
            ),
            "bias": strategy["bias"],
            "rationale": _build_fno_rationale(
                "NIFTY",
                strategy_regime,
                strategy["bias"],
                vix,
                mode,
                directional_bias,
                bias_confidence,
                (options_context or {}).get("NIFTY"),
                (market_levels or {}).get("NIFTY"),
                (intraday_context or {}).get("NIFTY"),
            ),
        })

    if banknifty_ltp:
        ideas.append({
            "instrument": "BANKNIFTY",
            "strategy": strategy["type"],
            "strikes": _format_fno_strikes(
                strategy["type"],
                banknifty_ltp,
                100,
                underlying="BANKNIFTY",
                mode=mode,
                vix=vix,
            ),
            "bias": strategy["bias"],
            "rationale": _build_fno_rationale(
                "BANKNIFTY",
                strategy_regime,
                strategy["bias"],
                vix,
                mode,
                directional_bias,
                bias_confidence,
                (options_context or {}).get("BANKNIFTY"),
                (market_levels or {}).get("BANKNIFTY"),
                (intraday_context or {}).get("BANKNIFTY"),
            ),
        })

    return ideas
