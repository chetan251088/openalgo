from __future__ import annotations

import os
import threading
from typing import Any, Dict, Tuple

from utils.logging import get_logger

from .agent import AutoScalperAgent
from .config import (
    AdvisorConfig,
    AgentConfig,
    ExecutionConfig,
    ModelTunerConfig,
    PlaybookConfig,
    RiskConfig,
    build_agent_config,
    load_execution_config,
)
from .learning import LearningConfig
from .model_tuner import get_model_tuning_service

logger = get_logger(__name__)


class AutoScalperManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.agent: AutoScalperAgent | None = None
        self.last_config: Dict[str, Any] = {}

    def start(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        with self.lock:
            if self.agent and self.agent.is_alive():
                return False, "Auto scalper already running"

            api_key = payload.get("apikey") or payload.get("api_key")
            if not api_key:
                return False, "API key required"

            agent_cfg = build_agent_config(payload)
            risk_cfg = RiskConfig(
                per_trade_max_loss=float(payload.get("per_trade_max_loss", 2000)),
                daily_max_loss=float(payload.get("daily_max_loss", 10000)),
                max_trades_per_min=int(payload.get("max_trades_per_min", 10)),
                min_entry_gap_ms=int(payload.get("min_entry_gap_ms", 3000)),
                cooldown_after_loss_s=int(payload.get("cooldown_after_loss_s", 25)),
                flip_cooldown_s=int(payload.get("flip_cooldown_s", 5)),
                min_hold_before_flip_s=int(payload.get("min_flip_hold_s", 3)),
                max_trade_duration_s=int(payload.get("max_trade_duration_s", 60)),
                time_exit_tighten_pts=float(payload.get("time_exit_tighten_pts", 1.0)),
                spread_max_nifty=float(payload.get("spread_max_nifty", 0.2)),
                spread_max_sensex=float(payload.get("spread_max_sensex", 0.4)),
                stale_timeout_s=int(payload.get("stale_timeout_s", 30)),
                stop_on_stale=bool(payload.get("stop_on_stale", True)),
            )
            playbook_cfg = PlaybookConfig(
                momentum_ticks=int(payload.get("momentum_ticks", 4)),
                tp_points=float(payload.get("tp_points", 8)),
                sl_points=float(payload.get("sl_points", 5)),
                trail_distance=float(payload.get("trail_distance", 2)),
                trail_step=float(payload.get("trail_step", 0.05)),
                trailing_enabled=bool(payload.get("trailing_enabled", True)),
                trailing_override_tp=bool(payload.get("trailing_override_tp", True)),
            )
            advisor_cfg = AdvisorConfig(
                enabled=bool(payload.get("advisor_enabled", False)),
                auto_apply=bool(payload.get("advisor_auto_apply", True)),
                provider=str(payload.get("advisor_provider", "none")),
                url=payload.get("advisor_url"),
                model=payload.get("advisor_model"),
                base_url=payload.get("advisor_base_url"),
                interval_s=int(payload.get("advisor_interval_s", 45)),
            )
            learning_cfg = LearningConfig(
                enabled=bool(payload.get("learning_enabled", True)),
                auto_apply=bool(payload.get("learning_auto_apply", True)),
                tune_interval_s=int(payload.get("learning_interval_s", 60)),
                min_trades=int(payload.get("learning_min_trades", 10)),
                exploration_rate=float(payload.get("learning_exploration", 0.15)),
            )
            model_tuner_cfg = ModelTunerConfig(
                enabled=bool(payload.get("model_tuner_enabled", False)),
                provider=str(payload.get("model_tuner_provider", "none")),
                model=payload.get("model_tuner_model"),
                base_url=payload.get("model_tuner_base_url"),
                timeout_s=float(payload.get("model_tuner_timeout_s", 4.0)),
                interval_s=int(payload.get("model_tuner_interval_s", 900)),
                min_trades=int(payload.get("model_tuner_min_trades", 30)),
                auto_apply_paper=bool(payload.get("model_tuner_auto_apply_paper", True)),
                apply_clamps=bool(payload.get("model_tuner_apply_clamps", True)),
                notify_email=bool(payload.get("model_tuner_notify_email", False)),
                notify_telegram=bool(payload.get("model_tuner_notify_telegram", False)),
                underlying=str(payload.get("model_tuner_underlying", "AUTO")).upper(),
            )
            execution_cfg = load_execution_config()

            # override exchange if provided (payload exchange is underlying, so map if needed)
            option_exchange = payload.get("option_exchange")
            if option_exchange:
                execution_cfg.exchange = option_exchange
            else:
                raw_exchange = payload.get("exchange")
                if raw_exchange == "NSE_INDEX":
                    execution_cfg.exchange = "NFO"
                elif raw_exchange == "BSE_INDEX":
                    execution_cfg.exchange = "BFO"

            execution_cfg.api_key = api_key

            ws_url = os.getenv("WEBSOCKET_URL", "ws://127.0.0.1:8765")
            self.agent = AutoScalperAgent(
                agent_config=agent_cfg,
                risk_config=risk_cfg,
                playbook_config=playbook_cfg,
                advisor_config=advisor_cfg,
                learning_config=learning_cfg,
                execution_config=execution_cfg,
                api_key=api_key,
                ws_url=ws_url,
            )
            get_model_tuning_service(model_tuner_cfg)
            self.last_config = payload
            self.agent.start()
            logger.info("Auto scalper started")
            return True, "Auto scalper started"

    def stop(self, reason: str = "Stopped") -> Tuple[bool, str]:
        with self.lock:
            if not self.agent:
                return False, "Auto scalper not running"
            self.agent.stop(reason)
            self.agent = None
            logger.info("Auto scalper stopped")
            return True, "Auto scalper stopped"

    def status(self) -> Dict[str, Any]:
        with self.lock:
            if not self.agent:
                return {"running": False, "enabled": False}
            snapshot = self.agent.get_status()
            return snapshot

    def update(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        with self.lock:
            if not self.agent:
                return False, "Auto scalper not running"
            # update modes live
            if "paper_mode" in payload or "assist_only" in payload:
                self.agent.execution.update_modes(
                    paper_mode=payload.get("paper_mode"), assist_only=payload.get("assist_only")
                )
            if "trade_mode" in payload:
                self.agent.agent_config.trade_mode = str(payload.get("trade_mode", "AUTO")).upper()
            if "reverse_trades" in payload:
                self.agent.agent_config.reverse_trades = bool(payload.get("reverse_trades"))
            if "avg_enabled" in payload:
                self.agent.agent_config.avg_enabled = bool(payload.get("avg_enabled"))
                if not self.agent.agent_config.avg_enabled:
                    self.agent.avg_session = None
            if "avg_only_profit" in payload:
                self.agent.agent_config.avg_only_profit = bool(payload.get("avg_only_profit"))
            if "rr_guard_enabled" in payload:
                self.agent.agent_config.rr_guard_enabled = bool(payload.get("rr_guard_enabled"))
            if "max_qty" in payload:
                self.agent.agent_config.max_qty = int(payload.get("max_qty") or 0)
            if "min_move_pts" in payload:
                self.agent.agent_config.min_move_pts = float(payload.get("min_move_pts") or 0)
            if "index_bias_mode" in payload:
                self.agent.agent_config.index_bias_mode = str(payload.get("index_bias_mode", "FILTER")).upper()
            if "index_bias_min_score" in payload:
                self.agent.agent_config.index_bias_min_score = int(payload.get("index_bias_min_score") or 2)
            if "index_ema_enabled" in payload:
                self.agent.agent_config.index_ema_enabled = bool(payload.get("index_ema_enabled"))
            if "index_vwap_enabled" in payload:
                self.agent.agent_config.index_vwap_enabled = bool(payload.get("index_vwap_enabled"))
            if "index_rsi_enabled" in payload:
                self.agent.agent_config.index_rsi_enabled = bool(payload.get("index_rsi_enabled"))
            if "index_adx_enabled" in payload:
                self.agent.agent_config.index_adx_enabled = bool(payload.get("index_adx_enabled"))
            if "index_supertrend_enabled" in payload:
                self.agent.agent_config.index_supertrend_enabled = bool(payload.get("index_supertrend_enabled"))
            if "index_rsi_bull" in payload:
                self.agent.agent_config.index_rsi_bull = float(payload.get("index_rsi_bull") or 55.0)
            if "index_rsi_bear" in payload:
                self.agent.agent_config.index_rsi_bear = float(payload.get("index_rsi_bear") or 45.0)
            if "index_adx_min" in payload:
                self.agent.agent_config.index_adx_min = float(payload.get("index_adx_min") or 18.0)
            if "index_vwap_buffer" in payload:
                self.agent.agent_config.index_vwap_buffer = float(payload.get("index_vwap_buffer") or 0.0)
            if "breakeven_enabled" in payload:
                self.agent.agent_config.breakeven_enabled = bool(payload.get("breakeven_enabled"))
            if "breakeven_delay_s" in payload:
                self.agent.agent_config.breakeven_delay_s = float(payload.get("breakeven_delay_s") or 0)
            if "breakeven_buffer_pts" in payload:
                self.agent.agent_config.breakeven_buffer_pts = float(payload.get("breakeven_buffer_pts") or 0)
            if "profit_lock_pts" in payload:
                self.agent.agent_config.profit_lock_pts = float(payload.get("profit_lock_pts") or 0)
            if "profit_lock_rs" in payload:
                self.agent.agent_config.profit_lock_rs = float(payload.get("profit_lock_rs") or 0)
            playbook_changes = {}
            for key in ("momentum_ticks", "tp_points", "sl_points", "trail_distance", "trail_step"):
                if key in payload:
                    if key == "momentum_ticks":
                        playbook_changes[key] = int(payload.get(key))
                    else:
                        playbook_changes[key] = float(payload.get(key))
            if playbook_changes:
                self.agent.playbook_manager.apply_adjustments(playbook_changes)
            if any(
                key in payload
                for key in (
                    "model_tuner_enabled",
                    "model_tuner_provider",
                    "model_tuner_model",
                    "model_tuner_base_url",
                    "model_tuner_timeout_s",
                    "model_tuner_interval_s",
                    "model_tuner_min_trades",
                    "model_tuner_auto_apply_paper",
                    "model_tuner_apply_clamps",
                    "model_tuner_notify_email",
                    "model_tuner_notify_telegram",
                    "model_tuner_underlying",
                )
            ):
                current = get_model_tuning_service().config
                updated = ModelTunerConfig(
                    enabled=bool(payload.get("model_tuner_enabled", current.enabled)),
                    provider=str(payload.get("model_tuner_provider", current.provider)),
                    model=payload.get("model_tuner_model", current.model),
                    base_url=payload.get("model_tuner_base_url", current.base_url),
                    timeout_s=float(payload.get("model_tuner_timeout_s", current.timeout_s)),
                    interval_s=int(payload.get("model_tuner_interval_s", current.interval_s)),
                    min_trades=int(payload.get("model_tuner_min_trades", current.min_trades)),
                    auto_apply_paper=bool(
                        payload.get("model_tuner_auto_apply_paper", current.auto_apply_paper)
                    ),
                    apply_clamps=bool(payload.get("model_tuner_apply_clamps", current.apply_clamps)),
                    notify_email=bool(
                        payload.get("model_tuner_notify_email", current.notify_email)
                    ),
                    notify_telegram=bool(
                        payload.get("model_tuner_notify_telegram", current.notify_telegram)
                    ),
                    underlying=str(payload.get("model_tuner_underlying", current.underlying)).upper(),
                    db_path=current.db_path,
                )
                get_model_tuning_service(updated)
            if payload:
                self.last_config.update(payload)
            return True, "Updated"

    def apply_model_tuning(self, changes: Dict[str, Any]) -> Tuple[bool, str]:
        with self.lock:
            if not self.agent:
                return False, "Auto scalper not running"
            playbook_fields = ("momentum_ticks", "tp_points", "sl_points", "trail_distance", "trail_step")
            playbook_changes = {k: v for k, v in changes.items() if k in playbook_fields}
            if playbook_changes:
                self.agent.playbook_manager.apply_adjustments(playbook_changes)
            if "index_bias_min_score" in changes:
                self.agent.agent_config.index_bias_min_score = int(changes["index_bias_min_score"])
            if "index_rsi_bull" in changes:
                self.agent.agent_config.index_rsi_bull = float(changes["index_rsi_bull"])
            if "index_rsi_bear" in changes:
                self.agent.agent_config.index_rsi_bear = float(changes["index_rsi_bear"])
            if "index_adx_min" in changes:
                self.agent.agent_config.index_adx_min = float(changes["index_adx_min"])
            if "index_vwap_buffer" in changes:
                self.agent.agent_config.index_vwap_buffer = float(changes["index_vwap_buffer"])
            if self.last_config is not None:
                self.last_config.update(changes)
            return True, "Model tuning applied"


_manager = AutoScalperManager()


def get_ai_scalper_manager() -> AutoScalperManager:
    return _manager
