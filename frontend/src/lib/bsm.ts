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
