# AI Scalper control endpoints
import os

from flask import Blueprint, jsonify, request

from services.ai_scalper.manager import get_ai_scalper_manager
from services.ai_scalper.advisor import build_advisor
from services.ai_scalper.config import AdvisorConfig, ModelTunerConfig
from services.ai_scalper.learning import get_learning_store
from services.ai_scalper.log_store import get_auto_trade_log_store
from services.ai_scalper.model_tuner import get_model_tuning_service
from services.ai_scalper.model_tuner_scheduler import init_model_tuner_scheduler

ai_scalper_bp = Blueprint("ai_scalper", __name__)


@ai_scalper_bp.route("/ai_scalper/start", methods=["POST"])
def ai_scalper_start():
    payload = request.get_json(silent=True) or {}
    manager = get_ai_scalper_manager()
    ok, message = manager.start(payload)
    status = "success" if ok else "error"
    return jsonify({"status": status, "message": message})


@ai_scalper_bp.route("/ai_scalper/stop", methods=["POST"])
def ai_scalper_stop():
    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason", "Stopped")
    manager = get_ai_scalper_manager()
    ok, message = manager.stop(reason)
    status = "success" if ok else "error"
    return jsonify({"status": status, "message": message})


@ai_scalper_bp.route("/ai_scalper/status", methods=["GET"])
def ai_scalper_status():
    manager = get_ai_scalper_manager()
    return jsonify(manager.status())


@ai_scalper_bp.route("/ai_scalper/update", methods=["POST"])
def ai_scalper_update():
    payload = request.get_json(silent=True) or {}
    manager = get_ai_scalper_manager()
    ok, message = manager.update(payload)
    status = "success" if ok else "error"
    return jsonify({"status": status, "message": message})


@ai_scalper_bp.route("/ai_scalper/advisor_stub", methods=["POST"])
def ai_scalper_advisor_stub():
    """
    Lightweight advisor stub for local testing.
    Returns simple parameter tweaks based on volatility/playbook.
    """
    payload = request.get_json(silent=True) or {}
    try:
        volatility = float(payload.get("volatility", 0) or 0)
    except (TypeError, ValueError):
        volatility = 0
    playbook = str(payload.get("playbook", "")).lower()

    changes = {}
    notes = "No change"
    if volatility >= 1.5:
        changes = {"momentum_ticks": 3, "tp_points": 5, "sl_points": 8}
        notes = "High volatility: baseline"
    elif playbook == "chop" or volatility < 0.6:
        changes = {"momentum_ticks": 4, "tp_points": 4, "sl_points": 6}
        notes = "Choppy tape: tighten"

    return jsonify({"changes": changes, "notes": notes})


@ai_scalper_bp.route("/ai_scalper/test_advisor", methods=["GET", "POST"])
def ai_scalper_test_advisor():
    try:
        payload = request.get_json(silent=True) or {}
        if request.method == "GET":
            payload = {**payload, **request.args}
        provider = payload.get("provider", "none")
        config = AdvisorConfig(
            enabled=True,
            auto_apply=True,
            provider=str(provider),
            url=payload.get("url"),
            model=payload.get("model"),
            base_url=payload.get("base_url"),
        )
        advisor = build_advisor(config)
        context = payload.get("context") or {"volatility": 0.8, "playbook": "baseline"}
        update = advisor.get_update(context)
        if not update:
            return jsonify({"status": "error", "message": "No response from advisor"}), 200
        return jsonify({"status": "success", "changes": update.changes, "notes": update.notes}), 200
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Advisor test failed: {exc}"}), 200


@ai_scalper_bp.route("/ai_scalper/key_status", methods=["GET"])
def ai_scalper_key_status():
    """Return boolean flags for whether advisor keys are present in environment."""
    openai = bool(os.getenv("OPENAI_API_KEY"))
    anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    ollama = True  # local by default; assume available if base URL is set
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
    if not ollama_base:
        ollama = False
    return jsonify({"openai": openai, "anthropic": anthropic, "ollama": ollama})


@ai_scalper_bp.route("/ai_scalper/learning/summary", methods=["GET"])
def ai_scalper_learning_summary():
    limit = int(request.args.get("limit", 500))
    store = get_learning_store()
    return jsonify(store.summary(limit))


@ai_scalper_bp.route("/ai_scalper/learning/trades", methods=["GET"])
def ai_scalper_learning_trades():
    limit = int(request.args.get("limit", 200))
    store = get_learning_store()
    return jsonify({"trades": store.fetch_trades(limit)})


@ai_scalper_bp.route("/ai_scalper/learning/replay", methods=["POST"])
def ai_scalper_learning_replay():
    payload = request.get_json(silent=True) or {}
    limit = int(payload.get("limit", 500))
    store = get_learning_store()
    return jsonify({"summary": store.summary(limit), "trades": store.fetch_trades(min(200, limit))})


@ai_scalper_bp.route("/ai_scalper/logs", methods=["POST"])
def ai_scalper_logs_ingest():
    payload = request.get_json(silent=True) or {}
    events = payload.get("events") or payload.get("event") or []
    if isinstance(events, dict):
        events = [events]
    store = get_auto_trade_log_store()
    store.enqueue(events)
    return jsonify({"status": "success", "count": len(events)})


@ai_scalper_bp.route("/ai_scalper/logs", methods=["GET"])
def ai_scalper_logs_fetch():
    store = get_auto_trade_log_store()
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


@ai_scalper_bp.route("/ai_scalper/analytics", methods=["GET"])
def ai_scalper_analytics():
    store = get_auto_trade_log_store()
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


@ai_scalper_bp.route("/ai_scalper/model/status", methods=["GET"])
def ai_scalper_model_status():
    manager = get_ai_scalper_manager()
    scheduler = init_model_tuner_scheduler()
    service = get_model_tuning_service()
    return jsonify({"status": "success", **service.get_status(manager=manager, scheduler=scheduler)})


@ai_scalper_bp.route("/ai_scalper/model/recommendations", methods=["GET"])
def ai_scalper_model_recommendations():
    limit = int(request.args.get("limit", 20))
    service = get_model_tuning_service()
    runs = service.get_runs(limit)
    return jsonify({"status": "success", "runs": runs})


@ai_scalper_bp.route("/ai_scalper/model/run", methods=["POST"])
def ai_scalper_model_run():
    payload = request.get_json(silent=True) or {}
    manager = get_ai_scalper_manager()
    scheduler = init_model_tuner_scheduler()
    service = get_model_tuning_service()

    config = service.config
    updated_config = ModelTunerConfig(
        enabled=bool(payload.get("enabled", config.enabled)),
        provider=str(payload.get("provider", config.provider)),
        model=payload.get("model", config.model),
        base_url=payload.get("base_url", config.base_url),
        timeout_s=float(payload.get("timeout_s", config.timeout_s)),
        interval_s=int(payload.get("interval_s", config.interval_s)),
        min_trades=int(payload.get("min_trades", config.min_trades)),
        auto_apply_paper=bool(payload.get("auto_apply_paper", config.auto_apply_paper)),
        apply_clamps=bool(payload.get("apply_clamps", config.apply_clamps)),
        notify_email=bool(payload.get("notify_email", config.notify_email)),
        notify_telegram=bool(payload.get("notify_telegram", config.notify_telegram)),
        underlying=str(payload.get("underlying", config.underlying)).upper(),
        db_path=config.db_path,
    )
    service.update_config(updated_config)

    schedule_type = (payload.get("schedule_type") or "").lower().strip()
    if schedule_type in {"off", "none", "disabled"}:
        scheduler.clear_schedule()
    elif schedule_type == "interval":
        try:
            interval_s = int(payload.get("interval_s", config.interval_s))
            if interval_s <= 0:
                raise ValueError("Interval must be positive")
            scheduler.schedule_interval(interval_s)
        except Exception as exc:
            return jsonify({"status": "error", "message": f"Invalid interval: {exc}"}), 200
    elif schedule_type == "daily":
        try:
            time_of_day = payload.get("time_of_day", "20:00")
            scheduler.schedule_daily(time_of_day)
        except Exception as exc:
            return jsonify({"status": "error", "message": f"Invalid schedule time: {exc}"}), 200

    run_now = payload.get("run_now", True)
    if not run_now:
        return jsonify({"status": "success", "message": "Updated model tuner config"})

    ok, message, run_id = service.enqueue_run(
        manager=manager,
        objective=payload.get("objective"),
        requested_by="api",
    )
    status = "success" if ok else "error"
    return jsonify({"status": status, "message": message, "run_id": run_id})


@ai_scalper_bp.route("/ai_scalper/model/apply", methods=["POST"])
def ai_scalper_model_apply():
    payload = request.get_json(silent=True) or {}
    run_id = payload.get("run_id")
    if not run_id:
        return jsonify({"status": "error", "message": "run_id required"}), 200
    manager = get_ai_scalper_manager()
    service = get_model_tuning_service()
    ok, message = service.apply_recommendation(run_id, manager)
    status = "success" if ok else "error"
    return jsonify({"status": status, "message": message})
