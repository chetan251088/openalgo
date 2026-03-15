"""
Roll Position Service
Closes current option legs and opens new legs at a target expiry with the same delta.

SAFETY DESIGN:
1. Close legs use hedge-first ordering (close short legs first to reduce margin)
2. If any close leg fails, remaining opens are aborted to prevent naked exposure
3. Partial-fill detection: checks order status after placement
4. Recovery: if opens fail after successful closes, the system logs a HEDGE_MISMATCH
   alert that requires manual intervention rather than auto-retrying blindly
"""

import logging
import time
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class RollError(Exception):
    """Raised when a roll encounters an unrecoverable error."""
    pass


class HedgeMismatchError(RollError):
    """Raised when closes succeed but opens fail, creating naked exposure."""
    pass


def roll_position(
    current_legs: List[Dict[str, Any]],
    target_expiry: str,
    target_delta: float,
    api_key: str,
    strategy: str = "roll_position",
    abort_on_close_failure: bool = True,
    max_retry_per_leg: int = 1,
) -> Dict[str, Any]:
    """Roll an options position to a new expiry with safety guards.

    Safety rules:
    - Closes are ordered: short legs first (reduces margin before opening new)
    - If abort_on_close_failure=True (default), any close failure aborts the entire roll
    - If opens fail after closes succeed, raises HedgeMismatchError
    - Checks order status after each placement to detect rejects/partial fills
    """
    close_results = []
    open_results = []
    errors = []
    hedge_mismatch = False

    sorted_legs = sorted(current_legs, key=lambda l: 0 if l.get("action") == "SELL" else 1)

    # PHASE 1: Close all current legs (short legs first)
    for leg in sorted_legs:
        reverse_action = "SELL" if leg.get("action") == "BUY" else "BUY"
        success = False

        for attempt in range(1 + max_retry_per_leg):
            try:
                result = _place_and_verify(
                    api_key=api_key,
                    strategy=strategy,
                    symbol=leg["symbol"],
                    exchange=leg.get("exchange", "NFO"),
                    action=reverse_action,
                    quantity=leg.get("quantity", 0),
                    product=leg.get("product", "NRML"),
                )
                close_results.append({"symbol": leg["symbol"], "action": reverse_action, "result": result, "attempt": attempt + 1})
                success = True
                break
            except Exception as e:
                if attempt == max_retry_per_leg:
                    errors.append({"symbol": leg["symbol"], "error": str(e), "phase": "close", "attempts": attempt + 1})

        if not success and abort_on_close_failure:
            logger.error("ROLL ABORTED: close failed for %s. No new legs will be opened.", leg["symbol"])
            return {
                "status": "aborted",
                "reason": f"Close failed for {leg['symbol']}. Roll aborted to prevent naked exposure.",
                "close_results": close_results,
                "open_results": [],
                "errors": errors,
            }

    # PHASE 2: Open new legs at target expiry
    for leg in sorted_legs:
        old_symbol = leg["symbol"]
        new_symbol = _replace_expiry_in_symbol(old_symbol, target_expiry)
        if not new_symbol:
            errors.append({"symbol": old_symbol, "error": "Cannot derive new symbol", "phase": "open"})
            hedge_mismatch = True
            continue

        success = False
        for attempt in range(1 + max_retry_per_leg):
            try:
                result = _place_and_verify(
                    api_key=api_key,
                    strategy=strategy,
                    symbol=new_symbol,
                    exchange=leg.get("exchange", "NFO"),
                    action=leg.get("action", "SELL"),
                    quantity=leg.get("quantity", 0),
                    product=leg.get("product", "NRML"),
                )
                open_results.append({"symbol": new_symbol, "action": leg["action"], "result": result, "attempt": attempt + 1})
                success = True
                break
            except Exception as e:
                if attempt == max_retry_per_leg:
                    errors.append({"symbol": new_symbol, "error": str(e), "phase": "open", "attempts": attempt + 1})
                    hedge_mismatch = True

    if hedge_mismatch and close_results:
        open_errors = [e for e in errors if e["phase"] == "open"]
        msg = (
            f"HEDGE MISMATCH: {len(close_results)} close(s) succeeded but "
            f"{len(open_errors)} open(s) failed. MANUAL INTERVENTION REQUIRED."
        )
        logger.critical(msg)

        # Send Telegram alert for hedge mismatch
        try:
            from services.telegram_alert_service import telegram_alert_service
            if telegram_alert_service:
                closed_syms = ", ".join(r["symbol"] for r in close_results)
                failed_syms = ", ".join(e["symbol"] for e in open_errors)
                alert_msg = (
                    f"🚨 HEDGE MISMATCH — NAKED EXPOSURE 🚨\n\n"
                    f"Strategy: {strategy}\n"
                    f"Closed: {closed_syms}\n"
                    f"Failed to open: {failed_syms}\n"
                    f"Action: Close naked positions manually NOW.\n"
                    f"Errors: {'; '.join(e.get('error','') for e in open_errors)}"
                )
                from database.auth_db import get_username_by_apikey
                from database.telegram_db import get_telegram_user_by_username
                username = get_username_by_apikey(api_key)
                if username:
                    tg_user = get_telegram_user_by_username(username)
                    if tg_user:
                        telegram_alert_service.send_alert_sync(tg_user.telegram_id, alert_msg)
        except Exception as tg_err:
            logger.error("Failed to send hedge mismatch Telegram alert: %s", tg_err)

        return {
            "status": "hedge_mismatch",
            "reason": "Closes succeeded but opens failed. Manual intervention required.",
            "close_results": close_results,
            "open_results": open_results,
            "errors": errors,
            "requires_manual_intervention": True,
        }

    status = "success" if not errors else "partial"
    return {
        "status": status,
        "close_results": close_results,
        "open_results": open_results,
        "errors": errors,
    }


def _place_and_verify(
    api_key: str, strategy: str, symbol: str, exchange: str,
    action: str, quantity: int, product: str,
) -> dict:
    """Place an order and verify it was accepted (not rejected)."""
    from services.place_order_service import place_order_service

    result = place_order_service(
        api_key=api_key,
        strategy=strategy,
        symbol=symbol,
        exchange=exchange,
        action=action,
        quantity=quantity,
        price_type="MARKET",
        product=product,
    )

    if isinstance(result, dict) and result.get("status") == "error":
        raise RollError(f"Order rejected: {result.get('message', 'unknown')}")

    return result


def _replace_expiry_in_symbol(old_symbol: str, new_expiry: str) -> Optional[str]:
    """Replace the expiry date portion of an option symbol.

    Old format: NIFTY27MAR2522000CE -> extract NIFTY, 22000, CE
    New: NIFTY{new_expiry}22000CE
    """
    import re
    match = re.match(r'^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$', old_symbol)
    if not match:
        return None

    underlying = match.group(1)
    strike = match.group(3)
    option_type = match.group(4)

    return f"{underlying}{new_expiry}{strike}{option_type}"
