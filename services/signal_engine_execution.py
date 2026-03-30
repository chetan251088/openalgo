"""
Signal Engine Execution — Phase 3

Leg builder: converts a signal + options chain into specific tradeable legs.
Order placer: submits legs via OpenAlgo's /api/v1/placeorder.

No state. Pure functions.
"""

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Strike intervals per underlying
_STRIKE_INTERVAL: dict[str, int] = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
    "SENSEX":    100,
    "FINNIFTY":  50,
}

# Default wing widths (sell–buy spread) per underlying
_WING_WIDTH: dict[str, int] = {
    "NIFTY":     200,
    "BANKNIFTY": 500,
    "SENSEX":    500,
    "FINNIFTY":  200,
}

_PLACEORDER_TIMEOUT = 10  # seconds


def _snap(price: float, symbol: str) -> float:
    """Round price to nearest valid strike for this underlying."""
    interval = _STRIKE_INTERVAL.get(symbol, 50)
    return round(round(price / interval) * interval, 2)


def _find_strike(chain: list[dict], target: float, side: str) -> dict | None:
    """
    Find the chain row closest to target strike.
    side = 'ce' or 'pe'
    Returns the dict for that leg (ce or pe sub-dict) plus 'strike' and 'symbol'.
    """
    best = None
    best_dist = float("inf")
    for row in chain:
        leg = row.get(side) or {}
        if not leg.get("exists", True):
            continue
        dist = abs(row["strike"] - target)
        if dist < best_dist:
            best_dist = dist
            best = {"strike": row["strike"], **leg}
    return best


# ---------------------------------------------------------------------------
# Leg builder
# ---------------------------------------------------------------------------

def build_legs(signal: dict, chain: list[dict]) -> dict:
    """
    Build specific option legs for the recommended strategy.

    Returns:
        {
          "legs": [
            {"action": "sell"|"buy", "type": "CE"|"PE",
             "strike": 24000, "symbol": "NFO:NIFTY...",
             "ltp": 85.5, "exchange": "NFO"},
            ...
          ],
          "net_credit": 150.3,   # total premium (sell - buy)
          "max_loss": 49.7,      # per unit
          "max_loss_per_lot": 1242,
          "lot_size": 25,
          "expiry": "...",
          "error": None | str
        }
    """
    sym = signal.get("symbol", "NIFTY")
    exchange = signal.get("exchange", "NFO")
    strategy = (signal.get("strategy") or {}).get("name", "")
    sd = signal.get("sd_range_1") or {}
    spot = signal.get("spot") or 0
    max_pain = signal.get("max_pain")
    oi_walls = signal.get("oi_walls") or {}

    wing = _WING_WIDTH.get(sym, 200)
    lot_size = {"NIFTY": 25, "BANKNIFTY": 15, "SENSEX": 20}.get(sym, 25)

    if not chain or not spot:
        return {"legs": [], "net_credit": 0, "max_loss": 0, "max_loss_per_lot": 0,
                "lot_size": lot_size, "error": "No chain data available"}

    sd_lo = sd.get("lo", spot * 0.97)
    sd_hi = sd.get("hi", spot * 1.03)

    # Use max pain as anchor if available and within SD range
    if max_pain and sd_lo < max_pain < sd_hi:
        anchor = max_pain
    else:
        anchor = spot

    legs: list[dict] = []
    error: str | None = None

    def make_leg(action: str, side: str, target_strike: float) -> dict | None:
        snapped = _snap(target_strike, sym)
        found = _find_strike(chain, snapped, side)
        if not found:
            return None
        return {
            "action": action,
            "type": side.upper(),
            "strike": found["strike"],
            "symbol": f"{exchange}:{found.get('symbol', '')}",
            "ltp": found.get("ltp", 0) or 0,
            "exchange": exchange,
        }

    try:
        if "Iron Condor" in strategy or "Strangle" in strategy:
            # Short Iron Condor: sell CE above sd_hi, sell PE below sd_lo, buy wings
            ce_sell_target = _snap(sd_hi * 1.01, sym)
            pe_sell_target = _snap(sd_lo * 0.99, sym)
            # Prefer OI wall strikes
            if oi_walls.get("ce_walls"):
                ce_sell_target = min(oi_walls["ce_walls"])
            if oi_walls.get("pe_walls"):
                pe_sell_target = max(oi_walls["pe_walls"])

            l1 = make_leg("sell", "pe", pe_sell_target)
            l2 = make_leg("buy",  "pe", pe_sell_target - wing)
            l3 = make_leg("sell", "ce", ce_sell_target)
            l4 = make_leg("buy",  "ce", ce_sell_target + wing)
            legs = [x for x in [l1, l2, l3, l4] if x]

        elif "Iron Butterfly" in strategy:
            # Short Iron Butterfly: sell ATM CE+PE, buy wings
            l1 = make_leg("sell", "pe", anchor)
            l2 = make_leg("buy",  "pe", anchor - wing)
            l3 = make_leg("sell", "ce", anchor)
            l4 = make_leg("buy",  "ce", anchor + wing)
            legs = [x for x in [l1, l2, l3, l4] if x]

        elif "Bull Put Spread" in strategy:
            pe_sell_target = _snap(sd_lo * 0.99, sym)
            if oi_walls.get("pe_walls"):
                pe_sell_target = max(oi_walls["pe_walls"])
            l1 = make_leg("sell", "pe", pe_sell_target)
            l2 = make_leg("buy",  "pe", pe_sell_target - wing)
            legs = [x for x in [l1, l2] if x]

        elif "Bear Call Spread" in strategy:
            ce_sell_target = _snap(sd_hi * 1.01, sym)
            if oi_walls.get("ce_walls"):
                ce_sell_target = min(oi_walls["ce_walls"])
            l1 = make_leg("sell", "ce", ce_sell_target)
            l2 = make_leg("buy",  "ce", ce_sell_target + wing)
            legs = [x for x in [l1, l2] if x]

        elif "Long Straddle" in strategy:
            l1 = make_leg("buy", "pe", anchor)
            l2 = make_leg("buy", "ce", anchor)
            legs = [x for x in [l1, l2] if x]

        elif "Long Butterfly" in strategy:
            l1 = make_leg("buy",  "pe", anchor - wing)
            l2 = make_leg("sell", "pe", anchor)
            l3 = make_leg("sell", "ce", anchor)
            l4 = make_leg("buy",  "ce", anchor + wing)
            legs = [x for x in [l1, l2, l3, l4] if x]

        else:
            error = f"No leg builder for strategy: {strategy}"

    except Exception as exc:
        error = str(exc)
        log.error("build_legs error: %s", exc, exc_info=True)

    # Calculate net credit and max loss
    net_credit = 0.0
    for leg in legs:
        ltp = float(leg.get("ltp", 0))
        net_credit += ltp if leg["action"] == "sell" else -ltp

    max_loss = 0.0
    if legs:
        sells = [l for l in legs if l["action"] == "sell"]
        buys  = [l for l in legs if l["action"] == "buy"]
        if sells and buys:
            # For defined-risk: max_loss = wing_width - net_credit
            max_loss = max(0.0, wing - max(0, net_credit))
        else:
            # Undefined risk: use 3× credit as proxy
            max_loss = abs(net_credit) * 3

    return {
        "legs": legs,
        "net_credit": round(net_credit, 2),
        "max_loss": round(max_loss, 2),
        "max_loss_per_lot": round(max_loss * lot_size, 0),
        "lot_size": lot_size,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Lot sizing
# ---------------------------------------------------------------------------

def calc_lots(
    max_loss_per_lot: float,
    capital: float | None = None,
    risk_pct: float = 0.01,
    default_lots: int = 1,
    max_lots: int = 3,
) -> int:
    """
    Kelly/risk-rule lot sizing.
    If capital is provided: lots = floor(capital * risk_pct / max_loss_per_lot)
    Otherwise: default_lots (from settings).
    Always capped at max_lots.
    """
    if capital and max_loss_per_lot > 0:
        lots = int(capital * risk_pct / max_loss_per_lot)
        return max(1, min(lots, max_lots))
    return max(1, min(default_lots, max_lots))


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def _base_url() -> str:
    return os.getenv("OPENALGO_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def place_orders(
    legs: list[dict],
    lots: int,
    api_key: str,
    strategy_tag: str = "SignalEngine",
    product: str = "NRML",
) -> dict:
    """
    Place all legs as MARKET orders via /api/v1/placeorder.

    Returns:
        {"success": True, "results": [...], "errors": [...]}
    """
    if not legs:
        return {"success": False, "results": [], "errors": ["No legs to place"]}
    if not api_key:
        return {"success": False, "results": [], "errors": ["No API key configured"]}

    url = f"{_base_url()}/api/v1/placeorder"
    results: list[dict] = []
    errors: list[str] = []

    for leg in legs:
        sym_parts = leg["symbol"].split(":")
        symbol_clean = sym_parts[-1] if sym_parts else leg["symbol"]
        exchange = leg.get("exchange", "NFO")
        lot_size = {"NIFTY": 25, "BANKNIFTY": 15, "SENSEX": 20}.get(
            symbol_clean.split("N")[0] if "N" in symbol_clean else "NIFTY", 25
        )
        quantity = lots * lot_size

        payload = {
            "apikey": api_key,
            "strategy": strategy_tag,
            "exchange": exchange,
            "symbol": symbol_clean,
            "action": "BUY" if leg["action"] == "buy" else "SELL",
            "quantity": str(quantity),
            "pricetype": "MARKET",
            "product": product,
            "price": "0",
            "trigger_price": "0",
            "disclosed_quantity": "0",
        }

        try:
            resp = requests.post(url, json=payload, timeout=_PLACEORDER_TIMEOUT)
            data = resp.json()
            results.append({
                "symbol": symbol_clean,
                "action": leg["action"],
                "qty": quantity,
                "status": data.get("status", "unknown"),
                "orderid": data.get("orderid"),
            })
            if data.get("status") != "success":
                errors.append(f"{symbol_clean}: {data.get('message', 'order failed')}")
        except Exception as exc:
            errors.append(f"{symbol_clean}: {exc}")
            results.append({"symbol": symbol_clean, "action": leg["action"], "qty": quantity,
                            "status": "error", "error": str(exc)})

    return {
        "success": len(errors) == 0,
        "results": results,
        "errors": errors,
    }
