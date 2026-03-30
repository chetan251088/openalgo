// Web Worker — no imports. Uses self.onmessage / self.postMessage directly.

export interface SimulationInput {
  spot: number
  sigma: number   // annual volatility as decimal (e.g. 0.182)
  mu: number      // annual drift as decimal (e.g. 0)
  nPaths: number  // number of Monte Carlo paths
  days: number    // days to expiry
  legs: SimLeg[]
}

export interface SimLeg {
  action: 'buy' | 'sell'
  qty: number
  type: 'CE' | 'PE'
  strike: number
  premium: number // cost per unit (positive = paid for buy, received for sell)
}

export interface SimulationOutput {
  paths: number[][]  // [pathIndex][stepIndex] = price — downsampled to max 100 steps
  finalPnls: number[]
  spotMin: number
  spotMax: number
}

// ---------------------------------------------------------------------------
// Box-Muller normal random variate
// ---------------------------------------------------------------------------
function randn(): number {
  let u1: number
  let u2: number
  do {
    u1 = Math.random()
  } while (u1 === 0) // avoid log(0)
  u2 = Math.random()
  return Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2)
}

// ---------------------------------------------------------------------------
// Payoff calculation at terminal price S_T
// ---------------------------------------------------------------------------
function calcPnl(legs: SimLeg[], sT: number): number {
  let total = 0
  for (const leg of legs) {
    const intrinsic =
      leg.type === 'CE'
        ? Math.max(sT - leg.strike, 0)
        : Math.max(leg.strike - sT, 0)
    const sign = leg.action === 'buy' ? 1 : -1
    total += sign * leg.qty * (intrinsic - leg.premium)
  }
  return total
}

// ---------------------------------------------------------------------------
// Main simulation
// ---------------------------------------------------------------------------
function runSimulation(input: SimulationInput): SimulationOutput {
  const { spot, sigma, mu, nPaths, days, legs } = input

  // Total GBM steps (8 intraday steps per day)
  const totalSteps = days * 8
  const dt = days / 365 / totalSteps

  // Pre-compute GBM drift and diffusion coefficients
  const drift = (mu - 0.5 * sigma * sigma) * dt
  const diffusion = sigma * Math.sqrt(dt)

  // Downsampling: store at most 100 price points per path
  const MAX_STORED_STEPS = 100
  const stride = Math.max(1, Math.ceil(totalSteps / MAX_STORED_STEPS))
  const storedStepCount = Math.ceil(totalSteps / stride) + 1 // +1 for initial spot

  const paths: number[][] = new Array(nPaths)
  const finalPnls: number[] = new Array(nPaths)

  let globalMin = Infinity
  let globalMax = -Infinity

  for (let p = 0; p < nPaths; p++) {
    const stored: number[] = new Array(storedStepCount)
    stored[0] = spot

    let s = spot
    let storedIdx = 1

    for (let t = 1; t <= totalSteps; t++) {
      s = s * Math.exp(drift + diffusion * randn())

      if (t % stride === 0 || t === totalSteps) {
        stored[storedIdx++] = s
        if (s < globalMin) globalMin = s
        if (s > globalMax) globalMax = s
      }
    }

    // Trim array to actual stored length (in case of rounding)
    paths[p] = stored.slice(0, storedIdx)
    finalPnls[p] = calcPnl(legs, s)
  }

  return {
    paths,
    finalPnls,
    spotMin: globalMin,
    spotMax: globalMax,
  }
}

// ---------------------------------------------------------------------------
// Worker message handler
// ---------------------------------------------------------------------------
self.onmessage = (event: MessageEvent<SimulationInput>) => {
  const result = runSimulation(event.data)
  self.postMessage(result)
}
