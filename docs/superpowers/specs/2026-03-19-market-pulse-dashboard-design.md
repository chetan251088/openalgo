# Market Pulse Dashboard — "Should I Be Trading India?"

## Overview

A Bloomberg Terminal-style market dashboard that evaluates the Indian stock market environment (NSE/BSE, Nifty-centric) and outputs a clear YES / CAUTION / NO trading decision for swing and day traders. Includes rule-based stock/F&O screener and provider-agnostic AI analyst commentary.

**Route**: `/market-pulse` (full-width layout, no sidebar)
**Primary user**: Single trader using Zerodha (primary) and Dhan (secondary) brokers
**Core focus**: NIFTY 50, SENSEX, BANKNIFTY

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Data sourcing | Broker APIs (Zerodha/Dhan) for prices + NSE India website for breadth | Self-contained for core data, NSE for market-wide breadth |
| Layout | Full-width, Command Center (Layout B) | Maximizes screen real estate, decision + rules always visible |
| Route | `/market-pulse` under `FullWidthLayout` | Same pattern as scalping dashboard |
| Data pipeline | Single Flask endpoint + server-side cache (30s TTL) | Simple, fast for single-user. No background threads |
| Scoring | Fully automated, no manual input | Zero maintenance. Open dashboard, get answer |
| Transparency | Rules Firing panel shows every scoring rule | User can see exactly how decisions are made |
| Thresholds | Editable constants in `market_pulse_config.py` | Adjustable without touching scoring logic |
| Execution Window | Full implementation with DuckDB state tracking | Real market decisions require robust signal quality |
| Trade ideas | Hybrid: rule-based screener + AI commentary | Deterministic picks + expert analyst voice |
| LLM layer | Provider-agnostic (Claude, OpenAI, Gemini, Ollama) | Flexibility to swap providers via `.env` config |
| Mode toggle | Swing Trading / Day Trading | Day trading adds intraday execution signals |

## Data Sources

### Broker APIs (Zerodha / Dhan)

- **Core indices**: NIFTY 50, SENSEX, BANKNIFTY, India VIX
- **Sector indices** (12): Nifty Bank, IT, FMCG, Auto, Pharma, Metal, PSU Bank, Energy, Financial Services, Realty, Consumer Durables, Media
- **Forex**: USDINR (currency futures on NSE)
- **Historical OHLCV**: Daily data for indices (200 days) for computing MAs, RSI, slopes
- **Nifty 50 constituents**: Daily OHLCV (50 symbols × 50 days) for breakout tracking and execution window
- **Option chain**: Nifty options for Put/Call Ratio computation

### NSE India Website

- **Advance/Decline**: Daily advancers vs decliners (NSE-wide)
- **New Highs/Lows**: 52-week highs vs lows on NSE
- **Market breadth**: % of stocks above key moving averages (if available from NSE endpoints)
- **Pre-open data**: Market sentiment before open

### Derived / Static Data

- **RBI Stance**: Auto-derived from India VIX regime + USDINR trend
- **Event Calendar**: Static JSON file (`data/market_events.json`) updated monthly
- **G-Sec 10Y Yield**: Proxied from bond ETF or manual JSON entry
- **Execution Window state**: Multi-day breakout tracking in DuckDB

## Data Flow

```
Zerodha/Dhan APIs + NSE Website + Static JSON
                    │
                    ▼
    services/market_pulse_service.py
    │ fetch + normalize + cache (30s TTL)
    ▼
    services/market_pulse_scoring.py
    │ 5 category scores + execution window + decision
    ▼
    services/market_pulse_screener.py
    │ rule-based stock/F&O picks
    ▼
    services/market_pulse_analyst.py
    │ LLM commentary (5 min refresh, independent)
    ▼
    blueprints/market_pulse.py → GET /api/v1/marketpulse
    │ single JSON response with everything
    ▼
    React: MarketPulse.tsx (polls every 45s)
```

## Scoring System

### Category Scores (each 0–100)

**Market Quality Score** = weighted average of 5 categories:

#### 1. Volatility Score (25%)

| Sub-component | Weight | Formula |
|---------------|--------|---------|
| VIX Level | 40% | VIX 11–16 → 80–100; 16–22 → 50–80; >25 → 0–30; <10 → 60 (complacency penalty) |
| VIX 5d Slope | 25% | Falling → +20 bonus; Flat → 0; Rising sharply → –30 penalty |
| VIX 1Y Percentile | 20% | Below 30th → 80+; 30–70th → 50–80; Above 80th → 0–30 |
| Nifty PCR | 15% | PCR 0.8–1.3 → 70–90; >1.5 → 50 (extreme fear); <0.6 → 40 (extreme greed) |

#### 2. Momentum Score (25%)

| Sub-component | Weight | Formula |
|---------------|--------|---------|
| Sector Participation | 35% | % of 12 sectors above 20d MA. 12/12 → 100; 8+ → 75; 4–7 → 50; <4 → 20 |
| Leadership Spread | 25% | Top 3 vs Bottom 3 sector 5d return spread. 2–5% → 80; >8% → 40 (too concentrated) |
| Higher Highs % | 25% | % of Nifty 50 stocks at 20d highs. >40% → 90; 20–40% → 65; <10% → 20 |
| Sector Rotation Health | 15% | Leadership rotating (healthy) vs collapsing to 1–2 sectors. Diversity bonus/penalty |

#### 3. Trend Score (20%)

| Sub-component | Weight | Formula |
|---------------|--------|---------|
| Nifty vs MAs | 35% | Above 200d +30; Above 50d +25; Above 20d +20; Below all → 10 |
| BankNifty vs 50d | 20% | Above → +20 (risk-on); Below → –10 (financials weak) |
| Nifty 14d RSI | 25% | 50–65 → 85 (strong); 40–50 → 60; >75 → 50 (overbought); <30 → 30 (oversold) |
| MA Slope | 20% | 50d & 200d both rising → 90; Mixed → 50; Both falling → 15 |

Regime classification derived: Uptrend (above all MAs, rising slopes) | Downtrend (below all, falling) | Chop (mixed signals)

#### 4. Breadth Score (20%)

| Sub-component | Weight | Formula |
|---------------|--------|---------|
| A/D Ratio | 30% | NSE advancers/decliners. >2.0 → 95; 1.2–2.0 → 70; 0.8–1.2 → 50; <0.5 → 15 |
| % Above 50d MA | 30% | Nifty 50 constituents. >70% → 90; 50–70% → 65; 30–50% → 40; <30% → 15 |
| % Above 200d MA | 20% | Nifty 50 constituents. >80% → 90; 60–80% → 70; <40% → 25 |
| New Highs vs Lows | 20% | NSE 52w highs vs lows. Highs > 3× Lows → 90; Highs > Lows → 65; Lows dominate → 20 |

#### 5. Macro / Liquidity Score (10%)

| Sub-component | Weight | Formula |
|---------------|--------|---------|
| USDINR Trend | 35% | Stable/falling → 85; Rising slowly → 55; Spiking → 20 |
| RBI Stance Proxy | 25% | Dovish (VIX low + INR stable) → 85; Neutral → 60; Hawkish (VIX rising + INR weak) → 30 |
| Event Risk | 25% | No events 72h → 90; Minor event → 65; Major event (RBI MPC, Budget, FOMC) → 30 |
| Global Risk Proxy | 15% | Nifty-USDINR correlation: moving together (risk-off) → 30; decoupled → 75 |

### Decision Logic

| Score | Decision | Position Sizing |
|-------|----------|-----------------|
| 80–100 | **YES** | Full position sizing, press risk in liquid Nifty/NSE leaders |
| 60–79 | **CAUTION** | Half size, A+ setups only, focus on relative strength leaders |
| <60 | **NO** | Avoid trading or trade very small, preserve capital |

### Execution Window Score (0–100, separate)

Does NOT change Market Quality Score weighting. Shown prominently and factored into natural-language explanation.

#### Swing Mode Signals

| Signal | Weight | Measurement |
|--------|--------|-------------|
| Breakout Hold Rate | 30% | % of Nifty 50 breakouts (20d high cross) still above pivot after 1–3 days |
| Follow-Through | 30% | Avg gain in sessions 1–3 post-breakout |
| Failure Rate | 20% | % of breakouts reversing within 2 sessions |
| Pullback Buying | 20% | Are touches of 20d MA being bought (bounce rate)? |

#### Day Trading Mode (additional signals)

| Signal | Weight | Measurement |
|--------|--------|-------------|
| Trend Consistency | 25% | Is Nifty closing in upper/lower 25% of daily range? (conviction) |
| Gap Fill Rate | 25% | Are gap-ups holding or fading? (last 5 sessions OHLCV) |
| Sector Follow-Through | 25% | Yesterday's leading sectors still leading today? |
| VIX-Price Divergence | 25% | VIX dropping + price rising = healthy; VIX spiking = traps |

**State storage**: DuckDB table tracking daily breakout events, entry prices, hold status. Updated on each data refresh during market hours. Persists across sessions.

## Trade Ideas

### Equity Ideas (Rule-Based Screener)

Screens Nifty 50 constituents based on current market regime:

- **Uptrend regime**: Stocks breaking out above 20d high with volume, near MA support, showing relative strength vs Nifty
- **Downtrend regime**: Stocks with weakest relative strength, below all MAs (AVOID signals)
- **Chop regime**: Stocks holding above 200d MA with low volatility (HOLD signals)

Each pick includes: Symbol, Signal (BUY/SELL/HOLD/AVOID), Entry, Stop Loss, Target, Conviction (HIGH/MED/LOW), Reason.

### F&O Ideas (Rule-Based Strategy Selection)

Strategy type selected by VIX regime + trend:

| VIX Regime | Trend | Suggested Strategy |
|------------|-------|--------------------|
| Low (<15) | Uptrend | Buy Calls, Bull Call Spreads |
| Low (<15) | Downtrend | Buy Puts, Bear Put Spreads |
| High (>20) | Any | Sell Strangles/Straddles, Iron Condors |
| Moderate | Uptrend | Bull Call Spreads, Sell OTM Puts |
| Moderate | Downtrend | Bear Put Spreads, Sell OTM Calls |

Specific strikes sourced from option chain data. Each idea includes: Instrument, Strategy, Strike(s), Premium, Max Risk, R:R ratio, Rationale.

### AI Analyst Commentary (Provider-Agnostic)

- Receives full market pulse snapshot (all scores, sector data, rules firing, screener picks)
- Generates plain-English analysis tying everything together
- Refreshes every 5 minutes or on-demand (independent of 45s data refresh)
- Does NOT select stocks — only explains why the rule-based picks make sense

## LLM Provider Architecture

Abstract interface with pluggable implementations:

```python
class LLMProvider(ABC):
    def generate(self, prompt: str, system_prompt: str) -> str: ...

class ClaudeProvider(LLMProvider): ...    # Anthropic API
class OpenAIProvider(LLMProvider): ...    # OpenAI / Azure
class GeminiProvider(LLMProvider): ...    # Google Gemini
class OllamaProvider(LLMProvider): ...    # Local models
```

Configuration via `.env`:
```
LLM_PROVIDER=claude          # claude | openai | gemini | ollama
LLM_API_KEY=sk-...           # API key for chosen provider
LLM_MODEL=claude-sonnet-4-6  # Model name
LLM_BASE_URL=                # For Ollama/custom endpoints
```

## UI Layout

### Visual Style

- Dark blue/black background (`#0a0e1a`)
- Monospace font (JetBrains Mono / Fira Code)
- Green (#4ade80) / Red (#f87171) / Amber (#fbbf24) indicators
- Bloomberg Terminal aesthetic: dense, high-signal, minimal chrome

### Layout Structure (Command Center — Layout B)

```
┌──────────────────────────────────────────────────────────────────┐
│ TICKER BAR: NIFTY SENSEX BANKNIFTY VIX USDINR sectors  [SWING│DAY] ●LIVE 12s ago ↻ │
├──────────────────────────────────────────────────────────────────┤
│ ⚠ ALERT: RBI MPC Meeting in 2 days — expect volatility    [✕] │
├─────────────────────┬────────────────────────────────────────────┤
│                     │  ┌──────────┬──────────┬──────────┐       │
│   SHOULD I TRADE?   │  │VOLATILITY│ MOMENTUM │  TREND   │       │
│      ╔═══╗          │  │   82     │   75     │   85     │       │
│      ║YES║          │  ├──────────┼──────────┼──────────┤       │
│      ╚═══╝          │  │ BREADTH  │  MACRO   │EXECUTION │       │
│  Quality:78 Exec:72 │  │   68     │   71     │   72     │       │
│                     │  └──────────┴──────────┴──────────┘       │
│  TERMINAL ANALYSIS  │                                           │
│  Strong uptrend...  │  SECTOR HEATMAP          [1D│5D│20D]     │
│                     │  BANK ████████████ +1.2%                  │
│  RULES FIRING       │  AUTO ██████████  +0.8%                   │
│  ✓ VIX moderate     │  IT   ████       -0.5%                    │
│  ✓ Nifty > all MAs  │  METAL██████     -0.9%                    │
│  ⚠ Breadth narrow   │                                           │
│  ✓ USDINR stable    │  SCORE CONTRIBUTION BAR                   │
│                     │  [VOL 20.5|MOM 18.8|TRD 17|BRD 13.6|MAC] │
├─────────────────────┴────────────────────────────────────────────┤
│ EQUITY IDEAS                                                     │
│ HDFCBANK  BUY  1695  SL:1660  TGT:1780  HIGH  Breakout+sector  │
│ TATAMOTORS BUY 985   SL:960   TGT:1040  HIGH  Pullback to 50d  │
│ INFY     AVOID  —    —        —         —     IT sector lagging │
├──────────────────────────────────────────────────────────────────┤
│ F&O IDEAS                     VIX:Low | Regime:Uptrend          │
│ NIFTY    Buy CE    24900CE    ₹185    R:R 1:2.5  Low VIX+trend │
│ BANKNIFTY Bull Spread 52000/52500CE  ₹220  R:R 1:1.8  Leader   │
└──────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Purpose |
|-----------|---------|
| `TickerBar.tsx` | Scrolling ticker with index prices, mode toggle, LIVE indicator, refresh |
| `AlertBanner.tsx` | Event risk warning (dismissible), shown when events within 72 hours |
| `HeroDecision.tsx` | YES/CAUTION/NO badge with Market Quality + Execution scores |
| `TerminalAnalysis.tsx` | AI-generated plain-English summary |
| `RulesFiring.tsx` | Transparent list of every scoring rule with ✓/⚠ status |
| `ScorePanel.tsx` | Reusable score card (used 6×) with value, direction, metrics, progress bar |
| `SectorHeatmap.tsx` | Horizontal bar chart sorted by performance, 1D/5D/20D toggle |
| `ScoreBreakdown.tsx` | Stacked contribution bar showing each category's weight in total score |
| `EquityIdeas.tsx` | Stock picks table with BUY/SELL/HOLD/AVOID signals |
| `FnoIdeas.tsx` | F&O strategy recommendations table |
| `ModeSwitcher.tsx` | Swing / Day Trading toggle |

## File Structure

### Backend (Python)

```
blueprints/market_pulse.py              # Flask routes: page + API
restx_api/market_pulse.py               # GET /api/v1/marketpulse REST endpoint

services/market_pulse_service.py        # Data fetcher + aggregator (broker + NSE)
services/market_pulse_scoring.py        # Scoring engine (5 categories + execution window)
services/market_pulse_screener.py       # Rule-based stock/F&O screener
services/market_pulse_analyst.py        # LLM analyst layer (provider-agnostic)
services/market_pulse_nse.py            # NSE India website data fetcher
services/market_pulse_config.py         # All scoring thresholds + weights

data/market_events.json                 # Event calendar (RBI MPC, Budget, CPI, FOMC)
data/nifty50_constituents.json          # Nifty 50 stock list with sector mappings
data/sector_indices.json                # 12 sector index symbols for broker API
```

### Frontend (React + TypeScript)

```
frontend/src/pages/MarketPulse.tsx                    # Main page component

frontend/src/components/market-pulse/
  TickerBar.tsx                                        # Scrolling ticker
  AlertBanner.tsx                                      # Event risk warning
  HeroDecision.tsx                                     # Decision badge
  TerminalAnalysis.tsx                                 # AI summary
  RulesFiring.tsx                                      # Scoring rules list
  ScorePanel.tsx                                       # Reusable score card
  SectorHeatmap.tsx                                    # Sector performance bars
  ScoreBreakdown.tsx                                   # Contribution bar
  EquityIdeas.tsx                                      # Stock picks table
  FnoIdeas.tsx                                         # F&O ideas table
  ModeSwitcher.tsx                                     # Swing/Day toggle

frontend/src/api/market-pulse.ts                       # API client
frontend/src/hooks/useMarketPulse.ts                   # TanStack Query hook (45s polling)
```

### Static Data

```
data/market_events.json                                # Updated monthly
data/nifty50_constituents.json                         # Updated on index rebalance
data/sector_indices.json                               # Rarely changes
```

## API Response Shape

```json
{
  "status": "success",
  "data": {
    "decision": "YES",
    "market_quality_score": 78,
    "execution_window_score": 72,
    "mode": "swing",
    "scores": {
      "volatility":  { "score": 82, "weight": 0.25, "direction": "healthy",  "rules": [...] },
      "momentum":    { "score": 75, "weight": 0.25, "direction": "strong",   "rules": [...] },
      "trend":       { "score": 85, "weight": 0.20, "direction": "uptrend",  "rules": [...] },
      "breadth":     { "score": 68, "weight": 0.20, "direction": "moderate", "rules": [...] },
      "macro":       { "score": 71, "weight": 0.10, "direction": "stable",   "rules": [...] }
    },
    "ticker": { ... },
    "sectors": [ ... ],
    "alerts": [ ... ],
    "equity_ideas": [ ... ],
    "fno_ideas": [ ... ],
    "analysis": "...",
    "execution_details": { ... },
    "updated_at": "2026-03-19T10:15:32+05:30",
    "cache_ttl": 30
  }
}
```

## Technical Requirements

- **Auto-refresh**: Frontend polls every 45 seconds via TanStack Query
- **Server cache**: 30-second TTL on all data (in-memory dict)
- **AI commentary**: Separate 5-minute refresh cycle (independent of data)
- **Loading states**: Skeleton components while fetching
- **Error handling**: Graceful degradation per data source (mark as "Data unavailable")
- **Execution Window state**: DuckDB table persists across sessions
- **Performance**: Speed is critical — aggressive caching, no unnecessary re-renders

## Environment Variables

```
# LLM Analyst Configuration
LLM_PROVIDER=claude              # claude | openai | gemini | ollama
LLM_API_KEY=                     # API key for chosen provider
LLM_MODEL=claude-sonnet-4-6     # Model name
LLM_BASE_URL=                    # For Ollama/custom endpoints
```
