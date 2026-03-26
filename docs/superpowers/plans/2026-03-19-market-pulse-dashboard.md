# Market Pulse Dashboard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Bloomberg Terminal-style "Should I Be Trading India?" dashboard with automated scoring, rule-based screener, and AI analyst commentary.

**Architecture:** Flask backend with 30s-cached data pipeline (broker APIs + NSE website), scoring engine computing 5 category scores + execution window, rule-based stock/F&O screener, provider-agnostic LLM analyst. React frontend with 45s polling, dark terminal aesthetic, full-width layout.

**Tech Stack:** Flask, Flask-RESTX, marshmallow, DuckDB, requests, pandas, React 19, TypeScript, TanStack Query, Tailwind CSS, Zustand

**Spec:** `docs/superpowers/specs/2026-03-19-market-pulse-dashboard-design.md`

---

## File Map

### Backend (Create)
| File | Responsibility |
|------|---------------|
| `services/market_pulse_config.py` | All scoring thresholds, weights, symbol lists — single source of truth |
| `data/market_events.json` | Static event calendar (RBI MPC, Budget, FOMC dates) |
| `data/nifty50_constituents.json` | Nifty 50 stock list with sector mappings |
| `data/sector_indices.json` | 12 sector index symbols for broker API |
| `services/market_pulse_nse.py` | NSE India website data fetcher (advance/decline, highs/lows) |
| `services/market_pulse_data.py` | Broker data fetcher + aggregator + 30s cache (design calls this `market_pulse_service.py` — we split fetching from orchestration; the blueprint in `blueprints/market_pulse.py` acts as the orchestrator) |
| `services/market_pulse_scoring.py` | Scoring engine: 5 categories + execution window + decision |
| `services/market_pulse_execution.py` | Execution window score with DuckDB state tracking |
| `services/market_pulse_screener.py` | Rule-based equity + F&O screener |
| `services/market_pulse_analyst.py` | Provider-agnostic LLM analyst layer |
| `blueprints/market_pulse.py` | Flask blueprint: page route + API endpoint |
| `test/test_market_pulse_scoring.py` | Unit tests for scoring engine |
| `test/test_market_pulse_screener.py` | Unit tests for screener |

### Backend (Modify)
| File | Change |
|------|--------|
| `app.py` | Register `market_pulse_bp` blueprint |
| `.sample.env` | Add `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` |

### Frontend (Create)
| File | Responsibility |
|------|---------------|
| `frontend/src/pages/MarketPulse.tsx` | Main page component with grid layout |
| `frontend/src/api/market-pulse.ts` | API client for `/market-pulse/api/data` (session-auth, used by React frontend; design also specifies a RESTX endpoint at `/api/v1/marketpulse` for external API-key access — add that as a future task) |
| `frontend/src/hooks/useMarketPulse.ts` | TanStack Query hook with 45s polling |
| `frontend/src/components/market-pulse/TickerBar.tsx` | Scrolling ticker with mode toggle + LIVE indicator |
| `frontend/src/components/market-pulse/AlertBanner.tsx` | Event risk warning banner |
| `frontend/src/components/market-pulse/HeroDecision.tsx` | YES/CAUTION/NO badge with scores |
| `frontend/src/components/market-pulse/TerminalAnalysis.tsx` | AI-generated summary |
| `frontend/src/components/market-pulse/RulesFiring.tsx` | Transparent scoring rules list |
| `frontend/src/components/market-pulse/ScorePanel.tsx` | Reusable score card (used 6×) |
| `frontend/src/components/market-pulse/SectorHeatmap.tsx` | Sector performance bars |
| `frontend/src/components/market-pulse/ScoreBreakdown.tsx` | Stacked contribution bar |
| `frontend/src/components/market-pulse/EquityIdeas.tsx` | Stock picks table |
| `frontend/src/components/market-pulse/FnoIdeas.tsx` | F&O ideas table |
| `frontend/src/components/market-pulse/ModeSwitcher.tsx` | Swing / Day Trading toggle (design spec line 275) |

### Frontend (Modify)
| File | Change |
|------|--------|
| `frontend/src/App.tsx` | Add lazy import + route under `<FullWidthLayout>` |

---

## Chunk 1: Static Data + Config + NSE Fetcher

### Task 1: Create scoring config

**Files:**
- Create: `services/market_pulse_config.py`

- [ ] **Step 1: Create the config file with all thresholds and weights**

```python
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
    "usdinr_weight": 0.35,
    "rbi_stance_weight": 0.25,
    "event_risk_weight": 0.25,
    "global_risk_weight": 0.15,
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
    "NIFTY": {"symbol": "NIFTY 50", "exchange": "NSE"},
    "SENSEX": {"symbol": "SENSEX", "exchange": "BSE"},
    "BANKNIFTY": {"symbol": "NIFTY BANK", "exchange": "NSE"},
    "INDIAVIX": {"symbol": "INDIA VIX", "exchange": "NSE"},
    "FINNIFTY": {"symbol": "NIFTY FIN SERVICE", "exchange": "NSE"},
}

SECTOR_INDICES = {
    "BANK": {"symbol": "NIFTY BANK", "exchange": "NSE"},
    "IT": {"symbol": "NIFTY IT", "exchange": "NSE"},
    "FMCG": {"symbol": "NIFTY FMCG", "exchange": "NSE"},
    "AUTO": {"symbol": "NIFTY AUTO", "exchange": "NSE"},
    "PHARMA": {"symbol": "NIFTY PHARMA", "exchange": "NSE"},
    "METAL": {"symbol": "NIFTY METAL", "exchange": "NSE"},
    "PSUBANK": {"symbol": "NIFTY PSU BANK", "exchange": "NSE"},
    "ENERGY": {"symbol": "NIFTY ENERGY", "exchange": "NSE"},
    "FINSERV": {"symbol": "NIFTY FIN SERVICE", "exchange": "NSE"},
    "REALTY": {"symbol": "NIFTY REALTY", "exchange": "NSE"},
    "CONSDUR": {"symbol": "NIFTY CONSR DURBL", "exchange": "NSE"},
    "MEDIA": {"symbol": "NIFTY MEDIA", "exchange": "NSE"},
}

# USDINR currency futures
USDINR_SYMBOL = {"symbol": "USDINR", "exchange": "CDS"}

# Data freshness
CACHE_TTL_SECONDS = 30
ANALYST_REFRESH_SECONDS = 300  # 5 minutes
FRONTEND_POLL_SECONDS = 45

# LLM defaults (overridden by .env)
LLM_DEFAULTS = {
    "provider": "claude",
    "model": "claude-sonnet-4-6",
}
```

- [ ] **Step 2: Commit**

```bash
git add services/market_pulse_config.py
git commit -m "feat(market-pulse): add scoring config with all thresholds and weights"
```

### Task 2: Create static data files

**Files:**
- Create: `data/market_events.json`
- Create: `data/nifty50_constituents.json`
- Create: `data/sector_indices.json`

- [ ] **Step 1: Create market events calendar**

```json
{
  "events": [
    {"date": "2026-04-09", "time": "10:00", "type": "major", "name": "RBI MPC Decision", "category": "rbi"},
    {"date": "2026-06-06", "time": "10:00", "type": "major", "name": "RBI MPC Decision", "category": "rbi"},
    {"date": "2026-08-07", "time": "10:00", "type": "major", "name": "RBI MPC Decision", "category": "rbi"},
    {"date": "2026-10-03", "time": "10:00", "type": "major", "name": "RBI MPC Decision", "category": "rbi"},
    {"date": "2026-12-05", "time": "10:00", "type": "major", "name": "RBI MPC Decision", "category": "rbi"},
    {"date": "2026-05-13", "time": "17:30", "type": "major", "name": "India CPI Release", "category": "macro"},
    {"date": "2026-05-30", "time": "17:30", "type": "major", "name": "India GDP Q4 Release", "category": "macro"},
    {"date": "2026-05-06", "time": "23:30", "type": "major", "name": "US FOMC Decision", "category": "global"},
    {"date": "2026-06-17", "time": "23:30", "type": "major", "name": "US FOMC Decision", "category": "global"},
    {"date": "2026-07-29", "time": "23:30", "type": "major", "name": "US FOMC Decision", "category": "global"},
    {"date": "2026-02-01", "time": "11:00", "type": "major", "name": "Union Budget 2026", "category": "budget"},
    {"date": "2026-04-15", "time": "17:30", "type": "minor", "name": "India WPI Release", "category": "macro"},
    {"date": "2026-05-01", "time": "17:30", "type": "minor", "name": "India PMI Manufacturing", "category": "macro"}
  ]
}
```

> **Schema note:** The `time` field is optional (HH:MM, IST). If omitted, defaults to `"09:15"` (market open).
> Same-day events remain visible until their scheduled time passes (not midnight).

- [ ] **Step 2: Create Nifty 50 constituents**

```json
{
  "last_updated": "2026-03-19",
  "constituents": [
    {"symbol": "RELIANCE", "exchange": "NSE", "sector": "ENERGY", "weight": 10.2},
    {"symbol": "TCS", "exchange": "NSE", "sector": "IT", "weight": 4.5},
    {"symbol": "HDFCBANK", "exchange": "NSE", "sector": "BANK", "weight": 8.8},
    {"symbol": "INFY", "exchange": "NSE", "sector": "IT", "weight": 5.1},
    {"symbol": "ICICIBANK", "exchange": "NSE", "sector": "BANK", "weight": 7.2},
    {"symbol": "HINDUNILVR", "exchange": "NSE", "sector": "FMCG", "weight": 2.8},
    {"symbol": "ITC", "exchange": "NSE", "sector": "FMCG", "weight": 3.5},
    {"symbol": "SBIN", "exchange": "NSE", "sector": "PSUBANK", "weight": 3.0},
    {"symbol": "BHARTIARTL", "exchange": "NSE", "sector": "IT", "weight": 3.8},
    {"symbol": "KOTAKBANK", "exchange": "NSE", "sector": "BANK", "weight": 2.9},
    {"symbol": "LT", "exchange": "NSE", "sector": "FINSERV", "weight": 2.5},
    {"symbol": "AXISBANK", "exchange": "NSE", "sector": "BANK", "weight": 2.3},
    {"symbol": "ASIANPAINT", "exchange": "NSE", "sector": "CONSDUR", "weight": 1.5},
    {"symbol": "MARUTI", "exchange": "NSE", "sector": "AUTO", "weight": 1.8},
    {"symbol": "TATAMOTORS", "exchange": "NSE", "sector": "AUTO", "weight": 1.7},
    {"symbol": "SUNPHARMA", "exchange": "NSE", "sector": "PHARMA", "weight": 1.9},
    {"symbol": "TITAN", "exchange": "NSE", "sector": "CONSDUR", "weight": 1.6},
    {"symbol": "BAJFINANCE", "exchange": "NSE", "sector": "FINSERV", "weight": 2.5},
    {"symbol": "WIPRO", "exchange": "NSE", "sector": "IT", "weight": 1.2},
    {"symbol": "HCLTECH", "exchange": "NSE", "sector": "IT", "weight": 2.0},
    {"symbol": "ULTRACEMCO", "exchange": "NSE", "sector": "CONSDUR", "weight": 1.1},
    {"symbol": "NTPC", "exchange": "NSE", "sector": "ENERGY", "weight": 1.5},
    {"symbol": "POWERGRID", "exchange": "NSE", "sector": "ENERGY", "weight": 1.2},
    {"symbol": "TATASTEEL", "exchange": "NSE", "sector": "METAL", "weight": 1.0},
    {"symbol": "NESTLEIND", "exchange": "NSE", "sector": "FMCG", "weight": 0.9},
    {"symbol": "TECHM", "exchange": "NSE", "sector": "IT", "weight": 0.8},
    {"symbol": "BAJAJFINSV", "exchange": "NSE", "sector": "FINSERV", "weight": 1.3},
    {"symbol": "M&M", "exchange": "NSE", "sector": "AUTO", "weight": 2.2},
    {"symbol": "ONGC", "exchange": "NSE", "sector": "ENERGY", "weight": 1.1},
    {"symbol": "ADANIENT", "exchange": "NSE", "sector": "ENERGY", "weight": 1.0},
    {"symbol": "ADANIPORTS", "exchange": "NSE", "sector": "ENERGY", "weight": 1.2},
    {"symbol": "COALINDIA", "exchange": "NSE", "sector": "METAL", "weight": 0.8},
    {"symbol": "GRASIM", "exchange": "NSE", "sector": "CONSDUR", "weight": 0.9},
    {"symbol": "JSWSTEEL", "exchange": "NSE", "sector": "METAL", "weight": 1.0},
    {"symbol": "CIPLA", "exchange": "NSE", "sector": "PHARMA", "weight": 0.9},
    {"symbol": "DRREDDY", "exchange": "NSE", "sector": "PHARMA", "weight": 0.8},
    {"symbol": "BPCL", "exchange": "NSE", "sector": "ENERGY", "weight": 0.7},
    {"symbol": "EICHERMOT", "exchange": "NSE", "sector": "AUTO", "weight": 0.8},
    {"symbol": "DIVISLAB", "exchange": "NSE", "sector": "PHARMA", "weight": 0.6},
    {"symbol": "APOLLOHOSP", "exchange": "NSE", "sector": "PHARMA", "weight": 0.9},
    {"symbol": "HEROMOTOCO", "exchange": "NSE", "sector": "AUTO", "weight": 0.7},
    {"symbol": "SHRIRAMFIN", "exchange": "NSE", "sector": "FINSERV", "weight": 0.6},
    {"symbol": "TRENT", "exchange": "NSE", "sector": "CONSDUR", "weight": 0.7},
    {"symbol": "SBILIFE", "exchange": "NSE", "sector": "FINSERV", "weight": 0.6},
    {"symbol": "HDFCLIFE", "exchange": "NSE", "sector": "FINSERV", "weight": 0.5},
    {"symbol": "BAJAJ-AUTO", "exchange": "NSE", "sector": "AUTO", "weight": 0.8},
    {"symbol": "BEL", "exchange": "NSE", "sector": "ENERGY", "weight": 0.7},
    {"symbol": "INDUSINDBK", "exchange": "NSE", "sector": "BANK", "weight": 0.6},
    {"symbol": "HINDALCO", "exchange": "NSE", "sector": "METAL", "weight": 0.8},
    {"symbol": "BRITANNIA", "exchange": "NSE", "sector": "FMCG", "weight": 0.6}
  ]
}
```

- [ ] **Step 3: Create sector indices reference**

```json
{
  "indices": [
    {"key": "BANK", "name": "Nifty Bank", "symbol": "NIFTY BANK", "exchange": "NSE"},
    {"key": "IT", "name": "Nifty IT", "symbol": "NIFTY IT", "exchange": "NSE"},
    {"key": "FMCG", "name": "Nifty FMCG", "symbol": "NIFTY FMCG", "exchange": "NSE"},
    {"key": "AUTO", "name": "Nifty Auto", "symbol": "NIFTY AUTO", "exchange": "NSE"},
    {"key": "PHARMA", "name": "Nifty Pharma", "symbol": "NIFTY PHARMA", "exchange": "NSE"},
    {"key": "METAL", "name": "Nifty Metal", "symbol": "NIFTY METAL", "exchange": "NSE"},
    {"key": "PSUBANK", "name": "Nifty PSU Bank", "symbol": "NIFTY PSU BANK", "exchange": "NSE"},
    {"key": "ENERGY", "name": "Nifty Energy", "symbol": "NIFTY ENERGY", "exchange": "NSE"},
    {"key": "FINSERV", "name": "Nifty Financial Services", "symbol": "NIFTY FIN SERVICE", "exchange": "NSE"},
    {"key": "REALTY", "name": "Nifty Realty", "symbol": "NIFTY REALTY", "exchange": "NSE"},
    {"key": "CONSDUR", "name": "Nifty Consumer Durables", "symbol": "NIFTY CONSR DURBL", "exchange": "NSE"},
    {"key": "MEDIA", "name": "Nifty Media", "symbol": "NIFTY MEDIA", "exchange": "NSE"}
  ]
}
```

- [ ] **Step 4: Commit**

```bash
git add data/market_events.json data/nifty50_constituents.json data/sector_indices.json
git commit -m "feat(market-pulse): add static data files for events, constituents, sectors"
```

### Task 3: NSE India website data fetcher

**Files:**
- Create: `services/market_pulse_nse.py`

- [ ] **Step 1: Create NSE fetcher with advance/decline + highs/lows**

```python
"""
Fetch market breadth data from NSE India website.
Provides advance/decline ratios and 52-week highs/lows.
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
}

# Module-level session for cookie reuse
_session: requests.Session | None = None
_session_created_at: float = 0
_SESSION_MAX_AGE = 300  # refresh session every 5 min


def _get_session() -> requests.Session:
    """Get or create an NSE session with cookies."""
    global _session, _session_created_at
    now = time.time()
    if _session is None or (now - _session_created_at) > _SESSION_MAX_AGE:
        _session = requests.Session()
        _session.headers.update(_NSE_HEADERS)
        # Hit homepage first to get cookies
        try:
            _session.get(_NSE_BASE, timeout=10)
        except Exception:
            pass
        _session_created_at = now
    return _session


def _nse_get(path: str) -> dict | None:
    """Make a GET request to NSE API endpoint."""
    sess = _get_session()
    try:
        resp = sess.get(f"{_NSE_BASE}{path}", timeout=15)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("NSE %s returned status %d", path, resp.status_code)
    except Exception as e:
        logger.warning("NSE fetch failed for %s: %s", path, e)
        # Reset session on failure
        global _session
        _session = None
    return None


def fetch_advance_decline() -> dict[str, Any]:
    """Fetch NSE advance/decline data.

    Returns: {"advances": int, "declines": int, "unchanged": int, "ad_ratio": float}
    """
    data = _nse_get("/api/market-data-pre-open?key=NIFTY%2050")
    if data is None:
        # Fallback: try market status endpoint
        data = _nse_get("/api/marketStatus")

    # Try the live market endpoint for A/D
    ad_data = _nse_get("/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O")
    if ad_data and "advance" in ad_data:
        advances = ad_data.get("advance", {}).get("advances", 0)
        declines = ad_data.get("advance", {}).get("declines", 0)
        unchanged = ad_data.get("advance", {}).get("unchanged", 0)
        ad_ratio = round(advances / max(declines, 1), 2)
        return {
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "ad_ratio": ad_ratio,
        }

    # Absolute fallback
    return {"advances": 0, "declines": 0, "unchanged": 0, "ad_ratio": 1.0, "error": "unavailable"}


def fetch_highs_lows() -> dict[str, Any]:
    """Fetch 52-week highs and lows from NSE.

    Returns: {"highs_52w": int, "lows_52w": int, "ratio": float}
    """
    data = _nse_get("/api/live-analysis-variations?index=gainers52w")
    highs = 0
    if data and "data" in data:
        highs = len(data["data"])

    data_lows = _nse_get("/api/live-analysis-variations?index=losers52w")
    lows = 0
    if data_lows and "data" in data_lows:
        lows = len(data_lows["data"])

    ratio = round(highs / max(lows, 1), 2)
    return {"highs_52w": highs, "lows_52w": lows, "ratio": ratio}


def fetch_market_breadth() -> dict[str, Any]:
    """Aggregate all NSE breadth data into a single dict."""
    ad = fetch_advance_decline()
    hl = fetch_highs_lows()
    return {
        "advance_decline": ad,
        "highs_lows": hl,
    }
```

- [ ] **Step 2: Commit**

```bash
git add services/market_pulse_nse.py
git commit -m "feat(market-pulse): add NSE India breadth data fetcher"
```

---

## Chunk 2: Data Service + Scoring Engine

### Task 4: Broker data fetcher + aggregator

**Files:**
- Create: `services/market_pulse_data.py`

- [ ] **Step 1: Create data service with 30s cache**

```python
"""
Market Pulse data aggregation service.
Fetches from broker APIs (via existing OpenAlgo services) + NSE website.
Caches for 30 seconds.
"""

import json
import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from services.market_pulse_config import (
    CACHE_TTL_SECONDS,
    EXECUTION_SWING,
    INDEX_SYMBOLS,
    SECTOR_INDICES,
    USDINR_SYMBOL,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ─────────────────────────────────────────────
_cache: dict[str, Any] = {}
_cache_ts: float = 0


def _is_cache_valid() -> bool:
    return (time.time() - _cache_ts) < CACHE_TTL_SECONDS and bool(_cache)


def _load_json(filename: str) -> dict:
    """Load a JSON file from the data/ directory."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", filename)
    with open(path, "r") as f:
        return json.load(f)


def _get_constituents() -> list[dict]:
    """Load Nifty 50 constituent list."""
    data = _load_json("nifty50_constituents.json")
    return data.get("constituents", [])


def _get_events() -> list[dict]:
    """Load market events calendar."""
    data = _load_json("market_events.json")
    return data.get("events", [])


# ── Broker Data Fetching ────────────────────────────────────────

def _fetch_quote(symbol: str, exchange: str) -> dict | None:
    """Fetch a single quote via the existing quotes service."""
    try:
        from database.auth_db import get_api_key_for_tradingview
        from services.quotes_service import get_quotes

        api_key = get_api_key_for_tradingview()
        if not api_key:
            logger.warning("No API key available for market pulse")
            return None

        success, data, _ = get_quotes(symbol=symbol, exchange=exchange, api_key=api_key)
        if success and data.get("status") == "success":
            return data.get("data", {})
    except Exception as e:
        logger.warning("Quote fetch failed for %s:%s - %s", symbol, exchange, e)
    return None


def _fetch_history(symbol: str, exchange: str, days: int = 200) -> pd.DataFrame | None:
    """Fetch historical OHLCV via existing history service."""
    try:
        from database.auth_db import get_api_key_for_tradingview
        from services.history_service import get_history

        api_key = get_api_key_for_tradingview()
        if not api_key:
            return None

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        success, data, _ = get_history(
            symbol=symbol,
            exchange=exchange,
            interval="D",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            api_key=api_key,
        )

        if success and data.get("status") == "success":
            candles = data.get("data", [])
            if candles:
                df = pd.DataFrame(candles)
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df
    except Exception as e:
        logger.warning("History fetch failed for %s:%s - %s", symbol, exchange, e)
    return None


def _fetch_option_chain_pcr() -> float | None:
    """Compute Nifty Put/Call Ratio from option chain OI."""
    try:
        from database.auth_db import get_api_key_for_tradingview
        from services.option_chain_service import get_option_chain

        api_key = get_api_key_for_tradingview()
        if not api_key:
            return None

        success, data, _ = get_option_chain(
            symbol="NIFTY", exchange="NFO", api_key=api_key
        )
        if success and data.get("status") == "success":
            chain = data.get("data", [])
            total_put_oi = sum(row.get("put_oi", 0) for row in chain)
            total_call_oi = sum(row.get("call_oi", 0) for row in chain)
            if total_call_oi > 0:
                return round(total_put_oi / total_call_oi, 3)
    except Exception as e:
        logger.warning("PCR fetch failed: %s", e)
    return None


# ── Technical Indicators ────────────────────────────────────────

def compute_sma(series: pd.Series, period: int) -> float | None:
    """Compute simple moving average."""
    if len(series) < period:
        return None
    return round(series.tail(period).mean(), 2)


def compute_rsi(series: pd.Series, period: int = 14) -> float | None:
    """Compute RSI."""
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.tail(period).mean()
    avg_loss = loss.tail(period).mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_slope(series: pd.Series, period: int = 5) -> float | None:
    """Compute slope as percentage change over period."""
    if len(series) < period:
        return None
    return round((series.iloc[-1] - series.iloc[-period]) / series.iloc[-period] * 100, 4)


def compute_percentile(current: float, series: pd.Series) -> float | None:
    """Compute percentile rank of current value in historical series."""
    if len(series) < 20:
        return None
    below = (series < current).sum()
    return round(below / len(series) * 100, 1)


# ── Main Aggregation ────────────────────────────────────────────

def fetch_market_data(mode: str = "swing") -> dict[str, Any]:
    """Fetch and aggregate all market data. Returns cached if fresh.

    Args:
        mode: "swing" or "day"

    Returns: dict with all data needed for scoring.
    """
    global _cache, _cache_ts

    if _is_cache_valid() and _cache.get("mode") == mode:
        return _cache

    result: dict[str, Any] = {"mode": mode, "errors": []}

    # 1. Index quotes (current prices)
    ticker = {}
    for key, info in INDEX_SYMBOLS.items():
        quote = _fetch_quote(info["symbol"], info["exchange"])
        if quote:
            ticker[key] = quote
        else:
            result["errors"].append(f"Quote unavailable: {key}")
    result["ticker"] = ticker

    # 2. USDINR
    usdinr_quote = _fetch_quote(USDINR_SYMBOL["symbol"], USDINR_SYMBOL["exchange"])
    if usdinr_quote:
        ticker["USDINR"] = usdinr_quote

    # 3. Sector indices
    sectors = {}
    for key, info in SECTOR_INDICES.items():
        quote = _fetch_quote(info["symbol"], info["exchange"])
        if quote:
            sectors[key] = quote
    result["sectors"] = sectors

    # 4. Historical data for Nifty (for MAs, RSI, slopes)
    nifty_hist = _fetch_history("NIFTY 50", "NSE", days=250)
    result["nifty_history"] = nifty_hist

    # 5. Historical data for BankNifty
    banknifty_hist = _fetch_history("NIFTY BANK", "NSE", days=100)
    result["banknifty_history"] = banknifty_hist

    # 6. India VIX history (for percentile and slope)
    vix_hist = _fetch_history("INDIA VIX", "NSE", days=260)
    result["vix_history"] = vix_hist

    # 7. USDINR history
    usdinr_hist = _fetch_history(USDINR_SYMBOL["symbol"], USDINR_SYMBOL["exchange"], days=50)
    result["usdinr_history"] = usdinr_hist

    # 8. Sector index histories (for MAs, to check above/below 20d)
    sector_histories = {}
    for key, info in SECTOR_INDICES.items():
        hist = _fetch_history(info["symbol"], info["exchange"], days=50)
        if hist is not None:
            sector_histories[key] = hist
    result["sector_histories"] = sector_histories

    # 9. Nifty 50 constituent histories (for breadth + execution window)
    # IMPORTANT: Must fetch 200+ days for % above 200d MA breadth scoring
    # and chop-regime 200d MA logic. The design specifies 50 symbols × 50 days
    # but that only covers execution window breakout tracking. Breadth scoring
    # (design line 117) and chop regime (design line 169) need 200d.
    # We fetch 250 days (extra buffer for market holidays).
    _CONSTITUENT_HISTORY_DAYS = 250  # enough for 200d MA + holiday buffer
    constituents = _get_constituents()
    constituent_data = {}
    for c in constituents:
        hist = _fetch_history(c["symbol"], c["exchange"], days=_CONSTITUENT_HISTORY_DAYS)
        if hist is not None:
            constituent_data[c["symbol"]] = {"history": hist, "sector": c["sector"]}
    result["constituent_data"] = constituent_data

    # 10. Nifty PCR
    pcr = _fetch_option_chain_pcr()
    result["pcr"] = pcr

    # 11. NSE breadth
    try:
        from services.market_pulse_nse import fetch_market_breadth
        breadth = fetch_market_breadth()
        result["nse_breadth"] = breadth
    except Exception as e:
        logger.warning("NSE breadth fetch failed: %s", e)
        result["errors"].append("NSE breadth unavailable")
        result["nse_breadth"] = None

    # 12. Events calendar
    result["events"] = _get_events()

    # 13. Computed indicators for Nifty
    if nifty_hist is not None and "close" in nifty_hist.columns:
        closes = nifty_hist["close"]
        result["nifty_indicators"] = {
            "sma_20": compute_sma(closes, 20),
            "sma_50": compute_sma(closes, 50),
            "sma_200": compute_sma(closes, 200),
            "rsi_14": compute_rsi(closes, 14),
            "slope_50d": compute_slope(
                pd.Series([compute_sma(closes.head(len(closes) - i), 50) for i in range(5)][::-1]),
                5,
            ) if len(closes) >= 55 else None,
            "slope_200d": compute_slope(
                pd.Series([compute_sma(closes.head(len(closes) - i), 200) for i in range(5)][::-1]),
                5,
            ) if len(closes) >= 205 else None,
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
        }
    else:
        result["nifty_indicators"] = {}

    # 14. BankNifty indicators
    if banknifty_hist is not None and "close" in banknifty_hist.columns:
        closes = banknifty_hist["close"]
        result["banknifty_indicators"] = {
            "sma_50": compute_sma(closes, 50),
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
        }
    else:
        result["banknifty_indicators"] = {}

    # 15. VIX indicators
    if vix_hist is not None and "close" in vix_hist.columns:
        closes = vix_hist["close"]
        current_vix = closes.iloc[-1] if len(closes) > 0 else None
        result["vix_indicators"] = {
            "current": current_vix,
            "slope_5d": compute_slope(closes, 5),
            "percentile_1y": compute_percentile(current_vix, closes) if current_vix else None,
        }
    else:
        result["vix_indicators"] = {}

    # 16. USDINR indicators
    if usdinr_hist is not None and "close" in usdinr_hist.columns:
        closes = usdinr_hist["close"]
        result["usdinr_indicators"] = {
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
            "slope_5d": compute_slope(closes, 5),
            "slope_20d": compute_slope(closes, 20),
        }
    else:
        result["usdinr_indicators"] = {}

    result["updated_at"] = time.time()
    _cache = result
    _cache_ts = time.time()

    return result
```

- [ ] **Step 2: Commit**

```bash
git add services/market_pulse_data.py
git commit -m "feat(market-pulse): add broker data fetcher with 30s cache and indicators"
```

### Task 5: Scoring engine

**Files:**
- Create: `services/market_pulse_scoring.py`
- Create: `test/test_market_pulse_scoring.py`

- [ ] **Step 1: Write scoring tests**

```python
"""Tests for Market Pulse scoring engine."""

import pytest
from services.market_pulse_scoring import (
    score_volatility,
    score_momentum,
    score_trend,
    score_breadth,
    score_macro,
    compute_market_quality,
    get_decision,
)


class TestVolatilityScoring:
    def test_optimal_vix_range(self):
        """VIX in 11-16 should score high."""
        score, rules = score_volatility(
            vix_current=13.5, vix_slope_5d=-0.5, vix_percentile=40, pcr=1.0
        )
        assert 70 <= score <= 100, f"Optimal VIX should score high, got {score}"

    def test_spike_vix(self):
        """VIX above 25 should score low."""
        score, rules = score_volatility(
            vix_current=28, vix_slope_5d=3.0, vix_percentile=90, pcr=1.5
        )
        assert score < 40, f"Spike VIX should score low, got {score}"

    def test_complacency_penalty(self):
        """VIX below 10 should get complacency penalty."""
        score, rules = score_volatility(
            vix_current=9, vix_slope_5d=0, vix_percentile=5, pcr=0.8
        )
        assert score < 80, f"Complacent VIX should be penalized, got {score}"

    def test_missing_data_graceful(self):
        """Should handle None values gracefully."""
        score, rules = score_volatility(
            vix_current=None, vix_slope_5d=None, vix_percentile=None, pcr=None
        )
        assert 0 <= score <= 100


class TestTrendScoring:
    def test_strong_uptrend(self):
        """All MAs bullish should score high."""
        score, rules = score_trend(
            nifty_ltp=24500, sma_20=24200, sma_50=23800, sma_200=22500,
            banknifty_ltp=52000, banknifty_sma50=51000,
            rsi=58, slope_50d=0.5, slope_200d=0.2,
        )
        assert score >= 75, f"Strong uptrend should score high, got {score}"

    def test_downtrend(self):
        """Below all MAs should score low."""
        score, rules = score_trend(
            nifty_ltp=21000, sma_20=22000, sma_50=23000, sma_200=24000,
            banknifty_ltp=48000, banknifty_sma50=50000,
            rsi=35, slope_50d=-0.5, slope_200d=-0.2,
        )
        assert score < 40, f"Downtrend should score low, got {score}"


class TestBreadthScoring:
    def test_broad_participation(self):
        """Strong breadth should score high."""
        score, rules = score_breadth(
            ad_ratio=2.5, pct_above_50d=75, pct_above_200d=85,
            highs_52w=80, lows_52w=10,
        )
        assert score >= 75

    def test_narrow_breadth(self):
        """Weak breadth should score low."""
        score, rules = score_breadth(
            ad_ratio=0.4, pct_above_50d=25, pct_above_200d=35,
            highs_52w=5, lows_52w=50,
        )
        assert score < 40


class TestDecisionLogic:
    def test_yes_decision(self):
        assert get_decision(85) == "YES"

    def test_caution_decision(self):
        assert get_decision(70) == "CAUTION"

    def test_no_decision(self):
        assert get_decision(45) == "NO"

    def test_boundary_80(self):
        assert get_decision(80) == "YES"

    def test_boundary_60(self):
        assert get_decision(60) == "CAUTION"

    def test_boundary_59(self):
        assert get_decision(59) == "NO"


class TestMarketQuality:
    def test_weighted_average(self):
        """Market quality should be weighted average of 5 scores."""
        scores = {
            "volatility": 80,
            "momentum": 70,
            "trend": 90,
            "breadth": 60,
            "macro": 50,
        }
        quality = compute_market_quality(scores)
        # 80*0.25 + 70*0.25 + 90*0.20 + 60*0.20 + 50*0.10
        # = 20 + 17.5 + 18 + 12 + 5 = 72.5
        assert quality == 73  # rounded
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_market_pulse_scoring.py -v
```

Expected: FAIL (module not found)

- [ ] **Step 3: Implement scoring engine**

```python
"""
Market Pulse scoring engine.
Computes 5 category scores + decision from market data.
All thresholds imported from market_pulse_config.py.
"""

import logging
from typing import Any

from services.market_pulse_config import (
    BREADTH,
    CATEGORY_WEIGHTS,
    DECISION_THRESHOLDS,
    MACRO,
    MOMENTUM,
    TREND,
    VOLATILITY,
)

logger = logging.getLogger(__name__)


def _clamp(value: float, low: float = 0, high: float = 100) -> int:
    """Clamp value to [low, high] and round."""
    return int(max(low, min(high, round(value))))


def _safe(val, default=0):
    """Return val if not None, else default."""
    return val if val is not None else default


# ── Volatility Score ────────────────────────────────────────────

def score_volatility(
    vix_current: float | None,
    vix_slope_5d: float | None,
    vix_percentile: float | None,
    pcr: float | None,
) -> tuple[int, list[dict]]:
    """Score volatility 0-100 with rules fired."""
    rules = []
    sub_scores = []
    V = VOLATILITY

    # VIX Level
    vix = _safe(vix_current, 15)
    if vix < V["vix_complacency_threshold"]:
        s = 60
        rules.append({"rule": "VIX complacency", "detail": f"VIX {vix:.1f} < {V['vix_complacency_threshold']}", "impact": "penalty"})
    elif V["vix_optimal_low"] <= vix <= V["vix_optimal_high"]:
        s = 80 + (V["vix_optimal_high"] - vix) / (V["vix_optimal_high"] - V["vix_optimal_low"]) * 20
        rules.append({"rule": "VIX in optimal range", "detail": f"VIX {vix:.1f} in [{V['vix_optimal_low']}-{V['vix_optimal_high']}]", "impact": "positive"})
    elif vix <= V["vix_elevated_high"]:
        s = 50 + (V["vix_elevated_high"] - vix) / (V["vix_elevated_high"] - V["vix_optimal_high"]) * 30
        rules.append({"rule": "VIX elevated", "detail": f"VIX {vix:.1f} in [{V['vix_optimal_high']}-{V['vix_elevated_high']}]", "impact": "neutral"})
    elif vix >= V["vix_spike_threshold"]:
        s = max(0, 30 - (vix - V["vix_spike_threshold"]) * 3)
        rules.append({"rule": "VIX spike", "detail": f"VIX {vix:.1f} >= {V['vix_spike_threshold']}", "impact": "negative"})
    else:
        s = 40
    sub_scores.append(("vix_level", _clamp(s), V["vix_level_weight"]))

    # VIX Slope
    slope = _safe(vix_slope_5d, 0)
    if slope < -1:
        s = 80 + V["slope_falling_bonus"]
        rules.append({"rule": "VIX falling", "detail": f"5d slope {slope:.2f}%", "impact": "positive"})
    elif slope > 2:
        s = max(0, 50 + V["slope_rising_penalty"])
        rules.append({"rule": "VIX rising sharply", "detail": f"5d slope {slope:.2f}%", "impact": "negative"})
    else:
        s = 65
    sub_scores.append(("vix_slope", _clamp(s), V["vix_slope_weight"]))

    # VIX Percentile
    pct = _safe(vix_percentile, 50)
    if pct < V["percentile_low_threshold"]:
        s = 80 + (V["percentile_low_threshold"] - pct) / V["percentile_low_threshold"] * 20
        rules.append({"rule": "VIX low percentile", "detail": f"{pct:.0f}th percentile (1Y)", "impact": "positive"})
    elif pct > V["percentile_high_threshold"]:
        s = max(0, 30 - (pct - V["percentile_high_threshold"]))
        rules.append({"rule": "VIX high percentile", "detail": f"{pct:.0f}th percentile (1Y)", "impact": "negative"})
    else:
        s = 50 + (V["percentile_high_threshold"] - pct) / (V["percentile_high_threshold"] - V["percentile_low_threshold"]) * 30
    sub_scores.append(("vix_percentile", _clamp(s), V["vix_percentile_weight"]))

    # PCR
    pcr_val = _safe(pcr, 1.0)
    if V["pcr_healthy_low"] <= pcr_val <= V["pcr_healthy_high"]:
        s = 70 + (1 - abs(pcr_val - 1.05) / 0.5) * 20
        rules.append({"rule": "PCR healthy range", "detail": f"PCR {pcr_val:.2f}", "impact": "positive"})
    elif pcr_val > V["pcr_extreme_fear"]:
        s = 50
        rules.append({"rule": "PCR extreme fear", "detail": f"PCR {pcr_val:.2f} > {V['pcr_extreme_fear']}", "impact": "neutral"})
    elif pcr_val < V["pcr_extreme_greed"]:
        s = 40
        rules.append({"rule": "PCR extreme greed", "detail": f"PCR {pcr_val:.2f} < {V['pcr_extreme_greed']}", "impact": "negative"})
    else:
        s = 60
    sub_scores.append(("pcr", _clamp(s), V["pcr_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Momentum Score ──────────────────────────────────────────────

def score_momentum(
    sectors_above_20d: int,
    total_sectors: int,
    leadership_spread: float,
    higher_highs_pct: float,
    rotation_diversity: int,
) -> tuple[int, list[dict]]:
    """Score momentum 0-100."""
    rules = []
    sub_scores = []
    M = MOMENTUM

    # Sector participation
    if sectors_above_20d >= total_sectors:
        s = 100
    elif sectors_above_20d >= M["sectors_strong"]:
        s = 75
    elif sectors_above_20d >= M["sectors_moderate_low"]:
        s = 50
    else:
        s = 20
    rules.append({"rule": "Sector participation", "detail": f"{sectors_above_20d}/{total_sectors} sectors above 20d MA", "impact": "positive" if s >= 70 else "negative" if s < 40 else "neutral"})
    sub_scores.append(("participation", _clamp(s), M["sector_participation_weight"]))

    # Leadership spread
    spread = _safe(leadership_spread, 3)
    if M["spread_healthy_low"] <= spread <= M["spread_healthy_high"]:
        s = 80
        rules.append({"rule": "Healthy leadership spread", "detail": f"Top3-Bot3 spread {spread:.1f}%", "impact": "positive"})
    elif spread > M["spread_concentrated"]:
        s = 40
        rules.append({"rule": "Concentrated leadership", "detail": f"Spread {spread:.1f}% > {M['spread_concentrated']}%", "impact": "negative"})
    else:
        s = 60
    sub_scores.append(("spread", _clamp(s), M["leadership_spread_weight"]))

    # Higher highs
    hh = _safe(higher_highs_pct, 20)
    if hh > M["highs_strong"]:
        s = 90
    elif hh > M["highs_moderate"]:
        s = 65
    elif hh > M["highs_weak"]:
        s = 40
    else:
        s = 20
    rules.append({"rule": "Higher highs participation", "detail": f"{hh:.0f}% of Nifty 50 at 20d highs", "impact": "positive" if s >= 65 else "negative"})
    sub_scores.append(("higher_highs", _clamp(s), M["higher_highs_weight"]))

    # Rotation health
    if rotation_diversity >= 4:
        s = 85
        rules.append({"rule": "Healthy sector rotation", "detail": f"{rotation_diversity} sectors leading", "impact": "positive"})
    elif rotation_diversity >= 2:
        s = 55
    else:
        s = 25
        rules.append({"rule": "Narrow leadership", "detail": f"Only {rotation_diversity} sector(s) leading", "impact": "negative"})
    sub_scores.append(("rotation", _clamp(s), M["rotation_health_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Trend Score ─────────────────────────────────────────────────

def score_trend(
    nifty_ltp: float | None,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
    banknifty_ltp: float | None,
    banknifty_sma50: float | None,
    rsi: float | None,
    slope_50d: float | None,
    slope_200d: float | None,
) -> tuple[int, list[dict]]:
    """Score trend 0-100."""
    rules = []
    sub_scores = []
    T = TREND

    # Nifty vs MAs
    ltp = _safe(nifty_ltp)
    above_count = 0
    s = 10
    if sma_200 and ltp > sma_200:
        s += 30
        above_count += 1
    if sma_50 and ltp > sma_50:
        s += 25
        above_count += 1
    if sma_20 and ltp > sma_20:
        s += 20
        above_count += 1

    if above_count == 3:
        rules.append({"rule": "Nifty above all MAs", "detail": f"Above 20/50/200 DMA", "impact": "positive"})
    elif above_count == 0:
        rules.append({"rule": "Nifty below all MAs", "detail": "Below 20/50/200 DMA", "impact": "negative"})
    else:
        rules.append({"rule": f"Nifty above {above_count}/3 MAs", "detail": f"{above_count} MAs bullish", "impact": "neutral"})
    sub_scores.append(("nifty_ma", _clamp(s), T["nifty_ma_weight"]))

    # BankNifty vs 50d
    bn_ltp = _safe(banknifty_ltp)
    bn_sma = _safe(banknifty_sma50)
    if bn_ltp and bn_sma and bn_ltp > bn_sma:
        s = 80
        rules.append({"rule": "BankNifty above 50d MA", "detail": "Risk-on signal from financials", "impact": "positive"})
    else:
        s = 35
        rules.append({"rule": "BankNifty below 50d MA", "detail": "Financials weak", "impact": "negative"})
    sub_scores.append(("banknifty", _clamp(s), T["banknifty_weight"]))

    # RSI
    rsi_val = _safe(rsi, 50)
    if T["rsi_strong_low"] <= rsi_val <= T["rsi_strong_high"]:
        s = 85
        rules.append({"rule": "RSI in strong zone", "detail": f"RSI {rsi_val:.1f}", "impact": "positive"})
    elif rsi_val > T["rsi_overbought"]:
        s = 50
        rules.append({"rule": "RSI overbought", "detail": f"RSI {rsi_val:.1f} > {T['rsi_overbought']}", "impact": "neutral"})
    elif rsi_val < T["rsi_oversold"]:
        s = 30
        rules.append({"rule": "RSI oversold", "detail": f"RSI {rsi_val:.1f} < {T['rsi_oversold']}", "impact": "negative"})
    elif rsi_val >= 40:
        s = 60
    else:
        s = 40
    sub_scores.append(("rsi", _clamp(s), T["rsi_weight"]))

    # MA Slopes
    s50 = _safe(slope_50d, 0)
    s200 = _safe(slope_200d, 0)
    if s50 > 0 and s200 > 0:
        s = 90
        rules.append({"rule": "Both MAs rising", "detail": f"50d slope {s50:.2f}%, 200d slope {s200:.2f}%", "impact": "positive"})
    elif s50 < 0 and s200 < 0:
        s = 15
        rules.append({"rule": "Both MAs falling", "detail": f"50d slope {s50:.2f}%, 200d slope {s200:.2f}%", "impact": "negative"})
    else:
        s = 50
        rules.append({"rule": "Mixed MA slopes", "detail": f"50d: {s50:.2f}%, 200d: {s200:.2f}%", "impact": "neutral"})
    sub_scores.append(("ma_slope", _clamp(s), T["ma_slope_weight"]))

    total = sum(s * w for _, s, w in sub_scores)

    return _clamp(total), rules


# ── Breadth Score ───────────────────────────────────────────────

def score_breadth(
    ad_ratio: float | None,
    pct_above_50d: float | None,
    pct_above_200d: float | None,
    highs_52w: int | None,
    lows_52w: int | None,
) -> tuple[int, list[dict]]:
    """Score breadth 0-100."""
    rules = []
    sub_scores = []
    B = BREADTH

    # A/D Ratio
    ad = _safe(ad_ratio, 1.0)
    if ad >= B["ad_strong"]:
        s = 95
    elif ad >= B["ad_healthy_low"]:
        s = 70
    elif ad >= B["ad_neutral_low"]:
        s = 50
    elif ad >= B["ad_weak"]:
        s = 30
    else:
        s = 15
    rules.append({"rule": "Advance/Decline ratio", "detail": f"A/D {ad:.2f}", "impact": "positive" if s >= 70 else "negative" if s < 40 else "neutral"})
    sub_scores.append(("ad_ratio", _clamp(s), B["ad_ratio_weight"]))

    # % above 50d
    pct50 = _safe(pct_above_50d, 50)
    if pct50 > 70:
        s = 90
    elif pct50 > 50:
        s = 65
    elif pct50 > 30:
        s = 40
    else:
        s = 15
    rules.append({"rule": "% above 50d MA", "detail": f"{pct50:.0f}% of Nifty 50", "impact": "positive" if s >= 65 else "negative" if s < 40 else "neutral"})
    sub_scores.append(("above_50d", _clamp(s), B["above_50d_weight"]))

    # % above 200d
    pct200 = _safe(pct_above_200d, 60)
    if pct200 > 80:
        s = 90
    elif pct200 > 60:
        s = 70
    elif pct200 > 40:
        s = 45
    else:
        s = 25
    rules.append({"rule": "% above 200d MA", "detail": f"{pct200:.0f}% of Nifty 50", "impact": "positive" if s >= 70 else "negative" if s < 45 else "neutral"})
    sub_scores.append(("above_200d", _clamp(s), B["above_200d_weight"]))

    # New highs vs lows
    h = _safe(highs_52w, 0)
    l = _safe(lows_52w, 0)
    ratio = h / max(l, 1)
    if ratio > 3:
        s = 90
    elif h > l:
        s = 65
    else:
        s = 20
    rules.append({"rule": "52w highs vs lows", "detail": f"Highs: {h}, Lows: {l}", "impact": "positive" if s >= 65 else "negative"})
    sub_scores.append(("highs_lows", _clamp(s), B["highs_lows_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Macro Score ─────────────────────────────────────────────────

def score_macro(
    usdinr_slope_5d: float | None,
    usdinr_slope_20d: float | None,
    vix_current: float | None,
    event_hours_away: float | None,
    event_type: str | None,
    nifty_usdinr_corr: float | None,
) -> tuple[int, list[dict]]:
    """Score macro/liquidity 0-100."""
    rules = []
    sub_scores = []
    MC = MACRO

    # USDINR Trend
    slope5 = _safe(usdinr_slope_5d, 0)
    slope20 = _safe(usdinr_slope_20d, 0)
    if slope5 <= 0 and slope20 <= 0:
        s = 85
        rules.append({"rule": "USDINR stable/falling", "detail": f"5d: {slope5:.2f}%, 20d: {slope20:.2f}%", "impact": "positive"})
    elif slope5 > 0.5:
        s = 20
        rules.append({"rule": "USDINR spiking", "detail": f"5d slope {slope5:.2f}%", "impact": "negative"})
    else:
        s = 55
        rules.append({"rule": "USDINR rising slowly", "detail": f"5d: {slope5:.2f}%", "impact": "neutral"})
    sub_scores.append(("usdinr", _clamp(s), MC["usdinr_weight"]))

    # RBI Stance Proxy
    vix = _safe(vix_current, 15)
    if vix < 14 and slope5 <= 0:
        s = 85
        rules.append({"rule": "RBI stance: dovish proxy", "detail": f"Low VIX ({vix:.1f}) + stable INR", "impact": "positive"})
    elif vix > 20 and slope5 > 0.3:
        s = 30
        rules.append({"rule": "RBI stance: hawkish proxy", "detail": f"High VIX ({vix:.1f}) + weak INR", "impact": "negative"})
    else:
        s = 60
        rules.append({"rule": "RBI stance: neutral proxy", "detail": f"VIX {vix:.1f}, INR slope {slope5:.2f}%", "impact": "neutral"})
    sub_scores.append(("rbi", _clamp(s), MC["rbi_stance_weight"]))

    # Event Risk
    hours = _safe(event_hours_away, 999)
    etype = event_type or "none"
    if hours > MC["event_major_hours"]:
        s = 90
        rules.append({"rule": "No imminent events", "detail": f"Next event in {hours:.0f}h", "impact": "positive"})
    elif etype == "major":
        s = 30
        rules.append({"rule": "Major event imminent", "detail": f"{etype} event in {hours:.0f}h", "impact": "negative"})
    else:
        s = 65
        rules.append({"rule": "Minor event approaching", "detail": f"{etype} event in {hours:.0f}h", "impact": "neutral"})
    sub_scores.append(("event", _clamp(s), MC["event_risk_weight"]))

    # Global risk proxy — Nifty-USDINR correlation
    # IMPORTANT: None means data unavailable → neutral score (50), NOT the
    # positive "decoupled" branch. Using _safe(x, 0) here would silently
    # boost macro by falling into the "decoupled" path.
    if nifty_usdinr_corr is None:
        s = 50
        rules.append({"rule": "Global risk proxy unavailable", "detail": "Insufficient data for Nifty-USDINR correlation", "impact": "neutral"})
    elif abs(nifty_usdinr_corr) > 0.6:
        s = 30
        rules.append({"rule": "Nifty-USDINR correlated (risk-off)", "detail": f"Correlation {nifty_usdinr_corr:.2f}", "impact": "negative"})
    else:
        s = 75
        rules.append({"rule": "Nifty-USDINR decoupled", "detail": f"Correlation {nifty_usdinr_corr:.2f}", "impact": "positive"})
    sub_scores.append(("global", _clamp(s), MC["global_risk_weight"]))

    total = sum(s * w for _, s, w in sub_scores)
    return _clamp(total), rules


# ── Market Quality Score ────────────────────────────────────────

def compute_market_quality(scores: dict[str, int]) -> int:
    """Compute weighted market quality score."""
    total = 0
    for category, weight in CATEGORY_WEIGHTS.items():
        total += scores.get(category, 50) * weight
    return _clamp(total)


def get_decision(score: int) -> str:
    """Return YES / CAUTION / NO based on score."""
    if score >= DECISION_THRESHOLDS["yes_min"]:
        return "YES"
    elif score >= DECISION_THRESHOLDS["caution_min"]:
        return "CAUTION"
    return "NO"


def classify_regime(
    nifty_ltp: float | None,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
    slope_50d: float | None,
    vix_current: float | None,
) -> str:
    """Classify market regime: uptrend / downtrend / chop."""
    ltp = _safe(nifty_ltp)
    above = sum(1 for ma in [sma_20, sma_50, sma_200] if ma and ltp > ma)
    slope = _safe(slope_50d, 0)
    vix = _safe(vix_current, 15)

    if above >= 3 and slope > 0:
        return "uptrend"
    elif above == 0 and slope < 0:
        return "downtrend"
    else:
        return "chop"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest test/test_market_pulse_scoring.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add services/market_pulse_scoring.py test/test_market_pulse_scoring.py
git commit -m "feat(market-pulse): add scoring engine with 5 categories + tests"
```

### Task 6: Execution window score

**Files:**
- Create: `services/market_pulse_execution.py`

- [ ] **Step 1: Create execution window with DuckDB state tracking**

```python
"""
Execution Window Score — tracks breakout quality over multiple sessions.
Uses DuckDB for persistent state.
"""

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd

from services.market_pulse_config import EXECUTION_DAY, EXECUTION_SWING

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db", "market_pulse.duckdb"
)


def _get_connection():
    """Get DuckDB connection."""
    import duckdb
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = duckdb.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS breakout_events (
            symbol VARCHAR,
            breakout_date DATE,
            breakout_price DOUBLE,
            day1_close DOUBLE,
            day2_close DOUBLE,
            day3_close DOUBLE,
            held BOOLEAN DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def track_breakouts(constituent_data: dict[str, dict]) -> None:
    """Detect and track new breakouts from constituent OHLCV data."""
    today = date.today()
    conn = _get_connection()

    try:
        for symbol, data in constituent_data.items():
            hist = data.get("history")
            if hist is None or len(hist) < 25:
                continue

            closes = hist["close"]
            highs_20d = closes.iloc[-(EXECUTION_SWING["breakout_period"] + 1):-1].max()
            current = closes.iloc[-1]

            # New breakout: current close > 20d high
            if current > highs_20d:
                existing = conn.execute(
                    "SELECT 1 FROM breakout_events WHERE symbol = ? AND breakout_date = ?",
                    [symbol, today.isoformat()]
                ).fetchone()

                if not existing:
                    conn.execute(
                        "INSERT INTO breakout_events (symbol, breakout_date, breakout_price) VALUES (?, ?, ?)",
                        [symbol, today.isoformat(), float(current)]
                    )

            # Update follow-through for recent breakouts
            recent = conn.execute(
                "SELECT symbol, breakout_date, breakout_price FROM breakout_events "
                "WHERE breakout_date >= ? AND held IS NULL",
                [(today - timedelta(days=5)).isoformat()]
            ).fetchall()

            for sym, bdate, bprice in recent:
                if sym != symbol:
                    continue
                days_since = (today - bdate).days if isinstance(bdate, date) else 0
                if days_since >= 1 and len(closes) > 1:
                    conn.execute(
                        f"UPDATE breakout_events SET day{min(days_since, 3)}_close = ? WHERE symbol = ? AND breakout_date = ?",
                        [float(current), sym, bdate]
                    )
                if days_since >= 3:
                    held = current >= bprice * 0.98  # within 2% of breakout price
                    conn.execute(
                        "UPDATE breakout_events SET held = ? WHERE symbol = ? AND breakout_date = ?",
                        [held, sym, bdate]
                    )

        conn.commit()
    finally:
        conn.close()


def compute_execution_window_swing() -> tuple[int, dict[str, Any]]:
    """Compute swing mode execution window score from DuckDB state."""
    conn = _get_connection()
    try:
        lookback = date.today() - timedelta(days=EXECUTION_SWING["lookback_days"])
        rows = conn.execute(
            "SELECT * FROM breakout_events WHERE breakout_date >= ?",
            [lookback.isoformat()]
        ).fetchdf()

        if rows.empty:
            return 50, {"note": "No breakout events tracked yet", "breakouts": 0}

        total = len(rows)
        held = rows["held"].sum() if "held" in rows.columns else 0
        failed = total - held if held else 0

        # Breakout hold rate
        hold_rate = (held / max(total, 1)) * 100

        # Follow-through (avg gain day1-3)
        gains = []
        for _, row in rows.iterrows():
            bp = row.get("breakout_price", 0)
            if bp > 0:
                for col in ["day1_close", "day2_close", "day3_close"]:
                    val = row.get(col)
                    if val and val > 0:
                        gains.append((val - bp) / bp * 100)
        avg_followthrough = sum(gains) / max(len(gains), 1) if gains else 0

        # Failure rate
        failure_rate = (failed / max(total, 1)) * 100

        # Score components
        E = EXECUTION_SWING
        hold_score = min(100, hold_rate * 1.2)
        ft_score = min(100, max(0, 50 + avg_followthrough * 20))
        fail_score = max(0, 100 - failure_rate * 1.5)
        pullback_score = 60  # placeholder — would need intraday data

        score = int(
            hold_score * E["breakout_hold_weight"]
            + ft_score * E["follow_through_weight"]
            + fail_score * E["failure_rate_weight"]
            + pullback_score * E["pullback_buying_weight"]
        )

        details = {
            "breakouts": total,
            "held": int(held),
            "hold_rate": round(hold_rate, 1),
            "avg_followthrough_pct": round(avg_followthrough, 2),
            "failure_rate": round(failure_rate, 1),
        }

        return max(0, min(100, score)), details
    finally:
        conn.close()


def compute_execution_window_day(market_data: dict) -> tuple[int, dict[str, Any]]:
    """Compute day trading mode execution window additions."""
    details = {}
    scores = []
    E = EXECUTION_DAY

    # Trend consistency: closing in upper/lower 25% of daily range
    nifty_hist = market_data.get("nifty_history")
    if nifty_hist is not None and len(nifty_hist) >= 5:
        recent = nifty_hist.tail(5)
        conviction_days = 0
        for _, row in recent.iterrows():
            rng = row["high"] - row["low"]
            if rng > 0:
                pos = (row["close"] - row["low"]) / rng
                if pos >= 0.75 or pos <= 0.25:
                    conviction_days += 1
        trend_consistency = (conviction_days / 5) * 100
        scores.append(("trend_consistency", trend_consistency, E["trend_consistency_weight"]))
        details["trend_consistency"] = round(trend_consistency, 1)

    # Gap fill rate
    if nifty_hist is not None and len(nifty_hist) >= 6:
        recent = nifty_hist.tail(E["gap_lookback_days"] + 1)
        gaps_held = 0
        total_gaps = 0
        for i in range(1, len(recent)):
            prev_close = recent.iloc[i - 1]["close"]
            curr_open = recent.iloc[i]["open"]
            curr_close = recent.iloc[i]["close"]
            gap = (curr_open - prev_close) / prev_close * 100
            if abs(gap) > 0.2:  # significant gap
                total_gaps += 1
                if gap > 0 and curr_close >= curr_open:  # gap up held
                    gaps_held += 1
                elif gap < 0 and curr_close <= curr_open:  # gap down held
                    gaps_held += 1
        gap_rate = (gaps_held / max(total_gaps, 1)) * 100 if total_gaps > 0 else 50
        scores.append(("gap_fill", gap_rate, E["gap_fill_weight"]))
        details["gap_hold_rate"] = round(gap_rate, 1)

    # Sector follow-through
    sector_hists = market_data.get("sector_histories", {})
    if len(sector_hists) >= 6:
        yesterday_returns = {}
        today_returns = {}
        for key, hist in sector_hists.items():
            if len(hist) >= 3:
                yesterday_returns[key] = (hist["close"].iloc[-2] - hist["close"].iloc[-3]) / hist["close"].iloc[-3] * 100
                today_returns[key] = (hist["close"].iloc[-1] - hist["close"].iloc[-2]) / hist["close"].iloc[-2] * 100

        if yesterday_returns and today_returns:
            yesterday_leaders = sorted(yesterday_returns, key=yesterday_returns.get, reverse=True)[:3]
            still_leading = sum(1 for s in yesterday_leaders if today_returns.get(s, 0) > 0)
            ft_score = (still_leading / 3) * 100
            scores.append(("sector_ft", ft_score, E["sector_followthrough_weight"]))
            details["sector_followthrough"] = round(ft_score, 1)

    # VIX-Price divergence
    vix_ind = market_data.get("vix_indicators", {})
    nifty_ind = market_data.get("nifty_indicators", {})
    vix_slope = vix_ind.get("slope_5d")
    nifty_hist_df = market_data.get("nifty_history")
    if vix_slope is not None and nifty_hist_df is not None and len(nifty_hist_df) >= 5:
        nifty_5d_return = (nifty_hist_df["close"].iloc[-1] - nifty_hist_df["close"].iloc[-5]) / nifty_hist_df["close"].iloc[-5] * 100
        if vix_slope < 0 and nifty_5d_return > 0:
            div_score = 90  # healthy
        elif vix_slope > 1 and nifty_5d_return > 0:
            div_score = 30  # trap risk
        else:
            div_score = 55
        scores.append(("vix_div", div_score, E["vix_divergence_weight"]))
        details["vix_price_divergence"] = div_score

    if not scores:
        return 50, {"note": "Insufficient data for day trading signals"}

    total = sum(s * w for _, s, w in scores)
    weight_sum = sum(w for _, _, w in scores)
    normalized = total / weight_sum if weight_sum > 0 else 50

    return max(0, min(100, int(normalized))), details
```

- [ ] **Step 2: Commit**

```bash
git add services/market_pulse_execution.py
git commit -m "feat(market-pulse): add execution window with DuckDB state tracking"
```

---

## Chunk 3: Screener + LLM Analyst + Blueprint

### Task 7: Rule-based screener

**Files:**
- Create: `services/market_pulse_screener.py`
- Create: `test/test_market_pulse_screener.py`

- [ ] **Step 1: Write screener tests**

```python
"""Tests for Market Pulse screener."""

import pytest
from services.market_pulse_screener import (
    select_fno_strategy,
)


class TestFnoStrategySelection:
    def test_low_vix_uptrend(self):
        strategy = select_fno_strategy(vix=12, regime="uptrend")
        assert "call" in strategy["type"].lower() or "bull" in strategy["type"].lower()

    def test_high_vix_any(self):
        strategy = select_fno_strategy(vix=22, regime="uptrend")
        assert "sell" in strategy["type"].lower() or "iron" in strategy["type"].lower()

    def test_low_vix_downtrend(self):
        strategy = select_fno_strategy(vix=12, regime="downtrend")
        assert "put" in strategy["type"].lower() or "bear" in strategy["type"].lower()
```

- [ ] **Step 2: Create screener implementation**

```python
"""
Rule-based equity and F&O screener.
Screens Nifty 50 constituents based on market regime.
"""

import logging
from typing import Any

import pandas as pd

from services.market_pulse_config import EXECUTION_SWING

logger = logging.getLogger(__name__)


def screen_equities(
    constituent_data: dict[str, dict],
    regime: str,
    nifty_history: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Screen Nifty 50 stocks for trade ideas based on regime.

    Args:
        constituent_data: {symbol: {"history": DataFrame, "sector": str}}
        regime: "uptrend" | "downtrend" | "chop"
        nifty_history: Nifty 50 OHLCV for relative strength calc

    Returns: List of equity ideas sorted by conviction.
    """
    ideas = []

    nifty_return_5d = None
    if nifty_history is not None and len(nifty_history) >= 5:
        nc = nifty_history["close"]
        nifty_return_5d = (nc.iloc[-1] - nc.iloc[-5]) / nc.iloc[-5] * 100

    for symbol, data in constituent_data.items():
        hist = data.get("history")
        sector = data.get("sector", "")
        if hist is None or len(hist) < 25:
            continue

        closes = hist["close"]
        ltp = closes.iloc[-1]

        # Compute indicators
        sma_20 = closes.tail(20).mean()
        sma_50 = closes.tail(50).mean() if len(closes) >= 50 else None
        sma_200 = closes.tail(200).mean() if len(closes) >= 200 else None
        high_20d = closes.iloc[-21:-1].max() if len(closes) >= 21 else closes.max()

        # 5d return for relative strength
        stock_return_5d = (ltp - closes.iloc[-5]) / closes.iloc[-5] * 100 if len(closes) >= 5 else 0
        rs_vs_nifty = stock_return_5d - (nifty_return_5d or 0)

        # ATR for stop loss
        if len(hist) >= 14:
            tr = pd.concat([
                hist["high"] - hist["low"],
                (hist["high"] - hist["close"].shift(1)).abs(),
                (hist["low"] - hist["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = tr.tail(14).mean()
        else:
            atr = (hist["high"] - hist["low"]).tail(5).mean()

        idea = {
            "symbol": symbol,
            "sector": sector,
            "ltp": round(ltp, 2),
            "rs_vs_nifty": round(rs_vs_nifty, 2),
        }

        if regime == "uptrend":
            # BUY: breakouts + pullbacks to MA + strong RS
            if ltp > high_20d and rs_vs_nifty > 0:
                idea["signal"] = "BUY"
                idea["reason"] = "20d breakout + relative strength"
                idea["conviction"] = "HIGH" if rs_vs_nifty > 2 else "MED"
                idea["entry"] = round(ltp, 2)
                idea["stop_loss"] = round(ltp - 1.5 * atr, 2)
                idea["target"] = round(ltp + 2.5 * atr, 2)
                ideas.append(idea)
            elif sma_20 and abs(ltp - sma_20) / sma_20 < 0.01 and ltp > (sma_50 or 0):
                idea["signal"] = "BUY"
                idea["reason"] = "Pullback to 20d MA support"
                idea["conviction"] = "MED"
                idea["entry"] = round(ltp, 2)
                idea["stop_loss"] = round(sma_20 - atr, 2)
                idea["target"] = round(ltp + 2 * atr, 2)
                ideas.append(idea)

        elif regime == "downtrend":
            # AVOID: weak RS stocks below all MAs
            if rs_vs_nifty < -2 and ltp < sma_20:
                idea["signal"] = "AVOID"
                idea["reason"] = "Weak RS + below 20d MA"
                idea["conviction"] = "HIGH" if ltp < (sma_50 or ltp + 1) else "MED"
                idea["entry"] = None
                idea["stop_loss"] = None
                idea["target"] = None
                ideas.append(idea)

        else:  # chop
            if sma_200 and ltp > sma_200 and abs(stock_return_5d) < 2:
                idea["signal"] = "HOLD"
                idea["reason"] = "Above 200d MA, low volatility"
                idea["conviction"] = "LOW"
                idea["entry"] = None
                idea["stop_loss"] = round(sma_200 * 0.98, 2)
                idea["target"] = None
                ideas.append(idea)

    # Sort by conviction then RS
    conv_order = {"HIGH": 0, "MED": 1, "LOW": 2}
    ideas.sort(key=lambda x: (conv_order.get(x.get("conviction", "LOW"), 3), -x.get("rs_vs_nifty", 0)))

    return ideas[:10]  # Top 10 ideas


def select_fno_strategy(vix: float, regime: str) -> dict[str, str]:
    """Select F&O strategy type based on VIX regime + trend."""
    if vix < 15:
        if regime == "uptrend":
            return {"type": "Buy Calls / Bull Call Spreads", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Buy Puts / Bear Put Spreads", "bias": "bearish"}
        else:
            return {"type": "Bull Call Spreads", "bias": "neutral-bullish"}
    elif vix > 20:
        return {"type": "Sell Strangles / Iron Condors", "bias": "neutral"}
    else:
        if regime == "uptrend":
            return {"type": "Bull Call Spreads / Sell OTM Puts", "bias": "bullish"}
        elif regime == "downtrend":
            return {"type": "Bear Put Spreads / Sell OTM Calls", "bias": "bearish"}
        else:
            return {"type": "Iron Condors", "bias": "neutral"}


def generate_fno_ideas(
    regime: str,
    vix: float,
    nifty_ltp: float | None,
    banknifty_ltp: float | None,
) -> list[dict[str, Any]]:
    """Generate F&O trade ideas."""
    ideas = []
    strategy = select_fno_strategy(vix, regime)

    if nifty_ltp:
        atm_strike = round(nifty_ltp / 50) * 50
        if "call" in strategy["type"].lower() or "bull" in strategy["type"].lower():
            ideas.append({
                "instrument": "NIFTY",
                "strategy": strategy["type"].split("/")[0].strip(),
                "strikes": f"{atm_strike}CE",
                "bias": strategy["bias"],
                "rationale": f"VIX {vix:.1f} + {regime} regime",
            })
        if "put" in strategy["type"].lower() or "bear" in strategy["type"].lower():
            ideas.append({
                "instrument": "NIFTY",
                "strategy": strategy["type"].split("/")[0].strip(),
                "strikes": f"{atm_strike}PE",
                "bias": strategy["bias"],
                "rationale": f"VIX {vix:.1f} + {regime} regime",
            })
        if "sell" in strategy["type"].lower() or "iron" in strategy["type"].lower():
            ideas.append({
                "instrument": "NIFTY",
                "strategy": strategy["type"].split("/")[0].strip(),
                "strikes": f"{atm_strike - 200}PE / {atm_strike + 200}CE",
                "bias": strategy["bias"],
                "rationale": f"High VIX {vix:.1f} — premium selling",
            })

    if banknifty_ltp:
        atm_bn = round(banknifty_ltp / 100) * 100
        ideas.append({
            "instrument": "BANKNIFTY",
            "strategy": strategy["type"].split("/")[0].strip(),
            "strikes": f"{atm_bn}CE" if regime != "downtrend" else f"{atm_bn}PE",
            "bias": strategy["bias"],
            "rationale": "Sector leader — financials",
        })

    return ideas
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest test/test_market_pulse_screener.py -v
```

- [ ] **Step 4: Commit**

```bash
git add services/market_pulse_screener.py test/test_market_pulse_screener.py
git commit -m "feat(market-pulse): add rule-based equity and F&O screener"
```

### Task 8: LLM analyst layer

**Files:**
- Create: `services/market_pulse_analyst.py`

- [ ] **Step 1: Create provider-agnostic LLM analyst**

```python
"""
Provider-agnostic LLM analyst for Market Pulse.
Generates plain-English market commentary from scores and data.
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# ── In-memory cache for analyst output ──────────────────────────
# Cache is keyed by mode (swing/day) so switching modes doesn't serve stale text.
# Design spec says analyst refreshes independently on a 5-min cycle; the caller
# should return the cached value immediately and NOT block on LLM if cache exists.
_analyst_cache: dict[str, str] = {}        # mode → text
_analyst_cache_ts: dict[str, float] = {}   # mode → timestamp
_ANALYST_TTL = int(os.getenv("ANALYST_REFRESH_SECONDS", "300"))  # 5 min


def _is_analyst_cache_valid(mode: str) -> bool:
    ts = _analyst_cache_ts.get(mode, 0)
    return (time.time() - ts) < _ANALYST_TTL and mode in _analyst_cache


# ── LLM Provider Interface ──────────────────────────────────────

class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system_prompt: str) -> str:
        ...


class ClaudeProvider(LLMProvider):
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.getenv("LLM_API_KEY"))
        self.model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    def generate(self, prompt: str, system_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class OpenAIProvider(LLMProvider):
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or None,
        )
        self.model = os.getenv("LLM_MODEL", "gpt-4o")

    def generate(self, prompt: str, system_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content


class GeminiProvider(LLMProvider):
    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("LLM_API_KEY"))
        self.model = genai.GenerativeModel(os.getenv("LLM_MODEL", "gemini-pro"))

    def generate(self, prompt: str, system_prompt: str) -> str:
        response = self.model.generate_content(f"{system_prompt}\n\n{prompt}")
        return response.text


class OllamaProvider(LLMProvider):
    def __init__(self):
        self.base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434")
        self.model = os.getenv("LLM_MODEL", "llama3")

    def generate(self, prompt: str, system_prompt: str) -> str:
        import requests
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "system": system_prompt,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        return resp.json().get("response", "")


# ── Provider Factory ────────────────────────────────────────────

_PROVIDERS = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


def _get_provider() -> LLMProvider | None:
    provider_name = os.getenv("LLM_PROVIDER", "").lower()
    if not provider_name or not os.getenv("LLM_API_KEY"):
        return None
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        logger.warning("Unknown LLM provider: %s", provider_name)
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("Failed to init LLM provider %s: %s", provider_name, e)
        return None


# ── System Prompt ───────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Indian equity market analyst providing concise, actionable commentary for a swing/day trader. Your analysis must:

1. Reference Indian benchmarks: Nifty 50, Bank Nifty, India VIX, USDINR, sector indices
2. Tie back to the Market Quality Score and Execution Window Score
3. Explain the Decision (YES/CAUTION/NO) in plain English
4. Highlight which scoring categories are driving the decision
5. Note any risks or opportunities in the current environment
6. Be concise: 3-5 sentences max

Speak like a Bloomberg terminal analyst — direct, professional, data-driven. No hedging or disclaimers."""


def _build_prompt(pulse_data: dict[str, Any]) -> str:
    """Build the analyst prompt from market pulse data."""
    scores = pulse_data.get("scores", {})
    lines = [
        f"Decision: {pulse_data.get('decision', 'N/A')}",
        f"Market Quality Score: {pulse_data.get('market_quality_score', 'N/A')}/100",
        f"Execution Window Score: {pulse_data.get('execution_window_score', 'N/A')}/100",
        f"Mode: {pulse_data.get('mode', 'swing')}",
        f"Regime: {pulse_data.get('regime', 'N/A')}",
        "",
        "Category Scores:",
    ]
    for cat, info in scores.items():
        if isinstance(info, dict):
            lines.append(f"  {cat}: {info.get('score', 'N/A')}/100 ({info.get('direction', '')})")
        else:
            lines.append(f"  {cat}: {info}/100")

    # Add key rules firing
    all_rules = []
    for cat, info in scores.items():
        if isinstance(info, dict):
            for r in info.get("rules", []):
                all_rules.append(f"  [{cat}] {r.get('rule', '')}: {r.get('detail', '')} ({r.get('impact', '')})")
    if all_rules:
        lines.append("")
        lines.append("Key Rules Firing:")
        lines.extend(all_rules[:15])

    # Add sector summary
    sectors = pulse_data.get("sectors_summary", [])
    if sectors:
        lines.append("")
        lines.append("Sector Performance (5d):")
        for s in sectors[:6]:
            lines.append(f"  {s.get('name', '')}: {s.get('return_5d', 0):+.1f}%")

    lines.append("")
    lines.append("Generate a concise terminal-style analysis explaining this market environment.")

    return "\n".join(lines)


# ── Main Entry Point ────────────────────────────────────────────

def generate_analysis(pulse_data: dict[str, Any], mode: str = "swing", force: bool = False) -> str:
    """Return cached AI analyst commentary; refresh in background if stale.

    This function is designed to be NON-BLOCKING for the API caller:
    - If cache is valid → return immediately.
    - If cache is stale/missing → return stale cache or fallback NOW,
      then kick off a background thread to refresh the LLM cache.
    - If force=True → block and regenerate (used by manual refresh button).

    Args:
        pulse_data: Full market pulse response data
        mode: 'swing' or 'day' — each mode caches independently
        force: Force synchronous refresh (blocks until LLM responds)

    Returns: Analysis text string. Falls back to rule-based summary on LLM failure.
    """
    global _analyst_cache, _analyst_cache_ts

    # Fast path: cache hit
    if not force and _is_analyst_cache_valid(mode):
        return _analyst_cache[mode]

    # Force refresh: block and regenerate
    if force:
        return _refresh_analysis_sync(pulse_data, mode)

    # Non-blocking path: return stale/fallback immediately, refresh in background
    stale = _analyst_cache.get(mode)

    import threading
    threading.Thread(
        target=_refresh_analysis_sync,
        args=(pulse_data, mode),
        daemon=True,
    ).start()

    return stale if stale else _fallback_analysis(pulse_data)


def _refresh_analysis_sync(pulse_data: dict[str, Any], mode: str) -> str:
    """Synchronously generate LLM analysis and update cache. Thread-safe."""
    global _analyst_cache, _analyst_cache_ts

    provider = _get_provider()
    if provider is None:
        return _fallback_analysis(pulse_data)

    try:
        prompt = _build_prompt(pulse_data)
        analysis = provider.generate(prompt, SYSTEM_PROMPT)
        _analyst_cache[mode] = analysis
        _analyst_cache_ts[mode] = time.time()
        return analysis
    except Exception as e:
        logger.warning("LLM analysis failed for mode=%s: %s", mode, e)
        return _fallback_analysis(pulse_data)


def _fallback_analysis(pulse_data: dict[str, Any]) -> str:
    """Rule-based fallback when LLM is unavailable."""
    decision = pulse_data.get("decision", "CAUTION")
    score = pulse_data.get("market_quality_score", 50)
    regime = pulse_data.get("regime", "chop")
    exec_score = pulse_data.get("execution_window_score", 50)

    if decision == "YES":
        base = f"Market Quality {score}/100 signals a favorable trading environment."
    elif decision == "CAUTION":
        base = f"Market Quality {score}/100 — selective opportunities only."
    else:
        base = f"Market Quality {score}/100 — capital preservation mode."

    regime_text = {
        "uptrend": "Nifty in uptrend regime with positive MA alignment.",
        "downtrend": "Nifty in downtrend — avoid aggressive positioning.",
        "chop": "Choppy, range-bound conditions — patience required.",
    }.get(regime, "Mixed signals across indicators.")

    exec_text = f"Execution Window {exec_score}/100 — "
    if exec_score >= 70:
        exec_text += "breakouts are holding, follow-through is healthy."
    elif exec_score >= 50:
        exec_text += "mixed breakout quality, selectivity advised."
    else:
        exec_text += "breakouts failing frequently, wait for confirmation."

    return f"{base} {regime_text} {exec_text}"
```

- [ ] **Step 2: Commit**

```bash
git add services/market_pulse_analyst.py
git commit -m "feat(market-pulse): add provider-agnostic LLM analyst layer"
```

### Task 9: Flask blueprint + API endpoint

**Files:**
- Create: `blueprints/market_pulse.py`
- Modify: `app.py`

- [ ] **Step 1: Create blueprint**

```python
"""
Market Pulse blueprint — "Should I Be Trading India?"
Serves the React page and provides the API endpoint.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request, session

from services.market_pulse_config import CATEGORY_WEIGHTS, SECTOR_INDICES

logger = logging.getLogger(__name__)

market_pulse_bp = Blueprint("market_pulse", __name__, url_prefix="/market-pulse")


def _require_auth():
    """Check session-based authentication."""
    if not session.get("logged_in"):
        return jsonify({"status": "error", "message": "Not authenticated"}), 401
    return None


def _compute_event_proximity(events: list[dict]) -> tuple[float | None, str | None]:
    """Find nearest upcoming event and hours away.

    Uses the optional 'time' field (HH:MM IST) from each event entry.
    If omitted, defaults to 09:15 (market open). Same-day events stay
    visible until their scheduled time passes — they don't vanish at midnight.
    """
    now = datetime.now()
    nearest_hours = None
    nearest_type = None

    for event in events:
        try:
            date_str = event["date"]
            time_str = event.get("time", "09:15")  # default to market open
            event_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            hours_away = (event_dt - now).total_seconds() / 3600
            if 0 <= hours_away <= 168:  # within 7 days
                if nearest_hours is None or hours_away < nearest_hours:
                    nearest_hours = hours_away
                    nearest_type = event.get("type", "minor")
        except (ValueError, KeyError):
            continue

    return nearest_hours, nearest_type


def _compute_breadth_from_constituents(constituent_data: dict) -> dict:
    """Compute % above 50d and 200d MA from constituent data."""
    above_50d = 0
    above_200d = 0
    total = 0

    for symbol, data in constituent_data.items():
        hist = data.get("history")
        if hist is None or "close" not in hist.columns:
            continue
        closes = hist["close"]
        ltp = closes.iloc[-1]
        total += 1

        if len(closes) >= 50:
            sma_50 = closes.tail(50).mean()
            if ltp > sma_50:
                above_50d += 1

        if len(closes) >= 200:
            sma_200 = closes.tail(200).mean()
            if ltp > sma_200:
                above_200d += 1

    if total == 0:
        return {"pct_above_50d": 50, "pct_above_200d": 60}

    return {
        "pct_above_50d": round(above_50d / total * 100, 1),
        "pct_above_200d": round(above_200d / total * 100, 1),
    }


def _compute_momentum_data(sector_histories: dict, constituent_data: dict) -> dict:
    """Compute momentum sub-scores from sector histories."""
    sectors_above_20d = 0
    sector_returns_5d = {}

    for key, hist in sector_histories.items():
        if hist is None or "close" not in hist.columns or len(hist) < 20:
            continue
        closes = hist["close"]
        sma_20 = closes.tail(20).mean()
        if closes.iloc[-1] > sma_20:
            sectors_above_20d += 1
        if len(closes) >= 5:
            ret = (closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] * 100
            sector_returns_5d[key] = round(ret, 2)

    # Leadership spread
    sorted_returns = sorted(sector_returns_5d.values())
    if len(sorted_returns) >= 6:
        top3 = sum(sorted_returns[-3:]) / 3
        bot3 = sum(sorted_returns[:3]) / 3
        spread = top3 - bot3
    else:
        spread = 3.0

    # Higher highs % (Nifty 50 at 20d highs)
    highs_count = 0
    total_const = 0
    for sym, data in constituent_data.items():
        hist = data.get("history")
        if hist is None or len(hist) < 21:
            continue
        closes = hist["close"]
        total_const += 1
        high_20d = closes.iloc[-21:-1].max()
        if closes.iloc[-1] >= high_20d:
            highs_count += 1

    higher_highs_pct = (highs_count / max(total_const, 1)) * 100

    # Rotation diversity (how many sectors have positive 5d return)
    positive_sectors = sum(1 for v in sector_returns_5d.values() if v > 0)

    return {
        "sectors_above_20d": sectors_above_20d,
        "total_sectors": len(sector_histories),
        "leadership_spread": round(spread, 2),
        "higher_highs_pct": round(higher_highs_pct, 1),
        "rotation_diversity": positive_sectors,
        "sector_returns_5d": sector_returns_5d,
    }


@market_pulse_bp.route("/api/data", methods=["GET"])
def market_pulse_api():
    """Main API endpoint — returns full market pulse data."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    mode = request.args.get("mode", "swing")
    if mode not in ("swing", "day"):
        mode = "swing"

    try:
        from services.market_pulse_data import fetch_market_data
        from services.market_pulse_scoring import (
            classify_regime,
            compute_market_quality,
            get_decision,
            score_breadth,
            score_macro,
            score_momentum,
            score_trend,
            score_volatility,
        )
        from services.market_pulse_execution import (
            compute_execution_window_day,
            compute_execution_window_swing,
            track_breakouts,
        )
        from services.market_pulse_screener import (
            generate_fno_ideas,
            screen_equities,
        )
        from services.market_pulse_analyst import generate_analysis

        # 1. Fetch all data (30s cached)
        data = fetch_market_data(mode=mode)

        # 2. Extract indicators
        ni = data.get("nifty_indicators", {})
        bi = data.get("banknifty_indicators", {})
        vi = data.get("vix_indicators", {})
        ui = data.get("usdinr_indicators", {})

        # 3. Compute breadth from constituents
        breadth_data = _compute_breadth_from_constituents(data.get("constituent_data", {}))
        nse_breadth = data.get("nse_breadth") or {}
        ad_data = nse_breadth.get("advance_decline", {})
        hl_data = nse_breadth.get("highs_lows", {})

        # 4. Compute momentum data
        momentum_data = _compute_momentum_data(
            data.get("sector_histories", {}),
            data.get("constituent_data", {}),
        )

        # 5. Event proximity
        event_hours, event_type = _compute_event_proximity(data.get("events", []))

        # 6. Score all categories
        vol_score, vol_rules = score_volatility(
            vix_current=vi.get("current"),
            vix_slope_5d=vi.get("slope_5d"),
            vix_percentile=vi.get("percentile_1y"),
            pcr=data.get("pcr"),
        )

        mom_score, mom_rules = score_momentum(
            sectors_above_20d=momentum_data["sectors_above_20d"],
            total_sectors=momentum_data["total_sectors"],
            leadership_spread=momentum_data["leadership_spread"],
            higher_highs_pct=momentum_data["higher_highs_pct"],
            rotation_diversity=momentum_data["rotation_diversity"],
        )

        trend_score, trend_rules = score_trend(
            nifty_ltp=ni.get("ltp"),
            sma_20=ni.get("sma_20"),
            sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"),
            banknifty_ltp=bi.get("ltp"),
            banknifty_sma50=bi.get("sma_50"),
            rsi=ni.get("rsi_14"),
            slope_50d=ni.get("slope_50d"),
            slope_200d=ni.get("slope_200d"),
        )

        breadth_score, breadth_rules = score_breadth(
            ad_ratio=ad_data.get("ad_ratio"),
            pct_above_50d=breadth_data["pct_above_50d"],
            pct_above_200d=breadth_data["pct_above_200d"],
            highs_52w=hl_data.get("highs_52w"),
            lows_52w=hl_data.get("lows_52w"),
        )

        # Compute Nifty-USDINR 20d rolling correlation for Global Risk Proxy.
        # When corr is unavailable (insufficient data), pass None which the scorer
        # must treat as "unavailable" → neutral score (50), NOT the positive
        # "decoupled" branch. See design line 127.
        nifty_usdinr_corr = None
        nifty_hist = data.get("nifty_history")
        usdinr_hist = data.get("usdinr_history")
        if (nifty_hist is not None and usdinr_hist is not None
                and "close" in nifty_hist.columns and "close" in usdinr_hist.columns
                and len(nifty_hist) >= 20 and len(usdinr_hist) >= 20):
            import numpy as np
            n_ret = nifty_hist["close"].pct_change().dropna().tail(20).values
            u_ret = usdinr_hist["close"].pct_change().dropna().tail(20).values
            min_len = min(len(n_ret), len(u_ret))
            if min_len >= 15:
                corr_matrix = np.corrcoef(n_ret[-min_len:], u_ret[-min_len:])
                nifty_usdinr_corr = round(float(corr_matrix[0, 1]), 3)

        macro_score, macro_rules = score_macro(
            usdinr_slope_5d=ui.get("slope_5d"),
            usdinr_slope_20d=ui.get("slope_20d"),
            vix_current=vi.get("current"),
            event_hours_away=event_hours,
            event_type=event_type,
            nifty_usdinr_corr=nifty_usdinr_corr,
        )

        # 7. Market Quality Score + Decision
        category_scores = {
            "volatility": vol_score,
            "momentum": mom_score,
            "trend": trend_score,
            "breadth": breadth_score,
            "macro": macro_score,
        }
        market_quality = compute_market_quality(category_scores)
        decision = get_decision(market_quality)

        # 8. Regime
        regime = classify_regime(
            nifty_ltp=ni.get("ltp"),
            sma_20=ni.get("sma_20"),
            sma_50=ni.get("sma_50"),
            sma_200=ni.get("sma_200"),
            slope_50d=ni.get("slope_50d"),
            vix_current=vi.get("current"),
        )

        # 9. Execution Window
        try:
            track_breakouts(data.get("constituent_data", {}))
        except Exception as e:
            logger.warning("Breakout tracking failed: %s", e)

        exec_score, exec_details = compute_execution_window_swing()
        if mode == "day":
            day_score, day_details = compute_execution_window_day(data)
            exec_score = int((exec_score + day_score) / 2)
            exec_details.update(day_details)

        # 10. Screener
        equity_ideas = screen_equities(
            data.get("constituent_data", {}),
            regime,
            data.get("nifty_history"),
        )

        vix_current = vi.get("current", 15)
        nifty_ltp = ni.get("ltp")
        banknifty_ltp = bi.get("ltp")
        fno_ideas = generate_fno_ideas(regime, vix_current, nifty_ltp, banknifty_ltp)

        # 11. Sector summary for heatmap
        sectors_summary = []
        for key, returns_5d in sorted(
            momentum_data["sector_returns_5d"].items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            info = SECTOR_INDICES.get(key, {})
            quote = data.get("sectors", {}).get(key, {})
            # Compute 20d return from sector history if available
            sector_hist = data.get("sector_histories", {}).get(key)
            return_20d = None
            if sector_hist is not None and len(sector_hist) >= 20:
                c20 = sector_hist["close"]
                return_20d = round((c20.iloc[-1] - c20.iloc[-20]) / c20.iloc[-20] * 100, 2)

            sectors_summary.append({
                "key": key,
                "name": info.get("symbol", key),
                "ltp": quote.get("ltp"),
                "return_5d": returns_5d,
                "return_1d": quote.get("change_pct") if isinstance(quote.get("change_pct"), (int, float)) else None,
                "return_20d": return_20d,
            })

        # 12. Alerts (use event time field so same-day events stay visible)
        alerts = []
        if event_hours is not None and event_hours <= 72:
            for event in data.get("events", []):
                try:
                    date_str = event["date"]
                    time_str = event.get("time", "09:15")
                    event_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    hours = (event_dt - datetime.now()).total_seconds() / 3600
                    if 0 <= hours <= 72:
                        alerts.append({
                            "type": event.get("type", "minor"),
                            "name": event.get("name", "Unknown event"),
                            "date": event["date"],
                            "time": time_str,
                            "hours_away": round(hours, 1),
                        })
                except (ValueError, KeyError):
                    continue

        # 13. Build response
        pulse_response = {
            "decision": decision,
            "market_quality_score": market_quality,
            "execution_window_score": exec_score,
            "mode": mode,
            "regime": regime,
            "scores": {
                "volatility": {"score": vol_score, "weight": CATEGORY_WEIGHTS["volatility"], "direction": _direction(vol_rules), "rules": vol_rules},
                "momentum": {"score": mom_score, "weight": CATEGORY_WEIGHTS["momentum"], "direction": _direction(mom_rules), "rules": mom_rules},
                "trend": {"score": trend_score, "weight": CATEGORY_WEIGHTS["trend"], "direction": _direction(trend_rules), "rules": trend_rules},
                "breadth": {"score": breadth_score, "weight": CATEGORY_WEIGHTS["breadth"], "direction": _direction(breadth_rules), "rules": breadth_rules},
                "macro": {"score": macro_score, "weight": CATEGORY_WEIGHTS["macro"], "direction": _direction(macro_rules), "rules": macro_rules},
            },
            "ticker": data.get("ticker", {}),
            "sectors": sectors_summary,
            "alerts": alerts,
            "equity_ideas": equity_ideas,
            "fno_ideas": fno_ideas,
            "execution_details": exec_details,
            "errors": data.get("errors", []),
            "updated_at": datetime.now().isoformat(),
            "cache_ttl": 30,
        }

        # 14. AI Analysis (non-blocking: returns cached/fallback immediately,
        #     refreshes LLM in background thread when stale)
        try:
            pulse_response["sectors_summary"] = sectors_summary
            force_analysis = request.args.get("refresh") == "1"
            analysis = generate_analysis(pulse_response, mode=mode, force=force_analysis)
            pulse_response["analysis"] = analysis
        except Exception as e:
            logger.warning("Analysis generation failed: %s", e)
            pulse_response["analysis"] = None

        return jsonify({"status": "success", "data": pulse_response})

    except Exception as e:
        logger.exception("Market pulse API error: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


def _direction(rules: list[dict]) -> str:
    """Derive overall direction from rules."""
    positive = sum(1 for r in rules if r.get("impact") == "positive")
    negative = sum(1 for r in rules if r.get("impact") == "negative")
    if positive > negative:
        return "healthy" if positive >= 3 else "improving"
    elif negative > positive:
        return "weakening" if negative >= 3 else "risk-off"
    return "neutral"
```

- [ ] **Step 2: Register blueprint in app.py**

Find the line with `app.register_blueprint(intelligence_bp)` in `app.py` and add after it:

```python
from blueprints.market_pulse import market_pulse_bp
app.register_blueprint(market_pulse_bp)
```

- [ ] **Step 3: Commit**

```bash
git add blueprints/market_pulse.py app.py
git commit -m "feat(market-pulse): add Flask blueprint with full API endpoint"
```

---

## Chunk 4: Frontend — API + Hook + Components

### Task 10: Frontend API client + hook

**Files:**
- Create: `frontend/src/api/market-pulse.ts`
- Create: `frontend/src/hooks/useMarketPulse.ts`

- [ ] **Step 1: Create API client**

```typescript
import { webClient } from './client'

export interface MarketPulseRule {
  rule: string
  detail: string
  impact: 'positive' | 'negative' | 'neutral'
}

export interface CategoryScore {
  score: number
  weight: number
  direction: string
  rules: MarketPulseRule[]
}

export interface TickerItem {
  ltp?: number
  change_pct?: number
  open?: number
  high?: number
  low?: number
  prev_close?: number
}

export interface SectorData {
  key: string
  name: string
  ltp: number | null
  return_5d: number
  return_1d: number | null
  return_20d: number | null
}

export interface EquityIdea {
  symbol: string
  sector: string
  signal: 'BUY' | 'SELL' | 'HOLD' | 'AVOID'
  ltp: number
  entry: number | null
  stop_loss: number | null
  target: number | null
  conviction: 'HIGH' | 'MED' | 'LOW'
  reason: string
  rs_vs_nifty: number
}

export interface FnoIdea {
  instrument: string
  strategy: string
  strikes: string
  bias: string
  rationale: string
}

export interface AlertItem {
  type: 'major' | 'minor'
  name: string
  date: string
  hours_away: number
}

export interface MarketPulseData {
  decision: 'YES' | 'CAUTION' | 'NO'
  market_quality_score: number
  execution_window_score: number
  mode: 'swing' | 'day'
  regime: 'uptrend' | 'downtrend' | 'chop'
  scores: {
    volatility: CategoryScore
    momentum: CategoryScore
    trend: CategoryScore
    breadth: CategoryScore
    macro: CategoryScore
  }
  ticker: Record<string, TickerItem>
  sectors: SectorData[]
  alerts: AlertItem[]
  equity_ideas: EquityIdea[]
  fno_ideas: FnoIdea[]
  analysis: string | null
  execution_details: Record<string, any>
  errors: string[]
  updated_at: string
  cache_ttl: number
}

export async function fetchMarketPulse(mode: 'swing' | 'day' = 'swing'): Promise<MarketPulseData> {
  const response = await webClient.get('/market-pulse/api/data', {
    params: { mode },
  })
  if (response.data?.status === 'success') {
    return response.data.data
  }
  throw new Error(response.data?.message || 'Failed to fetch market pulse')
}
```

- [ ] **Step 2: Create TanStack Query hook with 45s polling**

```typescript
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { fetchMarketPulse, type MarketPulseData } from '@/api/market-pulse'

const POLL_INTERVAL = 45_000 // 45 seconds

export function useMarketPulse() {
  const [mode, setMode] = useState<'swing' | 'day'>('swing')
  const queryClient = useQueryClient()

  const { data, isLoading, error, dataUpdatedAt } = useQuery<MarketPulseData>({
    queryKey: ['market-pulse', mode],
    queryFn: () => fetchMarketPulse(mode),
    refetchInterval: POLL_INTERVAL,
    staleTime: 30_000,
    retry: 2,
  })

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['market-pulse', mode] })
  }, [queryClient, mode])

  const secondsAgo = dataUpdatedAt
    ? Math.round((Date.now() - dataUpdatedAt) / 1000)
    : null

  return {
    data,
    isLoading,
    error,
    mode,
    setMode,
    refresh,
    secondsAgo,
  }
}
```

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/api/market-pulse.ts src/hooks/useMarketPulse.ts
git commit -m "feat(market-pulse): add frontend API client and polling hook"
```

### Task 11: Terminal-style UI components

**Files:**
- Create: `frontend/src/components/market-pulse/TickerBar.tsx`
- Create: `frontend/src/components/market-pulse/AlertBanner.tsx`
- Create: `frontend/src/components/market-pulse/HeroDecision.tsx`
- Create: `frontend/src/components/market-pulse/ScorePanel.tsx`
- Create: `frontend/src/components/market-pulse/RulesFiring.tsx`
- Create: `frontend/src/components/market-pulse/TerminalAnalysis.tsx`
- Create: `frontend/src/components/market-pulse/SectorHeatmap.tsx`
- Create: `frontend/src/components/market-pulse/ScoreBreakdown.tsx`
- Create: `frontend/src/components/market-pulse/EquityIdeas.tsx`
- Create: `frontend/src/components/market-pulse/FnoIdeas.tsx`
- Create: `frontend/src/components/market-pulse/ModeSwitcher.tsx`

- [ ] **Step 1: Create TickerBar**

```typescript
// frontend/src/components/market-pulse/TickerBar.tsx
import { cn } from '@/lib/utils'
import { ModeSwitcher } from './ModeSwitcher'
import type { MarketPulseData } from '@/api/market-pulse'

interface TickerBarProps {
  data: MarketPulseData | undefined
  mode: 'swing' | 'day'
  onModeChange: (mode: 'swing' | 'day') => void
  secondsAgo: number | null
  onRefresh: () => void
  isLoading: boolean
}

const TICKER_KEYS = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'INDIAVIX', 'USDINR'] as const

export function TickerBar({ data, mode, onModeChange, secondsAgo, onRefresh, isLoading }: TickerBarProps) {
  return (
    <div className="flex items-center justify-between px-4 py-2 bg-[#0d1117] border-b border-[#1b2332] font-mono text-xs">
      {/* Ticker items */}
      <div className="flex items-center gap-4 overflow-x-auto">
        {TICKER_KEYS.map((key) => {
          const item = data?.ticker?.[key]
          const changePct = item?.change_pct
          const color = changePct == null ? 'text-gray-400' : changePct >= 0 ? 'text-green-400' : 'text-red-400'
          return (
            <div key={key} className="flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-gray-500">{key}</span>
              <span className={color}>{item?.ltp?.toLocaleString('en-IN') ?? '—'}</span>
              {changePct != null && (
                <span className={cn('text-[10px]', color)}>
                  {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                </span>
              )}
            </div>
          )
        })}
        {/* Sector tickers */}
        {data?.sectors?.slice(0, 4).map((s) => (
          <div key={s.key} className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="text-gray-500">{s.key}</span>
            <span className={s.return_1d != null && s.return_1d >= 0 ? 'text-green-400' : 'text-red-400'}>
              {s.ltp?.toLocaleString('en-IN') ?? '—'}
            </span>
          </div>
        ))}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 ml-4">
        {/* Mode toggle — uses shared ModeSwitcher component */}
        <ModeSwitcher mode={mode} onChange={onModeChange} />

        {/* Status */}
        <div className="flex items-center gap-1.5">
          <div className={cn('w-1.5 h-1.5 rounded-full', isLoading ? 'bg-amber-400 animate-pulse' : 'bg-green-400')} />
          <span className="text-gray-500 text-[10px]">
            {isLoading ? 'UPDATING' : 'LIVE'}
          </span>
        </div>

        {/* Last updated */}
        {secondsAgo != null && (
          <span className="text-gray-600 text-[10px]">{secondsAgo}s ago</span>
        )}

        {/* Refresh */}
        <button
          onClick={onRefresh}
          className="text-gray-500 hover:text-gray-300 text-[10px]"
          title="Refresh"
        >
          ↻
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create AlertBanner**

```typescript
// frontend/src/components/market-pulse/AlertBanner.tsx
import { useState } from 'react'
import type { AlertItem } from '@/api/market-pulse'

interface AlertBannerProps {
  alerts: AlertItem[]
}

export function AlertBanner({ alerts }: AlertBannerProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  const visible = alerts.filter((a) => !dismissed.has(a.name))
  if (visible.length === 0) return null

  return (
    <div className="px-4 py-1.5 bg-amber-900/20 border-b border-amber-700/30 font-mono text-xs">
      {visible.map((alert) => (
        <div key={alert.name} className="flex items-center justify-between">
          <span>
            <span className="text-amber-400 mr-2">⚠</span>
            <span className="text-amber-300">{alert.name}</span>
            <span className="text-amber-600 ml-2">
              {alert.hours_away < 24
                ? `in ${Math.round(alert.hours_away)}h`
                : `on ${alert.date}`}
            </span>
          </span>
          <button
            onClick={() => setDismissed((prev) => new Set([...prev, alert.name]))}
            className="text-amber-600 hover:text-amber-400 ml-4"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Create HeroDecision**

```typescript
// frontend/src/components/market-pulse/HeroDecision.tsx
import { cn } from '@/lib/utils'

interface HeroDecisionProps {
  decision: 'YES' | 'CAUTION' | 'NO'
  qualityScore: number
  executionScore: number
}

const DECISION_STYLES = {
  YES: { bg: 'bg-green-500/10', border: 'border-green-500/40', text: 'text-green-400', glow: 'shadow-green-500/20' },
  CAUTION: { bg: 'bg-amber-500/10', border: 'border-amber-500/40', text: 'text-amber-400', glow: 'shadow-amber-500/20' },
  NO: { bg: 'bg-red-500/10', border: 'border-red-500/40', text: 'text-red-400', glow: 'shadow-red-500/20' },
}

export function HeroDecision({ decision, qualityScore, executionScore }: HeroDecisionProps) {
  const style = DECISION_STYLES[decision]

  return (
    <div className={cn('flex flex-col items-center justify-center p-6 rounded-lg border font-mono', style.bg, style.border)}>
      <div className="text-gray-500 text-[10px] uppercase tracking-widest mb-2">Should I Trade India?</div>

      {/* Decision badge */}
      <div className={cn('text-4xl font-bold tracking-wider mb-4', style.text, 'drop-shadow-lg')}>
        {decision}
      </div>

      {/* Score circle */}
      <div className="relative w-24 h-24 mb-4">
        <svg className="w-24 h-24 -rotate-90" viewBox="0 0 100 100">
          <circle cx="50" cy="50" r="42" fill="none" stroke="#1b2332" strokeWidth="6" />
          <circle
            cx="50" cy="50" r="42" fill="none"
            stroke={decision === 'YES' ? '#4ade80' : decision === 'CAUTION' ? '#fbbf24' : '#f87171'}
            strokeWidth="6"
            strokeDasharray={`${qualityScore * 2.64} 264`}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={cn('text-2xl font-bold', style.text)}>{qualityScore}</span>
        </div>
      </div>

      <div className="text-gray-500 text-[10px] uppercase tracking-wider">Market Quality Score</div>

      {/* Execution Window */}
      <div className="mt-3 flex items-center gap-2">
        <span className="text-gray-500 text-[10px]">EXEC WINDOW</span>
        <span className={cn(
          'text-sm font-bold',
          executionScore >= 70 ? 'text-green-400' : executionScore >= 50 ? 'text-amber-400' : 'text-red-400'
        )}>
          {executionScore}
        </span>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create ScorePanel**

```typescript
// frontend/src/components/market-pulse/ScorePanel.tsx
import { cn } from '@/lib/utils'
import type { CategoryScore } from '@/api/market-pulse'

interface ScorePanelProps {
  name: string
  data: CategoryScore
}

const DIRECTION_COLOR: Record<string, string> = {
  healthy: 'text-green-400',
  improving: 'text-green-300',
  neutral: 'text-gray-400',
  weakening: 'text-amber-400',
  'risk-off': 'text-red-400',
}

export function ScorePanel({ name, data }: ScorePanelProps) {
  const scoreColor = data.score >= 70 ? 'text-green-400' : data.score >= 50 ? 'text-amber-400' : 'text-red-400'
  const barColor = data.score >= 70 ? 'bg-green-500' : data.score >= 50 ? 'bg-amber-500' : 'bg-red-500'

  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-gray-400 text-[10px] uppercase tracking-wider">{name}</span>
        <span className={cn('text-lg font-bold', scoreColor)}>{data.score}</span>
      </div>

      {/* Progress bar */}
      <div className="h-1 bg-[#1b2332] rounded-full mb-2">
        <div className={cn('h-1 rounded-full transition-all', barColor)} style={{ width: `${data.score}%` }} />
      </div>

      {/* Direction */}
      <div className="flex items-center gap-1">
        <span className="text-gray-600 text-[10px]">STATUS</span>
        <span className={cn('text-[10px] uppercase', DIRECTION_COLOR[data.direction] || 'text-gray-400')}>
          {data.direction}
        </span>
      </div>

      {/* Weight */}
      <div className="text-gray-600 text-[10px] mt-1">
        Weight: {(data.weight * 100).toFixed(0)}% | Contribution: {(data.score * data.weight).toFixed(1)}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Create RulesFiring**

```typescript
// frontend/src/components/market-pulse/RulesFiring.tsx
import { cn } from '@/lib/utils'
import type { CategoryScore } from '@/api/market-pulse'

interface RulesFiringProps {
  scores: Record<string, CategoryScore>
}

export function RulesFiring({ scores }: RulesFiringProps) {
  const allRules = Object.entries(scores).flatMap(([category, data]) =>
    data.rules.map((r) => ({ ...r, category }))
  )

  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono overflow-y-auto max-h-64">
      <div className="text-gray-400 text-[10px] uppercase tracking-wider mb-2">Rules Firing</div>
      <div className="space-y-1">
        {allRules.map((r, i) => (
          <div key={i} className="flex items-start gap-1.5 text-[11px]">
            <span className={cn(
              r.impact === 'positive' ? 'text-green-400' : r.impact === 'negative' ? 'text-red-400' : 'text-amber-400'
            )}>
              {r.impact === 'positive' ? '✓' : r.impact === 'negative' ? '✗' : '⚠'}
            </span>
            <div>
              <span className="text-gray-300">{r.rule}</span>
              <span className="text-gray-600 ml-1">{r.detail}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Create TerminalAnalysis**

```typescript
// frontend/src/components/market-pulse/TerminalAnalysis.tsx
interface TerminalAnalysisProps {
  analysis: string | null
}

export function TerminalAnalysis({ analysis }: TerminalAnalysisProps) {
  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
      <div className="text-gray-400 text-[10px] uppercase tracking-wider mb-2">Terminal Analysis</div>
      <p className="text-gray-300 text-xs leading-relaxed">
        {analysis || 'Awaiting analysis...'}
      </p>
    </div>
  )
}
```

- [ ] **Step 7: Create SectorHeatmap**

```typescript
// frontend/src/components/market-pulse/SectorHeatmap.tsx
import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { SectorData } from '@/api/market-pulse'

interface SectorHeatmapProps {
  sectors: SectorData[]
}

export function SectorHeatmap({ sectors }: SectorHeatmapProps) {
  const [period, setPeriod] = useState<'1d' | '5d' | '20d'>('5d')

  const getVal = (s: SectorData) => {
    if (period === '1d') return s.return_1d ?? 0
    if (period === '20d') return s.return_20d ?? 0
    return s.return_5d
  }

  const sorted = [...sectors].sort((a, b) => getVal(b) - getVal(a))

  const maxAbs = Math.max(...sorted.map((s) => Math.abs(getVal(s))), 1)

  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-gray-400 text-[10px] uppercase tracking-wider">Sector Heatmap</span>
        <div className="flex gap-1">
          {(['1d', '5d', '20d'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={cn(
                'px-1.5 py-0.5 text-[9px] uppercase rounded',
                period === p ? 'bg-blue-600/30 text-blue-400' : 'text-gray-600 hover:text-gray-400'
              )}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1">
        {sorted.map((s) => {
          const val = getVal(s)
          const barWidth = (Math.abs(val) / maxAbs) * 100
          const isPositive = val >= 0

          return (
            <div key={s.key} className="flex items-center gap-2 text-[11px]">
              <span className="text-gray-500 w-16 text-right">{s.key}</span>
              <div className="flex-1 h-3 bg-[#161b22] rounded overflow-hidden relative">
                <div
                  className={cn('h-full rounded', isPositive ? 'bg-green-500/40' : 'bg-red-500/40')}
                  style={{ width: `${barWidth}%` }}
                />
              </div>
              <span className={cn('w-14 text-right', isPositive ? 'text-green-400' : 'text-red-400')}>
                {val >= 0 ? '+' : ''}{val.toFixed(1)}%
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 8: Create ScoreBreakdown**

```typescript
// frontend/src/components/market-pulse/ScoreBreakdown.tsx
import { cn } from '@/lib/utils'
import type { CategoryScore } from '@/api/market-pulse'

interface ScoreBreakdownProps {
  scores: Record<string, CategoryScore>
  totalScore: number
}

const CATEGORY_COLORS: Record<string, string> = {
  volatility: 'bg-purple-500',
  momentum: 'bg-blue-500',
  trend: 'bg-green-500',
  breadth: 'bg-amber-500',
  macro: 'bg-cyan-500',
}

export function ScoreBreakdown({ scores, totalScore }: ScoreBreakdownProps) {
  const contributions = Object.entries(scores).map(([key, data]) => ({
    key,
    contribution: data.score * data.weight,
    score: data.score,
    weight: data.weight,
  }))

  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
      <div className="text-gray-400 text-[10px] uppercase tracking-wider mb-2">Score Contribution</div>

      {/* Stacked bar */}
      <div className="flex h-4 rounded overflow-hidden mb-3">
        {contributions.map(({ key, contribution }) => (
          <div
            key={key}
            className={cn(CATEGORY_COLORS[key], 'opacity-70')}
            style={{ width: `${(contribution / Math.max(totalScore, 1)) * 100}%` }}
            title={`${key}: ${contribution.toFixed(1)}`}
          />
        ))}
      </div>

      {/* Legend */}
      <div className="grid grid-cols-5 gap-1 text-[10px]">
        {contributions.map(({ key, contribution, score, weight }) => (
          <div key={key} className="text-center">
            <div className="flex items-center justify-center gap-1">
              <div className={cn('w-2 h-2 rounded-sm', CATEGORY_COLORS[key])} />
              <span className="text-gray-500 uppercase">{key.slice(0, 3)}</span>
            </div>
            <div className="text-gray-400">{contribution.toFixed(1)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 9: Create EquityIdeas**

```typescript
// frontend/src/components/market-pulse/EquityIdeas.tsx
import { cn } from '@/lib/utils'
import type { EquityIdea } from '@/api/market-pulse'

interface EquityIdeasProps {
  ideas: EquityIdea[]
}

const SIGNAL_STYLES: Record<string, string> = {
  BUY: 'text-green-400 bg-green-500/10',
  SELL: 'text-red-400 bg-red-500/10',
  HOLD: 'text-amber-400 bg-amber-500/10',
  AVOID: 'text-gray-400 bg-gray-500/10',
}

export function EquityIdeas({ ideas }: EquityIdeasProps) {
  if (ideas.length === 0) {
    return (
      <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
        <div className="text-gray-400 text-[10px] uppercase tracking-wider mb-2">Equity Ideas</div>
        <div className="text-gray-600 text-xs">No ideas matching current regime criteria</div>
      </div>
    )
  }

  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
      <div className="text-gray-400 text-[10px] uppercase tracking-wider mb-2">Equity Ideas</div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-gray-600 text-left">
              <th className="pb-1 pr-3">Symbol</th>
              <th className="pb-1 pr-3">Signal</th>
              <th className="pb-1 pr-3 text-right">LTP</th>
              <th className="pb-1 pr-3 text-right">Entry</th>
              <th className="pb-1 pr-3 text-right">SL</th>
              <th className="pb-1 pr-3 text-right">Target</th>
              <th className="pb-1 pr-3">Conv</th>
              <th className="pb-1">Reason</th>
            </tr>
          </thead>
          <tbody>
            {ideas.map((idea) => (
              <tr key={idea.symbol} className="border-t border-[#1b2332]/50">
                <td className="py-1 pr-3 text-gray-300 font-semibold">{idea.symbol}</td>
                <td className="py-1 pr-3">
                  <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-bold', SIGNAL_STYLES[idea.signal])}>
                    {idea.signal}
                  </span>
                </td>
                <td className="py-1 pr-3 text-right text-gray-400">{idea.ltp?.toLocaleString('en-IN')}</td>
                <td className="py-1 pr-3 text-right text-gray-400">{idea.entry?.toLocaleString('en-IN') ?? '—'}</td>
                <td className="py-1 pr-3 text-right text-red-400/70">{idea.stop_loss?.toLocaleString('en-IN') ?? '—'}</td>
                <td className="py-1 pr-3 text-right text-green-400/70">{idea.target?.toLocaleString('en-IN') ?? '—'}</td>
                <td className="py-1 pr-3">
                  <span className={cn(
                    'text-[10px]',
                    idea.conviction === 'HIGH' ? 'text-green-400' : idea.conviction === 'MED' ? 'text-amber-400' : 'text-gray-500'
                  )}>
                    {idea.conviction}
                  </span>
                </td>
                <td className="py-1 text-gray-500 truncate max-w-[200px]">{idea.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 10: Create FnoIdeas**

```typescript
// frontend/src/components/market-pulse/FnoIdeas.tsx
import { cn } from '@/lib/utils'
import type { FnoIdea } from '@/api/market-pulse'

interface FnoIdeasProps {
  ideas: FnoIdea[]
  regime: string
  vixLevel?: number
}

export function FnoIdeas({ ideas, regime, vixLevel }: FnoIdeasProps) {
  return (
    <div className="bg-[#0d1117] border border-[#1b2332] rounded p-3 font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-gray-400 text-[10px] uppercase tracking-wider">F&O Ideas</span>
        <span className="text-gray-600 text-[10px]">
          VIX: {vixLevel?.toFixed(1) ?? '—'} | Regime: {regime}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-gray-600 text-left">
              <th className="pb-1 pr-3">Instrument</th>
              <th className="pb-1 pr-3">Strategy</th>
              <th className="pb-1 pr-3">Strikes</th>
              <th className="pb-1 pr-3">Bias</th>
              <th className="pb-1">Rationale</th>
            </tr>
          </thead>
          <tbody>
            {ideas.map((idea, i) => (
              <tr key={i} className="border-t border-[#1b2332]/50">
                <td className="py-1 pr-3 text-gray-300 font-semibold">{idea.instrument}</td>
                <td className="py-1 pr-3 text-blue-400">{idea.strategy}</td>
                <td className="py-1 pr-3 text-amber-300">{idea.strikes}</td>
                <td className="py-1 pr-3">
                  <span className={cn(
                    'text-[10px]',
                    idea.bias === 'bullish' ? 'text-green-400' : idea.bias === 'bearish' ? 'text-red-400' : 'text-gray-400'
                  )}>
                    {idea.bias}
                  </span>
                </td>
                <td className="py-1 text-gray-500">{idea.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 11: Create ModeSwitcher**

```typescript
// frontend/src/components/market-pulse/ModeSwitcher.tsx
import { cn } from '@/lib/utils'

interface ModeSwitcherProps {
  mode: 'swing' | 'day'
  onChange: (mode: 'swing' | 'day') => void
}

const MODES = [
  { value: 'swing' as const, label: 'Swing', description: 'Multi-day holds' },
  { value: 'day' as const, label: 'Day', description: 'Intraday only' },
]

export function ModeSwitcher({ mode, onChange }: ModeSwitcherProps) {
  return (
    <div className="flex items-center gap-1 bg-[#161b22] rounded p-0.5">
      {MODES.map((m) => (
        <button
          key={m.value}
          onClick={() => onChange(m.value)}
          title={m.description}
          className={cn(
            'px-2 py-0.5 text-[10px] font-mono uppercase rounded transition-colors',
            mode === m.value
              ? 'bg-blue-600/30 text-blue-400'
              : 'text-gray-600 hover:text-gray-400'
          )}
        >
          {m.label}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 12: Commit all components**

```bash
cd frontend
git add src/components/market-pulse/
git commit -m "feat(market-pulse): add all Bloomberg terminal-style UI components"
```

### Task 12: Main page + routing

**Files:**
- Create: `frontend/src/pages/MarketPulse.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create main page**

```typescript
// frontend/src/pages/MarketPulse.tsx
import { useMarketPulse } from '@/hooks/useMarketPulse'
import { TickerBar } from '@/components/market-pulse/TickerBar'
import { AlertBanner } from '@/components/market-pulse/AlertBanner'
import { HeroDecision } from '@/components/market-pulse/HeroDecision'
import { ScorePanel } from '@/components/market-pulse/ScorePanel'
import { RulesFiring } from '@/components/market-pulse/RulesFiring'
import { TerminalAnalysis } from '@/components/market-pulse/TerminalAnalysis'
import { SectorHeatmap } from '@/components/market-pulse/SectorHeatmap'
import { ScoreBreakdown } from '@/components/market-pulse/ScoreBreakdown'
import { EquityIdeas } from '@/components/market-pulse/EquityIdeas'
import { FnoIdeas } from '@/components/market-pulse/FnoIdeas'

export default function MarketPulse() {
  const { data, isLoading, mode, setMode, refresh, secondsAgo } = useMarketPulse()

  if (isLoading && !data) {
    return (
      <div className="h-screen bg-[#0a0e1a] flex items-center justify-center font-mono">
        <div className="text-center">
          <div className="text-gray-400 text-sm animate-pulse">Loading Market Pulse...</div>
          <div className="mt-2 text-gray-600 text-xs">Fetching data from broker APIs and NSE</div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen bg-[#0a0e1a] flex flex-col overflow-hidden text-white">
      {/* Ticker Bar */}
      <TickerBar
        data={data}
        mode={mode}
        onModeChange={setMode}
        secondsAgo={secondsAgo}
        onRefresh={refresh}
        isLoading={isLoading}
      />

      {/* Alert Banner */}
      {data?.alerts && data.alerts.length > 0 && <AlertBanner alerts={data.alerts} />}

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Top Row: Hero + Score Panels */}
        <div className="grid grid-cols-12 gap-3">
          {/* Left: Hero Decision + Analysis + Rules */}
          <div className="col-span-4 space-y-3">
            <HeroDecision
              decision={data?.decision ?? 'CAUTION'}
              qualityScore={data?.market_quality_score ?? 0}
              executionScore={data?.execution_window_score ?? 0}
            />
            <TerminalAnalysis analysis={data?.analysis ?? null} />
            {data?.scores && <RulesFiring scores={data.scores} />}
          </div>

          {/* Right: Score Panels + Heatmap + Breakdown */}
          <div className="col-span-8 space-y-3">
            {/* Score Grid */}
            {data?.scores && (
              <div className="grid grid-cols-3 gap-3">
                <ScorePanel name="Volatility" data={data.scores.volatility} />
                <ScorePanel name="Momentum" data={data.scores.momentum} />
                <ScorePanel name="Trend" data={data.scores.trend} />
                <ScorePanel name="Breadth" data={data.scores.breadth} />
                <ScorePanel name="Macro" data={data.scores.macro} />
                <ScorePanel
                  name="Execution"
                  data={{
                    score: data.execution_window_score,
                    weight: 0,
                    direction: data.execution_window_score >= 70 ? 'healthy' : data.execution_window_score >= 50 ? 'neutral' : 'weakening',
                    rules: [],
                  }}
                />
              </div>
            )}

            {/* Sector Heatmap */}
            {data?.sectors && <SectorHeatmap sectors={data.sectors} />}

            {/* Score Breakdown */}
            {data?.scores && (
              <ScoreBreakdown scores={data.scores} totalScore={data.market_quality_score} />
            )}
          </div>
        </div>

        {/* Bottom Row: Trade Ideas */}
        <div className="grid grid-cols-2 gap-3">
          <EquityIdeas ideas={data?.equity_ideas ?? []} />
          <FnoIdeas
            ideas={data?.fno_ideas ?? []}
            regime={data?.regime ?? 'chop'}
            vixLevel={data?.ticker?.INDIAVIX?.ltp}
          />
        </div>

        {/* Errors */}
        {data?.errors && data.errors.length > 0 && (
          <div className="bg-red-900/10 border border-red-800/30 rounded p-2 font-mono text-[10px] text-red-400">
            Data issues: {data.errors.join(' | ')}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add route to App.tsx**

In `frontend/src/App.tsx`, add the lazy import near other lazy imports:

```typescript
const MarketPulse = lazy(() => import('@/pages/MarketPulse'))
```

Add the route inside the `<Route element={<FullWidthLayout />}>` block:

```typescript
<Route path="/market-pulse" element={<MarketPulse />} />
```

- [ ] **Step 3: Add Flask catch-all route for /market-pulse**

In `blueprints/react_app.py`, find the React catch-all route function and add `/market-pulse` to the list of React-handled routes (same pattern as `/scalping`).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/MarketPulse.tsx frontend/src/App.tsx blueprints/react_app.py
git commit -m "feat(market-pulse): add main page with full layout and routing"
```

### Task 13: Build and verify

- [ ] **Step 1: Build frontend**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 2: Run backend tests**

```bash
uv run pytest test/test_market_pulse_scoring.py test/test_market_pulse_screener.py -v
```

Expected: ALL PASS.

- [ ] **Step 3: Verify app starts**

```bash
uv run python -c "from blueprints.market_pulse import market_pulse_bp; print('Blueprint OK')"
uv run python -c "from services.market_pulse_scoring import compute_market_quality; print('Scoring OK')"
uv run python -c "from services.market_pulse_screener import select_fno_strategy; print('Screener OK')"
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(market-pulse): complete Market Pulse dashboard build"
```
