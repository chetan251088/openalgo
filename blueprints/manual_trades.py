from flask import Blueprint, jsonify, request

from services.manual_trade_log_store import get_manual_trade_log_store

manual_trades_bp = Blueprint("manual_trades", __name__)


@manual_trades_bp.route("/manual_trades/logs", methods=["POST"])
def manual_trades_logs_ingest():
    payload = request.get_json(silent=True) or {}
    events = payload.get("events") or payload.get("event") or []
    if isinstance(events, dict):
        events = [events]
    store = get_manual_trade_log_store()
    store.enqueue(events)
    return jsonify({"status": "success", "count": len(events)})


@manual_trades_bp.route("/manual_trades/logs", methods=["GET"])
def manual_trades_logs_fetch():
    store = get_manual_trade_log_store()
    limit = int(request.args.get("limit", 200))
    mode = request.args.get("mode")
    source = request.args.get("source")
    symbol = request.args.get("symbol")
    side = request.args.get("side")
    underlying = request.args.get("underlying")
    since = request.args.get("since")
    until = request.args.get("until")
    logs = store.fetch(
        limit=limit,
        mode=mode,
        source=source,
        symbol=symbol,
        side=side,
        underlying=underlying,
        since=since,
        until=until,
    )
    return jsonify({"status": "success", "logs": logs})


@manual_trades_bp.route("/manual_trades/analytics", methods=["GET"])
def manual_trades_analytics():
    store = get_manual_trade_log_store()
    limit = int(request.args.get("limit", 2000))
    mode = request.args.get("mode")
    source = request.args.get("source")
    symbol = request.args.get("symbol")
    side = request.args.get("side")
    underlying = request.args.get("underlying")
    since = request.args.get("since")
    until = request.args.get("until")
    bucket = float(request.args.get("bucket", 50))
    interval_min = int(request.args.get("interval_min", 5))
    data = store.analytics(
        limit=limit,
        mode=mode,
        source=source,
        symbol=symbol,
        side=side,
        underlying=underlying,
        since=since,
        until=until,
        bucket=bucket,
        interval_min=interval_min,
    )
    return jsonify({"status": "success", **data})
