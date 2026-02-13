import os
import re

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource

from database.auth_db import verify_api_key
from limiter import limiter
from services.scalping_flow_bridge_service import (
    acknowledge_flow_virtual_entries,
    enqueue_flow_virtual_entry,
    get_pending_flow_virtual_entries,
)
from utils.logging import get_logger

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("scalpingbridge", description="Scalping flow-to-virtual TP/SL bridge API")
logger = get_logger(__name__)

_OPTION_SIDE_RE = re.compile(r"(CE|PE)$", re.IGNORECASE)


def _error(message: str, status_code: int):
    return make_response(jsonify({"status": "error", "message": message}), status_code)


def _require_user_from_apikey(payload: dict):
    api_key = str(payload.get("apikey", "") or "").strip()
    if not api_key:
        return None, _error("apikey is required", 400)
    user_id = verify_api_key(api_key)
    if not user_id:
        return None, _error("Invalid openalgo apikey", 403)
    return str(user_id), None


def _derive_side(symbol: str, explicit_side: str) -> str:
    side = str(explicit_side or "").strip().upper()
    if side in {"CE", "PE"}:
        return side
    match = _OPTION_SIDE_RE.search(symbol.strip().upper())
    if match:
        return match.group(1).upper()
    return "CE"


@api.route("/", strict_slashes=False)
class ScalpingBridgeEnqueue(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Enqueue a flow-triggered entry for scalping virtual TP/SL attachment."""
        payload = request.get_json(silent=True) or {}
        user_id, err = _require_user_from_apikey(payload)
        if err:
            return err

        symbol = str(payload.get("symbol", "") or "").strip().upper()
        exchange = str(payload.get("exchange", "NFO") or "NFO").strip().upper()
        action = str(payload.get("action", "BUY") or "BUY").strip().upper()
        quantity = int(float(payload.get("quantity", 0) or 0))
        tp_points = float(payload.get("tp_points", payload.get("tpPoints", 0)) or 0)
        sl_points = float(payload.get("sl_points", payload.get("slPoints", 0)) or 0)
        entry_price = float(payload.get("entry_price", payload.get("entryPrice", 0)) or 0)
        order_id = str(payload.get("order_id", payload.get("orderId", "")) or "").strip()
        source = str(payload.get("source", "FLOW") or "FLOW").strip().upper()
        managed_by = str(payload.get("managed_by", payload.get("managedBy", "flow")) or "flow").strip().lower()
        side = _derive_side(symbol, str(payload.get("side", "")))

        if not symbol:
            return _error("symbol is required", 400)
        if action not in {"BUY", "SELL"}:
            return _error("action must be BUY or SELL", 400)
        if quantity <= 0:
            return _error("quantity must be positive", 400)

        event = enqueue_flow_virtual_entry(
            user_id,
            {
                "symbol": symbol,
                "exchange": exchange,
                "side": side,
                "action": action,
                "quantity": quantity,
                "tp_points": max(0.0, tp_points),
                "sl_points": max(0.0, sl_points),
                "entry_price": max(0.0, entry_price),
                "order_id": order_id,
                "source": source,
                "managed_by": managed_by,
            },
        )

        return make_response(
            jsonify(
                {
                    "status": "success",
                    "message": "Scalping bridge entry enqueued",
                    "data": event,
                }
            ),
            200,
        )


@api.route("/pending", strict_slashes=False)
class ScalpingBridgePending(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Read pending flow-triggered entries for a user."""
        payload = request.get_json(silent=True) or {}
        user_id, err = _require_user_from_apikey(payload)
        if err:
            return err

        limit = int(float(payload.get("limit", 50) or 50))
        after_id = int(float(payload.get("after_id", payload.get("afterId", 0)) or 0))
        entries = get_pending_flow_virtual_entries(user_id, limit=limit, after_id=after_id)
        return make_response(
            jsonify(
                {
                    "status": "success",
                    "data": {
                        "entries": entries,
                        "count": len(entries),
                    },
                }
            ),
            200,
        )


@api.route("/ack", strict_slashes=False)
class ScalpingBridgeAck(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Acknowledge consumed flow-triggered entries."""
        payload = request.get_json(silent=True) or {}
        user_id, err = _require_user_from_apikey(payload)
        if err:
            return err

        raw_ids = payload.get("ids", payload.get("entry_ids", []))
        ids = raw_ids if isinstance(raw_ids, list) else []
        acked = acknowledge_flow_virtual_entries(user_id, ids)
        return make_response(
            jsonify(
                {
                    "status": "success",
                    "data": {
                        "acked": acked,
                    },
                }
            ),
            200,
        )
