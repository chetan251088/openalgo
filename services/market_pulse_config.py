"""
Market Pulse Dashboard — All scoring thresholds, weights, and symbol definitions.
Edit values here to tune scoring without touching logic.
"""

# ── Scoring Weights ──────────────────────────────────────────────
CATEGORY_WEIGHTS = {
    "volatility": 0.25,
    "momentum": 0.25,
    "trend": 0.20,
    "breadth": 0.20,
    "macro": 0.10,
}

# ── Decision Thresholds ──────────────────────────────────────────
DECISION_THRESHOLDS = {
    "yes_min": 80,      # >= 80 → YES
    "caution_min": 60,   # >= 60 → CAUTION
    # < 60 → NO
}

# ── Volatility Scoring ──────────────────────────────────────────
VOLATILITY = {
    "vix_level_weight": 0.40,
    "vix_slope_weight": 0.25,
    "vix_percentile_weight": 0.20,
    "pcr_weight": 0.15,
    # VIX level ranges → scores
    "vix_optimal_low": 11,
    "vix_optimal_high": 16,
    "vix_elevated_high": 22,
    "vix_spike_threshold": 25,
    "vix_complacency_threshold": 10,
    # VIX slope
    "slope_falling_bonus": 20,
    "slope_rising_penalty": -30,
    # VIX percentile
    "percentile_low_threshold": 30,
    "percentile_high_threshold": 80,
    # PCR ranges
    "pcr_healthy_low": 0.8,
    "pcr_healthy_high": 1.3,
    "pcr_extreme_fear": 1.5,
    "pcr_extreme_greed": 0.6,
}

# ── Momentum Scoring ────────────────────────────────────────────
MOMENTUM = {
    "sector_participation_weight": 0.35,
    "leadership_spread_weight": 0.25,
    "higher_highs_weight": 0.25,
    "rotation_health_weight": 0.15,
    # Sector participation thresholds (out of 12 sectors)
    "sectors_strong": 8,
    "sectors_moderate_low": 4,
    # Leadership spread (top3 vs bottom3 5d return spread %)
    "spread_healthy_low": 2.0,
    "spread_healthy_high": 5.0,
    "spread_concentrated": 8.0,
    # Higher highs (% of Nifty 50 at 20d highs)
    "highs_strong": 40,
    "highs_moderate": 20,
    "highs_weak": 10,
}

# ── Trend Scoring ───────────────────────────────────────────────
TREND = {
    "nifty_ma_weight": 0.35,
    "banknifty_weight": 0.20,
    "rsi_weight": 0.25,
    "ma_slope_weight": 0.20,
    # RSI ranges
    "rsi_strong_low": 50,
    "rsi_strong_high": 65,
    "rsi_overbought": 75,
    "rsi_oversold": 30,
    # MA periods used
    "ma_periods": [20, 50, 200],
}

# ── Breadth Scoring ─────────────────────────────────────────────
BREADTH = {
    "ad_ratio_weight": 0.30,
    "above_50d_weight": 0.30,
    "above_200d_weight": 0.20,
    "highs_lows_weight": 0.20,
    # A/D ratio thresholds
    "ad_strong": 2.0,
    "ad_healthy_low": 1.2,
    "ad_neutral_low": 0.8,
    "ad_weak": 0.5,
}

# ── Macro Scoring ───────────────────────────────────────────────
MACRO = {
    "usdinr_weight": 0.25,
    "rbi_stance_weight": 0.20,
    "event_risk_weight": 0.20,
    "global_risk_weight": 0.15,
    "institutional_weight": 0.20,
    # Event horizon (hours)
    "event_major_hours": 72,
}

# ── Execution Window (Swing) ────────────────────────────────────
EXECUTION_SWING = {
    "breakout_hold_weight": 0.30,
    "follow_through_weight": 0.30,
    "failure_rate_weight": 0.20,
    "pullback_buying_weight": 0.20,
    "lookback_days": 10,
    "breakout_period": 20,  # 20-day high cross = breakout
    "hold_check_days": 3,   # check if held after 1-3 days
}

# ── Execution Window (Day Trading additions) ────────────────────
EXECUTION_DAY = {
    "trend_consistency_weight": 0.25,
    "gap_fill_weight": 0.25,
    "sector_followthrough_weight": 0.25,
    "vix_divergence_weight": 0.25,
    "range_conviction_pct": 25,  # closing in top/bottom 25% of range
    "gap_lookback_days": 5,
}

# ── Index Symbols (Zerodha format) ──────────────────────────────
INDEX_SYMBOLS = {
    "NIFTY": {"symbol": "NIFTY", "exchange": "NSE_INDEX"},
    "SENSEX": {"symbol": "SENSEX", "exchange": "BSE_INDEX"},
    "BANKNIFTY": {"symbol": "BANKNIFTY", "exchange": "NSE_INDEX"},
    "INDIAVIX": {"symbol": "INDIAVIX", "exchange": "NSE_INDEX"},
    "FINNIFTY": {"symbol": "FINNIFTY", "exchange": "NSE_INDEX"},
}

SECTOR_INDICES = {
    "BANK": {"symbol": "BANKNIFTY", "exchange": "NSE_INDEX"},
    "IT": {"symbol": "NIFTYIT", "exchange": "NSE_INDEX"},
    "FMCG": {"symbol": "NIFTYFMCG", "exchange": "NSE_INDEX"},
    "AUTO": {"symbol": "NIFTYAUTO", "exchange": "NSE_INDEX"},
    "PHARMA": {"symbol": "NIFTYPHARMA", "exchange": "NSE_INDEX"},
    "METAL": {"symbol": "NIFTYMETAL", "exchange": "NSE_INDEX"},
    "PSUBANK": {"symbol": "NIFTYPSUBANK", "exchange": "NSE_INDEX"},
    "ENERGY": {"symbol": "NIFTYENERGY", "exchange": "NSE_INDEX"},
    "FINSERV": {"symbol": "FINNIFTY", "exchange": "NSE_INDEX"},
    "REALTY": {"symbol": "NIFTYREALTY", "exchange": "NSE_INDEX"},
    "CONSDUR": {"symbol": "NIFTYCONSUMPTION", "exchange": "NSE_INDEX"},
    "MEDIA": {"symbol": "NIFTYMEDIA", "exchange": "NSE_INDEX"},
}

# USDINR currency futures
USDINR_SYMBOL = {"symbol": "USDINR", "exchange": "CDS"}

# Data freshness
CACHE_TTL_SECONDS = 30
RESPONSE_CACHE_TTL_SECONDS = 30
ANALYST_REFRESH_SECONDS = 300  # 5 minutes
FRONTEND_POLL_SECONDS = 45

# LLM defaults (overridden by .env)
LLM_DEFAULTS = {
    "provider": "claude",
    "model": "claude-sonnet-4-6",
}
