# Mock Replay UI and API: chart + controls + mock trading (all stored in mock DB)
import os
from flask import Blueprint, jsonify, request, send_from_directory

from database.mock_trading_db import (
    init_db,
    place_mock_order,
    fill_mock_order,
    get_mock_orders,
    get_mock_positions,
    get_mock_trades,
    close_mock_position,
    cancel_mock_order,
)
from services.mock_replay.replay_engine import REGIMES

mock_replay_bp = Blueprint("mock_replay", __name__)

# Project root (parent of blueprints/)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@mock_replay_bp.route("/mock-replay")
def mock_replay_page():
    return send_from_directory(_ROOT, "mock_replay.html")


@mock_replay_bp.route("/mock-replay/config")
def mock_replay_config():
    return jsonify({
        "wsUrl": os.getenv("MOCK_REPLAY_WS_URL",
                         f"ws://127.0.0.1:{os.getenv('MOCK_WS_PORT', '8770')}"),
        "regimes": REGIMES,
    })


@mock_replay_bp.route("/mock-replay/api/replay-range")
def api_replay_range():
    """Return available 1m date range from Historify for symbol/exchange (for UI date picker)."""
    from services.mock_replay.replay_engine import get_replay_range
    symbol = request.args.get("symbol", "NIFTY 50")
    exchange = request.args.get("exchange", "NSE_INDEX")
    r = get_replay_range(symbol, exchange)
    if not r:
        return jsonify({"status": "success", "data": None})
    return jsonify({
        "status": "success",
        "data": {
            "first_timestamp": r.get("first_timestamp"),
            "last_timestamp": r.get("last_timestamp"),
            "record_count": r.get("record_count"),
        },
    })


@mock_replay_bp.route("/mock-replay/api/orders", methods=["GET"])
def api_mock_orders():
    init_db()
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"status": "success", "data": get_mock_orders(limit=limit)})


@mock_replay_bp.route("/mock-replay/api/orders/place", methods=["POST"])
def api_mock_place_order():
    init_db()
    data = request.get_json() or {}
    symbol = data.get("symbol")
    exchange = data.get("exchange", "NFO")
    action = data.get("action", "BUY")
    quantity = data.get("quantity", 1)
    order_type = data.get("order_type", "MARKET")
    price = data.get("price")
    if not symbol:
        return jsonify({"status": "error", "message": "symbol required"}), 400
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "quantity must be integer"}), 400
    out = place_mock_order(symbol=symbol, exchange=exchange, action=action, quantity=quantity, order_type=order_type, price=price)
    fill_price = data.get("ltp") or price
    if order_type == "MARKET" and (fill_price is not None and float(fill_price) > 0):
        fill_mock_order(out["order_id"], float(fill_price))
        out["status"] = "complete"
        out["filled_price"] = float(fill_price)
    return jsonify({"status": "success", "data": out})


@mock_replay_bp.route("/mock-replay/api/orders/<order_id>/fill", methods=["POST"])
def api_mock_fill_order(order_id):
    init_db()
    data = request.get_json() or {}
    price = data.get("price") or data.get("ltp")
    if price is None:
        return jsonify({"status": "error", "message": "price or ltp required"}), 400
    if fill_mock_order(order_id, float(price)):
        return jsonify({"status": "success", "message": "Order filled"})
    return jsonify({"status": "error", "message": "Order not found or already filled"}), 404


@mock_replay_bp.route("/mock-replay/api/orders/<order_id>/cancel", methods=["POST"])
def api_mock_cancel_order(order_id):
    init_db()
    if cancel_mock_order(order_id):
        return jsonify({"status": "success", "message": "Order cancelled"})
    return jsonify({"status": "error", "message": "Order not found or not open"}), 404


@mock_replay_bp.route("/mock-replay/api/positions", methods=["GET"])
def api_mock_positions():
    init_db()
    return jsonify({"status": "success", "data": get_mock_positions()})


@mock_replay_bp.route("/mock-replay/api/positions/close", methods=["POST"])
def api_mock_close_position():
    init_db()
    data = request.get_json() or {}
    symbol = data.get("symbol")
    exchange = data.get("exchange", "NFO")
    close_price = data.get("price") or data.get("ltp")
    if not symbol or close_price is None:
        return jsonify({"status": "error", "message": "symbol and price/ltp required"}), 400
    result = close_mock_position(symbol, exchange, float(close_price))
    if result:
        return jsonify({"status": "success", "data": result})
    return jsonify({"status": "error", "message": "No position for symbol"}), 404


@mock_replay_bp.route("/mock-replay/api/trades", methods=["GET"])
def api_mock_trades():
    init_db()
    limit = request.args.get("limit", 200, type=int)
    return jsonify({"status": "success", "data": get_mock_trades(limit=limit)})
