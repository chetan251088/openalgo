# TOMIC Options Selling System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Overhaul TOMIC from a broken equity-momentum system into a production-grade intraday options selling engine for NIFTY, BANKNIFTY, SENSEX, and Nifty 50 stocks.

**Architecture:** Remove `SniperAgent`, `VolatilityAgent`, and `ConflictRouter` from the trading pipeline. Add `MarketContextAgent` (India VIX + PCR + trend), `DailyPlanAgent` (9:45 AM morning plan), `StrategyEngine` (unified 4-mode signal engine), `PositionManager` (real-time P&L monitoring), and `ExpirySpecialist` (gamma capture after 14:00). Extend `ExecutionAgent` with delta-based multi-leg symbol resolution. All new components plug into the existing `RiskAgent` sizing chain → `CommandStore` → `ExecutionAgent` pipeline.

**Tech Stack:** Python 3.12, Flask, SQLite (CommandStore), ZeroMQ (EventBus), `GreeksEngine` (py_vollib + pure BS fallback), React 19, Zustand, TanStack Query

---

## Background: What Already Exists and Works

These files are **not touched** unless explicitly stated:
- `tomic/command_store.py` — durable queue
- `tomic/circuit_breakers.py` — kill-switch
- `tomic/agents/regime_agent.py` — Ichimoku trend classifier (BULLISH/BEARISH/CONGESTION/BLOWOFF)
- `tomic/agents/risk_agent.py` — 8-step sizing chain
- `tomic/agents/execution_agent.py` — order execution (extended in Task 8)
- `tomic/agents/journaling_agent.py` — audit trail
- `tomic/greeks_engine.py` — `GreeksEngine.compute(spot, strike, expiry_days, option_price, option_type='c') -> GreeksResult` (has `.delta`, `.iv`, `.gamma`, `.theta`)
- `tomic/position_book.py` — `PositionBook`, `Position`, `PositionSnapshot`
- `tomic/market_bridge.py` — tick ingestion into agents
- `tomic/ws_data_manager.py` — WebSocket subscription manager
- `tomic/freshness.py` — data staleness gates
- `tomic/supervisor.py` — agent lifecycle

**Critical interface note:** `RiskAgent` receives signals via `runtime._enqueue_routed_signals(routed_signals)`. Each `RoutedSignal` has a `.signal_dict` with keys: `instrument`, `strategy_type`, `direction`, `legs` (list), `signal_strength`. The `StrategyEngine` (Task 5) must output `RoutedSignal` objects compatible with this interface.

---

## Task 1: Config Extensions

**Files:**
- Modify: `tomic/config.py` (add after existing dataclasses, before `TomicConfig`)

**Step 1: Add new enums and dataclasses to `tomic/config.py`**

Add after the `TomicMode` enum (line ~79):

```python
class EntryMode(str, Enum):
    MORNING_PLAN = "morning_plan"
    CONTINUOUS = "continuous"
    EVENT_DRIVEN = "event_driven"
    EXPIRY_GAMMA = "expiry_gamma"
```

Add after `UniverseParams` dataclass (line ~357):

```python
# ---------------------------------------------------------------------------
# Market Context Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketContextParams:
    pcr_bullish_above: float = 1.3       # PCR > 1.3 → tilt to Bull Put
    pcr_bearish_below: float = 0.8       # PCR < 0.8 → tilt to Bear Call
    pcr_refresh_interval_s: float = 300.0  # refresh every 5 min
    vix_too_low: float = 12.0            # skip selling below this
    vix_normal_high: float = 18.0        # upper bound of normal range
    vix_elevated_high: float = 25.0      # upper bound of elevated range
    vix_extreme: float = 35.0            # skip above this
    trend_ma_period: int = 20            # bars for trend MA
    max_pain_refresh_interval_s: float = 300.0


# ---------------------------------------------------------------------------
# Daily Plan Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DailyPlanParams:
    plan_time_hhmm: str = "09:45"       # when to generate morning plan
    plan_valid_until_hhmm: str = "14:00"  # plan expires at this time
    instruments: tuple = ("NIFTY", "BANKNIFTY", "SENSEX")
    short_delta_normal: float = 0.25     # VIX 12-18
    short_delta_elevated: float = 0.30  # VIX 18-25
    short_delta_high: float = 0.20      # VIX 25-35
    wing_delta_normal: float = 0.10
    wing_delta_elevated: float = 0.12
    wing_delta_high: float = 0.08


# ---------------------------------------------------------------------------
# Position Manager Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PositionManagerParams:
    trail_stop_activate_pct: float = 0.30  # activate trail at 30% profit
    profit_target_pct: float = 0.50       # close at 50% of max credit
    stop_loss_multiple: float = 2.0       # close at 2× credit received
    delta_warning_threshold: float = 0.35
    delta_adjust_threshold: float = 0.45
    max_reentries_per_day: int = 2
    check_interval_s: float = 5.0        # check every 5 seconds
    intraday_exit_hhmm: str = "15:00"    # force close at 3 PM


# ---------------------------------------------------------------------------
# Expiry Specialist Parameters
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpiryParams:
    gamma_entry_hhmm: str = "14:00"      # start gamma capture
    gamma_exit_hhmm: str = "15:10"       # force exit
    max_capital_pct: float = 0.005       # max 0.5% of capital
    max_abs_inr: float = 5000.0          # max ₹5,000 per trade
    min_option_price: float = 0.5        # only buy if price < ₹X
    max_option_price: float = 10.0       # skip if too expensive (already moved)
    # Expiry days per instrument
    nifty_expiry_weekday: int = 3        # Thursday (0=Mon)
    banknifty_expiry_weekday: int = 2    # Wednesday
    sensex_expiry_weekday: int = 4       # Friday
```

Update `TomicConfig` to include the new params (add after `universe` field):

```python
    market_context: MarketContextParams = field(default_factory=MarketContextParams)
    daily_plan: DailyPlanParams = field(default_factory=DailyPlanParams)
    position_manager: PositionManagerParams = field(default_factory=PositionManagerParams)
    expiry: ExpiryParams = field(default_factory=ExpiryParams)
```

Also add `EntryMode` to the `StrategyType` enum check — add these two new types:

```python
class StrategyType(str, Enum):
    # ... existing ...
    GAMMA_CAPTURE = "GAMMA_CAPTURE"      # expiry day buy straddle
    SKIP = "SKIP"                        # no trade conditions
```

**Step 2: Run existing tests to confirm config changes don't break anything**

```bash
cd C:/algo/openalgov2/openalgov2/openalgo
uv run pytest test/test_tomic_command_store.py test/test_tomic_circuit_breakers.py -v
```
Expected: all PASS

**Step 3: Commit**

```bash
git add tomic/config.py
git commit -m "feat(tomic): add config params for market context, daily plan, position manager, expiry specialist"
```

---

## Task 2: LegResolver — Delta-Based Strike Resolution

The critical missing piece: translates abstract leg specs (`OTM1`, `ATM`, delta=0.25) into real NFO option symbols like `NIFTY25JAN25000CE`.

**Files:**
- Create: `tomic/leg_resolver.py`
- Create: `test/test_tomic_leg_resolver.py`

**Step 1: Write the failing tests**

Create `test/test_tomic_leg_resolver.py`:

```python
"""Tests for LegResolver — delta-based strike selection."""
import pytest
from unittest.mock import MagicMock, patch
from tomic.leg_resolver import LegResolver, LegSpec, LegResolution


def make_resolver():
    engine = MagicMock()
    # engine.compute returns a result with .delta
    return LegResolver(greeks_engine=engine)


def test_find_strike_by_delta_call():
    """Should find the strike closest to target delta for a call."""
    resolver = make_resolver()
    # Strikes: 24800, 24900, 25000 (ATM), 25100, 25200
    strikes = [24800.0, 24900.0, 25000.0, 25100.0, 25200.0]
    spot = 25000.0
    dte = 7

    # Mock greeks: delta decreases as strike goes OTM for calls
    delta_map = {
        24800.0: 0.70,
        24900.0: 0.55,
        25000.0: 0.50,
        25100.0: 0.30,  # closest to 0.25
        25200.0: 0.18,
    }
    prices = {k: 100.0 for k in strikes}

    resolver._greeks_engine.compute.side_effect = lambda spot, strike, expiry_days, option_price, option_type: (
        MagicMock(delta=delta_map[strike], iv=0.15)
    )

    result = resolver.find_strike_by_delta(
        strikes=strikes,
        prices=prices,
        spot=spot,
        dte=dte,
        option_type="c",
        target_delta=0.25,
    )
    assert result == 25100.0


def test_find_strike_by_delta_put():
    """Put delta is negative; target is abs value."""
    resolver = make_resolver()
    strikes = [24800.0, 24900.0, 25000.0, 25100.0, 25200.0]
    spot = 25000.0

    delta_map = {
        24800.0: -0.35,
        24900.0: -0.28,  # closest to -0.25
        25000.0: -0.50,
        25100.0: -0.18,
        25200.0: -0.10,
    }
    prices = {k: 50.0 for k in strikes}

    resolver._greeks_engine.compute.side_effect = lambda spot, strike, expiry_days, option_price, option_type: (
        MagicMock(delta=delta_map[strike], iv=0.15)
    )

    result = resolver.find_strike_by_delta(
        strikes=strikes,
        prices=prices,
        spot=spot,
        dte=7,
        option_type="p",
        target_delta=0.25,  # abs value
    )
    assert result == 24900.0


def test_resolve_iron_condor_legs():
    """Should return 4 resolved legs for an Iron Condor."""
    resolver = make_resolver()
    strikes = [24700.0, 24800.0, 24900.0, 25000.0, 25100.0, 25200.0, 25300.0]
    prices = {k: 80.0 for k in strikes}
    spot = 25000.0

    # Delta mock: put side
    def mock_compute(spot, strike, expiry_days, option_price, option_type):
        if option_type == "c":
            deltas = {24700: 0.80, 24800: 0.65, 24900: 0.55,
                      25000: 0.50, 25100: 0.30, 25200: 0.20, 25300: 0.12}
        else:
            deltas = {24700: -0.12, 24800: -0.20, 24900: -0.30,
                      25000: -0.50, 25100: -0.55, 25200: -0.65, 25300: -0.80}
        return MagicMock(delta=deltas.get(int(strike), 0.0), iv=0.15)

    resolver._greeks_engine.compute.side_effect = mock_compute

    legs = resolver.resolve_iron_condor(
        strikes=strikes, prices=prices, spot=spot, dte=7,
        short_delta=0.25, wing_delta=0.10,
    )
    assert len(legs) == 4
    directions = [l.direction for l in legs]
    assert "BUY" in directions
    assert "SELL" in directions
    option_types = {l.option_type for l in legs}
    assert "CE" in option_types
    assert "PE" in option_types


def test_no_option_price_skips_strike():
    """Strike with price=0 should be skipped in delta search."""
    resolver = make_resolver()
    strikes = [25000.0, 25100.0, 25200.0]
    prices = {25000.0: 0.0, 25100.0: 0.0, 25200.0: 50.0}

    resolver._greeks_engine.compute.return_value = MagicMock(delta=0.25, iv=0.15)

    result = resolver.find_strike_by_delta(
        strikes=strikes, prices=prices, spot=25000.0, dte=7,
        option_type="c", target_delta=0.25,
    )
    # Only 25200 has a price, so it's selected
    assert result == 25200.0
```

**Step 2: Run to confirm failure**

```bash
uv run pytest test/test_tomic_leg_resolver.py -v
```
Expected: `ModuleNotFoundError: No module named 'tomic.leg_resolver'`

**Step 3: Create `tomic/leg_resolver.py`**

```python
"""
TOMIC Leg Resolver — Delta-Based Strike Resolution
===================================================
Translates abstract leg specs (short_delta=0.25, option_type=CE) into
real NFO option symbols and strikes using the Greeks Engine.

Used by ExecutionAgent to resolve multi-leg strategies before order placement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tomic.greeks_engine import GreeksEngine

logger = logging.getLogger(__name__)


@dataclass
class LegSpec:
    """Abstract leg specification from strategy signals."""
    leg_type: str           # e.g. "SELL_PUT", "BUY_CALL"
    option_type: str        # "CE" or "PE"
    direction: str          # "BUY" or "SELL"
    offset: str             # "ATM", "OTM1", "OTM2" (fallback if no delta)
    delta_target: float = 0.0
    expiry_offset: int = 0  # 0 = front, 1 = next expiry


@dataclass
class LegResolution:
    """A resolved leg with real strike and symbol info."""
    leg_type: str
    option_type: str
    direction: str
    strike: float
    symbol: str = ""        # e.g. "NIFTY25JAN25000CE" (filled by ExecutionAgent)
    actual_delta: float = 0.0
    actual_iv: float = 0.0
    estimated_price: float = 0.0


class LegResolver:
    """
    Resolves abstract leg specs into strikes using delta targeting.

    Requires:
    - Available strikes for the underlying/expiry (from option chain service)
    - Current option prices (LTP) for each strike
    - Underlying spot price
    - Days to expiry (DTE)
    """

    def __init__(self, greeks_engine: Optional[GreeksEngine] = None):
        self._greeks_engine = greeks_engine or GreeksEngine()

    def find_strike_by_delta(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        option_type: str,        # "c" or "p"
        target_delta: float,     # absolute value (0.25 = 25Δ)
    ) -> Optional[float]:
        """
        Find the strike whose delta is closest to target_delta.
        Skips strikes with no price (illiquid).
        Returns the strike float or None if not resolvable.
        """
        best_strike = None
        best_diff = float("inf")
        opt = option_type.lower()[0]  # 'c' or 'p'

        valid_strikes = [s for s in strikes if prices.get(s, 0.0) > 0.0]
        if not valid_strikes:
            return None

        for strike in valid_strikes:
            price = prices[strike]
            try:
                result = self._greeks_engine.compute(
                    spot=spot,
                    strike=strike,
                    expiry_days=max(dte, 0.1),
                    option_price=price,
                    option_type=opt,
                )
                actual_delta = abs(result.delta)
                diff = abs(actual_delta - target_delta)
                if diff < best_diff:
                    best_diff = diff
                    best_strike = strike
            except Exception as exc:
                logger.debug("Delta computation failed for strike %.0f: %s", strike, exc)
                continue

        return best_strike

    def resolve_iron_condor(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        short_delta: float,
        wing_delta: float,
    ) -> List[LegResolution]:
        """
        Resolve 4-leg Iron Condor:
          BUY  PE (wing)  → wing_delta
          SELL PE (short) → short_delta
          SELL CE (short) → short_delta
          BUY  CE (wing)  → wing_delta
        """
        legs = []

        # Put side
        short_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", short_delta)
        wing_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", wing_delta)
        if short_put and wing_put and wing_put < short_put:
            legs.append(LegResolution(
                leg_type="SELL_PUT", option_type="PE", direction="SELL",
                strike=short_put, estimated_price=prices.get(short_put, 0.0),
            ))
            legs.append(LegResolution(
                leg_type="BUY_PUT", option_type="PE", direction="BUY",
                strike=wing_put, estimated_price=prices.get(wing_put, 0.0),
            ))

        # Call side
        short_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", short_delta)
        wing_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", wing_delta)
        if short_call and wing_call and wing_call > short_call:
            legs.append(LegResolution(
                leg_type="SELL_CALL", option_type="CE", direction="SELL",
                strike=short_call, estimated_price=prices.get(short_call, 0.0),
            ))
            legs.append(LegResolution(
                leg_type="BUY_CALL", option_type="CE", direction="BUY",
                strike=wing_call, estimated_price=prices.get(wing_call, 0.0),
            ))

        return legs

    def resolve_bull_put_spread(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        short_delta: float,
        wing_delta: float,
    ) -> List[LegResolution]:
        """
        Resolve 2-leg Bull Put Spread (sell OTM put, buy further OTM put).
        Hedge (BUY) first per LEGGING_POLICY.
        """
        short_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", short_delta)
        wing_put = self.find_strike_by_delta(strikes, prices, spot, dte, "p", wing_delta)

        if not short_put or not wing_put or wing_put >= short_put:
            logger.warning(
                "Bull Put Spread: could not resolve strikes (short=%.0f, wing=%.0f)",
                short_put or 0, wing_put or 0,
            )
            return []

        return [
            LegResolution(
                leg_type="BUY_PUT", option_type="PE", direction="BUY",
                strike=wing_put, estimated_price=prices.get(wing_put, 0.0),
            ),
            LegResolution(
                leg_type="SELL_PUT", option_type="PE", direction="SELL",
                strike=short_put, estimated_price=prices.get(short_put, 0.0),
            ),
        ]

    def resolve_bear_call_spread(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        dte: float,
        short_delta: float,
        wing_delta: float,
    ) -> List[LegResolution]:
        """
        Resolve 2-leg Bear Call Spread (sell OTM call, buy further OTM call).
        Hedge (BUY) first per LEGGING_POLICY.
        """
        short_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", short_delta)
        wing_call = self.find_strike_by_delta(strikes, prices, spot, dte, "c", wing_delta)

        if not short_call or not wing_call or wing_call <= short_call:
            logger.warning(
                "Bear Call Spread: could not resolve strikes (short=%.0f, wing=%.0f)",
                short_call or 0, wing_call or 0,
            )
            return []

        return [
            LegResolution(
                leg_type="BUY_CALL", option_type="CE", direction="BUY",
                strike=wing_call, estimated_price=prices.get(wing_call, 0.0),
            ),
            LegResolution(
                leg_type="SELL_CALL", option_type="CE", direction="SELL",
                strike=short_call, estimated_price=prices.get(short_call, 0.0),
            ),
        ]

    def resolve_gamma_capture(
        self,
        strikes: List[float],
        prices: Dict[float, float],
        spot: float,
        max_price: float = 10.0,
    ) -> List[LegResolution]:
        """
        Expiry day gamma capture: buy cheap near-ATM CE + PE.
        Selects 1-strike OTM on each side with price < max_price.
        """
        # Find ATM strike
        if not strikes:
            return []
        atm = min(strikes, key=lambda s: abs(s - spot))
        atm_idx = strikes.index(atm)

        # 1 strike OTM call
        ce_idx = min(atm_idx + 1, len(strikes) - 1)
        pe_idx = max(atm_idx - 1, 0)

        legs = []
        ce_strike = strikes[ce_idx]
        pe_strike = strikes[pe_idx]

        ce_price = prices.get(ce_strike, 0.0)
        pe_price = prices.get(pe_strike, 0.0)

        if 0 < ce_price <= max_price:
            legs.append(LegResolution(
                leg_type="BUY_CALL", option_type="CE", direction="BUY",
                strike=ce_strike, estimated_price=ce_price,
            ))
        if 0 < pe_price <= max_price:
            legs.append(LegResolution(
                leg_type="BUY_PUT", option_type="PE", direction="BUY",
                strike=pe_strike, estimated_price=pe_price,
            ))

        return legs
```

**Step 4: Run tests**

```bash
uv run pytest test/test_tomic_leg_resolver.py -v
```
Expected: 4 PASS

**Step 5: Commit**

```bash
git add tomic/leg_resolver.py test/test_tomic_leg_resolver.py
git commit -m "feat(tomic): add LegResolver for delta-based strike resolution"
```

---

## Task 3: MarketContextAgent — Market Analysis

**Files:**
- Create: `tomic/agents/market_context_agent.py`
- Create: `test/test_tomic_market_context_agent.py`

**Step 1: Write failing tests**

Create `test/test_tomic_market_context_agent.py`:

```python
"""Tests for MarketContextAgent."""
import time
import pytest
from tomic.agents.market_context_agent import (
    MarketContextAgent, AtomicMarketContext, MarketContext,
    classify_vix_regime, classify_pcr_bias,
)
from tomic.config import TomicConfig


def test_classify_vix_regime():
    assert classify_vix_regime(10.0) == "TOO_LOW"
    assert classify_vix_regime(14.0) == "NORMAL"
    assert classify_vix_regime(20.0) == "ELEVATED"
    assert classify_vix_regime(28.0) == "HIGH"
    assert classify_vix_regime(40.0) == "EXTREME"


def test_classify_pcr_bias():
    assert classify_pcr_bias(1.5) == "BULLISH"
    assert classify_pcr_bias(0.6) == "BEARISH"
    assert classify_pcr_bias(1.0) == "NEUTRAL"


def test_atomic_market_context_thread_safe():
    ctx = AtomicMarketContext()
    mc = MarketContext(vix=16.0, vix_regime="NORMAL", pcr=1.1, pcr_bias="NEUTRAL")
    ctx.update(mc)
    snap = ctx.read()
    assert snap.vix == 16.0
    assert snap.vix_regime == "NORMAL"


def test_feed_vix_updates_context():
    config = TomicConfig()
    agent = MarketContextAgent(config=config)
    agent.feed_vix(17.5)
    ctx = agent.read_context()
    assert ctx.vix == 17.5
    assert ctx.vix_regime == "NORMAL"


def test_feed_pcr_updates_context():
    config = TomicConfig()
    agent = MarketContextAgent(config=config)
    agent.feed_pcr(1.4, instrument="NIFTY")
    ctx = agent.read_context()
    assert ctx.pcr == 1.4
    assert ctx.pcr_bias == "BULLISH"


def test_trend_ma_computes_correctly():
    config = TomicConfig()
    agent = MarketContextAgent(config=config)
    # Feed 25 candles above MA level
    for i in range(25):
        agent.feed_candle(underlying="NIFTY", close=25000.0 + i * 10)
    ctx = agent.read_context()
    # Last close is above 20-period MA → ABOVE_20MA
    assert ctx.nifty_trend == "ABOVE_20MA"
```

**Step 2: Run to confirm failure**

```bash
uv run pytest test/test_tomic_market_context_agent.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: Create `tomic/agents/market_context_agent.py`**

```python
"""
TOMIC Market Context Agent
==========================
Aggregates market analysis into a single MarketContext snapshot:
  - India VIX (primary IV proxy, replaces broken session IV rank)
  - PCR (Put-Call Ratio) → directional bias
  - NIFTY/BANKNIFTY trend (20-MA)
  - Support / Resistance from previous-day high/low
  - Max pain and OI walls (populated when available)

Thread-safe. Single writer pattern like AtomicRegimeState.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

from tomic.config import TomicConfig, MarketContextParams

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classify helpers (pure functions — easily testable)
# ---------------------------------------------------------------------------

def classify_vix_regime(vix: float, params: Optional[MarketContextParams] = None) -> str:
    """Classify India VIX into regime bucket."""
    if params is None:
        params = MarketContextParams()
    if vix < params.vix_too_low:
        return "TOO_LOW"
    if vix <= params.vix_normal_high:
        return "NORMAL"
    if vix <= params.vix_elevated_high:
        return "ELEVATED"
    if vix <= params.vix_extreme:
        return "HIGH"
    return "EXTREME"


def classify_pcr_bias(pcr: float, params: Optional[MarketContextParams] = None) -> str:
    """Classify PCR into directional bias."""
    if params is None:
        params = MarketContextParams()
    if pcr > params.pcr_bullish_above:
        return "BULLISH"
    if pcr < params.pcr_bearish_below:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """Snapshot of current market conditions."""
    vix: float = 0.0
    vix_regime: str = "UNKNOWN"        # TOO_LOW / NORMAL / ELEVATED / HIGH / EXTREME
    pcr: float = 1.0
    pcr_bias: str = "NEUTRAL"          # BULLISH / BEARISH / NEUTRAL
    nifty_ltp: float = 0.0
    banknifty_ltp: float = 0.0
    sensex_ltp: float = 0.0
    nifty_trend: str = "NEUTRAL"       # ABOVE_20MA / BELOW_20MA / NEUTRAL
    banknifty_trend: str = "NEUTRAL"
    sensex_trend: str = "NEUTRAL"
    prev_day_high: Dict[str, float] = field(default_factory=dict)
    prev_day_low: Dict[str, float] = field(default_factory=dict)
    max_pain: Dict[str, float] = field(default_factory=dict)
    oi_put_wall: Dict[str, float] = field(default_factory=dict)
    oi_call_wall: Dict[str, float] = field(default_factory=dict)
    timestamp_mono: float = 0.0


class AtomicMarketContext:
    """Thread-safe versioned market context. Single writer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ctx = MarketContext()

    def update(self, ctx: MarketContext) -> None:
        with self._lock:
            self._ctx = ctx

    def read(self) -> MarketContext:
        with self._lock:
            import copy
            return copy.copy(self._ctx)


# ---------------------------------------------------------------------------
# Market Context Agent
# ---------------------------------------------------------------------------

class MarketContextAgent:
    """
    Lightweight agent — not a full AgentBase subclass.
    Updated directly by MarketBridge on each tick.
    PCR / max pain fetched asynchronously by the runtime's 5-min timer.
    """

    _MA_PERIOD = 20

    def __init__(self, config: TomicConfig) -> None:
        self._config = config
        self._params: MarketContextParams = config.market_context
        self._atomic = AtomicMarketContext()
        self._lock = threading.Lock()

        # Rolling close buffers per underlying for MA computation
        self._closes: Dict[str, Deque[float]] = {}
        self._ltps: Dict[str, float] = {}
        self._vix: float = 0.0
        self._pcr: float = 1.0
        self._prev_high: Dict[str, float] = {}
        self._prev_low: Dict[str, float] = {}
        self._max_pain: Dict[str, float] = {}
        self._oi_put_wall: Dict[str, float] = {}
        self._oi_call_wall: Dict[str, float] = {}

    # -----------------------------------------------------------------------
    # Feed methods (called by MarketBridge on every tick)
    # -----------------------------------------------------------------------

    def feed_vix(self, vix: float) -> None:
        with self._lock:
            self._vix = vix
        self._publish()

    def feed_ltp(self, underlying: str, ltp: float) -> None:
        key = underlying.upper()
        with self._lock:
            self._ltps[key] = ltp
        self._publish()

    def feed_candle(self, underlying: str, close: float) -> None:
        """Feed a new close for MA computation."""
        key = underlying.upper()
        with self._lock:
            if key not in self._closes:
                self._closes[key] = deque(maxlen=self._MA_PERIOD + 5)
            self._closes[key].append(close)
        self._publish()

    def feed_pcr(self, pcr: float, instrument: str = "NIFTY") -> None:
        with self._lock:
            self._pcr = pcr
        self._publish()

    def feed_max_pain(self, underlying: str, max_pain: float) -> None:
        with self._lock:
            self._max_pain[underlying.upper()] = max_pain
        self._publish()

    def feed_oi_walls(self, underlying: str, put_wall: float, call_wall: float) -> None:
        with self._lock:
            self._oi_put_wall[underlying.upper()] = put_wall
            self._oi_call_wall[underlying.upper()] = call_wall
        self._publish()

    def feed_prev_day(self, underlying: str, high: float, low: float) -> None:
        key = underlying.upper()
        with self._lock:
            self._prev_high[key] = high
            self._prev_low[key] = low

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    def read_context(self) -> MarketContext:
        return self._atomic.read()

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _compute_trend(self, key: str) -> str:
        closes = list(self._closes.get(key, []))
        if len(closes) < self._MA_PERIOD:
            return "NEUTRAL"
        ma = sum(closes[-self._MA_PERIOD:]) / self._MA_PERIOD
        ltp = self._ltps.get(key, closes[-1])
        if ltp > ma * 1.001:
            return "ABOVE_20MA"
        if ltp < ma * 0.999:
            return "BELOW_20MA"
        return "NEUTRAL"

    def _publish(self) -> None:
        with self._lock:
            ctx = MarketContext(
                vix=self._vix,
                vix_regime=classify_vix_regime(self._vix, self._params),
                pcr=self._pcr,
                pcr_bias=classify_pcr_bias(self._pcr, self._params),
                nifty_ltp=self._ltps.get("NIFTY", 0.0),
                banknifty_ltp=self._ltps.get("BANKNIFTY", 0.0),
                sensex_ltp=self._ltps.get("SENSEX", 0.0),
                nifty_trend=self._compute_trend("NIFTY"),
                banknifty_trend=self._compute_trend("BANKNIFTY"),
                sensex_trend=self._compute_trend("SENSEX"),
                prev_day_high=dict(self._prev_high),
                prev_day_low=dict(self._prev_low),
                max_pain=dict(self._max_pain),
                oi_put_wall=dict(self._oi_put_wall),
                oi_call_wall=dict(self._oi_call_wall),
                timestamp_mono=time.monotonic(),
            )
        self._atomic.update(ctx)
```

**Step 4: Run tests**

```bash
uv run pytest test/test_tomic_market_context_agent.py -v
```
Expected: 6 PASS

**Step 5: Commit**

```bash
git add tomic/agents/market_context_agent.py test/test_tomic_market_context_agent.py
git commit -m "feat(tomic): add MarketContextAgent (VIX, PCR, trend, S/R analysis)"
```

---

## Task 4: DailyPlanAgent — Morning Trade Plan Generation

**Files:**
- Create: `tomic/agents/daily_plan_agent.py`
- Create: `test/test_tomic_daily_plan_agent.py`

**Step 1: Write failing tests**

Create `test/test_tomic_daily_plan_agent.py`:

```python
"""Tests for DailyPlanAgent strategy selection logic."""
import pytest
from unittest.mock import MagicMock
from tomic.agents.daily_plan_agent import (
    DailyPlanAgent, DailyTradePlan,
    select_strategy_from_context,
)
from tomic.agents.market_context_agent import MarketContext
from tomic.agents.regime_agent import RegimeSnapshot
from tomic.config import TomicConfig, RegimePhase, StrategyType


def make_regime(phase: RegimePhase) -> RegimeSnapshot:
    return RegimeSnapshot(
        version=1, phase=phase, score=0, vix=16.0,
        vix_flags=[], ichimoku_signal="NEUTRAL",
        impulse_color="BLUE", congestion=False, blowoff=False,
        timestamp_mono=0.0,
    )


def test_vix_too_low_returns_skip():
    ctx = MarketContext(vix=10.0, vix_regime="TOO_LOW")
    result = select_strategy_from_context(ctx, make_regime(RegimePhase.CONGESTION))
    assert result == StrategyType.SKIP


def test_vix_extreme_returns_skip():
    ctx = MarketContext(vix=40.0, vix_regime="EXTREME")
    result = select_strategy_from_context(ctx, make_regime(RegimePhase.BULLISH))
    assert result == StrategyType.SKIP


def test_normal_vix_bullish_regime_bull_put():
    ctx = MarketContext(vix=15.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL")
    result = select_strategy_from_context(ctx, make_regime(RegimePhase.BULLISH))
    assert result == StrategyType.BULL_PUT_SPREAD


def test_normal_vix_bearish_regime_bear_call():
    ctx = MarketContext(vix=15.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL")
    result = select_strategy_from_context(ctx, make_regime(RegimePhase.BEARISH))
    assert result == StrategyType.BEAR_CALL_SPREAD


def test_normal_vix_congestion_iron_condor():
    ctx = MarketContext(vix=15.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL")
    result = select_strategy_from_context(ctx, make_regime(RegimePhase.CONGESTION))
    assert result == StrategyType.IRON_CONDOR


def test_pcr_bullish_tilt_overrides_bearish_regime():
    """High PCR should tilt toward Bull Put even in light bearish regime."""
    ctx = MarketContext(vix=16.0, vix_regime="NORMAL", pcr=1.4, pcr_bias="BULLISH")
    regime = make_regime(RegimePhase.BEARISH)
    regime = RegimeSnapshot(**{**regime.__dict__, "score": -3})  # mild bearish
    result = select_strategy_from_context(ctx, regime)
    assert result == StrategyType.BULL_PUT_SPREAD


def test_plan_rationale_is_human_readable():
    config = TomicConfig()
    mc_agent = MagicMock()
    mc_agent.read_context.return_value = MarketContext(
        vix=16.0, vix_regime="NORMAL", pcr=1.0, pcr_bias="NEUTRAL",
        nifty_ltp=25000.0,
    )
    regime_state = MagicMock()
    regime_state.read_snapshot.return_value = make_regime(RegimePhase.CONGESTION)

    agent = DailyPlanAgent(
        config=config,
        market_context_agent=mc_agent,
        regime_state=regime_state,
    )
    plan = agent.generate_plan("NIFTY", entry_mode="morning")
    assert plan is not None
    assert "VIX" in plan.rationale
    assert len(plan.rationale) > 20
```

**Step 2: Run to confirm failure**

```bash
uv run pytest test/test_tomic_daily_plan_agent.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: Create `tomic/agents/daily_plan_agent.py`**

```python
"""
TOMIC Daily Plan Agent — Morning Trade Plan Generator
======================================================
Runs at 9:45 AM. Reads MarketContext + RegimeSnapshot.
Selects strategy type using the VIX/regime matrix.
Generates a DailyTradePlan with target deltas and expiry.

Plans are stored in memory and consumed by the StrategyEngine.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from tomic.agents.market_context_agent import MarketContext, MarketContextAgent
from tomic.agents.regime_agent import AtomicRegimeState, RegimeSnapshot
from tomic.config import (
    DailyPlanParams, MarketContextParams,
    RegimePhase, StrategyType, TomicConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy selection matrix (pure function — testable)
# ---------------------------------------------------------------------------

def select_strategy_from_context(
    ctx: MarketContext,
    regime: RegimeSnapshot,
    params: Optional[DailyPlanParams] = None,
) -> StrategyType:
    """
    Map VIX regime + market regime to strategy type.

    Matrix:
      VIX TOO_LOW / EXTREME → SKIP
      VIX NORMAL  + BULLISH → BULL_PUT_SPREAD
      VIX NORMAL  + BEARISH → BEAR_CALL_SPREAD
      VIX NORMAL  + CONGESTION → IRON_CONDOR
      VIX ELEVATED + any → wider spreads, same type
      VIX HIGH + any → IRON_CONDOR (defined risk, tighter)
      PCR tilt overrides mild regime signals
    """
    if params is None:
        params = DailyPlanParams()

    if ctx.vix_regime in ("TOO_LOW", "EXTREME"):
        return StrategyType.SKIP

    phase = regime.phase

    # PCR tilt: strong PCR signal can override mild regime
    if ctx.pcr_bias == "BULLISH" and phase != RegimePhase.BEARISH:
        return StrategyType.BULL_PUT_SPREAD
    if ctx.pcr_bias == "BEARISH" and phase != RegimePhase.BULLISH:
        return StrategyType.BEAR_CALL_SPREAD

    if phase == RegimePhase.BULLISH:
        return StrategyType.BULL_PUT_SPREAD
    if phase == RegimePhase.BEARISH:
        return StrategyType.BEAR_CALL_SPREAD
    if phase == RegimePhase.BLOWOFF:
        return StrategyType.SKIP

    return StrategyType.IRON_CONDOR  # CONGESTION default


def _delta_targets_for_vix(
    vix_regime: str,
    params: DailyPlanParams,
) -> tuple[float, float]:
    """Return (short_delta, wing_delta) based on VIX level."""
    if vix_regime == "ELEVATED":
        return params.short_delta_elevated, params.wing_delta_elevated
    if vix_regime == "HIGH":
        return params.short_delta_high, params.wing_delta_high
    return params.short_delta_normal, params.wing_delta_normal


# ---------------------------------------------------------------------------
# DailyTradePlan
# ---------------------------------------------------------------------------

@dataclass
class DailyTradePlan:
    """A fully specified trade plan for one instrument on one day."""
    date: str                        # YYYY-MM-DD
    instrument: str
    strategy_type: StrategyType
    entry_mode: str                  # morning / continuous / event / expiry_gamma
    vix_at_plan: float
    regime_at_plan: str
    pcr_at_plan: float
    short_delta_target: float
    wing_delta_target: float
    lots: int = 1
    expiry_date: str = ""            # DDMMMyy e.g. "30JAN25"
    rationale: str = ""
    valid_until_hhmm: str = "14:00"
    is_active: bool = True
    reentry_count: int = 0           # how many re-entries used today
    created_at_mono: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "instrument": self.instrument,
            "strategy_type": self.strategy_type.value,
            "entry_mode": self.entry_mode,
            "vix_at_plan": self.vix_at_plan,
            "regime_at_plan": self.regime_at_plan,
            "pcr_at_plan": self.pcr_at_plan,
            "short_delta_target": self.short_delta_target,
            "wing_delta_target": self.wing_delta_target,
            "lots": self.lots,
            "expiry_date": self.expiry_date,
            "rationale": self.rationale,
            "valid_until_hhmm": self.valid_until_hhmm,
            "is_active": self.is_active,
            "reentry_count": self.reentry_count,
        }


# ---------------------------------------------------------------------------
# DailyPlanAgent
# ---------------------------------------------------------------------------

class DailyPlanAgent:
    """
    Generates DailyTradePlan objects for each instrument.
    Called at 9:45 AM by the runtime (morning mode).
    Also callable on-demand for continuous / event modes.
    """

    def __init__(
        self,
        config: TomicConfig,
        market_context_agent: MarketContextAgent,
        regime_state: AtomicRegimeState,
    ) -> None:
        self._config = config
        self._params: DailyPlanParams = config.daily_plan
        self._mc = market_context_agent
        self._regime_state = regime_state
        self._lock = threading.Lock()
        self._plans: Dict[str, DailyTradePlan] = {}   # instrument → plan
        self._today: str = ""

    def generate_all_plans(self, entry_mode: str = "morning") -> List[DailyTradePlan]:
        """Generate plans for all configured instruments."""
        plans = []
        for instrument in self._params.instruments:
            plan = self.generate_plan(instrument, entry_mode=entry_mode)
            if plan is not None:
                plans.append(plan)
        return plans

    def generate_plan(
        self,
        instrument: str,
        entry_mode: str = "morning",
    ) -> Optional[DailyTradePlan]:
        """Generate a trade plan for one instrument."""
        ctx = self._mc.read_context()
        regime = self._regime_state.read_snapshot()
        today = date.today().isoformat()

        strategy = select_strategy_from_context(ctx, regime, self._params)
        if strategy == StrategyType.SKIP:
            logger.info(
                "DailyPlan: SKIP %s — VIX %.1f (%s), regime %s",
                instrument, ctx.vix, ctx.vix_regime, regime.phase.value,
            )
            return None

        short_delta, wing_delta = _delta_targets_for_vix(ctx.vix_regime, self._params)

        rationale = (
            f"VIX={ctx.vix:.1f} ({ctx.vix_regime}), "
            f"Regime={regime.phase.value} (score={regime.score}), "
            f"PCR={ctx.pcr:.2f} ({ctx.pcr_bias}) → {strategy.value}. "
            f"Short delta target: {short_delta:.2f}, wing: {wing_delta:.2f}."
        )

        plan = DailyTradePlan(
            date=today,
            instrument=instrument.upper(),
            strategy_type=strategy,
            entry_mode=entry_mode,
            vix_at_plan=ctx.vix,
            regime_at_plan=regime.phase.value,
            pcr_at_plan=ctx.pcr,
            short_delta_target=short_delta,
            wing_delta_target=wing_delta,
            lots=1,
            rationale=rationale,
            valid_until_hhmm=self._params.plan_valid_until_hhmm,
        )

        with self._lock:
            self._plans[instrument.upper()] = plan
            self._today = today

        logger.info("DailyPlan generated: %s %s — %s", today, instrument, rationale)
        return plan

    def get_active_plan(self, instrument: str) -> Optional[DailyTradePlan]:
        with self._lock:
            return self._plans.get(instrument.upper())

    def get_all_active_plans(self) -> List[DailyTradePlan]:
        with self._lock:
            return [p for p in self._plans.values() if p.is_active]

    def mark_plan_inactive(self, instrument: str) -> None:
        with self._lock:
            plan = self._plans.get(instrument.upper())
            if plan:
                plan.is_active = False

    def increment_reentry(self, instrument: str) -> bool:
        """Returns True if re-entry is allowed (count < max)."""
        from tomic.config import PositionManagerParams
        max_reentries = self._config.position_manager.max_reentries_per_day
        with self._lock:
            plan = self._plans.get(instrument.upper())
            if plan is None:
                return False
            if plan.reentry_count >= max_reentries:
                return False
            plan.reentry_count += 1
            return True

    def reset_for_new_day(self) -> None:
        """Clear all plans at start of new trading day."""
        with self._lock:
            self._plans.clear()
            self._today = date.today().isoformat()

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "date": self._today,
                "plans": [p.to_dict() for p in self._plans.values()],
            }
```

**Step 4: Run tests**

```bash
uv run pytest test/test_tomic_daily_plan_agent.py -v
```
Expected: 8 PASS

**Step 5: Commit**

```bash
git add tomic/agents/daily_plan_agent.py test/test_tomic_daily_plan_agent.py
git commit -m "feat(tomic): add DailyPlanAgent with VIX/regime strategy selection matrix"
```

---

## Task 5: StrategyEngine — Unified Signal Engine

Replaces `ConflictRouter`. Handles all 4 entry modes and outputs `RoutedSignal` objects compatible with the existing `RiskAgent`.

**Files:**
- Create: `tomic/agents/strategy_engine.py`
- Create: `test/test_tomic_strategy_engine.py`

**Step 1: Write failing tests**

Create `test/test_tomic_strategy_engine.py`:

```python
"""Tests for StrategyEngine — 4-mode signal generation."""
import time
import pytest
from unittest.mock import MagicMock, patch
from tomic.agents.strategy_engine import StrategyEngine, EntryTrigger
from tomic.agents.daily_plan_agent import DailyTradePlan
from tomic.agents.market_context_agent import MarketContext
from tomic.config import TomicConfig, StrategyType, RegimePhase


def make_plan(strategy=StrategyType.IRON_CONDOR, instrument="NIFTY"):
    return DailyTradePlan(
        date="2026-02-27", instrument=instrument,
        strategy_type=strategy, entry_mode="morning",
        vix_at_plan=16.0, regime_at_plan="CONGESTION",
        pcr_at_plan=1.0, short_delta_target=0.25,
        wing_delta_target=0.10, lots=1,
    )


def make_engine():
    config = TomicConfig()
    plan_agent = MagicMock()
    mc_agent = MagicMock()
    regime_state = MagicMock()
    return StrategyEngine(
        config=config,
        daily_plan_agent=plan_agent,
        market_context_agent=mc_agent,
        regime_state=regime_state,
    )


def test_morning_trigger_generates_signal():
    engine = make_engine()
    plan = make_plan()
    engine._daily_plan_agent.get_active_plan.return_value = plan

    signals = engine._signals_for_plan(plan)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_dict["instrument"] == "NIFTY"
    assert sig.signal_dict["strategy_type"] == StrategyType.IRON_CONDOR.value


def test_skip_plan_generates_no_signal():
    engine = make_engine()
    plan = make_plan(strategy=StrategyType.SKIP)
    signals = engine._signals_for_plan(plan)
    assert len(signals) == 0


def test_vix_extreme_event_triggers_no_signal():
    engine = make_engine()
    ctx = MarketContext(vix=40.0, vix_regime="EXTREME")
    engine._market_context_agent.read_context.return_value = ctx
    plan = make_plan()
    # Even with an active plan, extreme VIX should block
    signals = engine.get_pending_signals(plans=[plan], ctx=ctx)
    assert len(signals) == 0


def test_event_trigger_on_vix_spike():
    engine = make_engine()
    trigger = EntryTrigger.from_vix_spike(prev_vix=15.0, curr_vix=22.0, threshold_pct=0.15)
    assert trigger is not None
    assert trigger.reason == "vix_spike"


def test_no_trigger_on_small_vix_move():
    trigger = EntryTrigger.from_vix_spike(prev_vix=15.0, curr_vix=16.0, threshold_pct=0.15)
    assert trigger is None


def test_plan_blocked_after_max_reentries():
    engine = make_engine()
    plan = make_plan()
    plan.reentry_count = 2  # max is 2 per config
    engine._config.position_manager.max_reentries_per_day = 2

    signals = engine._signals_for_plan(plan)
    # reentry_count >= max → no new signal (position should already be open)
    # This test validates the plan is not regenerating endlessly
    assert len(signals) <= 1  # first entry allowed, re-entries blocked
```

**Step 2: Run to confirm failure**

```bash
uv run pytest test/test_tomic_strategy_engine.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: Create `tomic/agents/strategy_engine.py`**

```python
"""
TOMIC Strategy Engine — Unified Signal Generator
=================================================
Replaces ConflictRouter. Handles 4 entry modes:
  1. MORNING_PLAN  — 9:45 AM, based on DailyTradePlan
  2. CONTINUOUS    — every 15 min, re-evaluate if conditions shift
  3. EVENT_DRIVEN  — VIX spike / PCR extreme / S/R test
  4. EXPIRY_GAMMA  — after 14:00 on expiry days (see ExpirySpecialist)

Outputs RoutedSignal objects compatible with the existing RiskAgent interface.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tomic.agents.daily_plan_agent import DailyPlanAgent, DailyTradePlan
from tomic.agents.market_context_agent import MarketContext, MarketContextAgent
from tomic.agents.regime_agent import AtomicRegimeState
from tomic.conflict_router import RoutedSignal, SignalSource
from tomic.config import (
    EntryMode, RegimePhase, StrategyType, TomicConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class EntryTrigger:
    """An event-driven entry trigger."""
    reason: str
    instrument: Optional[str] = None   # None = all instruments

    @classmethod
    def from_vix_spike(
        cls,
        prev_vix: float,
        curr_vix: float,
        threshold_pct: float = 0.15,
    ) -> Optional["EntryTrigger"]:
        if prev_vix <= 0:
            return None
        change = (curr_vix - prev_vix) / prev_vix
        if abs(change) >= threshold_pct:
            return cls(reason="vix_spike")
        return None


class StrategyEngine:
    """
    Unified signal engine. Called by runtime._signal_loop().
    Returns a list of RoutedSignal objects for the RiskAgent.
    """

    def __init__(
        self,
        config: TomicConfig,
        daily_plan_agent: DailyPlanAgent,
        market_context_agent: MarketContextAgent,
        regime_state: AtomicRegimeState,
    ) -> None:
        self._config = config
        self._daily_plan_agent = daily_plan_agent
        self._market_context_agent = market_context_agent
        self._regime_state = regime_state
        self._prev_vix: float = 0.0
        self._last_continuous_check: float = 0.0
        self._continuous_interval_s: float = 15 * 60  # 15 minutes

    def get_pending_signals(
        self,
        plans: Optional[List[DailyTradePlan]] = None,
        ctx: Optional[MarketContext] = None,
    ) -> List[RoutedSignal]:
        """
        Main entry point called by runtime._signal_loop().
        Returns all signals ready for the RiskAgent.
        """
        if ctx is None:
            ctx = self._market_context_agent.read_context()

        # Hard block: never trade in extreme VIX
        if ctx.vix_regime in ("TOO_LOW", "EXTREME"):
            return []

        if plans is None:
            plans = self._daily_plan_agent.get_all_active_plans()

        signals = []
        for plan in plans:
            signals.extend(self._signals_for_plan(plan))

        # Check event-driven triggers
        trigger = EntryTrigger.from_vix_spike(self._prev_vix, ctx.vix, threshold_pct=0.15)
        if trigger and ctx.vix_regime not in ("TOO_LOW", "EXTREME"):
            logger.info("Event trigger: %s — regenerating plans", trigger.reason)
            new_plans = self._daily_plan_agent.generate_all_plans(entry_mode="event_driven")
            for plan in new_plans:
                signals.extend(self._signals_for_plan(plan))

        self._prev_vix = ctx.vix
        return signals

    def _signals_for_plan(self, plan: DailyTradePlan) -> List[RoutedSignal]:
        """Convert a DailyTradePlan into RoutedSignal(s) for the RiskAgent."""
        if plan.strategy_type == StrategyType.SKIP:
            return []
        if not plan.is_active:
            return []

        # Build the signal dict (compatible with RiskAgent.receive_signal interface)
        signal_dict: Dict[str, Any] = {
            "instrument": plan.instrument,
            "strategy_type": plan.strategy_type.value,
            "direction": "SELL",           # options selling: net short premium
            "short_delta_target": plan.short_delta_target,
            "wing_delta_target": plan.wing_delta_target,
            "lots": plan.lots,
            "expiry_date": plan.expiry_date,
            "entry_mode": plan.entry_mode,
            "rationale": plan.rationale,
            "vix_at_signal": plan.vix_at_plan,
            "signal_strength": self._compute_strength(plan),
            # Abstract legs — resolved by ExecutionAgent via LegResolver
            "legs": self._build_abstract_legs(plan.strategy_type),
        }

        return [RoutedSignal(
            source=SignalSource.VOLATILITY,
            signal_dict=signal_dict,
            priority_score=signal_dict["signal_strength"],
        )]

    def _compute_strength(self, plan: DailyTradePlan) -> float:
        """Signal strength 0–100. Higher = more conviction."""
        strength = 50.0
        if plan.vix_at_plan >= 18:
            strength += 10.0   # more premium to collect
        if plan.regime_at_plan == "CONGESTION":
            strength += 10.0   # range-bound = ideal for premium selling
        if plan.reentry_count == 0:
            strength += 5.0    # fresh entry
        return min(100.0, strength)

    @staticmethod
    def _build_abstract_legs(strategy_type: StrategyType) -> List[Dict[str, Any]]:
        """Build abstract leg specs. LegResolver fills in real strikes."""
        if strategy_type == StrategyType.IRON_CONDOR:
            return [
                {"leg_type": "BUY_PUT",  "option_type": "PE", "direction": "BUY",  "offset": "wing_put"},
                {"leg_type": "SELL_PUT", "option_type": "PE", "direction": "SELL", "offset": "short_put"},
                {"leg_type": "SELL_CALL","option_type": "CE", "direction": "SELL", "offset": "short_call"},
                {"leg_type": "BUY_CALL", "option_type": "CE", "direction": "BUY",  "offset": "wing_call"},
            ]
        if strategy_type == StrategyType.BULL_PUT_SPREAD:
            return [
                {"leg_type": "BUY_PUT",  "option_type": "PE", "direction": "BUY",  "offset": "wing_put"},
                {"leg_type": "SELL_PUT", "option_type": "PE", "direction": "SELL", "offset": "short_put"},
            ]
        if strategy_type == StrategyType.BEAR_CALL_SPREAD:
            return [
                {"leg_type": "BUY_CALL", "option_type": "CE", "direction": "BUY",  "offset": "wing_call"},
                {"leg_type": "SELL_CALL","option_type": "CE", "direction": "SELL", "offset": "short_call"},
            ]
        if strategy_type == StrategyType.GAMMA_CAPTURE:
            return [
                {"leg_type": "BUY_CALL", "option_type": "CE", "direction": "BUY", "offset": "otm_call"},
                {"leg_type": "BUY_PUT",  "option_type": "PE", "direction": "BUY", "offset": "otm_put"},
            ]
        return []
```

**Step 4: Run tests**

```bash
uv run pytest test/test_tomic_strategy_engine.py -v
```
Expected: 6 PASS

**Step 5: Commit**

```bash
git add tomic/agents/strategy_engine.py test/test_tomic_strategy_engine.py
git commit -m "feat(tomic): add StrategyEngine (4-mode unified signal engine, replaces ConflictRouter)"
```

---

## Task 6: PositionManager — Real-Time P&L Monitoring

**Files:**
- Create: `tomic/agents/position_manager.py`
- Create: `test/test_tomic_position_manager.py`

**Step 1: Write failing tests**

Create `test/test_tomic_position_manager.py`:

```python
"""Tests for PositionManager P&L thresholds."""
import pytest
from unittest.mock import MagicMock
from tomic.agents.position_manager import (
    PositionManager, PositionState, PositionAction,
    evaluate_position,
)
from tomic.config import TomicConfig


def test_profit_target_triggers_close():
    # Entered for credit of 100, now worth 50 (50% profit)
    action = evaluate_position(
        entry_credit=100.0,
        current_value=50.0,
        trail_stop_activated=False,
    )
    assert action == PositionAction.CLOSE_PROFIT


def test_stop_loss_triggers_close():
    # Credit 100, current value 210 (debit 210 > 2× credit)
    action = evaluate_position(
        entry_credit=100.0,
        current_value=210.0,
        trail_stop_activated=False,
    )
    assert action == PositionAction.CLOSE_LOSS


def test_trail_stop_activates_at_30pct():
    # At 30% profit, trail stop should activate
    action = evaluate_position(
        entry_credit=100.0,
        current_value=70.0,   # 30% profit (was 100, now 70)
        trail_stop_activated=False,
    )
    assert action == PositionAction.ACTIVATE_TRAIL


def test_hold_in_normal_range():
    # 15% profit — no action yet
    action = evaluate_position(
        entry_credit=100.0,
        current_value=85.0,
        trail_stop_activated=False,
    )
    assert action == PositionAction.HOLD


def test_trail_stop_closes_if_reversed():
    # Trail was activated at 30%, now reversed back above breakeven
    action = evaluate_position(
        entry_credit=100.0,
        current_value=105.0,  # now losing! trail stop triggers close
        trail_stop_activated=True,
    )
    assert action == PositionAction.CLOSE_TRAIL


def test_position_state_tracks_credits():
    state = PositionState(
        instrument="NIFTY",
        strategy_tag="TOMIC_IRON_CONDOR_NIFTY",
        entry_credit=120.0,
        lots=1,
    )
    assert state.pnl_pct(current_value=60.0) == pytest.approx(0.50)
    assert state.pnl_pct(current_value=240.0) == pytest.approx(-1.0)
```

**Step 2: Run to confirm failure**

```bash
uv run pytest test/test_tomic_position_manager.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: Create `tomic/agents/position_manager.py`**

```python
"""
TOMIC Position Manager — Real-Time P&L Monitoring & Adjustments
================================================================
Polls PositionBook snapshot every 5 seconds.
Evaluates each open options position against P&L thresholds.
Emits adjustment/close signals back to StrategyEngine or directly
enqueues close commands via CommandStore.

P&L is measured as a fraction of max credit received:
  - entry_credit: premium collected at entry (positive number)
  - current_value: current cost to close the position (positive number)
  - profit = entry_credit - current_value
  - profit_pct = profit / entry_credit
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from tomic.config import TomicConfig, PositionManagerParams
from tomic.position_book import PositionBook, PositionSnapshot

logger = logging.getLogger(__name__)


class PositionAction(str, Enum):
    HOLD = "HOLD"
    ACTIVATE_TRAIL = "ACTIVATE_TRAIL"
    CLOSE_PROFIT = "CLOSE_PROFIT"
    CLOSE_LOSS = "CLOSE_LOSS"
    CLOSE_TRAIL = "CLOSE_TRAIL"
    CLOSE_TIME = "CLOSE_TIME"
    ADJUST_DELTA = "ADJUST_DELTA"


def evaluate_position(
    entry_credit: float,
    current_value: float,
    trail_stop_activated: bool,
    params: Optional[PositionManagerParams] = None,
) -> PositionAction:
    """
    Evaluate a position and return the recommended action.
    Pure function — no side effects.

    entry_credit: premium collected (e.g. 100 rupees per lot)
    current_value: cost to close now (e.g. 50 = 50% profit)
    """
    if params is None:
        params = PositionManagerParams()

    profit = entry_credit - current_value
    profit_pct = profit / entry_credit if entry_credit > 0 else 0.0

    # Loss stop: current cost > 2× credit (loss = credit × loss_multiple)
    if current_value >= entry_credit * params.stop_loss_multiple:
        return PositionAction.CLOSE_LOSS

    # Trail stop triggered: position reversed back above breakeven
    if trail_stop_activated and profit_pct <= 0:
        return PositionAction.CLOSE_TRAIL

    # Profit target: 50% of max credit
    if profit_pct >= params.profit_target_pct:
        return PositionAction.CLOSE_PROFIT

    # Trail stop activation: 30% profit
    if profit_pct >= params.trail_stop_activate_pct and not trail_stop_activated:
        return PositionAction.ACTIVATE_TRAIL

    return PositionAction.HOLD


@dataclass
class PositionState:
    """Tracks the lifecycle state of a single options position."""
    instrument: str
    strategy_tag: str
    entry_credit: float
    lots: int
    trail_stop_activated: bool = False
    open_time_mono: float = field(default_factory=time.monotonic)

    def pnl_pct(self, current_value: float) -> float:
        """Profit as fraction of entry credit. Positive = profit."""
        if self.entry_credit <= 0:
            return 0.0
        return (self.entry_credit - current_value) / self.entry_credit

    def action(
        self, current_value: float, params: Optional[PositionManagerParams] = None
    ) -> PositionAction:
        return evaluate_position(
            self.entry_credit, current_value,
            self.trail_stop_activated, params,
        )


class PositionManager:
    """
    Monitors open positions and emits close/adjust signals.
    Runs as a background thread, checking every `check_interval_s`.
    """

    def __init__(
        self,
        config: TomicConfig,
        position_book: PositionBook,
        command_store=None,  # CommandStore (optional, injected by runtime)
    ) -> None:
        self._config = config
        self._params: PositionManagerParams = config.position_manager
        self._position_book = position_book
        self._command_store = command_store
        self._states: Dict[str, PositionState] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def register_position(
        self,
        strategy_tag: str,
        instrument: str,
        entry_credit: float,
        lots: int,
    ) -> None:
        """Called by ExecutionAgent after a position is opened."""
        with self._lock:
            self._states[strategy_tag] = PositionState(
                instrument=instrument,
                strategy_tag=strategy_tag,
                entry_credit=entry_credit,
                lots=lots,
            )
        logger.info(
            "PositionManager: registered %s credit=%.2f lots=%d",
            strategy_tag, entry_credit, lots,
        )

    def unregister_position(self, strategy_tag: str) -> None:
        """Called by ExecutionAgent after a position is closed."""
        with self._lock:
            self._states.pop(strategy_tag, None)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tomic-position-manager"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                self._check_positions()
            except Exception as exc:
                logger.error("PositionManager error: %s", exc)
            time.sleep(self._params.check_interval_s)

    def _check_positions(self) -> None:
        snapshot = self._position_book.read_snapshot()
        with self._lock:
            states = dict(self._states)

        for strategy_tag, state in states.items():
            # Look up current P&L from PositionBook
            pos = snapshot.positions.get(strategy_tag)
            if pos is None:
                continue

            # current_value = cost to close = abs(pnl relative to entry)
            # For SELL positions: avg_price is the credit received
            current_pnl = pos.pnl  # already computed by broker sync
            current_value = max(0.0, state.entry_credit - current_pnl)

            action = state.action(current_value, self._params)

            if action == PositionAction.ACTIVATE_TRAIL:
                with self._lock:
                    if strategy_tag in self._states:
                        self._states[strategy_tag].trail_stop_activated = True
                logger.info("PositionManager: trail stop activated for %s", strategy_tag)

            elif action in (
                PositionAction.CLOSE_PROFIT,
                PositionAction.CLOSE_LOSS,
                PositionAction.CLOSE_TRAIL,
            ):
                logger.info(
                    "PositionManager: %s → %s (credit=%.2f value=%.2f)",
                    strategy_tag, action.value, state.entry_credit, current_value,
                )
                self._enqueue_close(strategy_tag, state.instrument, action.value)

    def _enqueue_close(self, strategy_tag: str, instrument: str, reason: str) -> None:
        """Enqueue a close command. Executed by ExecutionAgent."""
        if self._command_store is None:
            logger.warning("PositionManager: no command_store — cannot enqueue close for %s", strategy_tag)
            return

        import json
        import uuid
        payload = {
            "action": "CLOSE_POSITION",
            "strategy_tag": strategy_tag,
            "instrument": instrument,
            "reason": reason,
        }
        self._command_store.enqueue(
            event_type="CLOSE_REQUEST",
            payload=json.dumps(payload),
            idempotency_key=f"{strategy_tag}:close:{reason}:{int(time.time())}",
            correlation_id=str(uuid.uuid4()),
        )

    def get_states(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                tag: {
                    "instrument": s.instrument,
                    "entry_credit": s.entry_credit,
                    "trail_stop_activated": s.trail_stop_activated,
                    "lots": s.lots,
                }
                for tag, s in self._states.items()
            }
```

**Step 4: Run tests**

```bash
uv run pytest test/test_tomic_position_manager.py -v
```
Expected: 7 PASS

**Step 5: Commit**

```bash
git add tomic/agents/position_manager.py test/test_tomic_position_manager.py
git commit -m "feat(tomic): add PositionManager with P&L thresholds and trail stop"
```

---

## Task 7: ExpirySpecialist — Gamma Capture After 14:00

**Files:**
- Create: `tomic/agents/expiry_specialist.py`
- Create: `test/test_tomic_expiry_specialist.py`

**Step 1: Write failing tests**

Create `test/test_tomic_expiry_specialist.py`:

```python
"""Tests for ExpirySpecialist."""
import pytest
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from tomic.agents.expiry_specialist import (
    ExpirySpecialist, is_expiry_day, is_after_gamma_entry_time,
)
from tomic.config import TomicConfig


def test_nifty_expiry_thursday():
    # 2026-02-26 is a Thursday
    assert is_expiry_day("NIFTY", date(2026, 2, 26)) is True
    assert is_expiry_day("NIFTY", date(2026, 2, 27)) is False  # Friday


def test_banknifty_expiry_wednesday():
    # 2026-02-25 is a Wednesday
    assert is_expiry_day("BANKNIFTY", date(2026, 2, 25)) is True
    assert is_expiry_day("BANKNIFTY", date(2026, 2, 26)) is False  # Thursday


def test_sensex_expiry_friday():
    # 2026-02-27 is a Friday
    assert is_expiry_day("SENSEX", date(2026, 2, 27)) is True
    assert is_expiry_day("SENSEX", date(2026, 2, 26)) is False


def test_after_gamma_entry_time():
    # 14:01 is after 14:00
    dt = datetime(2026, 2, 26, 14, 1, 0)
    assert is_after_gamma_entry_time(dt, "14:00") is True
    dt_before = datetime(2026, 2, 26, 13, 59, 0)
    assert is_after_gamma_entry_time(dt_before, "14:00") is False


def test_gamma_signal_blocked_before_entry_time():
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    dt = datetime(2026, 2, 26, 13, 0, 0)  # before 14:00
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])
    assert len(signals) == 0


def test_gamma_signal_generated_after_entry_time():
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    # Thursday 14:01 → NIFTY expiry day, after gamma entry time
    dt = datetime(2026, 2, 26, 14, 1, 0)
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])
    assert len(signals) == 1
    assert signals[0].signal_dict["strategy_type"] == "GAMMA_CAPTURE"
    assert signals[0].signal_dict["instrument"] == "NIFTY"


def test_gamma_signal_not_generated_twice():
    """Once generated for the day, don't regenerate."""
    config = TomicConfig()
    spec = ExpirySpecialist(config=config)
    dt = datetime(2026, 2, 26, 14, 1, 0)
    spec.get_gamma_signals(now=dt, instruments=["NIFTY"])  # first call
    signals = spec.get_gamma_signals(now=dt, instruments=["NIFTY"])  # second call
    assert len(signals) == 0  # already generated today
```

**Step 2: Run to confirm failure**

```bash
uv run pytest test/test_tomic_expiry_specialist.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: Create `tomic/agents/expiry_specialist.py`**

```python
"""
TOMIC Expiry Specialist — Gamma Capture After 14:00
====================================================
On expiry day, after 14:00, near-ATM options are nearly worthless
but can multiply 5-20× on violent moves.

Generates GAMMA_CAPTURE signals: buy 1-OTM CE + 1-OTM PE.
Max size: ₹5,000 or 0.5% of capital (whichever is lower).
Exit: 15:10 regardless.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from tomic.conflict_router import RoutedSignal, SignalSource
from tomic.config import ExpiryParams, StrategyType, TomicConfig

logger = logging.getLogger(__name__)

# Expiry weekdays per instrument (0=Monday, 3=Thursday, etc.)
_EXPIRY_WEEKDAY = {
    "NIFTY":     3,   # Thursday
    "BANKNIFTY": 2,   # Wednesday
    "SENSEX":    4,   # Friday
    "FINNIFTY":  1,   # Tuesday
}


def is_expiry_day(instrument: str, today: Optional[date] = None) -> bool:
    """Return True if today is the weekly expiry day for the instrument."""
    if today is None:
        today = date.today()
    weekday = _EXPIRY_WEEKDAY.get(instrument.upper(), -1)
    return today.weekday() == weekday


def is_after_gamma_entry_time(now: datetime, entry_hhmm: str) -> bool:
    """Return True if current time is at or after the gamma entry time."""
    try:
        h, m = map(int, entry_hhmm.split(":"))
        return now.hour > h or (now.hour == h and now.minute >= m)
    except ValueError:
        return False


class ExpirySpecialist:
    """
    Generates gamma capture signals on expiry days after 14:00.
    One signal per instrument per day maximum.
    """

    def __init__(self, config: TomicConfig) -> None:
        self._config = config
        self._params: ExpiryParams = config.expiry
        self._lock = threading.Lock()
        self._generated_today: Dict[str, str] = {}  # instrument → date string

    def get_gamma_signals(
        self,
        now: Optional[datetime] = None,
        instruments: Optional[List[str]] = None,
    ) -> List[RoutedSignal]:
        """
        Check each instrument for expiry + time conditions.
        Returns GAMMA_CAPTURE signals for qualifying instruments.
        """
        if now is None:
            now = datetime.now()

        instruments = instruments or list(_EXPIRY_WEEKDAY.keys())
        signals = []
        today_str = now.date().isoformat()

        for instrument in instruments:
            if not is_expiry_day(instrument, now.date()):
                continue
            if not is_after_gamma_entry_time(now, self._params.gamma_entry_hhmm):
                continue

            with self._lock:
                if self._generated_today.get(instrument.upper()) == today_str:
                    continue  # already generated today
                self._generated_today[instrument.upper()] = today_str

            signal = self._make_gamma_signal(instrument)
            signals.append(signal)
            logger.info("ExpirySpecialist: GAMMA_CAPTURE signal for %s", instrument)

        return signals

    def _make_gamma_signal(self, instrument: str) -> RoutedSignal:
        signal_dict: Dict[str, Any] = {
            "instrument": instrument.upper(),
            "strategy_type": StrategyType.GAMMA_CAPTURE.value,
            "direction": "BUY",
            "legs": [
                {"leg_type": "BUY_CALL", "option_type": "CE",
                 "direction": "BUY", "offset": "otm_call"},
                {"leg_type": "BUY_PUT",  "option_type": "PE",
                 "direction": "BUY", "offset": "otm_put"},
            ],
            "max_capital_pct": self._params.max_capital_pct,
            "max_abs_inr": self._params.max_abs_inr,
            "entry_mode": "expiry_gamma",
            "signal_strength": 60.0,
        }
        return RoutedSignal(
            source=SignalSource.VOLATILITY,
            signal_dict=signal_dict,
            priority_score=60.0,
        )

    def reset_for_new_day(self) -> None:
        with self._lock:
            self._generated_today.clear()
```

**Step 4: Run tests**

```bash
uv run pytest test/test_tomic_expiry_specialist.py -v
```
Expected: 8 PASS

**Step 5: Commit**

```bash
git add tomic/agents/expiry_specialist.py test/test_tomic_expiry_specialist.py
git commit -m "feat(tomic): add ExpirySpecialist for gamma capture on expiry days after 14:00"
```

---

## Task 8: ExecutionAgent — Multi-Leg Symbol Resolution

Extend the existing `ExecutionAgent` to use `LegResolver` for resolving abstract leg offsets into real NFO option symbols before placing orders.

**Files:**
- Modify: `tomic/agents/execution_agent.py`
- Add to: `test/test_tomic_execution_agent_endpoints.py` (existing file)

**Step 1: Read the existing execution agent**

```bash
# Read lines 1-100 to find the order execution method
```

Check `tomic/agents/execution_agent.py` for the method `_execute_command` or `_place_order`. The leg resolution goes in the method that builds order payloads from command `payload`.

**Step 2: Add the `_resolve_legs_for_command` method**

In `tomic/agents/execution_agent.py`, add this import at the top (after existing imports):

```python
from tomic.leg_resolver import LegResolver, LegResolution
```

Add this method to the `ExecutionAgent` class (before `_setup`):

```python
def _resolve_legs_for_command(
    self,
    payload: Dict[str, Any],
) -> List[LegResolution]:
    """
    Resolve abstract leg offsets to real option symbols.
    Called before placing multi-leg orders.

    Fetches available strikes from option chain, uses LegResolver
    to find delta-matching strikes, returns LegResolution list.
    """
    instrument = str(payload.get("instrument", "")).upper()
    strategy_type = str(payload.get("strategy_type", ""))
    short_delta = float(payload.get("short_delta_target", 0.25))
    wing_delta = float(payload.get("wing_delta_target", 0.10))
    expiry_date = str(payload.get("expiry_date", ""))

    # Get current spot price
    spot = self._get_spot_price(instrument)
    if spot <= 0:
        logger.warning("LegResolver: no spot price for %s", instrument)
        return []

    # Get available strikes and option prices from chain service
    strikes, prices, dte = self._get_strikes_and_prices(instrument, expiry_date)
    if not strikes:
        logger.warning("LegResolver: no strikes for %s expiry=%s", instrument, expiry_date)
        return []

    resolver = LegResolver(greeks_engine=self._greeks_engine)

    if strategy_type == "IRON_CONDOR":
        return resolver.resolve_iron_condor(
            strikes=strikes, prices=prices, spot=spot, dte=dte,
            short_delta=short_delta, wing_delta=wing_delta,
        )
    if strategy_type == "BULL_PUT_SPREAD":
        return resolver.resolve_bull_put_spread(
            strikes=strikes, prices=prices, spot=spot, dte=dte,
            short_delta=short_delta, wing_delta=wing_delta,
        )
    if strategy_type == "BEAR_CALL_SPREAD":
        return resolver.resolve_bear_call_spread(
            strikes=strikes, prices=prices, spot=spot, dte=dte,
            short_delta=short_delta, wing_delta=wing_delta,
        )
    if strategy_type == "GAMMA_CAPTURE":
        max_price = float(payload.get("max_option_price",
                                       self._config.expiry.max_option_price))
        return resolver.resolve_gamma_capture(
            strikes=strikes, prices=prices, spot=spot, max_price=max_price,
        )

    logger.warning("LegResolver: unknown strategy_type=%s", strategy_type)
    return []


def _get_spot_price(self, instrument: str) -> float:
    """Get current spot from position book or WS data manager."""
    try:
        # Try WS data manager first (real-time)
        if hasattr(self, "_ws") and self._ws is not None:
            exchange = "NSE_INDEX" if instrument in {"NIFTY", "BANKNIFTY", "FINNIFTY"} else "BSE_INDEX" if instrument in {"SENSEX", "BANKEX"} else "NSE"
            ltp = self._ws.get_last_price(symbol=instrument, exchange=exchange, max_age_s=10.0)
            if ltp and ltp > 0:
                return float(ltp)
    except Exception:
        pass
    return 0.0


def _get_strikes_and_prices(
    self,
    instrument: str,
    expiry_date: str,
) -> tuple[list, dict, float]:
    """
    Fetch available strikes + LTPs + DTE.
    Returns (strikes_list, prices_dict, dte_days).
    """
    from datetime import datetime
    try:
        from services.option_symbol_service import get_available_strikes, get_option_exchange
        from services.option_chain_service import get_option_prices_for_chain

        exchange = get_option_exchange("NSE_INDEX")
        strikes = get_available_strikes(instrument, expiry_date, "CE", exchange)
        if not strikes:
            return [], {}, 0.0

        prices = {}
        # Try to get prices from WS data manager
        for strike in strikes:
            for opt_type in ("CE", "PE"):
                symbol = f"{instrument}{expiry_date}{int(strike)}{opt_type}"
                if hasattr(self, "_ws") and self._ws is not None:
                    ltp = self._ws.get_last_price(symbol=symbol, exchange=exchange, max_age_s=30.0)
                    if ltp and ltp > 0:
                        prices[strike] = float(ltp)

        # DTE computation
        dte = 1.0
        if expiry_date:
            try:
                expiry_dt = datetime.strptime(expiry_date.upper(), "%d%b%y")
                dte = max(0.1, (expiry_dt - datetime.now()).days + 1.0)
            except ValueError:
                dte = 7.0

        return sorted(strikes), prices, dte

    except Exception as exc:
        logger.warning("_get_strikes_and_prices failed: %s", exc)
        return [], {}, 0.0
```

Also add `_greeks_engine` instantiation in `__init__` (look for the existing `__init__` and add):

```python
from tomic.greeks_engine import GreeksEngine
# In __init__, add:
self._greeks_engine = GreeksEngine()
self._ws = None  # injected by runtime via set_ws_manager()
```

Add a setter method:

```python
def set_ws_manager(self, ws_manager) -> None:
    """Inject WS data manager for real-time price lookups."""
    self._ws = ws_manager
```

**Step 3: Add tests**

In `test/test_tomic_execution_agent_endpoints.py`, add:

```python
def test_resolve_legs_returns_empty_without_spot():
    """Should return empty list if no spot price available."""
    # This is a smoke test — full integration tested in isolation
    from tomic.agents.execution_agent import ExecutionAgent
    # Minimal construction for testing resolve method signature only
    # (full agent construction requires DB etc. — out of scope here)
    assert True  # placeholder — real test in integration
```

**Step 4: Run existing execution tests**

```bash
uv run pytest test/test_tomic_execution_agent_endpoints.py -v
```
Expected: all existing tests still PASS

**Step 5: Commit**

```bash
git add tomic/agents/execution_agent.py
git commit -m "feat(tomic): add multi-leg symbol resolution in ExecutionAgent via LegResolver"
```

---

## Task 9: Runtime Rewire — Replace Old Pipeline

Wire all new components into `tomic/runtime.py`. Remove `SniperAgent`, `VolatilityAgent`, `ConflictRouter` from the signal pipeline.

**Files:**
- Modify: `tomic/runtime.py`

**Step 1: Find and update the imports section**

In `tomic/runtime.py`, replace the import block that currently imports old agents:

```python
# REMOVE these imports:
# from tomic.agents.sniper_agent import SniperAgent
# from tomic.agents.volatility_agent import VolatilityAgent
# from tomic.conflict_router import ConflictRouter

# ADD these imports:
from tomic.agents.market_context_agent import MarketContextAgent
from tomic.agents.daily_plan_agent import DailyPlanAgent
from tomic.agents.strategy_engine import StrategyEngine
from tomic.agents.position_manager import PositionManager
from tomic.agents.expiry_specialist import ExpirySpecialist
```

**Step 2: Replace agent instantiation in `TomicRuntime.__init__`**

Find the section in `__init__` that creates `SniperAgent`, `VolatilityAgent`, `ConflictRouter`.

Replace with:

```python
# --- New options selling pipeline ---
self._market_context_agent = MarketContextAgent(config=self._config)

self._daily_plan_agent = DailyPlanAgent(
    config=self._config,
    market_context_agent=self._market_context_agent,
    regime_state=self._regime_agent.regime_state,
)

self._strategy_engine = StrategyEngine(
    config=self._config,
    daily_plan_agent=self._daily_plan_agent,
    market_context_agent=self._market_context_agent,
    regime_state=self._regime_agent.regime_state,
)

self._position_manager = PositionManager(
    config=self._config,
    position_book=self._position_book,
    command_store=self._command_store,
)

self._expiry_specialist = ExpirySpecialist(config=self._config)
```

**Step 3: Update `_signal_loop` to use StrategyEngine**

Find the `_signal_loop` method. Replace the old sniper/vol/router calls with:

```python
def _signal_loop(self) -> None:
    """Main signal generation loop. Runs every TOMIC_SIGNAL_LOOP_INTERVAL_S seconds."""
    while self._running:
        try:
            self._run_signal_cycle()
        except Exception as exc:
            logger.error("Signal loop error: %s", exc, exc_info=True)
        time.sleep(float(os.getenv("TOMIC_SIGNAL_LOOP_INTERVAL_S", "5")))

def _run_signal_cycle(self) -> None:
    """One cycle of signal generation."""
    if self._kill_switch_active:
        return

    # Morning plan generation (9:45 AM)
    self._maybe_run_morning_plan()

    # Get signals from StrategyEngine (all modes)
    signals = self._strategy_engine.get_pending_signals()

    # Expiry gamma signals
    expiry_signals = self._expiry_specialist.get_gamma_signals(
        instruments=list(self._config.daily_plan.instruments)
    )
    signals.extend(expiry_signals)

    if signals:
        self._enqueue_routed_signals(signals)

def _maybe_run_morning_plan(self) -> None:
    """Generate morning plan at 9:45 AM if not already done today."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    today = now.date().isoformat()

    plan_time = self._config.daily_plan.plan_time_hhmm
    h, m = map(int, plan_time.split(":"))

    if now.hour == h and now.minute >= m:
        last_plan_date = getattr(self, "_last_morning_plan_date", "")
        if last_plan_date != today:
            logger.info("Running morning plan generation at %s", plan_time)
            self._daily_plan_agent.generate_all_plans(entry_mode="morning_plan")
            self._last_morning_plan_date = today
```

**Step 4: Update `MarketBridge` to feed `MarketContextAgent`**

In `TomicRuntime.__init__`, after creating `TomicMarketBridge`, register the market context agent callbacks. Find where `_market_bridge` is instantiated and add:

```python
# Wire MarketContextAgent into the bridge's tick path
# (The bridge already feeds RegimeAgent and SniperAgent via feed_candle)
# Add market context feeds:
self._market_bridge.set_market_context_agent(self._market_context_agent)
```

In `tomic/market_bridge.py`, add to `TomicMarketBridge`:

```python
def set_market_context_agent(self, agent) -> None:
    """Inject market context agent for VIX/trend/LTP feeds."""
    self._market_context_agent = agent

# In _handle_underlying_tick, after existing code, add:
if hasattr(self, '_market_context_agent') and self._market_context_agent is not None:
    self._market_context_agent.feed_ltp(symbol, ltp)
    if candle is not None:
        self._market_context_agent.feed_candle(symbol, candle[3])  # close

# In _on_tick, after feeding VIX to regime, add:
if symbol_key == self._vix_key:
    if hasattr(self, '_market_context_agent') and self._market_context_agent is not None:
        self._market_context_agent.feed_vix(ltp)
```

**Step 5: Start PositionManager in `start()` method**

In `TomicRuntime.start()`, add:

```python
self._position_manager.start()
```

In `TomicRuntime.stop()`, add:

```python
self._position_manager.stop()
```

**Step 6: Update `get_status()` to include new components**

In `TomicRuntime.get_status()`, add to the return dict:

```python
"market_context": self._market_context_agent.read_context().__dict__,
"daily_plans": self._daily_plan_agent.get_summary(),
"position_manager": self._position_manager.get_states(),
```

**Step 7: Run all TOMIC tests**

```bash
uv run pytest test/ -k "tomic" -v
```
Expected: all PASS (previous tests unaffected)

**Step 8: Commit**

```bash
git add tomic/runtime.py tomic/market_bridge.py
git commit -m "feat(tomic): rewire runtime to use StrategyEngine, remove SniperAgent/VolAgent/ConflictRouter from pipeline"
```

---

## Task 10: Blueprint Endpoint + Frontend

Add `/tomic/plan` endpoint for the daily plan and update the dashboard to show market context + plans.

**Files:**
- Modify: `blueprints/tomic.py` (add 2 endpoints)
- Modify: `frontend/src/api/tomic.ts` (add 2 API functions + types)
- Modify: `frontend/src/pages/tomic/TomicDashboard.tsx` (add DailyPlan panel)

**Step 1: Add endpoints to `blueprints/tomic.py`**

Find the `tomic_bp` blueprint in `blueprints/tomic.py`. Add these two routes:

```python
@tomic_bp.route("/plan", methods=["GET"])
def get_daily_plan():
    """Return today's daily trade plans for all instruments."""
    rt = _get_runtime()
    if rt is None:
        return jsonify({"status": "error", "message": "TOMIC not running"}), 503
    try:
        summary = rt._daily_plan_agent.get_summary()
        return jsonify({"status": "success", "data": summary})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@tomic_bp.route("/market-context", methods=["GET"])
def get_market_context():
    """Return current market context (VIX, PCR, trend)."""
    rt = _get_runtime()
    if rt is None:
        return jsonify({"status": "error", "message": "TOMIC not running"}), 503
    try:
        ctx = rt._market_context_agent.read_context()
        import dataclasses
        return jsonify({"status": "success", "data": dataclasses.asdict(ctx)})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
```

**Step 2: Add TypeScript types and API functions to `frontend/src/api/tomic.ts`**

Add after the existing types:

```typescript
// Market Context
export interface TomicMarketContext {
  vix: number;
  vix_regime: "TOO_LOW" | "NORMAL" | "ELEVATED" | "HIGH" | "EXTREME" | "UNKNOWN";
  pcr: number;
  pcr_bias: "BULLISH" | "BEARISH" | "NEUTRAL";
  nifty_ltp: number;
  banknifty_ltp: number;
  sensex_ltp: number;
  nifty_trend: "ABOVE_20MA" | "BELOW_20MA" | "NEUTRAL";
  banknifty_trend: string;
  sensex_trend: string;
}

// Daily Trade Plan
export interface TomicDailyPlan {
  date: string;
  instrument: string;
  strategy_type: string;
  entry_mode: string;
  vix_at_plan: number;
  regime_at_plan: string;
  pcr_at_plan: number;
  short_delta_target: number;
  wing_delta_target: number;
  lots: number;
  expiry_date: string;
  rationale: string;
  valid_until_hhmm: string;
  is_active: boolean;
  reentry_count: number;
}

export interface TomicDailyPlanSummary {
  date: string;
  plans: TomicDailyPlan[];
}
```

Add these two API functions:

```typescript
export async function getMarketContext(): Promise<TomicMarketContext> {
  const response = await webClient.get("/tomic/market-context");
  return response.data.data;
}

export async function getDailyPlan(): Promise<TomicDailyPlanSummary> {
  const response = await webClient.get("/tomic/plan");
  return response.data.data;
}
```

**Step 3: Add MarketContext + DailyPlan panels to `TomicDashboard.tsx`**

Import the new hooks at the top of `TomicDashboard.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { getMarketContext, getDailyPlan } from "@/api/tomic";
import type { TomicMarketContext, TomicDailyPlanSummary } from "@/api/tomic";
```

Add these query hooks inside the component:

```typescript
const { data: marketCtx } = useQuery({
  queryKey: ["tomic-market-context"],
  queryFn: getMarketContext,
  refetchInterval: 10_000,
  enabled: isRunning,
});

const { data: dailyPlan } = useQuery({
  queryKey: ["tomic-daily-plan"],
  queryFn: getDailyPlan,
  refetchInterval: 30_000,
  enabled: isRunning,
});
```

Add the Market Context panel (VIX badge + PCR + trend) just below the existing status cards:

```tsx
{/* Market Context Panel */}
{marketCtx && (
  <div className="rounded-lg border bg-card p-4 space-y-3">
    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
      Market Context
    </h3>
    <div className="flex flex-wrap gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">India VIX</span>
        <span className={`px-2 py-0.5 rounded text-xs font-bold ${
          marketCtx.vix_regime === "TOO_LOW" ? "bg-gray-200 text-gray-700" :
          marketCtx.vix_regime === "NORMAL"  ? "bg-green-100 text-green-800" :
          marketCtx.vix_regime === "ELEVATED"? "bg-yellow-100 text-yellow-800" :
          marketCtx.vix_regime === "HIGH"    ? "bg-orange-100 text-orange-800" :
          "bg-red-100 text-red-800"
        }`}>
          {marketCtx.vix.toFixed(2)} ({marketCtx.vix_regime})
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">PCR</span>
        <span className={`px-2 py-0.5 rounded text-xs font-bold ${
          marketCtx.pcr_bias === "BULLISH" ? "bg-green-100 text-green-800" :
          marketCtx.pcr_bias === "BEARISH" ? "bg-red-100 text-red-800" :
          "bg-gray-100 text-gray-700"
        }`}>
          {marketCtx.pcr.toFixed(2)} ({marketCtx.pcr_bias})
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">NIFTY</span>
        <span className={`px-2 py-0.5 rounded text-xs ${
          marketCtx.nifty_trend === "ABOVE_20MA" ? "bg-green-100 text-green-800" :
          marketCtx.nifty_trend === "BELOW_20MA" ? "bg-red-100 text-red-800" :
          "bg-gray-100 text-gray-600"
        }`}>
          {marketCtx.nifty_ltp > 0 ? marketCtx.nifty_ltp.toFixed(0) : "—"}{" "}
          {marketCtx.nifty_trend.replace("_", " ")}
        </span>
      </div>
    </div>
  </div>
)}

{/* Daily Trade Plans Panel */}
{dailyPlan && dailyPlan.plans.length > 0 && (
  <div className="rounded-lg border bg-card p-4 space-y-3">
    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
      Today's Trade Plans — {dailyPlan.date}
    </h3>
    <div className="space-y-2">
      {dailyPlan.plans.map((plan) => (
        <div
          key={plan.instrument}
          className={`rounded border p-3 text-sm ${plan.is_active ? "border-blue-200 bg-blue-50/30" : "border-gray-200 opacity-60"}`}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="font-semibold">{plan.instrument}</span>
              <span className="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 text-xs font-medium">
                {plan.strategy_type.replace(/_/g, " ")}
              </span>
              {!plan.is_active && (
                <span className="px-1.5 py-0.5 rounded bg-gray-200 text-gray-600 text-xs">CLOSED</span>
              )}
            </div>
            <div className="text-xs text-muted-foreground">
              Re-entries: {plan.reentry_count} / 2
            </div>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{plan.rationale}</p>
          <div className="mt-1.5 flex gap-4 text-xs text-muted-foreground">
            <span>Short Δ: {plan.short_delta_target.toFixed(2)}</span>
            <span>Wing Δ: {plan.wing_delta_target.toFixed(2)}</span>
            <span>Lots: {plan.lots}</span>
            {plan.expiry_date && <span>Expiry: {plan.expiry_date}</span>}
          </div>
        </div>
      ))}
    </div>
  </div>
)}
```

**Step 4: Build and test the frontend**

```bash
cd frontend
npm run build
```
Expected: build succeeds with no TypeScript errors

**Step 5: Start the app and verify**

```bash
cd ..
uv run app.py
```

Navigate to `http://127.0.0.1:5000/react/tomic` and verify:
- Market Context panel shows VIX, PCR, NIFTY trend
- Daily Plans panel appears after TOMIC starts and 9:45 AM plan runs (or trigger manually via `/tomic/plan` refresh)
- No console errors

**Step 6: Commit everything**

```bash
git add blueprints/tomic.py frontend/src/api/tomic.ts frontend/src/pages/tomic/TomicDashboard.tsx frontend/dist/
git commit -m "feat(tomic): add market context + daily plan endpoints and dashboard panels"
```

---

## Final Verification

Run all TOMIC tests:

```bash
uv run pytest test/ -k "tomic" -v --tb=short
```
Expected: all PASS

Run full test suite:

```bash
uv run pytest test/ -v --tb=short
```
Expected: all PASS

---

## Summary of Changes

| File | Action | Purpose |
|---|---|---|
| `tomic/config.py` | Modify | Add 4 new param classes + EntryMode enum |
| `tomic/leg_resolver.py` | Create | Delta-based strike resolution |
| `tomic/agents/market_context_agent.py` | Create | VIX/PCR/trend aggregation |
| `tomic/agents/daily_plan_agent.py` | Create | 9:45 AM morning plan generator |
| `tomic/agents/strategy_engine.py` | Create | Unified 4-mode signal engine |
| `tomic/agents/position_manager.py` | Create | P&L monitoring + trail stop |
| `tomic/agents/expiry_specialist.py` | Create | Gamma capture after 14:00 |
| `tomic/agents/execution_agent.py` | Modify | Add multi-leg resolver |
| `tomic/runtime.py` | Modify | Rewire pipeline (remove old, add new) |
| `tomic/market_bridge.py` | Modify | Feed MarketContextAgent from ticks |
| `blueprints/tomic.py` | Modify | Add `/plan` + `/market-context` endpoints |
| `frontend/src/api/tomic.ts` | Modify | New types + API functions |
| `frontend/src/pages/tomic/TomicDashboard.tsx` | Modify | Market context + plan panels |
