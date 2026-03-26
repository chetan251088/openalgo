"""
Market Pulse — Alert System (Phase 6).

Rule-based alert engine with:
  - Score threshold alerts
  - Regime change alerts
  - High-conviction idea alerts
  - VIX spike/collapse alerts
  - Alert deduplication
  - Telegram/Webhook delivery
"""

import hashlib
import json
import logging
import os
import time
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Alert state ─────────────────────────────────────────────────
_last_alert_state: dict[str, Any] = {}
_alert_history: list[dict[str, Any]] = []
_alert_cooldowns: dict[str, float] = {}
_MAX_HISTORY = 100
_DEFAULT_COOLDOWN = max(60, int(os.getenv("MARKET_PULSE_ALERT_COOLDOWN", "300")))
_ALERTS_ENABLED = os.getenv("MARKET_PULSE_ALERTS_ENABLED", "true").lower() in ("1", "true", "yes")

# Default alert rules
DEFAULT_ALERT_RULES: list[dict[str, Any]] = [
    {
        "id": "quality_high",
        "name": "Quality Score High",
        "type": "threshold_above",
        "field": "market_quality_score",
        "threshold": 80,
        "message": "🟢 Market Quality Score crossed above 80 — favorable trading conditions",
        "enabled": True,
    },
    {
        "id": "quality_low",
        "name": "Quality Score Low",
        "type": "threshold_below",
        "field": "market_quality_score",
        "threshold": 40,
        "message": "🔴 Market Quality Score dropped below 40 — capital preservation mode",
        "enabled": True,
    },
    {
        "id": "regime_change",
        "name": "Regime Change",
        "type": "value_change",
        "field": "regime",
        "message": "⚡ Market regime changed to {new_value} (was {old_value})",
        "enabled": True,
    },
    {
        "id": "vix_spike",
        "name": "VIX Spike",
        "type": "threshold_above",
        "field": "vix_current",
        "threshold": 22,
        "message": "🚨 India VIX spiked above 22 — elevated volatility",
        "enabled": True,
    },
    {
        "id": "vix_collapse",
        "name": "VIX Collapse",
        "type": "threshold_below",
        "field": "vix_current",
        "threshold": 11,
        "message": "⚠️ India VIX below 11 — complacency warning",
        "enabled": True,
    },
    {
        "id": "high_conviction_buy",
        "name": "High Conviction Buy Signal",
        "type": "idea_alert",
        "conviction": "HIGH",
        "signal": "BUY",
        "message": "📈 HIGH conviction BUY signal: {symbol} @ {ltp} — {reason}",
        "enabled": True,
    },
    {
        "id": "decision_change",
        "name": "Decision Change",
        "type": "value_change",
        "field": "decision",
        "message": "🔄 Trading decision changed to {new_value} (was {old_value})",
        "enabled": True,
    },
]


def _alert_hash(alert_id: str) -> str:
    """Generate a deduplication key."""
    return f"alert:{alert_id}"


def _is_in_cooldown(alert_id: str) -> bool:
    """Check if alert is in cooldown period."""
    key = _alert_hash(alert_id)
    last_fired = _alert_cooldowns.get(key, 0)
    return (time.time() - last_fired) < _DEFAULT_COOLDOWN


def _fire_alert(rule: dict[str, Any], message: str, context: dict[str, Any] | None = None):
    """Record alert and attempt delivery."""
    alert_id = rule["id"]
    key = _alert_hash(alert_id)

    if _is_in_cooldown(alert_id):
        return

    _alert_cooldowns[key] = time.time()

    alert_record = {
        "id": alert_id,
        "name": rule["name"],
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "context": context,
    }

    _alert_history.append(alert_record)
    if len(_alert_history) > _MAX_HISTORY:
        _alert_history.pop(0)

    logger.info("Market Pulse alert fired: %s — %s", alert_id, message)

    # Attempt Telegram delivery
    _deliver_telegram(message)

    # Attempt webhook delivery
    _deliver_webhook(alert_record)


def _deliver_telegram(message: str):
    """Send alert via Telegram using existing infrastructure."""
    try:
        from services.telegram_alert_service import telegram_alert_service
        telegram_alert_service.send_broadcast_alert(
            f"📊 *Market Pulse Alert*\n\n{message}"
        )
    except Exception as e:
        logger.debug("Telegram delivery skipped: %s", e)


def _deliver_webhook(alert_record: dict[str, Any]):
    """Send alert to configured webhook URL."""
    webhook_url = os.getenv("MARKET_PULSE_WEBHOOK_URL")
    if not webhook_url:
        return

    try:
        import requests
        requests.post(
            webhook_url,
            json={
                "source": "market_pulse",
                "alert": alert_record,
            },
            timeout=5,
        )
    except Exception as e:
        logger.debug("Webhook delivery failed: %s", e)


def evaluate_alerts(
    pulse_data: dict[str, Any],
    rules: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all alert rules against current pulse data.

    Returns list of fired alerts.
    """
    if not _ALERTS_ENABLED:
        return []

    if rules is None:
        rules = DEFAULT_ALERT_RULES

    fired: list[dict[str, Any]] = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue

        rule_type = rule.get("type")

        if rule_type == "threshold_above":
            value = _extract_field(pulse_data, rule["field"])
            if value is not None and value >= rule["threshold"]:
                old_value = _last_alert_state.get(rule["field"])
                if old_value is None or old_value < rule["threshold"]:
                    msg = rule["message"]
                    _fire_alert(rule, msg)
                    fired.append({"rule": rule["id"], "message": msg})

        elif rule_type == "threshold_below":
            value = _extract_field(pulse_data, rule["field"])
            if value is not None and value <= rule["threshold"]:
                old_value = _last_alert_state.get(rule["field"])
                if old_value is None or old_value > rule["threshold"]:
                    msg = rule["message"]
                    _fire_alert(rule, msg)
                    fired.append({"rule": rule["id"], "message": msg})

        elif rule_type == "value_change":
            new_value = _extract_field(pulse_data, rule["field"])
            old_value = _last_alert_state.get(rule["field"])
            if old_value is not None and new_value is not None and new_value != old_value:
                msg = rule["message"].format(new_value=new_value, old_value=old_value)
                _fire_alert(rule, msg)
                fired.append({"rule": rule["id"], "message": msg})

        elif rule_type == "idea_alert":
            ideas = pulse_data.get("equity_ideas", [])
            for idea in ideas:
                if (
                    idea.get("conviction") == rule.get("conviction")
                    and idea.get("signal") == rule.get("signal")
                ):
                    msg = rule["message"].format(
                        symbol=idea.get("symbol", "?"),
                        ltp=idea.get("ltp", "?"),
                        reason=idea.get("reason", ""),
                    )
                    idea_alert_id = f"{rule['id']}:{idea.get('symbol')}"
                    rule_copy = {**rule, "id": idea_alert_id}
                    _fire_alert(rule_copy, msg)
                    fired.append({"rule": idea_alert_id, "message": msg})

    # Update state for next comparison
    for rule in rules:
        field = rule.get("field")
        if field:
            value = _extract_field(pulse_data, field)
            if value is not None:
                _last_alert_state[field] = value

    return fired


def _extract_field(data: dict, field: str) -> Any:
    """Extract a field from pulse data, supporting dotted paths."""
    if field == "vix_current":
        # Special case: extract from ticker
        ticker = data.get("ticker", {})
        vix = ticker.get("INDIAVIX", {})
        return vix.get("ltp")

    return data.get(field)


def get_alert_history(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent alert history."""
    return _alert_history[-limit:]


def get_alert_rules() -> list[dict[str, Any]]:
    """Return current alert rules."""
    return DEFAULT_ALERT_RULES
