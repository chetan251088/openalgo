# Options Simulator — Design Spec

**Date**: 2026-03-27
**Location**: Market Pulse → Options section → "Simulator" tab (new third tab)

## Summary

A Thalex-style multi-leg options simulator for NIFTY, BANKNIFTY, and SENSEX index options.
Users pick a strategy (or build custom legs), get live Greeks per leg, then run a Monte Carlo
Brownian motion simulation to see price path cloud + PnL histogram + statistics.

## Placement

- Wrap existing `OptionsPositioning` + `OptionsGreeksDashboard` in a shadcn `Tabs` component
  in `MarketPulse.tsx`, adding a third "Simulator" tab
- Tabs: **Positioning** | **Greeks** | **Simulator**

## Underlyings & Exchanges

| Underlying  | Option Exchange | Spot Exchange |
|-------------|----------------|---------------|
| NIFTY       | NFO            | NSE_INDEX     |
| BANKNIFTY   | NFO            | NSE_INDEX     |
| SENSEX      | BFO            | BSE_INDEX     |

## Components & Files

### New files
1. `frontend/src/lib/optionStrategies.ts` — 18 strategy definitions (relative-strike based)
2. `frontend/src/workers/simulationWorker.ts` — Web Worker: GBM Monte Carlo
3. `frontend/src/components/market-pulse/OptionsSimulator.tsx` — main component

### Modified files
4. `frontend/src/pages/MarketPulse.tsx` — wrap options section in Tabs

## Strategy Library (18 strategies)

Legs defined as `{ action, qty, type, strikeOffset }` where `strikeOffset` is # of strikes from ATM.

| Strategy           | Legs |
|--------------------|------|
| Long Call          | Buy 1 CE @ ATM |
| Long Put           | Buy 1 PE @ ATM |
| Short Call         | Sell 1 CE @ ATM |
| Short Put          | Sell 1 PE @ ATM |
| Long Straddle      | Buy 1 CE @ ATM + Buy 1 PE @ ATM |
| Short Straddle     | Sell 1 CE @ ATM + Sell 1 PE @ ATM |
| Long Strangle      | Buy 1 CE @ ATM+1 + Buy 1 PE @ ATM-1 |
| Short Strangle     | Sell 1 CE @ ATM+1 + Sell 1 PE @ ATM-1 |
| Bull Call Spread   | Buy 1 CE @ ATM + Sell 1 CE @ ATM+1 |
| Bear Call Spread   | Sell 1 CE @ ATM + Buy 1 CE @ ATM+1 |
| Bull Put Spread    | Sell 1 PE @ ATM-1 + Buy 1 PE @ ATM-2 |
| Bear Put Spread    | Buy 1 PE @ ATM + Sell 1 PE @ ATM-1 |
| Iron Condor        | Sell CE@ATM+1, Buy CE@ATM+2, Sell PE@ATM-1, Buy PE@ATM-2 |
| Iron Butterfly     | Sell CE@ATM, Sell PE@ATM, Buy CE@ATM+2, Buy PE@ATM-2 |
| Jade Lizard        | Sell PE@ATM-1, Sell CE@ATM, Buy CE@ATM+1 |
| Synthetic Long     | Buy CE@ATM + Sell PE@ATM |
| Synthetic Short    | Sell CE@ATM + Buy PE@ATM |
| Ratio Call Spread  | Buy 1 CE@ATM + Sell 2 CE@ATM+1 |

## Data Flow

1. User selects underlying + expiry
2. `useOptionChainLive` provides: `chain.strikes[]`, `atm_strike`, `underlying_ltp`, live LTP per option
3. Strategy selection → resolve legs: map `strikeOffset` → nearest actual strike from chain
4. Greeks (IV, Δ, Γ, Θ, ν) fetched via `/api/v1/optiongreeks` per leg (on leg add/change)
5. ATM IV read from ATM CE Greeks as the default simulation σ
6. "Run Simulation" → postMessage to Web Worker → returns `{ paths, finalPnls }`
7. Canvas renders Brownian motion cloud; histogram rendered on second canvas
8. Stats computed from `finalPnls` array

## OptionsSimulator Component Layout

```
┌─ Underlying selector | Expiry selector | Spot | ATM | ATM IV ─┐
│                                                                  │
│  [18 strategy pill buttons]                                      │
│                                                                  │
│ ┌─ Leg Builder ──────────────────────────── [+ Add Leg] ──────┐ │
│ │  Action | Qty | Type | Strike | LTP | IV | Δ | Γ | Θ | ν | × │ │
│ │  BUY     1    CALL   24500    182   18%  .49 .003 -14  28    │ │
│ │  BUY     1    PUT    24500    168   18%  -.51 .003 -14  28   │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  NET: Premium -350 | Δ -0.02 | Γ +0.006 | Θ -28 | ν +56        │
│       Breakeven: 24,150 / 24,850                                 │
│                                                                  │
│  σ [18.2%] | μ [0] | Paths [500] | Days [7]  [▶ Run]           │
│                                                                  │
│ ┌─ Price Path Cloud (canvas) ──────┐ ┌─ PnL Distribution ─────┐ │
│ │  Brownian motion fan             │ │  Histogram (canvas)     │ │
│ │  Blue=profit, Red=loss           │ │  Stats: Avg/Med/Win%    │ │
│ │  White=mean, Yellow=breakeven    │ │  Max Loss / Max Payoff  │ │
│ └──────────────────────────────────┘ └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## Simulation Worker (GBM)

Input: `{ spot, sigma, mu, nPaths, steps, legs: LegDef[] }`

```
LegDef: { action: 'buy'|'sell', qty: number, type: 'CE'|'PE', strike: number, premium: number }
```

Algorithm:
- For each path i: generate GBM price path S(t) = S0 * exp((μ - σ²/2)*dt + σ*√dt*Z)
- Terminal PnL = Σ legs: sign(action) * qty * (payoff(S_T, strike, type) - premium)
- Return: `{ paths: number[][], finalPnls: number[] }`

## API Authentication

- Use `useAuthStore().apiKey` (with fallback fetch from `/api/websocket/apikey`)
- Pass to `useOptionChainLive` for chain data
- Include in `/api/v1/optiongreeks` POST body

## Theme

- Zinc/slate app theme (`bg-zinc-900`, `border-zinc-800`, etc.)
- Strategy pills: `bg-zinc-800 hover:bg-zinc-700`, active: `bg-blue-900 border-blue-600`
- Buy toggle: green (`bg-green-700`), Sell: red (`bg-red-800`)
- Call: blue (`bg-blue-800`), Put: purple (`bg-purple-800`)
- Canvas background: `#09090b`

## Error States

- No chain data: show skeleton/loading state
- Worker not supported: fall back to main-thread simulation (warn in console)
- Greeks API fail: show "—" for Greeks, simulation still works with LTP as premium

## Out of Scope (v1)

- Calendar spreads (cross-expiry)
- Position sizing / lot multiplier
- Historical backtest
- Save/export strategy
