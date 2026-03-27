# Options Simulator — Sensibull-Parity Design Spec
**Date:** 2026-03-27
**Status:** Approved

## Overview

Upgrade the Options Strategy Simulator in Market Pulse from a pure Monte Carlo tool to a professional-grade platform with deterministic payoff curves, BSM scenario analysis, statistical POP, approximate margin, and per-leg value breakdown. Monte Carlo tab is preserved as a unique selling point.

## Architecture (Option B — New Tab + Extracted Modules)

### New Files
- `frontend/src/lib/bsm.ts` — Pure BSM math engine (no side effects, fully tree-shakeable)
- `frontend/src/components/market-pulse/PayoffChart.tsx` — Deterministic payoff canvas + scenario sliders

### Modified Files
- `frontend/src/components/market-pulse/OptionsSimulator.tsx` — Inner Payoff/Monte Carlo tabs, new leg columns, POP stat, margin badge
- `simulationWorker.ts` — **unchanged**

## BSM Engine (`bsm.ts`)

Pure functions, no imports, no state:

```ts
normCDF(x: number): number                    // Abramowitz & Stegun polynomial approximation
d1(S, K, T, r, σ): number
d2(S, K, T, r, σ): number
bsmPrice(type: 'CE'|'PE', S, K, T, r, σ): number
calcPOP(breakevens: number[], spot, T, σ): number  // via normCDF on d2 at each BE
calcSDCone(spot, σ, maxDays): SDConePoint[]         // 1SD+2SD at each day 0..maxDays
calcIntrinsic(type, spot, strike): number
calcTimeValue(ltp, intrinsic): number
calcEstMargin(legs, spot, underlying): number       // client-side SPAN approximation
```

**Constants:**
- `r = 0.065` (India 10-yr G-Sec yield, risk-free rate default)
- Lot sizes: NIFTY=25, BANKNIFTY=15, SENSEX=20

**Margin estimation logic:**
- Defined-risk (max_loss is finite and positive): `maxLoss × 1.5`
- Undefined-risk (naked short): `spot × lotSize × 0.15` per naked short lot
- Combined strategies: sum of individual short leg margin, reduced by defined-risk discount

**normCDF:** Uses Abramowitz & Stegun 7-coefficient polynomial (error < 3×10⁻⁷), no external deps.

## Payoff Chart (`PayoffChart.tsx`)

Canvas-based deterministic chart, redraws instantly on slider change (pure math, no worker).

### Props
```ts
interface PayoffChartProps {
  legs: SimulatorLeg[]
  spot: number
  sigma: number        // from simParams (ATM IV or VIX-derived)
  dte: number          // days to expiry
  breakevens: number[]
}
```

### What it renders
1. **SD Cone** — shaded bands at 1SD (rgba green/red 0.12) and 2SD (0.07) behind the curves
2. **Expiry payoff** (T=0) — yellow line, intrinsic-only P&L
3. **T+n BSM curve** — cyan line, theoretical P&L at `targetDTE` using BSM prices
4. **Current spot** — vertical white dashed line
5. **Breakeven labels** — at zero-cross points on x-axis
6. **Zero line** — horizontal dashed amber

### Scenario Controls (below canvas)
- `Target Date` slider: DTE → 0 (live redraw)
- `IV Offset` slider: −30% to +30% bump (live redraw)
- Both debounced to rAF for performance

### X-axis range: spot ± 12%
### Y-axis: strategy P&L (₹ per lot, qty-weighted)

## OptionsSimulator.tsx Changes

### Inner tab row
Below the net Greeks row, before simulation controls:
```
[ Payoff Chart ] [ Monte Carlo ]
```

### Leg table additions (2 columns after LTP)
- `Intr.` — intrinsic value: CE=max(0,S-K), PE=max(0,K-S)
- `TV` — time value: premium − intrinsic (always ≥ 0 if correctly priced)

### Stats panel additions
- **POP** — `calcPOP(breakevens, spot, T, σ)` shown as `68.4%`, colored green/red ≥/< 50%
- **Est. Margin** — `calcEstMargin(legs, spot, underlying)` shown as `₹82,500`, amber color

### Performance constraints
- Payoff chart redraws are rAF-gated (same pattern as existing path/hist canvases)
- BSM calls per redraw: O(spotPoints × legs) = ~200 × 4 = 800 calls — negligible (< 1ms)
- No new network calls on slider move
- `PayoffChart` wrapped in `React.memo` with custom `areEqual` (compares legs array length + spot + sigma + dte)

## Non-Goals
- Backend margin route (client-side approximation is sufficient)
- Real-time BSM live-updating on every tick (only updates on user action or leg change)
- Greeks surface (vol surface / smile) — out of scope
