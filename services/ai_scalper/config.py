from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Dict


@dataclass
class RiskConfig:
    per_trade_max_loss: float = 2000.0
    daily_max_loss: float = 10000.0
    max_trades_per_min: int = 10
    min_entry_gap_ms: int = 3000
    cooldown_after_loss_s: int = 25
    flip_cooldown_s: int = 5
    min_hold_before_flip_s: int = 3
    max_trade_duration_s: int = 60
    time_exit_tighten_pts: float = 1.0
    spread_max_nifty: float = 0.20
    spread_max_sensex: float = 0.40
    stale_timeout_s: int = 30
    stop_on_stale: bool = True


@dataclass
class PlaybookConfig:
    momentum_ticks: int = 4
    tp_points: float = 8.0
    sl_points: float = 5.0
    trail_distance: float = 2.0
    trail_step: float = 0.05
    trailing_enabled: bool = True
    trailing_override_tp: bool = True


@dataclass
class AdvisorConfig:
    enabled: bool = True
    auto_apply: bool = True
    provider: str = "none"  # none | http | openai | anthropic | ollama
    url: str | None = None
    model: str | None = None
    base_url: str | None = None
    anthropic_version: str = "2023-06-01"
    timeout_s: float = 0.6
    interval_s: int = 45


@dataclass
class ModelTunerConfig:
    enabled: bool = False
    provider: str = "none"  # openai | anthropic | ollama
    model: str | None = None
    base_url: str | None = None
    timeout_s: float = 4.0
    interval_s: int = 900
    min_trades: int = 30
    auto_apply_paper: bool = True
    apply_clamps: bool = True
    notify_email: bool = False
    notify_telegram: bool = False
    underlying: str = "AUTO"  # AUTO | NIFTY | SENSEX
    db_path: str = "db/ai_scalper_tuning.db"


@dataclass
class ExecutionConfig:
    host: str = "http://127.0.0.1:5000"
    api_key: str | None = None
    exchange: str = "NFO"
    product: str = "MIS"
    rate_limit_per_sec: int = 2
    strategy: str = "AI_SCALPER"


@dataclass
class AgentConfig:
    ce_symbol: str
    pe_symbol: str
    underlying: str
    expiry: str
    lot_size: int
    underlying_exchange: str | None = None
    entry_lots: int = 2
    scale_lots: int = 1
    max_lots_per_strike: int = 3
    max_qty: int = 0
    avg_window_s: int = 12
    avg_interval_s: int = 4
    avg_enabled: bool = True
    avg_only_profit: bool = True
    min_move_pts: float = 1.0
    candle_confirm_enabled: bool = True
    candle_confirm_mode: str = "EMA9"  # EMA9 | PREV
    candle_confirm_ticks: int = 4
    imbalance_ratio: float = 1.8
    underlying_direction_filter: bool = True
    underlying_momentum_ticks: int = 2
    relative_strength_enabled: bool = True
    relative_strength_diff: int = 1
    index_bias_mode: str = "FILTER"  # OFF | FILTER | STRONG
    index_bias_min_score: int = 2
    index_ema_enabled: bool = True
    index_vwap_enabled: bool = True
    index_rsi_enabled: bool = True
    index_adx_enabled: bool = True
    index_supertrend_enabled: bool = True
    index_rsi_bull: float = 55.0
    index_rsi_bear: float = 45.0
    index_adx_min: float = 18.0
    index_vwap_buffer: float = 0.0
    telegram_alerts_enabled: bool = False
    telegram_alerts_entry: bool = True
    telegram_alerts_exit: bool = True
    telegram_alerts_tune: bool = True
    assist_only: bool = False
    paper_mode: bool = True
    enable_imbalance: bool = False
    enable_depth: bool = True
    enable_spread_filter: bool = True
    tick_size: float = 0.05
    trade_mode: str = "AUTO"  # AUTO | CE | PE
    reverse_trades: bool = False
    rr_guard_enabled: bool = True
    breakeven_enabled: bool = True
    breakeven_delay_s: float = 1.0
    breakeven_buffer_pts: float = 1.0
    profit_lock_pts: float = 0.0
    profit_lock_rs: float = 0.0
    strike_offset: str = "ATM"  # ATM, ATM+1, ATM+2, etc
    expiry_otm: bool = True
    auto_roll: bool = True
    auto_roll_nifty: float = 50.0
    auto_roll_sensex: float = 100.0
    depth_level: int = 5


def load_execution_config() -> ExecutionConfig:
    host = os.getenv("HOST_SERVER", "http://127.0.0.1:5000").rstrip("/")
    api_key = os.getenv("AI_SCALPER_API_KEY")
    exchange = os.getenv("AI_SCALPER_EXCHANGE", "NFO")
    product = os.getenv("AI_SCALPER_PRODUCT", "MIS")
    rate_limit = int(os.getenv("AI_SCALPER_RPS", "2"))
    strategy = os.getenv("AI_SCALPER_STRATEGY", "AI_SCALPER")
    return ExecutionConfig(
        host=host,
        api_key=api_key,
        exchange=exchange,
        product=product,
        rate_limit_per_sec=rate_limit,
        strategy=strategy,
    )


def build_agent_config(payload: Dict[str, Any]) -> AgentConfig:
    return AgentConfig(
        ce_symbol=payload["ce_symbol"],
        pe_symbol=payload["pe_symbol"],
        underlying=payload["underlying"],
        underlying_exchange=payload.get("underlying_exchange"),
        expiry=payload["expiry"],
        lot_size=int(payload.get("lot_size", 1)),
        entry_lots=int(payload.get("entry_lots", 2)),
        scale_lots=int(payload.get("scale_lots", 1)),
        max_lots_per_strike=int(payload.get("max_lots_per_strike", 3)),
        max_qty=int(payload.get("max_qty", 0)),
        avg_window_s=int(payload.get("avg_window_s", 12)),
        avg_interval_s=int(payload.get("avg_interval_s", 4)),
        avg_enabled=bool(payload.get("avg_enabled", True)),
        avg_only_profit=bool(payload.get("avg_only_profit", True)),
        min_move_pts=float(payload.get("min_move_pts", 1.0)),
        candle_confirm_enabled=bool(payload.get("candle_confirm_enabled", True)),
        candle_confirm_mode=str(payload.get("candle_confirm_mode", "EMA9")).upper(),
        candle_confirm_ticks=int(payload.get("candle_confirm_ticks", 4)),
        imbalance_ratio=float(payload.get("imbalance_ratio", 1.8)),
        underlying_direction_filter=bool(payload.get("underlying_direction_filter", True)),
        underlying_momentum_ticks=int(payload.get("underlying_momentum_ticks", 2)),
        relative_strength_enabled=bool(payload.get("relative_strength_enabled", True)),
        relative_strength_diff=int(payload.get("relative_strength_diff", 1)),
        index_bias_mode=str(payload.get("index_bias_mode", "FILTER")).upper(),
        index_bias_min_score=int(payload.get("index_bias_min_score", 2)),
        index_ema_enabled=bool(payload.get("index_ema_enabled", True)),
        index_vwap_enabled=bool(payload.get("index_vwap_enabled", True)),
        index_rsi_enabled=bool(payload.get("index_rsi_enabled", True)),
        index_adx_enabled=bool(payload.get("index_adx_enabled", True)),
        index_supertrend_enabled=bool(payload.get("index_supertrend_enabled", True)),
        index_rsi_bull=float(payload.get("index_rsi_bull", 55.0)),
        index_rsi_bear=float(payload.get("index_rsi_bear", 45.0)),
        index_adx_min=float(payload.get("index_adx_min", 18.0)),
        index_vwap_buffer=float(payload.get("index_vwap_buffer", 0.0)),
        telegram_alerts_enabled=bool(payload.get("telegram_alerts_enabled", False)),
        telegram_alerts_entry=bool(payload.get("telegram_alerts_entry", True)),
        telegram_alerts_exit=bool(payload.get("telegram_alerts_exit", True)),
        telegram_alerts_tune=bool(payload.get("telegram_alerts_tune", True)),
        assist_only=bool(payload.get("assist_only", False)),
        paper_mode=bool(payload.get("paper_mode", True)),
        enable_imbalance=bool(payload.get("enable_imbalance", False)),
        enable_depth=bool(payload.get("enable_depth", True)),
        enable_spread_filter=bool(payload.get("enable_spread_filter", True)),
        tick_size=float(payload.get("tick_size", 0.05)),
        trade_mode=str(payload.get("trade_mode", "AUTO")).upper(),
        reverse_trades=bool(payload.get("reverse_trades", False)),
        rr_guard_enabled=bool(payload.get("rr_guard_enabled", True)),
        breakeven_enabled=bool(payload.get("breakeven_enabled", True)),
        breakeven_delay_s=float(payload.get("breakeven_delay_s", 1.0)),
        breakeven_buffer_pts=float(payload.get("breakeven_buffer_pts", 1.0)),
        profit_lock_pts=float(payload.get("profit_lock_pts", 0.0)),
        profit_lock_rs=float(payload.get("profit_lock_rs", 0.0)),
        strike_offset=str(payload.get("strike_offset", "ATM")).upper(),
        expiry_otm=bool(payload.get("expiry_otm", True)),
        auto_roll=bool(payload.get("auto_roll", True)),
        auto_roll_nifty=float(payload.get("auto_roll_nifty", 50.0)),
        auto_roll_sensex=float(payload.get("auto_roll_sensex", 100.0)),
        depth_level=int(payload.get("depth_level", 5)),
    )
