# TOMIC Options Selling System — Design Document
**Date:** 2026-02-27
**Status:** Approved

---

## Goal

Overhaul TOMIC from a directional equity-momentum system into a production-grade **intraday options selling system** for Indian markets, trading defined-risk spreads on NIFTY, BANKNIFTY, SENSEX, and Nifty 50 stocks.

---

## What We Keep vs Replace

### Keep (battle-tested)

| Component | Reason |
|---|---|
| `CommandStore` | Durable at-least-once delivery queue |
| `CircuitBreakerEngine` | Daily loss kill-switch, order rate limits |
| `RegimeAgent` | Ichimoku + Impulse System trend classification |
| `RiskAgent` | 8-step sizing chain (Carver, Half-Kelly, 2% rule, VIX overlay) |
| `ExecutionAgent` | Safety invariants, lease mechanics, legging policy — extended with multi-leg resolver |
| `JournalingAgent` | Audit trail |
| `EventBus` / `Supervisor` | Infrastructure |
| `PositionBook` | Position state |
| `FreshnessTracker` | Data staleness gates |
| `WSDataManager` + `MarketBridge` | Tick ingestion |

### Remove from Trading Pipeline

| Component | Reason |
|---|---|
| `SniperAgent` | Equity momentum patterns (VCP, cup-and-handle) wrong paradigm for options selling |
| `VolatilityAgent` | Broken IV rank (session-rolling buffer, not true 52-week). Replaced by VIX-based logic |
| `ConflictRouter` | No longer needed — `StrategyEngine` replaces dual-agent arbitration |

> SniperAgent and VolatilityAgent code is preserved but excluded from the runtime routing pipeline.

### Add (new components)

| Component | Purpose |
|---|---|
| `MarketContextAgent` | India VIX, PCR, trend, support/resistance, OI buildup, max pain |
| `DailyPlanAgent` | 9:45 AM morning plan — reads context, creates `DailyTradePlan` with specific strikes |
| `StrategyEngine` | Unified decision engine (Morning / Continuous / Event-driven / Expiry modes) |
| `PositionManager` | Tick-level P&L monitoring, delta alerts, adjustment and re-entry signals |
| Multi-leg resolver | Inside `ExecutionAgent` — translates delta targets to real NFO symbols |

---

## Architecture & Data Flow

```
India VIX + NIFTY/BANKNIFTY ticks + Option chain OI
          ↓
  MarketContextAgent (continuous — updates every tick / 1-min candle)
  → MarketContext {vix, pcr, trend, support, resistance, max_pain, regime}

          ↓
  ─────────────────────────────────────────────────────
  Mode A (9:45 AM):       DailyPlanAgent
  Mode B (15-min):        ContinuousAnalyzer
  Mode C (event trigger): EventTrigger (VIX spike / S/R test / PCR extreme)
  Mode D (expiry > 14:00):ExpirySpecialist
  ─────────────────────────────────────────────────────
          ↓ DailyTradePlan / TradeSignal
  StrategyEngine
  → ApprovedSignal {instrument, strategy_type, legs with delta targets, lots}

          ↓
  RiskAgent (8-step sizing — unchanged)
  → ORDER_REQUEST into CommandStore

          ↓
  ExecutionAgent (extended)
  ├── resolve delta targets → real NFO symbols
  ├── hedge-first legging (buy wings before selling shorts)
  └── place orders via OpenAlgo /api/v1/placeorder

  PositionManager (parallel — tick-level)
  ├── P&L monitoring per open position
  ├── trailing stop activation
  ├── adjustment triggers (delta breach)
  ├── stop-out execution
  └── re-entry evaluation after close
```

---

## Strategy Selection Matrix

India VIX is the primary IV signal (replaces broken session-rolling IV rank).
Regime Agent provides directional bias (BULLISH / BEARISH / CONGESTION).

```
India VIX  | Regime Phase  | Strategy           | Short Delta | Wing Delta
───────────┼───────────────┼────────────────────┼─────────────┼───────────
< 12       | Any           | SKIP               | —           | —
12–18      | BULLISH       | Bull Put Spread     | 0.25        | 0.10
12–18      | BEARISH       | Bear Call Spread    | 0.25        | 0.10
12–18      | CONGESTION    | Iron Condor         | 0.25/side   | 0.10/side
18–25      | BULLISH       | Bull Put Spread     | 0.30        | 0.12
18–25      | BEARISH       | Bear Call Spread    | 0.30        | 0.12
18–25      | CONGESTION    | Iron Condor         | 0.30/side   | 0.12/side
25–35      | Any           | Iron Condor (tight) | 0.20        | 0.08
> 35       | Any           | SKIP / buy hedge    | —           | —

EXPIRY DAY after 14:00 (regardless of VIX):
           | Any           | Gamma Capture       | Buy 1-OTM CE + 1-OTM PE
           |               |                     | Max: ₹5,000 or 0.5% capital
```

### PCR Tilt

- PCR > 1.3 → tilt toward Bull Put Spread (bullish lean)
- PCR < 0.8 → tilt toward Bear Call Spread (bearish lean)
- PCR 0.8–1.3 → neutral, prefer Iron Condor

### Instrument-Specific Parameters

| Instrument | Lot Size | Strike Step | Expiry Day   |
|---|---|---|---|
| NIFTY      | 25       | 50 pts      | Thursday     |
| BANKNIFTY  | 15       | 100 pts     | Wednesday    |
| SENSEX     | 10       | 100 pts     | Friday       |
| Nifty 50 stocks | varies | varies  | Last Thursday (monthly) |

---

## Position Management (Intraday, Dynamic)

### Profit Path
- P&L ≥ 30% of credit received → activate trailing stop (move SL to breakeven)
- P&L ≥ 50% of credit received → close position (standard profit target)
- P&L ≥ 70% → hold if delta is stable (aggressive mode)

### Loss Path
- Short leg delta > 0.35 → raise alert
- Short leg delta > 0.45 → close breached side, open new position 2 strikes further OTM
- P&L ≤ −1.5× credit → warning alert
- P&L ≤ −2× credit → mandatory stop-out, no exceptions

### Time Path
- 15:00 → close all intraday positions (before 15:15 auto square-off)
- 1 DTE → close to avoid overnight gamma risk

### Re-entry Rules
- After a profitable close: if VIX still in range, time remaining > 60 min, no circuit breakers active → re-evaluate and potentially re-enter
- Maximum 2 re-entries per instrument per day
- Re-entry uses same delta targets but may shift strikes if market has moved

---

## Expiry Day Gamma Capture (after 14:00)

Near expiry, options become nearly worthless but can multiply 5–20× on a big move.

- Only activates on the expiry day of the traded instrument
- Buy 1-strike OTM CE + 1-strike OTM PE (synthetic near-expiry straddle)
- Position size: min(₹5,000, 0.5% of capital) — lottery ticket sizing
- No stop loss (already cheap enough to let expire worthless)
- Exit: mandatory at 15:10 (5 min before auto square-off)
- Separate circuit breaker: max ₹10,000 per expiry day on gamma trades

---

## MarketContextAgent — Data Sources

| Signal | Source | Update Frequency |
|---|---|---|
| India VIX | `NSE_INDEX:INDIAVIX` WS tick | Per tick |
| NIFTY LTP | `NSE_INDEX:NIFTY` WS tick | Per tick |
| BANKNIFTY LTP | `NSE_INDEX:BANKNIFTY` WS tick | Per tick |
| NIFTY trend | 20-day MA computed from minute candles | Per new candle |
| PCR | OpenAlgo analytics API or OI sum from option chain | Every 5 min |
| Support / Resistance | Previous day high/low + weekly pivot | On plan generation |
| Max Pain | Sum of OI at each strike, min total loss | Every 5 min |
| OI Buildup | Highest OI concentration strike (CE and PE) | Every 5 min |

---

## DailyTradePlan Schema

```python
@dataclass
class DailyTradePlan:
    date: str                      # YYYY-MM-DD
    instrument: str                # e.g. "NIFTY"
    strategy_type: StrategyType    # IRON_CONDOR / BULL_PUT_SPREAD / BEAR_CALL_SPREAD
    entry_mode: str                # "morning" / "continuous" / "event"
    vix_at_plan: float
    regime_at_plan: str
    pcr_at_plan: float
    short_put_strike: float        # resolved strike (if applicable)
    long_put_strike: float
    short_call_strike: float       # resolved strike (if applicable)
    long_call_strike: float
    short_delta_target: float
    lots: int
    max_credit_target: float       # estimated credit in INR
    profit_target_pct: float       # e.g. 0.50
    loss_limit_multiplier: float   # e.g. 2.0
    expiry_date: str               # DDMMMyy format for NFO
    rationale: str                 # human-readable explanation
    valid_until: str               # time in HH:MM after which plan is stale
```

---

## Multi-Leg Symbol Resolution (ExecutionAgent Extension)

The execution agent receives abstract leg specs like `{"offset": "OTM1", "option_type": "CE", "direction": "SELL"}`. It resolves these to real symbols:

1. Get current underlying LTP from PositionBook / WS cache
2. Fetch available strikes from option chain service
3. For each leg, find the strike where `|delta - target_delta| is minimized`
   - Uses Greeks engine (already exists as `GreeksEngine`)
   - Falls back to closest OTM strike if delta unavailable
4. Construct NFO symbol: e.g. `NIFTY25JAN25000CE`
5. Place in correct order (wing first per `LEGGING_POLICY`)

---

## New Files

```
tomic/
  agents/
    market_context_agent.py    # NEW: VIX, PCR, trend, S/R, max pain
    daily_plan_agent.py        # NEW: 9:45 AM morning plan generation
    strategy_engine.py         # NEW: unified signal engine (replaces conflict_router)
    position_manager.py        # NEW: tick-level P&L monitoring and adjustments
    expiry_specialist.py       # NEW: gamma capture after 14:00 on expiry days
  leg_resolver.py              # NEW: delta-based strike resolution for multi-leg
```

### Modified Files

```
tomic/
  runtime.py                   # Wire in new agents, remove SniperAgent/VolAgent/ConflictRouter
  agents/execution_agent.py    # Add multi-leg symbol resolution using leg_resolver
  config.py                    # Add new config params (PCR thresholds, re-entry limits, etc.)
```

---

## Entry Modes Summary

| Mode | Trigger | Frequency |
|---|---|---|
| Morning Plan | 9:45 AM daily | Once per day per instrument |
| Continuous | Every 15 minutes | All day if conditions change |
| Event-driven | VIX spike >20%, PCR extreme, S/R test | As triggered |
| Expiry Gamma | 14:00 on expiry day | Once per instrument expiry |
