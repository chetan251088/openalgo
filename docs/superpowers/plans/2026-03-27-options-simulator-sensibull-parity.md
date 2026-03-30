# Options Simulator Sensibull-Parity Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic BSM payoff chart, SD cones, scenario sliders, statistical POP, intrinsic/time value breakdown, and approximate margin to the Options Simulator — all client-side, no backend changes.

**Architecture:** New `bsm.ts` pure-math lib → new `PayoffChart.tsx` canvas component → `OptionsSimulator.tsx` gains an inner Payoff|Monte Carlo tab, two new leg columns (Intr./TV), and updated stats (POP + Est. Margin). simulationWorker.ts is untouched.

**Tech Stack:** TypeScript, React, Canvas API, Black-Scholes-Merton math (zero external libs), React.memo with custom areEqual for perf.

---

## Chunk 1: BSM Math Engine

### Task 1: Create `frontend/src/lib/bsm.ts`

**Files:**
- Create: `frontend/src/lib/bsm.ts`

- [ ] **Step 1: Write the complete bsm.ts**

```typescript
// frontend/src/lib/bsm.ts
// Pure BSM math engine — no React, no side effects, fully tree-shakeable.

export const RISK_FREE_RATE = 0.065 // India 10-yr G-Sec yield (annualised)

export const LOT_SIZES: Record<string, number> = {
  NIFTY: 25,
  BANKNIFTY: 15,
  SENSEX: 20,
}

export interface BsmLeg {
  action: 'buy' | 'sell'
  qty: number
  type: 'CE' | 'PE'
  strike: number
  premium: number // per unit, positive always
}

// ---------------------------------------------------------------------------
// Core math
// ---------------------------------------------------------------------------

/**
 * Cumulative standard normal CDF.
 * Abramowitz & Stegun 7-coefficient polynomial (error < 3×10⁻⁷).
 */
export function normCDF(x: number): number {
  if (x <= -6) return 0
  if (x >= 6) return 1
  const neg = x < 0
  const z = neg ? -x : x
  const t = 1 / (1 + 0.2316419 * z)
  const poly =
    t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
  const pdf = Math.exp(-0.5 * z * z) / 2.5066282746310002 // 1/sqrt(2π)
  const p = 1 - pdf * poly
  return neg ? 1 - p : p
}

export function d1(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) return S >= K ? 10 : -10
  return (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T))
}

export function d2(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) return S >= K ? 10 : -10
  return d1(S, K, T, r, sigma) - sigma * Math.sqrt(T)
}

/**
 * Black-Scholes-Merton option price.
 * Returns intrinsic value when T ≤ 0 (at/past expiry).
 */
export function bsmPrice(
  type: 'CE' | 'PE',
  S: number,
  K: number,
  T: number,
  r: number,
  sigma: number,
): number {
  if (T <= 0) return type === 'CE' ? Math.max(S - K, 0) : Math.max(K - S, 0)
  const _d1 = d1(S, K, T, r, sigma)
  const _d2 = d2(S, K, T, r, sigma)
  if (type === 'CE') return S * normCDF(_d1) - K * Math.exp(-r * T) * normCDF(_d2)
  return K * Math.exp(-r * T) * normCDF(-_d2) - S * normCDF(-_d1)
}

// ---------------------------------------------------------------------------
// Value breakdown
// ---------------------------------------------------------------------------

export function calcIntrinsic(type: 'CE' | 'PE', spot: number, strike: number): number {
  return type === 'CE' ? Math.max(spot - strike, 0) : Math.max(strike - spot, 0)
}

export function calcTimeValue(premium: number, intrinsic: number): number {
  return Math.max(0, premium - intrinsic)
}

// ---------------------------------------------------------------------------
// SD Cone
// ---------------------------------------------------------------------------

export interface SDConePoint {
  spotLo2: number
  spotLo1: number
  spotHi1: number
  spotHi2: number
}

/**
 * 1SD and 2SD expected spot ranges at a given DTE using log-normal quantiles.
 * hi/lo = spot × exp(±n × σ × √T)
 */
export function calcSDCone(spot: number, sigma: number, dte: number): SDConePoint {
  const T = Math.max(0, dte) / 365
  const m1 = sigma * Math.sqrt(T)
  const m2 = 2 * m1
  return {
    spotLo2: spot * Math.exp(-m2),
    spotLo1: spot * Math.exp(-m1),
    spotHi1: spot * Math.exp(m1),
    spotHi2: spot * Math.exp(m2),
  }
}

// ---------------------------------------------------------------------------
// Probability of Profit
// ---------------------------------------------------------------------------

/**
 * Probability of Profit at expiry.
 *
 * Algorithm:
 *  1. Scan expiry payoff log-spaced across [spot×0.01, spot×4.0] to find profit regions.
 *  2. For each contiguous profit region [lo, hi]:
 *       P(lo < S_T < hi) = N(d2(spot, lo)) − N(d2(spot, hi))
 *     This uses the risk-neutral log-normal: P(S_T > K) = N(d2).
 *  3. Sum over all profit regions.
 */
export function calcPOP(
  legs: BsmLeg[],
  spot: number,
  T: number,
  sigma: number,
  r = RISK_FREE_RATE,
): number {
  if (T <= 0 || !legs.length || spot <= 0 || sigma <= 0) return 0

  const SAMPLES = 2000
  const xLo = spot * 0.01
  const xHi = spot * 4.0
  const logStep = Math.log(xHi / xLo) / SAMPLES

  const payoff = (S: number) =>
    legs.reduce((acc, leg) => {
      const intr = leg.type === 'CE' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0)
      return acc + (leg.action === 'buy' ? 1 : -1) * leg.qty * (intr - leg.premium)
    }, 0)

  const regions: Array<{ start: number; end: number }> = []
  let regionStart: number | null = null

  for (let i = 0; i <= SAMPLES; i++) {
    const S = xLo * Math.exp(logStep * i)
    const profit = payoff(S) > 0
    if (profit && regionStart === null) {
      regionStart = S
    } else if (!profit && regionStart !== null) {
      regions.push({ start: regionStart, end: S })
      regionStart = null
    }
  }
  if (regionStart !== null) regions.push({ start: regionStart, end: Infinity })

  let pop = 0
  for (const { start, end } of regions) {
    const pStart = normCDF(d2(spot, start, T, r, sigma))
    const pEnd = end === Infinity ? 0 : normCDF(d2(spot, end, T, r, sigma))
    pop += pStart - pEnd
  }

  return Math.min(1, Math.max(0, pop))
}

// ---------------------------------------------------------------------------
// Estimated Margin (client-side SPAN approximation)
// ---------------------------------------------------------------------------

/**
 * Approximate margin requirement.
 *
 * Defined-risk (max loss is finite and < spot × 0.8 per unit):
 *   maxLossPerUnit × lotSize × 1.5
 *
 * Undefined-risk (naked short):
 *   spot × lotSize × totalNakedShortQty × 0.15
 *
 * maxLoss is scanned at expiry across spot × [0.5, 2.0].
 */
export function calcEstMargin(legs: BsmLeg[], spot: number, underlying: string): number {
  const lotSize = LOT_SIZES[underlying] ?? 50

  let maxLossPerUnit = 0
  const SAMPLES = 500
  for (let i = 0; i <= SAMPLES; i++) {
    const S = spot * 0.5 + (spot * 1.5 * i) / SAMPLES
    const pnl = legs.reduce((acc, leg) => {
      const intr = leg.type === 'CE' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0)
      return acc + (leg.action === 'buy' ? 1 : -1) * leg.qty * (intr - leg.premium)
    }, 0)
    if (pnl < -maxLossPerUnit) maxLossPerUnit = -pnl
  }

  if (maxLossPerUnit > 0 && maxLossPerUnit < spot * 0.8) {
    return Math.round(maxLossPerUnit * lotSize * 1.5)
  }

  const nakedQty = legs
    .filter((l) => l.action === 'sell')
    .reduce((acc, l) => acc + l.qty, 0)

  return nakedQty > 0 ? Math.round(spot * lotSize * nakedQty * 0.15) : 0
}
```

- [ ] **Step 2: Verify the file has no TypeScript errors**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo/frontend
npx tsc --noEmit --project tsconfig.app.json 2>&1 | head -30
```
Expected: no errors referencing `bsm.ts`.

- [ ] **Step 3: Commit**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo
git add frontend/src/lib/bsm.ts
git commit -m "feat: add BSM math engine (normCDF, bsmPrice, calcPOP, calcSDCone, calcEstMargin)"
```

---

## Chunk 2: Payoff Chart Component

### Task 2: Create `frontend/src/components/market-pulse/PayoffChart.tsx`

**Files:**
- Create: `frontend/src/components/market-pulse/PayoffChart.tsx`

- [ ] **Step 1: Write the complete PayoffChart.tsx**

```typescript
// frontend/src/components/market-pulse/PayoffChart.tsx
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { bsmPrice, calcSDCone, RISK_FREE_RATE, type BsmLeg } from '@/lib/bsm'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PayoffChartProps {
  legs: BsmLeg[]
  spot: number
  sigma: number       // annualised vol as decimal (e.g. 0.18)
  dte: number         // days to expiry (slider max)
  breakevens: number[]
}

// ---------------------------------------------------------------------------
// Canvas helpers
// ---------------------------------------------------------------------------

function setupCanvas(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  canvas.width = rect.width * dpr
  canvas.height = rect.height * dpr
  const ctx = canvas.getContext('2d')!
  ctx.scale(dpr, dpr)
  return ctx
}

// ---------------------------------------------------------------------------
// Core draw function (pure — no React state read, safe to call from rAF)
// ---------------------------------------------------------------------------

function drawPayoff(
  canvas: HTMLCanvasElement,
  legs: BsmLeg[],
  spot: number,
  sigma: number,
  dte: number,
  targetDte: number,
  ivOffsetPct: number,   // e.g. 5 = +5% IV bump
  breakevens: number[],
) {
  const ctx = setupCanvas(canvas)
  const W = canvas.getBoundingClientRect().width
  const H = canvas.getBoundingClientRect().height

  ctx.fillStyle = '#09111a'
  ctx.fillRect(0, 0, W, H)

  if (!legs.length || !spot) return

  const sigmaAdj = Math.max(0.01, sigma + ivOffsetPct / 100)
  const T_target = Math.max(0, targetDte) / 365
  const POINTS = 200
  const xLo = spot * 0.88
  const xHi = spot * 1.12

  const spotPrices: number[] = []
  const expiryPnl: number[] = []
  const targetPnl: number[] = []

  for (let i = 0; i <= POINTS; i++) {
    const S = xLo + ((xHi - xLo) * i) / POINTS
    spotPrices.push(S)

    // Expiry (T=0): intrinsic P&L
    expiryPnl.push(
      legs.reduce((acc, leg) => {
        const intr = leg.type === 'CE' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0)
        return acc + (leg.action === 'buy' ? 1 : -1) * leg.qty * (intr - leg.premium)
      }, 0),
    )

    // T+n: BSM theoretical P&L
    if (T_target > 0) {
      targetPnl.push(
        legs.reduce((acc, leg) => {
          const price = bsmPrice(leg.type, S, leg.strike, T_target, RISK_FREE_RATE, sigmaAdj)
          return acc + (leg.action === 'buy' ? 1 : -1) * leg.qty * (price - leg.premium)
        }, 0),
      )
    }
  }

  const allPnl = [...expiryPnl, ...targetPnl]
  const rawMin = Math.min(...allPnl)
  const rawMax = Math.max(...allPnl)
  const yPad = Math.max(Math.abs(rawMax), Math.abs(rawMin)) * 0.18 || 100
  const yLo = rawMin - yPad
  const yHi = rawMax + yPad
  const yRange = yHi - yLo || 1

  const PAD = { l: 20, r: 20, t: 22, b: 20 }
  const iW = W - PAD.l - PAD.r
  const iH = H - PAD.t - PAD.b

  const toX = (S: number) => PAD.l + ((S - xLo) / (xHi - xLo)) * iW
  const toY = (pnl: number) => PAD.t + iH - ((pnl - yLo) / yRange) * iH

  // SD cones — use targetDte if set, else current dte
  const coneDte = targetDte > 0 ? targetDte : dte
  const cone = calcSDCone(spot, sigmaAdj, coneDte)

  const clampX = (x: number) => Math.max(PAD.l, Math.min(PAD.l + iW, x))
  const lo2X = clampX(toX(cone.spotLo2))
  const lo1X = clampX(toX(cone.spotLo1))
  const hi1X = clampX(toX(cone.spotHi1))
  const hi2X = clampX(toX(cone.spotHi2))

  // 2SD outer bands
  ctx.fillStyle = 'rgba(148,163,184,0.05)'
  ctx.fillRect(lo2X, PAD.t, lo1X - lo2X, iH)
  ctx.fillRect(hi1X, PAD.t, hi2X - hi1X, iH)

  // 1SD inner bands
  ctx.fillStyle = 'rgba(148,163,184,0.11)'
  ctx.fillRect(lo1X, PAD.t, clampX(toX(spot)) - lo1X, iH)
  ctx.fillRect(clampX(toX(spot)), PAD.t, hi1X - clampX(toX(spot)), iH)

  // SD labels
  ctx.font = '8px monospace'
  ctx.fillStyle = 'rgba(120,155,175,0.75)'
  if (lo1X > PAD.l + 6) ctx.fillText('1σ', lo1X - 14, PAD.t + 11)
  if (hi1X < PAD.l + iW - 6) ctx.fillText('1σ', hi1X + 3, PAD.t + 11)
  ctx.fillStyle = 'rgba(100,130,150,0.6)'
  if (lo2X > PAD.l + 4) ctx.fillText('2σ', lo2X - 14, PAD.t + 11)
  if (hi2X < PAD.l + iW - 4) ctx.fillText('2σ', hi2X + 3, PAD.t + 11)

  // Zero line
  const zeroY = toY(0)
  if (zeroY >= PAD.t && zeroY <= PAD.t + iH) {
    ctx.beginPath()
    ctx.setLineDash([4, 4])
    ctx.strokeStyle = 'rgba(253,230,138,0.3)'
    ctx.lineWidth = 1
    ctx.moveTo(PAD.l, zeroY)
    ctx.lineTo(PAD.l + iW, zeroY)
    ctx.stroke()
    ctx.setLineDash([])
  }

  // T+n BSM curve (cyan, behind expiry)
  if (targetPnl.length && T_target > 0) {
    ctx.beginPath()
    ctx.strokeStyle = '#22d3ee'
    ctx.lineWidth = 2
    ctx.globalAlpha = 0.82
    targetPnl.forEach((pnl, i) => {
      const x = toX(spotPrices[i])
      const y = toY(pnl)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()
    ctx.globalAlpha = 1
  }

  // Expiry payoff (yellow — drawn on top)
  ctx.beginPath()
  ctx.strokeStyle = '#fde68a'
  ctx.lineWidth = 2.5
  expiryPnl.forEach((pnl, i) => {
    const x = toX(spotPrices[i])
    const y = toY(pnl)
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.stroke()

  // Current spot vertical line
  const spotX = toX(spot)
  ctx.beginPath()
  ctx.setLineDash([3, 3])
  ctx.strokeStyle = 'rgba(255,255,255,0.4)'
  ctx.lineWidth = 1
  ctx.moveTo(spotX, PAD.t)
  ctx.lineTo(spotX, PAD.t + iH)
  ctx.stroke()
  ctx.setLineDash([])

  // Breakeven verticals
  breakevens.forEach((be) => {
    if (be < xLo || be > xHi) return
    const bx = toX(be)
    const zy = Math.max(PAD.t, Math.min(PAD.t + iH, zeroY))
    ctx.beginPath()
    ctx.setLineDash([2, 3])
    ctx.strokeStyle = 'rgba(253,230,138,0.55)'
    ctx.lineWidth = 1
    ctx.moveTo(bx, zy - 14)
    ctx.lineTo(bx, zy + 14)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = '#fde68a'
    ctx.font = '8px monospace'
    const label = be.toFixed(0)
    ctx.fillText(label, bx - label.length * 2.4, Math.min(PAD.t + iH - 2, zy + 23))
  })

  // Legend
  ctx.font = '9px monospace'
  ctx.fillStyle = '#fde68a'
  ctx.fillText('Expiry', PAD.l + 4, 15)
  if (T_target > 0) {
    ctx.fillStyle = '#22d3ee'
    ctx.fillText(`T+${targetDte}d BSM`, PAD.l + 50, 15)
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const PayoffChart = memo(
  function PayoffChart({ legs, spot, sigma, dte, breakevens }: PayoffChartProps) {
    const [targetDte, setTargetDte] = useState(() => Math.max(0, dte))
    const [ivOffsetPct, setIvOffsetPct] = useState(0)

    const canvasRef = useRef<HTMLCanvasElement | null>(null)
    const rafRef = useRef<number>(0)

    // Keep targetDte within [0, dte] when dte changes (e.g. expiry switch)
    useEffect(() => {
      setTargetDte((prev) => Math.min(prev, Math.max(0, dte)))
    }, [dte])

    const redraw = useCallback(() => {
      if (!canvasRef.current) return
      cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => {
        if (!canvasRef.current) return
        drawPayoff(canvasRef.current, legs, spot, sigma, dte, targetDte, ivOffsetPct, breakevens)
      })
    }, [legs, spot, sigma, dte, targetDte, ivOffsetPct, breakevens])

    useEffect(() => {
      redraw()
      return () => cancelAnimationFrame(rafRef.current)
    }, [redraw])

    // Redraw on container resize
    useEffect(() => {
      const canvas = canvasRef.current
      if (!canvas) return
      const ro = new ResizeObserver(() => redraw())
      ro.observe(canvas)
      return () => ro.disconnect()
    }, [redraw])

    return (
      <div className="flex flex-col gap-2">
        {/* Canvas */}
        <div
          className="relative overflow-hidden rounded-xl border border-[#1a3140] bg-[#09111a]"
          style={{ height: 220 }}
        >
          <canvas ref={canvasRef} className="w-full h-full" style={{ display: 'block' }} />
          {!legs.length && (
            <div className="absolute inset-0 flex items-center justify-center text-[10px] text-[#4a7b8a]">
              Add legs to see payoff chart
            </div>
          )}
        </div>

        {/* Scenario controls */}
        <div className="rounded-xl border border-[#1a3140] bg-[#09111a] px-3 py-2.5">
          <div className="flex flex-wrap gap-4">
            {/* Target Date */}
            <div className="flex flex-1 min-w-[130px] flex-col gap-1">
              <div className="flex justify-between text-[9px]">
                <span className="text-[#4a7b8a]">Target Date</span>
                <span className="text-[#22d3ee]">T+{targetDte}d</span>
              </div>
              <input
                type="range"
                min={0}
                max={Math.max(1, dte)}
                step={1}
                value={targetDte}
                onChange={(e) => setTargetDte(Number(e.target.value))}
                className="w-full h-1 accent-cyan-400"
              />
              <div className="flex justify-between text-[8px] text-[#2a4a5a]">
                <span>Today</span>
                <span>Expiry (DTE {dte})</span>
              </div>
            </div>

            {/* IV Offset */}
            <div className="flex flex-1 min-w-[130px] flex-col gap-1">
              <div className="flex justify-between text-[9px]">
                <span className="text-[#4a7b8a]">IV Offset</span>
                <span
                  className={
                    ivOffsetPct === 0
                      ? 'text-[#4a7b8a]'
                      : ivOffsetPct > 0
                        ? 'text-[#f87171]'
                        : 'text-[#4ade80]'
                  }
                >
                  {ivOffsetPct > 0 ? '+' : ''}
                  {ivOffsetPct}%
                </span>
              </div>
              <input
                type="range"
                min={-30}
                max={30}
                step={1}
                value={ivOffsetPct}
                onChange={(e) => setIvOffsetPct(Number(e.target.value))}
                className="w-full h-1 accent-cyan-400"
              />
              <div className="flex justify-between text-[8px] text-[#2a4a5a]">
                <span>−30%</span>
                <button
                  type="button"
                  onClick={() => setIvOffsetPct(0)}
                  className="text-[#2a4a5a] hover:text-[#4a7b8a] transition-colors"
                >
                  reset
                </button>
                <span>+30%</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  },
  // Custom areEqual — skip re-render if nothing payoff-relevant changed
  (prev, next) => {
    if (prev.spot !== next.spot || prev.sigma !== next.sigma || prev.dte !== next.dte) return false
    if (prev.legs.length !== next.legs.length || prev.breakevens.length !== next.breakevens.length)
      return false
    for (let i = 0; i < prev.legs.length; i++) {
      const p = prev.legs[i]
      const n = next.legs[i]
      if (
        p.action !== n.action ||
        p.type !== n.type ||
        p.strike !== n.strike ||
        p.premium !== n.premium ||
        p.qty !== n.qty
      )
        return false
    }
    for (let i = 0; i < prev.breakevens.length; i++) {
      if (Math.abs(prev.breakevens[i] - next.breakevens[i]) > 0.5) return false
    }
    return true
  },
)

export default PayoffChart
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo/frontend
npx tsc --noEmit --project tsconfig.app.json 2>&1 | head -30
```
Expected: no errors in `PayoffChart.tsx`.

- [ ] **Step 3: Commit**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo
git add frontend/src/components/market-pulse/PayoffChart.tsx
git commit -m "feat: add PayoffChart component (BSM payoff curves, SD cones, scenario sliders)"
```

---

## Chunk 3: OptionsSimulator Integration

### Task 3: Update `OptionsSimulator.tsx`

This task makes 5 targeted changes to the existing 1,334-line file. Each is described as a precise edit.

**Files:**
- Modify: `frontend/src/components/market-pulse/OptionsSimulator.tsx`

---

#### Change A — Add imports (at top of file, after existing imports)

- [ ] **Step 1: Add imports after `import { Button } from '@/components/ui/button'`**

```typescript
import PayoffChart from '@/components/market-pulse/PayoffChart'
import {
  calcIntrinsic,
  calcTimeValue,
  calcPOP,
  calcEstMargin,
  type BsmLeg,
} from '@/lib/bsm'
```

---

#### Change B — Add `activeSimView` state

- [ ] **Step 2: Add view state after the existing `const [greeks, setGreeks]` line**

Add this line:
```typescript
const [activeSimView, setActiveSimView] = useState<'payoff' | 'montecarlo'>('payoff')
```

---

#### Change C — Add `bsmLegs` + `pop` + `estMargin` memos

- [ ] **Step 3: Add three memos after the existing `netPremium` useMemo block**

```typescript
// Adapt SimulatorLeg[] → BsmLeg[] for bsm.ts functions
const bsmLegs = useMemo<BsmLeg[]>(
  () =>
    legs.map((l) => ({
      action: l.action,
      qty: l.qty,
      type: l.type,
      strike: l.strike,
      premium: l.premium,
    })),
  [legs],
)

// Statistical Probability of Profit (log-normal CDF, not Monte Carlo)
const pop = useMemo(() => {
  if (!bsmLegs.length || !spot || !simParams.sigma || !simParams.days) return null
  return calcPOP(bsmLegs, spot, simParams.days / 365, simParams.sigma)
}, [bsmLegs, spot, simParams.sigma, simParams.days])

// Estimated margin (client-side SPAN approximation)
const estMargin = useMemo(
  () => (bsmLegs.length && spot ? calcEstMargin(bsmLegs, spot, underlying) : 0),
  [bsmLegs, spot, underlying],
)
```

---

#### Change D — Add Intr./TV columns to leg table

- [ ] **Step 4: In the `<thead>` row, add two `<th>` cells after the existing LTP header**

Find this in the thead:
```typescript
<th className="pb-1 pr-2 text-right">LTP</th>
<th className="pb-1 pr-2 text-right">IV</th>
```

Replace with:
```typescript
<th className="pb-1 pr-2 text-right">LTP</th>
<th className="pb-1 pr-2 text-right">Intr.</th>
<th className="pb-1 pr-2 text-right">TV</th>
<th className="pb-1 pr-2 text-right">IV</th>
```

- [ ] **Step 5: In the tbody row, add two `<td>` cells after the existing LTP cell**

Find this in the tbody:
```typescript
{/* LTP */}
<td className="py-1 pr-2 text-right text-[#d8eef6]">{leg.ltp.toFixed(2)}</td>
{/* IV */}
```

Replace with:
```typescript
{/* LTP */}
<td className="py-1 pr-2 text-right text-[#d8eef6]">{leg.ltp.toFixed(2)}</td>
{/* Intrinsic */}
<td className="py-1 pr-2 text-right text-[#a78bfa]">
  {spot ? calcIntrinsic(leg.type, spot, leg.strike).toFixed(2) : '—'}
</td>
{/* Time Value */}
<td className="py-1 pr-2 text-right text-[#fb923c]">
  {spot
    ? calcTimeValue(leg.premium, calcIntrinsic(leg.type, spot, leg.strike)).toFixed(2)
    : '—'}
</td>
{/* IV */}
```

---

#### Change E — Add inner tab row + POP/Margin stats + PayoffChart render

- [ ] **Step 6: Add inner tab row + PayoffChart section**

Find the existing `{/* Simulation controls */}` comment block and insert the following **before** it:

```typescript
{/* Inner view tabs: Payoff Chart | Monte Carlo */}
{legs.length > 0 && (
  <div className="mt-4 flex items-center gap-1">
    {(
      [
        { id: 'payoff', label: 'Payoff Chart' },
        { id: 'montecarlo', label: 'Monte Carlo' },
      ] as const
    ).map(({ id, label }) => (
      <button
        key={id}
        type="button"
        onClick={() => setActiveSimView(id)}
        className={`rounded-full border px-3 py-0.5 text-[9px] uppercase tracking-wider transition-colors ${
          activeSimView === id
            ? 'border-[#2563eb] bg-[#1e3a5f] text-[#93c5fd]'
            : 'border-[#1f3340] bg-[#09111a] text-[#6b8797] hover:border-[#2a4a5a] hover:text-[#94a3b8]'
        }`}
      >
        {label}
      </button>
    ))}
  </div>
)}

{/* Payoff Chart view */}
{activeSimView === 'payoff' && legs.length > 0 && (
  <div className="mt-3">
    <PayoffChart
      legs={bsmLegs}
      spot={spot}
      sigma={simParams.sigma}
      dte={simParams.days}
      breakevens={breakevens}
    />

    {/* POP + Margin stats row */}
    {spot > 0 && (
      <div className="mt-2 grid grid-cols-2 gap-1.5">
        <div className="rounded-lg border border-[#1a3140] bg-[#09111a] px-3 py-2 text-center">
          <div className="text-[8px] uppercase tracking-widest text-[#4a7b8a]">POP (Statistical)</div>
          <div
            className="mt-0.5 text-[13px] font-semibold"
            style={{ color: pop !== null && pop >= 0.5 ? '#4ade80' : '#f87171' }}
          >
            {pop !== null ? `${(pop * 100).toFixed(1)}%` : '—'}
          </div>
          <div className="text-[8px] text-[#2a4a5a]">log-normal CDF at expiry</div>
        </div>
        <div className="rounded-lg border border-[#1a3140] bg-[#09111a] px-3 py-2 text-center">
          <div className="text-[8px] uppercase tracking-widest text-[#4a7b8a]">Est. Margin</div>
          <div className="mt-0.5 text-[13px] font-semibold text-[#fbbf24]">
            {estMargin > 0 ? `₹${estMargin.toLocaleString('en-IN')}` : '—'}
          </div>
          <div className="text-[8px] text-[#2a4a5a]">approx SPAN × 1.5</div>
        </div>
      </div>
    )}
  </div>
)}
```

- [ ] **Step 7: Wrap existing simulation controls + charts in the Monte Carlo view**

Find the comment `{/* Simulation controls */}` and wrap everything from that comment down to and including the `{/* Top 3 profitable paths */}` block in:

```typescript
{/* Monte Carlo view */}
{activeSimView === 'montecarlo' && (
  <>
    {/* ... all existing simulation controls, charts area, top 3 paths content ... */}
  </>
)}
```

The content inside the wrapper is unchanged — just wrapped.

- [ ] **Step 8: Add POP to Monte Carlo simStats panel (supplement Win%)**

In the `simStats` stats grid, find:
```typescript
{ label: 'Win %', value: `${simStats.winPct.toFixed(1)}%`, color: simStats.winPct >= 50 ? '#4ade80' : '#f87171' },
```

Replace with:
```typescript
{ label: 'Win % (MC)', value: `${simStats.winPct.toFixed(1)}%`, color: simStats.winPct >= 50 ? '#4ade80' : '#f87171' },
{ label: 'POP (CDF)', value: pop !== null ? `${(pop * 100).toFixed(1)}%` : '—', color: pop !== null && pop >= 0.5 ? '#4ade80' : '#f87171' },
```

And update the grid from `grid-cols-3` to `grid-cols-3` (it stays 3 cols, just one more item — wraps to 7 cards, fine).

---

#### Verification

- [ ] **Step 9: TypeScript check**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo/frontend
npx tsc --noEmit --project tsconfig.app.json 2>&1 | head -40
```
Expected: zero errors.

- [ ] **Step 10: Build**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo/frontend
npm run build 2>&1 | tail -10
```
Expected: `✓ built in ...`

- [ ] **Step 11: Commit**

```bash
cd /c/algo/openalgov2/openalgov2/openalgo
git add frontend/src/components/market-pulse/OptionsSimulator.tsx
git commit -m "feat: add Payoff Chart tab, Intr/TV columns, POP, and estimated margin to Options Simulator"
```

---

## Done

After all 3 tasks:
- `bsm.ts` — pure BSM math (normCDF, bsmPrice, calcPOP, calcSDCone, calcEstMargin, calcIntrinsic, calcTimeValue)
- `PayoffChart.tsx` — deterministic canvas chart (expiry + T+n curves, SD cones, scenario sliders)
- `OptionsSimulator.tsx` — Payoff|Monte Carlo inner tabs, Intr./TV leg columns, POP badge, Est. Margin badge

Tasks 1 and 2 are **fully independent** and can run in parallel.
Task 3 depends on both Task 1 (imports bsm.ts) and Task 2 (imports PayoffChart).
