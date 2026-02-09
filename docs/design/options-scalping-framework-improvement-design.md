# Options Scalping Framework Improvement Design

**Version:** 1.0  
**Date:** February 2026  
**Status:** Design Document  
**Scope:** Improve options scalping and auto-trading using OpenAlgo Tools and options analytics APIs

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Framework Overview](#2-current-framework-overview)
3. [Options Tools & Data Sources](#3-options-tools--data-sources)
4. [How Options Data Improves Scalping](#4-how-options-data-improves-scalping)
5. [Proposed Integration Architecture](#5-proposed-integration-architecture)
6. [Implementation Roadmap](#6-implementation-roadmap)
7. [API Reference for Options Data](#7-api-reference-for-options-data)
8. [Appendix: File Map](#8-appendix-file-map)

---

## 1. Executive Summary

This design document describes how to enhance the OpenAlgo options scalping framework—particularly the **auto-trading engine**—by integrating real-time options analytics data from the Tools feature. The goal is to:

- **Take better trades** — Use OI, PCR, GEX, Max Pain, and IV to filter and bias entries
- **Trail profit effectively** — Use gamma and GEX zones for dynamic trailing distance
- **Cut losses quickly** — Use OI unwinding, spread width, and IV signals for early exits

### Key Insight

The auto-trade engine today relies on **price ticks only** (LTP, momentum, regime). Options tools provide **derivative signals** (OI, PCR, GEX, IV) that capture market-maker positioning, sentiment, and implied move size. Combining both improves decision quality.

---

## 2. Current Framework Overview

### 2.1 Scalping & Auto-Trade Components

| Component | File | Purpose |
|-----------|------|---------|
| **Scalping Interface** | `scalping_interface.html` | Manual option chain scalping, BUY/SELL, Depth Scout, Scalp Radar |
| **Chart Window** | `chart_window.html` | Chart trading, TRIGGER orders, TP/SL overlays |
| **Auto Trading Window** | `auto_trading_window.html` | Automated CE/PE scalping, ~12.8K lines, main auto-trade logic |
| **Mock Replay** | `mock_replay.html` | Historical replay for backtesting |
| **Debug Scalping** | `debug_scalping.html` | Debug tools for scalping |

### 2.2 Auto-Trade Engine (Existing)

**Data Flow:**
```
WebSocket ticks → handleAutoTradeTick(side, ltp)
  → updateAutoMomentum() → getMomentumVelocity() → isNoTradeZone()
  → updateRegimeDetection() → autoCanEnter() → placeAutoEntry()
  → recordAutoEntry()

Exit: updateAutoTrailing() → checkVirtualTPSL() → executeVirtualTPSL()
  → closeAutoPosition() → recordAutoExit()
```

**Entry Filters (Current):**
- Momentum ticks ≥ threshold (4–10 depending on preset)
- Momentum velocity ≥ min move
- No-trade zone (30s range ≥ 2pts)
- Cooldown, max trades/min, regime filter
- Consecutive loss breaker

**Exit Logic (Current):**
- 5-stage trailing SL (BE → Lock → Trail → Tight → Accel)
- Virtual TP/SL (client-side monitoring)
- Fixed TP/SL, max duration, per-trade/daily loss limits

**Presets:**
- Sniper, Balanced, Scalper, Auto-Adaptive

### 2.3 Key State Objects

- **autoState** (~80+ fields): positions, momentum, trailing, P&L, regime, config
- **state** (scalping): selected strike, depth, positions, WebSocket connection

### 2.4 Backend Services

| Service | Path | Purpose |
|---------|------|---------|
| AI Scalper | `services/ai_scalper/` | Agent, risk engine, execution, learning |
| Manual Trade Log | `services/manual_trade_log_store.py` | Manual trade logging |
| Option Chain | `services/option_chain_service.py` | Option chain with quotes |

### 2.5 Databases

| Database | Purpose |
|----------|---------|
| `ai_scalper_logs.db` | Auto-trade ENTRY/EXIT logs |
| `manual_trade_logs.db` | Manual scalping + chart trades |
| `ai_scalper_ledger.db` | Learning bandit tuner |
| `ai_scalper_tuning.db` | Model tuning runs |

---

## 3. Options Tools & Data Sources

### 3.1 Tools Hub

The **Tools** page (`/tools`) provides 12 analytical tools. Nine are options-focused:

| # | Tool | Route | API Endpoint | Data Provided |
|---|------|-------|--------------|---------------|
| 1 | **Option Chain** | `/optionchain` | `POST /api/v1/optionchain` | LTP, bid/ask, OI, volume, lot size per strike |
| 2 | **Option Greeks** | `/ivchart` | `POST /ivchart/api/iv-data` | Historical IV, Delta, Theta, Vega, Gamma |
| 3 | **OI Tracker** | `/oitracker` | `POST /oitracker/api/oi-data` | CE/PE OI, PCR, OI change |
| 4 | **Max Pain** | `/maxpain` | `POST /oitracker/api/maxpain` | Max pain strike, pain distribution |
| 5 | **Straddle Chart** | `/straddle` | `POST /straddle/api/straddle-data` | ATM straddle, synthetic futures, implied move |
| 6 | **Vol Surface** | `/volsurface` | `POST /volsurface/api/surface-data` | 3D IV across strikes and expiries |
| 7 | **GEX Dashboard** | `/gex` | `POST /gex/api/gex-data` | Gamma exposure, OI walls, net GEX per strike |
| 8 | **IV Smile** | `/ivsmile` | `POST /ivsmile/api/iv-smile-data` | CE/PE IV curves, ATM IV, skew |
| 9 | **OI Profile** | `/oiprofile` | `POST /oiprofile/api/profile-data` | OI butterfly, daily OI change |

### 3.2 Per-Tool Data Details

#### Option Chain
- **Fields:** symbol, ltp, bid, ask, open, high, low, volume, oi, lotsize, tick_size, label (ATM/ITM/OTM)
- **Use for:** Liquidity, spread width, OI at strike, label for CE/PE

#### OI Tracker
- **Fields:** CE/PE OI per strike, total CE/PE OI, PCR (put-call ratio)
- **Use for:** Sentiment bias (CE vs PE), conviction at strike

#### Max Pain
- **Fields:** max_pain_strike, pain per strike
- **Use for:** Mean-reversion bias (spot vs max pain)

#### GEX Dashboard
- **Fields:** Net GEX per strike, top gamma strikes, OI walls
- **Use for:** Price magnets, support/resistance, flip zones

#### IV Smile / IV Chart
- **Fields:** ATM IV, IV by strike, IV percentile
- **Use for:** TP/SL width, position sizing, expected move

#### Straddle Chart
- **Fields:** ATM straddle price, synthetic futures, implied move
- **Use for:** Fair value, expected range

#### OI Profile
- **Fields:** OI butterfly, daily OI change per strike
- **Use for:** Conviction shifts, unwinding signals

---

## 4. How Options Data Improves Scalping

### 4.1 Better Entries

| Signal | Source | Logic |
|--------|--------|-------|
| **PCR bias** | OI Tracker | High PCR → bearish skew → bias CE or reduce PE size. Low PCR → bias PE or reduce CE. |
| **GEX walls** | GEX Dashboard | High GEX strike = magnet. Prefer CE below GEX support, PE above GEX resistance. Avoid fading large OI walls. |
| **Max Pain** | Max Pain | Spot below max pain → bias CE. Spot above → bias PE. Stronger near expiry. |
| **IV regime** | IV Chart/Smile | High IV → wider SL, smaller lots. Low IV → tighter SL, slightly larger lots. |
| **OI buildup** | OI Profile | OI increasing at strike = conviction. OI dropping = profit booking. |

### 4.2 Better Profit Trailing

| Signal | Source | Logic |
|--------|--------|-------|
| **Gamma proximity** | Option Chain / Greeks | Near ATM (high gamma) → trail tighter (1–1.5 pts). OTM → trail wider (2–3 pts). |
| **GEX flip zones** | GEX Dashboard | Approaching flip zone in profit → tighten trail or partial exit. |
| **OI unwinding** | OI Profile | Sharp OI drop at strike while in profit → trail more aggressively. |
| **IV crush** | IV Chart | After events, IV drops. Trail tighter or take profit earlier. |

### 4.3 Cutting Losses Quickly

| Signal | Source | Logic |
|--------|--------|-------|
| **OI buildup against** | OI Profile | OI increasing at strikes moving against you → exit earlier. |
| **Spread width** | Option Chain | Wide bid-ask = illiquid. Avoid new entries; tighten SL on existing. |
| **IV spike** | IV Chart | IV spike = potential chop. Tighten SL or exit. |
| **GEX break** | GEX Dashboard | Price breaks GEX support/resistance against you → exit quickly. |

---

## 5. Proposed Integration Architecture

### 5.1 High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Options Data Layer (NEW)                                  │
│                                                                             │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐               │
│  │ GEX Service     │ │ OI Tracker      │ │ IV Service      │               │
│  │ /gex/api/       │ │ /oitracker/     │ │ /ivchart/api/   │               │
│  │ gex-data        │ │ oi-data,        │ │ iv-data         │               │
│  │                 │ │ maxpain         │ │                 │               │
│  └────────┬────────┘ └────────┬────────┘ └────────┬────────┘               │
│           │                   │                   │                         │
│           └───────────────────┼───────────────────┘                         │
│                               ▼                                             │
│                    ┌──────────────────────────┐                             │
│                    │  Options Context Cache   │                             │
│                    │  Poll: every 30–60 sec   │                             │
│                    │  Fields:                 │                             │
│                    │  - pcr, maxPainStrike   │                             │
│                    │  - atmIV, ivPercentile  │                             │
│                    │  - topGammaStrikes      │                             │
│                    │  - oiChange, gexZones   │                             │
│                    └────────────┬─────────────┘                             │
└────────────────────────────────┼────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Auto-Trade Engine (EXISTING + EXTENSIONS)                 │
│                                                                             │
│  handleAutoTradeTick(side, ltp)                                             │
│       │                                                                     │
│       ├─► Entry: autoCanEnter(side)                                         │
│       │         + optionsContextFilter(side)  ◄── NEW                       │
│       │                                                                     │
│       ├─► Trailing: updateAutoTrailing(side, price)                         │
│       │         + getTrailDistance(gamma, ivPct)  ◄── NEW                   │
│       │                                                                     │
│       └─► Exit: checkVirtualTPSL()                                          │
│                + optionsEarlyExitCheck()  ◄── NEW                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Options Context Object (Proposed)

```javascript
optionsContext = {
  pcr: 1.15,              // Put-Call Ratio
  maxPainStrike: 26200,
  spotVsMaxPain: -50,     // spot - maxPain (negative = below)
  atmIV: 14.5,
  ivPercentile: 65,       // 0–100
  topGammaStrikes: [26200, 26150, 26250],
  gexFlipZones: [26180, 26220],
  oiChangeCE: {...},      // strike -> change
  oiChangePE: {...},
  spreadWidth: 0.10,      // at current strike
  lastUpdated: 1738948800
}
```

### 5.3 New Functions (Proposed)

| Function | Purpose |
|----------|---------|
| `fetchOptionsContext()` | Poll optionchain, oitracker, gex, ivchart; build optionsContext |
| `optionsContextFilter(side)` | Returns true/false: allow entry given PCR, GEX, Max Pain |
| `getTrailDistance(side, gammaLevel, ivPct)` | Dynamic trail distance from gamma and IV |
| `optionsEarlyExitCheck(side, ltp)` | Check OI unwinding, spread, IV; return exit reason or null |
| `applyOptionsBias(side)` | Adjust lot size or block CE/PE based on PCR |

### 5.4 Polling Strategy

- **Frequency:** 30–60 seconds (options data changes slowly)
- **Scope:** Only when auto-trade is ON and symbol/expiry known
- **Fallback:** If fetch fails, skip options filter (don't block trading)

---

## 6. Implementation Roadmap

### Phase 1: Foundation (Low Effort, High Impact)

| Task | Description | Effort |
|------|-------------|--------|
| 1.1 | Add `optionsContext` object and `fetchOptionsContext()` in auto_trading_window.html | Low |
| 1.2 | Integrate PCR-based CE/PE bias (or block one side) in `autoCanEnter()` | Low |
| 1.3 | Use IV percentile to scale TP/SL width | Low |
| 1.4 | Log `optionsContext` snapshot in `recordAutoEntry()` for analytics | Low |

### Phase 2: Entry Filters (Medium Effort)

| Task | Description | Effort |
|------|-------------|--------|
| 2.1 | Call `/oitracker/api/oi-data` and `/oitracker/api/maxpain` | Medium |
| 2.2 | Implement `optionsContextFilter(side)` using PCR, Max Pain | Medium |
| 2.3 | Call `/gex/api/gex-data`; add GEX wall proximity check | Medium |
| 2.4 | Add UI toggle: "Options Filters ON/OFF" | Low |

### Phase 3: Dynamic Trailing (Medium Effort)

| Task | Description | Effort |
|------|-------------|--------|
| 3.1 | Fetch gamma at current strike from option chain or greeks API | Medium |
| 3.2 | Implement `getTrailDistance(gammaLevel, ivPct)` | Medium |
| 3.3 | Integrate into `updateAutoTrailing()` | Low |

### Phase 4: Early Exit Signals (Medium Effort)

| Task | Description | Effort |
|------|-------------|--------|
| 4.1 | Track OI change over time; detect sharp drop | Medium |
| 4.2 | Implement `optionsEarlyExitCheck()` | Medium |
| 4.3 | Add spread-width staleness check | Low |
| 4.4 | Add IV spike check (if IV data available) | Low |

### Phase 5: Backend Integration (Optional)

| Task | Description | Effort |
|------|-------------|--------|
| 5.1 | Server Agent: fetch options context server-side | High |
| 5.2 | Add `/ai_scalper/options-context` endpoint | Medium |
| 5.3 | Cache options context in Redis or memory | Medium |

---

## 7. API Reference for Options Data

### 7.1 Option Chain

```
POST /api/v1/optionchain
Body: { apikey, underlying, exchange, expiry_date, strike_count }
Response: { status, underlying, underlying_ltp, expiry_date, atm_strike, chain[] }
  chain[].strike, chain[].ce.{ symbol, ltp, bid, ask, oi, volume, lotsize }, chain[].pe.{ ... }
```

### 7.2 OI Tracker

```
POST /oitracker/api/oi-data
Body: { underlying, exchange, expiry_date, api_key (or header) }
Response: { pcr, ce_oi, pe_oi, strikes: [{ strike, ce_oi, pe_oi, oi_change_ce, oi_change_pe }] }

POST /oitracker/api/maxpain
Body: { underlying, exchange, expiry_date, api_key }
Response: { max_pain_strike, pain_per_strike: [...] }
```

### 7.3 GEX Dashboard

```
POST /gex/api/gex-data
Body: { underlying, exchange, expiry_date, api_key }
Response: { strikes: [{ strike, net_gex, ce_gex, pe_gex }], top_gamma_strikes }
```

### 7.4 IV Chart

```
POST /ivchart/api/iv-data
Body: { symbol, exchange, underlying, expiry, interval, ... }
Response: { iv_data, atm_iv, ... }
```

### 7.5 IV Smile

```
POST /ivsmile/api/iv-smile-data
Body: { underlying, exchange, expiry_date, ... }
Response: { ce_iv_curve, pe_iv_curve, atm_iv }
```

### 7.6 Straddle

```
POST /straddle/api/straddle-data
Body: { underlying, exchange, expiry, ... }
Response: { straddle_price, synthetic_futures, implied_move }
```

### 7.7 OI Profile

```
POST /oiprofile/api/profile-data
Body: { underlying, exchange, expiry, ... }
Response: { oi_butterfly, oi_change }
```

---

## 8. Appendix: File Map

### Frontend (Jinja2 / HTML)

| File | Purpose |
|------|---------|
| `scalping_interface.html` | Manual scalping dashboard |
| `chart_window.html` | Chart trading, TRIGGER orders |
| `auto_trading_window.html` | Auto-trade engine (~12.8K lines) |
| `mock_replay.html` | Mock replay UI |
| `debug_scalping.html` | Debug tools |

### Frontend (React)

| File | Purpose |
|------|---------|
| `frontend/src/pages/Tools.tsx` | Tools hub (12 tools) |
| `frontend/src/pages/OptionChain.tsx` | Option chain UI |
| `frontend/src/pages/OITracker.tsx` | OI tracker UI |
| `frontend/src/pages/GEXDashboard.tsx` | GEX dashboard |
| `frontend/src/pages/MaxPain.tsx` | Max pain UI |
| `frontend/src/pages/IVChart.tsx` | IV chart |
| `frontend/src/pages/IVSmile.tsx` | IV smile |
| `frontend/src/pages/VolSurface.tsx` | Vol surface |
| `frontend/src/pages/StraddleChart.tsx` | Straddle chart |
| `frontend/src/pages/OIProfile.tsx` | OI profile |
| `frontend/src/pages/AutoTradeAnalytics.tsx` | Auto-trade analytics |
| `frontend/src/pages/AutoTradeModelTuning.tsx` | Model tuning |
| `frontend/src/pages/ManualTradeAnalytics.tsx` | Manual trade analytics |

### Backend Services

| File | Purpose |
|------|---------|
| `services/option_chain_service.py` | Option chain aggregation |
| `services/oi_tracker_service.py` | OI data, Max Pain |
| `services/gex_service.py` | Gamma exposure |
| `services/iv_chart_service.py` | IV / Greeks |
| `services/iv_smile_service.py` | IV smile |
| `services/vol_surface_service.py` | Vol surface |
| `services/oi_profile_service.py` | OI profile |
| `services/ai_scalper/*` | AI scalper agent, risk, execution |

### Blueprints

| File | Purpose |
|------|---------|
| `blueprints/scalping.py` | Scalping routes |
| `blueprints/ai_scalper.py` | AI scalper API |
| `blueprints/gex.py` | GEX API |
| `blueprints/oitracker.py` | OI tracker API |
| `blueprints/ivchart.py` | IV chart API |
| `blueprints/ivsmile.py` | IV smile API |
| `restx_api/option_chain.py` | Option chain REST |

### Related Documentation

| File | Purpose |
|------|---------|
| `docs/SCALPING_FRAMEWORK_DOCUMENTATION.md` | Scalping framework docs |
| `docs/SCALPING_FEATURES_MANUAL.md` | Features manual |
| `ai_scalper_architecture.md` | AI scalper overview |
| `plans/scalping_tool_improvements.md` | Scalping tool improvement plan |
| `.github/copilot-instructions.md` | Copilot instructions |
| `.github/instructions/scalping-auto-trade.instructions.md` | Auto-trade instructions |

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | Feb 2026 | Design Team | Initial design document |

---

**End of Document**
