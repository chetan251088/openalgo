"""
TOMIC Config — Canonical Runtime Configuration
================================================
All 90+ parameters from expert consultations. Role-based broker endpoints.
Per-strategy legging policies. Circuit breaker thresholds.

No hardcoded ports or broker-specific values in any agent code.
All endpoints resolved through BrokerEndpoints at startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Broker-Agnostic Endpoint Config (role-based, never hardcoded)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrokerEndpoints:
    """Resolved from .env.tomic at startup. Agents reference roles, never ports."""
    feed_primary_ws: str = ""
    feed_fallback_ws: str = ""
    execution_rest: str = ""
    execution_api_key: str = ""
    analytics_rest: str = ""       # OI/GEX/IV Smile/Vol Surface/Straddle APIs

    @classmethod
    def from_env(cls) -> "BrokerEndpoints":
        return cls(
            feed_primary_ws=os.getenv("TOMIC_FEED_PRIMARY_WS", ""),
            feed_fallback_ws=os.getenv("TOMIC_FEED_FALLBACK_WS", ""),
            execution_rest=os.getenv("TOMIC_EXECUTION_REST", ""),
            execution_api_key=os.getenv("TOMIC_EXECUTION_API_KEY", ""),
            analytics_rest=os.getenv("TOMIC_ANALYTICS_REST", ""),
        )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RegimePhase(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    CONGESTION = "CONGESTION"
    BLOWOFF = "BLOWOFF"


class LeggingPolicy(str, Enum):
    """Per-strategy legging policy — locked at config time, no runtime switching."""
    ATOMIC_PREFERRED = "ATOMIC_PREFERRED"   # basketorder first
    HEDGE_FIRST = "HEDGE_FIRST"             # buy protective leg first
    SHORT_FIRST_KILL_SWITCH = "SHORT_FIRST_KILL_SWITCH"  # short first + 3s market hedge
    SINGLE_LEG = "SINGLE_LEG"              # no multi-leg


class StrategyType(str, Enum):
    BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
    BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"
    IRON_CONDOR = "IRON_CONDOR"
    JADE_LIZARD = "JADE_LIZARD"
    SHORT_STRANGLE = "SHORT_STRANGLE"
    SHORT_STRADDLE = "SHORT_STRADDLE"
    RISK_REVERSAL = "RISK_REVERSAL"
    DITM_CALL = "DITM_CALL"
    DITM_PUT = "DITM_PUT"
    CALENDAR_DIAGONAL = "CALENDAR_DIAGONAL"


class TomicMode(str, Enum):
    SANDBOX = "sandbox"
    SEMI_AUTO = "semi_auto"
    FULL_AUTO = "full_auto"


# ---------------------------------------------------------------------------
# Per-Strategy Legging Policy Table (Invariant 1 from plan §5)
# ---------------------------------------------------------------------------

LEGGING_POLICY: Dict[StrategyType, LeggingPolicy] = {
    StrategyType.BULL_PUT_SPREAD:   LeggingPolicy.HEDGE_FIRST,      # buy long put → sell short put
    StrategyType.BEAR_CALL_SPREAD:  LeggingPolicy.HEDGE_FIRST,      # buy long call → sell short call
    StrategyType.IRON_CONDOR:       LeggingPolicy.HEDGE_FIRST,      # buy wings → sell shorts per side
    StrategyType.JADE_LIZARD:       LeggingPolicy.HEDGE_FIRST,      # call spread hedge + short put
    StrategyType.SHORT_STRANGLE:    LeggingPolicy.SHORT_FIRST_KILL_SWITCH,  # advanced naked premium sell
    StrategyType.SHORT_STRADDLE:    LeggingPolicy.SHORT_FIRST_KILL_SWITCH,  # advanced naked premium sell
    StrategyType.RISK_REVERSAL:     LeggingPolicy.SHORT_FIRST_KILL_SWITCH,  # premium capture; call is cheap
    StrategyType.DITM_CALL:         LeggingPolicy.SINGLE_LEG,       # no hedge leg
    StrategyType.DITM_PUT:          LeggingPolicy.SINGLE_LEG,       # no hedge leg
    StrategyType.CALENDAR_DIAGONAL: LeggingPolicy.HEDGE_FIRST,      # buy back month → sell front
}


# ---------------------------------------------------------------------------
# Regime Agent Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IchimokuParams:
    tenkan: int = 9
    kijun: int = 26
    senkou_b: int = 52


@dataclass(frozen=True)
class ImpulseParams:
    ema_period: int = 13
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


@dataclass(frozen=True)
class RegimeParams:
    ichimoku: IchimokuParams = field(default_factory=IchimokuParams)
    impulse: ImpulseParams = field(default_factory=ImpulseParams)
    # Congestion detection
    bbw_period: int = 20                     # Bollinger Band Width lookback
    congestion_min_candles: int = 5          # min candles overlapping for congestion
    # Blowoff detection
    blowoff_atr_multiple: float = 3.0        # price > N × ATR from 20-MA
    blowoff_volume_multiple: float = 2.0     # volume > N × 50-day avg
    # Regime scoring
    score_min: int = -20
    score_max: int = 20
    pcr_bullish_threshold: float = 1.2       # PCR > 1.2 + price > Cloud → bullish bonus
    pcr_bonus: int = 5
    # Multi-timeframe
    daily_tf: str = "1D"
    intraday_tf: str = "15min"


# ---------------------------------------------------------------------------
# VIX Rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VIXRules:
    stop_selling_below: float = 12.0         # premiums too low to sell
    sweet_spot_low: float = 15.0
    sweet_spot_high: float = 25.0
    defined_risk_only_above: float = 25.0
    half_size_above: float = 30.0
    halt_short_vega_above: float = 40.0
    score_cap_below_12: int = 5              # score capped at ±5 when VIX < 12


# ---------------------------------------------------------------------------
# Sniper Agent Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SniperParams:
    # VCP
    vcp_contractions_min: int = 2
    vcp_contractions_max: int = 4
    vcp_depth_ratio: float = 0.5             # each contraction ~half prior
    volume_ants_threshold: float = 0.5       # < 50% of 50-day avg
    # S/D Zones
    sd_zone_max_touches: int = 1             # invalidated after 1 touch
    # 3-C Cup
    cup_weeks_min: int = 3
    cup_weeks_max: int = 4
    # Signal ranking
    rs_lookback_days: int = 50
    sector_heat_limit: float = 0.20          # 20% margin per sector


# ---------------------------------------------------------------------------
# Volatility Agent Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VolatilityParams:
    # IV vs HV
    iv_hv_ratio_trigger: float = 1.25        # IV > 1.25 × HV → credit spreads
    iv_rank_credit_threshold: float = 50.0   # IV Rank > 50
    iv_rank_debit_threshold: float = 30.0    # IV Rank < 30 → DITM
    # Skew
    skew_put_call_ratio: float = 1.5         # 25Δ Put IV > 1.5× 25Δ Call IV → Risk Reversal
    # Spread parameters
    credit_short_delta_min: float = 0.20
    credit_short_delta_max: float = 0.30
    nifty_wing_width: int = 200              # points
    banknifty_wing_width: int = 500          # points
    # DTE
    income_dte_min: int = 30
    income_dte_max: int = 45
    momentum_dte_min: int = 5
    momentum_dte_max: int = 10
    # DITM Calls
    ditm_delta_min: float = 0.90
    ditm_delta_fallback: float = 0.80        # if spread > 50 paisa
    ditm_min_daily_volume: int = 500_000


# ---------------------------------------------------------------------------
# Risk / Sizing Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SizingParams:
    target_vol: float = 0.20                 # 20% annualized
    ewma_lookback: int = 35                  # 35-day EWMA for instrument vol
    half_kelly: bool = True
    max_risk_per_trade: float = 0.02         # 2% rule
    sector_heat_limit: float = 0.20          # 20% margin per sector
    idm_correlation_threshold: float = 0.7   # correlation > 0.7 → 0.7× reduction
    idm_reduction_factor: float = 0.7
    margin_reserve: float = 0.25             # 25% free margin always
    max_positions: int = 10
    max_per_underlying: int = 1              # 1 per underlying per direction


# ---------------------------------------------------------------------------
# Position Lifecycle Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LifecycleParams:
    credit_profit_take: float = 0.50         # close at 50% of max profit
    credit_loss_cut_multiple: float = 2.0    # close at 2× credit received
    time_stop_bars: int = 5                  # 5 bars no movement → close
    elder_safezone_lookback: int = 20        # for trailing DITM
    ma_exit_period: int = 20                 # close DITM below 20-MA


# ---------------------------------------------------------------------------
# Black Swan Hedge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlackSwanParams:
    put_delta: float = 0.05                  # 5-delta NIFTY puts
    bootstrap_budget_inr: float = 5000.0     # fixed ₹5K/month until 30 trades
    min_trades_for_pct: int = 30             # switch to % after 30 trades
    profit_pct_min: float = 0.01             # 1-2% of expected monthly profit
    profit_pct_max: float = 0.02
    underlying: str = "NIFTY"


# ---------------------------------------------------------------------------
# Circuit Breaker Thresholds (§6 — above all strategy logic)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CircuitBreakerThresholds:
    daily_max_loss_pct: float = 0.06         # -6% of capital → kill switch
    max_orders_per_minute: int = 30
    max_gross_notional_multiple: float = 5.0  # 5× capital
    per_underlying_margin_cap: float = 0.30   # 30% of used margin
    unhedged_timeout_seconds: float = 5.0     # Invariant 2


# ---------------------------------------------------------------------------
# Execution Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecutionParams:
    smart_order_delay: float = 0.5           # seconds between legs
    slippage_equity_max: float = 0.001       # 0.1% stock price
    slippage_option_ticks: int = 2           # 2 ticks options
    twap_volume_threshold: float = 0.10      # > 10% of 1-min avg vol → slice
    twap_slices: int = 4                     # 3-5 parts
    kill_switch_timeout: float = 3.0         # seconds for hedge leg market fill
    unfilled_limit_cancel_bars: int = 3      # cancel limit order after 3 bars
    no_entry_start: str = "09:15"            # no new entries 9:15-9:30
    no_entry_end: str = "09:30"
    auto_square_off: str = "15:15"           # MIS by 3:15 PM
    command_poll_interval: float = 0.1       # 100ms poll


# ---------------------------------------------------------------------------
# Data Freshness Thresholds (§4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreshnessThresholds:
    # Market data
    underlying_quote_max_age: float = 5.0    # seconds
    option_quote_max_age: float = 10.0
    feed_switch_cooldown: float = 30.0       # block orders for 30s after switch
    # Analytics data
    pcr_max_age: float = 120.0
    gex_max_age: float = 120.0
    max_pain_max_age: float = 300.0
    iv_greeks_max_age: float = 60.0
    iv_greeks_hard_block: float = 120.0      # block credit spreads after 120s
    vix_max_age: float = 60.0


# ---------------------------------------------------------------------------
# Observability (§13)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObservabilityThresholds:
    order_latency_p99_alert_ms: float = 500.0
    reject_rate_alert_pct: float = 10.0      # rolling 1h
    stale_blocks_per_hour_alert: int = 20
    feed_failovers_per_day_alert: int = 3
    lease_reclaims_per_hour_alert: int = 5


# ---------------------------------------------------------------------------
# Supervisor / Operational
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SupervisorParams:
    heartbeat_interval: float = 60.0         # seconds
    agent_restart_max_retries: int = 3
    agent_restart_backoff: float = 10.0      # seconds
    safe_shutdown_timeout: float = 5.0       # max seconds for agents to finish
    lease_timeout: float = 30.0              # seconds before lease expires
    exec_broker_consecutive_timeout_kill: int = 5  # kill switch after N timeouts


# ---------------------------------------------------------------------------
# Paper / Scaling
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaperParams:
    min_days: int = 30
    min_signals: int = 50
    max_drawdown_pct: float = 0.10           # DD < 10%
    sandbox_spread_leverage: float = 5.0     # 5× for defined-risk spreads


@dataclass(frozen=True)
class ScalingParams:
    month_1_pct: float = 0.25
    month_2_pct: float = 0.50
    month_3_pct: float = 1.00


# ---------------------------------------------------------------------------
# Universe Filtering (§9)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UniverseParams:
    daily_rs_top_n: int = 50                 # top 50 by RS(50d)
    daily_min_volume: int = 500_000
    intraday_rescore_interval_min: int = 15
    hot_list_size: int = 10
    always_included: tuple = ("NIFTY", "BANKNIFTY")


# ---------------------------------------------------------------------------
# Root Config — aggregates everything
# ---------------------------------------------------------------------------

@dataclass
class TomicConfig:
    """Root config object. Instantiated once at startup. Immutable during runtime."""
    mode: TomicMode = TomicMode.SANDBOX
    endpoints: BrokerEndpoints = field(default_factory=BrokerEndpoints.from_env)
    regime: RegimeParams = field(default_factory=RegimeParams)
    vix: VIXRules = field(default_factory=VIXRules)
    sniper: SniperParams = field(default_factory=SniperParams)
    volatility: VolatilityParams = field(default_factory=VolatilityParams)
    sizing: SizingParams = field(default_factory=SizingParams)
    lifecycle: LifecycleParams = field(default_factory=LifecycleParams)
    black_swan: BlackSwanParams = field(default_factory=BlackSwanParams)
    circuit_breakers: CircuitBreakerThresholds = field(default_factory=CircuitBreakerThresholds)
    execution: ExecutionParams = field(default_factory=ExecutionParams)
    freshness: FreshnessThresholds = field(default_factory=FreshnessThresholds)
    observability: ObservabilityThresholds = field(default_factory=ObservabilityThresholds)
    supervisor: SupervisorParams = field(default_factory=SupervisorParams)
    paper: PaperParams = field(default_factory=PaperParams)
    scaling: ScalingParams = field(default_factory=ScalingParams)
    universe: UniverseParams = field(default_factory=UniverseParams)

    @classmethod
    def load(cls, mode: Optional[str] = None) -> "TomicConfig":
        """Load config from environment. Override mode if provided."""
        resolved_mode = TomicMode(mode) if mode else TomicMode(
            os.getenv("TOMIC_MODE", "sandbox")
        )
        return cls(mode=resolved_mode)
