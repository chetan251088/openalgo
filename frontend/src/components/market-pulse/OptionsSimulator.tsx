import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useOptionChainLive } from '@/hooks/useOptionChainLive'
import { optionChainApi } from '@/api/option-chain'
import { apiClient } from '@/api/client'
import { STRATEGIES } from '@/lib/optionStrategies'
import type { SimulationInput, SimulationOutput } from '@/workers/simulationWorker'
import { Button } from '@/components/ui/button'
import PayoffChart from '@/components/market-pulse/PayoffChart'
import {
  calcIntrinsic,
  calcTimeValue,
  calcPOP,
  calcEstMargin,
  type BsmLeg,
} from '@/lib/bsm'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SimulatorLeg {
  id: string
  action: 'buy' | 'sell'
  qty: number
  type: 'CE' | 'PE'
  strike: number
  expiry: string
  symbol?: string
  ltp: number
  premium: number
}

interface GreekData {
  iv: number
  delta: number
  gamma: number
  theta: number
  vega: number
}

interface SimParams {
  sigma: number
  mu: number
  nPaths: number
  days: number
}

type Underlying = 'NIFTY' | 'BANKNIFTY' | 'SENSEX'

// Typical Indian market defaults (annualised, as decimal)
// σ: implied/realised vol fallback before ATM IV loads
// μ: risk-neutral = 0 is standard; 0.12 ≈ Nifty long-run drift
const INDEX_DEFAULTS: Record<Underlying, { sigma: number; mu: number; sigmaHint: string }> = {
  NIFTY:     { sigma: 0.15, mu: 0, sigmaHint: 'Typical 13–18% (India VIX ÷ 100)' },
  BANKNIFTY: { sigma: 0.20, mu: 0, sigmaHint: 'Typically 1.3× NIFTY vol (~18–25%)' },
  SENSEX:    { sigma: 0.15, mu: 0, sigmaHint: 'Broadly tracks NIFTY vol (~13–18%)' },
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getOptionExchange(u: Underlying): string {
  return u === 'SENSEX' ? 'BFO' : 'NFO'
}

function getUnderlyingExchange(u: Underlying): string {
  return u === 'SENSEX' ? 'BSE_INDEX' : 'NSE_INDEX'
}

function daysToExpiry(expiry: string): number {
  if (!expiry) return 7
  // expiry format: "10FEB26" or "10-FEB-26"
  const clean = expiry.replace(/-/g, '').toUpperCase()
  const months: Record<string, number> = {
    JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
    JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
  }
  try {
    const day = parseInt(clean.slice(0, 2), 10)
    const mon = months[clean.slice(2, 5)]
    const yr = 2000 + parseInt(clean.slice(5, 7), 10)
    if (isNaN(day) || mon === undefined || isNaN(yr)) return 7
    const expDate = new Date(yr, mon, day)
    const diff = (expDate.getTime() - Date.now()) / 86400000
    return Math.max(1, Math.ceil(diff))
  } catch {
    return 7
  }
}

function fmt2(v: number | undefined | null) {
  if (typeof v !== 'number') return '—'
  return v.toFixed(2)
}

function fmtPnl(v: number) {
  return `${v >= 0 ? '+' : ''}${v.toFixed(0)}`
}

// ---------------------------------------------------------------------------
// Canvas drawing helpers
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

const TOP3_COLORS = ['#fbbf24', '#94a3b8', '#f97316'] as const // gold, silver, bronze

function drawPathCanvas(
  canvas: HTMLCanvasElement,
  result: SimulationOutput,
  breakevens: number[],
  top3Indices: number[] = [],
  hoverStep?: number,
) {
  const ctx = setupCanvas(canvas)
  const W = canvas.getBoundingClientRect().width
  const H = canvas.getBoundingClientRect().height

  ctx.fillStyle = '#09111a'
  ctx.fillRect(0, 0, W, H)

  const { paths, finalPnls, spotMin, spotMax } = result
  const steps = paths[0]?.length ?? 0
  if (steps < 2) return

  const priceRange = spotMax - spotMin || 1
  const toX = (i: number) => (i / (steps - 1)) * (W - 20) + 10
  const toY = (price: number) =>
    H - 10 - ((price - spotMin) / priceRange) * (H - 20)

  const top3Set = new Set(top3Indices)

  // Draw background paths (skip top3 — drawn later on top)
  const sample = paths.length > 200 ? paths.filter((_, i) => i % Math.ceil(paths.length / 200) === 0) : paths
  sample.forEach((path, idx) => {
    const origIdx = paths.length > 200 ? idx * Math.ceil(paths.length / 200) : idx
    if (top3Set.has(origIdx)) return
    const pnl = finalPnls[origIdx] ?? finalPnls[0]
    const isProfit = pnl >= 0
    ctx.beginPath()
    ctx.strokeStyle = isProfit ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)'
    ctx.lineWidth = 0.7
    path.forEach((price, i) => {
      if (i === 0) ctx.moveTo(toX(i), toY(price))
      else ctx.lineTo(toX(i), toY(price))
    })
    ctx.stroke()
  })

  // Mean path
  const meanPath = Array.from({ length: steps }, (_, i) =>
    paths.reduce((acc, p) => acc + (p[i] ?? p[p.length - 1]), 0) / paths.length
  )
  ctx.beginPath()
  ctx.strokeStyle = '#e2e8f0'
  ctx.lineWidth = 1.5
  meanPath.forEach((price, i) => {
    if (i === 0) ctx.moveTo(toX(i), toY(price))
    else ctx.lineTo(toX(i), toY(price))
  })
  ctx.stroke()

  // Draw top 3 paths on top with distinct colors
  top3Indices.forEach((pathIdx, rank) => {
    const path = paths[pathIdx]
    if (!path) return
    const color = TOP3_COLORS[rank]
    ctx.beginPath()
    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.globalAlpha = 0.9
    path.forEach((price, i) => {
      if (i === 0) ctx.moveTo(toX(i), toY(price))
      else ctx.lineTo(toX(i), toY(price))
    })
    ctx.stroke()
    ctx.globalAlpha = 1

    // End-of-path label
    const lastPrice = path[path.length - 1]
    const lx = toX(steps - 1)
    const ly = toY(lastPrice)
    ctx.fillStyle = color
    ctx.font = 'bold 9px monospace'
    ctx.fillText(`#${rank + 1}`, lx - 18, ly - 3)
  })

  // Hover crosshair
  if (hoverStep !== undefined) {
    const x = toX(hoverStep)
    ctx.beginPath()
    ctx.setLineDash([3, 3])
    ctx.strokeStyle = 'rgba(148,194,209,0.5)'
    ctx.lineWidth = 1
    ctx.moveTo(x, 8)
    ctx.lineTo(x, H - 8)
    ctx.stroke()
    ctx.setLineDash([])

    // Dots on top3 paths at hover step
    top3Indices.forEach((pathIdx, rank) => {
      const path = paths[pathIdx]
      if (!path) return
      const price = path[Math.min(hoverStep, path.length - 1)]
      const color = TOP3_COLORS[rank]
      ctx.beginPath()
      ctx.arc(x, toY(price), 4, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
      ctx.strokeStyle = '#09111a'
      ctx.lineWidth = 1.5
      ctx.stroke()
    })

    // Dot on mean path at hover step
    const meanPrice = meanPath[Math.min(hoverStep, meanPath.length - 1)]
    ctx.beginPath()
    ctx.arc(x, toY(meanPrice), 3, 0, Math.PI * 2)
    ctx.fillStyle = '#e2e8f0'
    ctx.fill()
  }

  // Breakeven lines
  breakevens.forEach((be) => {
    if (be < spotMin || be > spotMax) return
    const y = toY(be)
    ctx.beginPath()
    ctx.setLineDash([4, 4])
    ctx.strokeStyle = '#fde68a'
    ctx.lineWidth = 1
    ctx.moveTo(10, y)
    ctx.lineTo(W - 10, y)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.fillStyle = '#fde68a'
    ctx.font = '9px monospace'
    ctx.fillText(be.toFixed(0), W - 36, y - 2)
  })

  ctx.fillStyle = '#4a7b8a'
  ctx.font = '9px monospace'
  ctx.fillText('Price Paths', 12, 14)
}

function drawHistCanvas(canvas: HTMLCanvasElement, finalPnls: number[], hoveredBin?: number) {
  const ctx = setupCanvas(canvas)
  const W = canvas.getBoundingClientRect().width
  const H = canvas.getBoundingClientRect().height

  ctx.fillStyle = '#09111a'
  ctx.fillRect(0, 0, W, H)

  if (!finalPnls.length) return

  const min = Math.min(...finalPnls)
  const max = Math.max(...finalPnls)
  const range = max - min || 1
  const BINS = 40
  const binWidth = range / BINS
  const counts = new Array(BINS).fill(0)
  for (const pnl of finalPnls) {
    const bin = Math.min(Math.floor((pnl - min) / binWidth), BINS - 1)
    counts[bin]++
  }
  const maxCount = Math.max(...counts)

  const pad = { l: 10, r: 10, t: 16, b: 10 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const bw = innerW / BINS

  counts.forEach((count, i) => {
    const pnlMid = min + (i + 0.5) * binWidth
    const barH = (count / maxCount) * innerH
    const x = pad.l + i * bw
    const y = pad.t + innerH - barH
    const isHovered = i === hoveredBin
    const alpha = isHovered ? 0.92 : 0.55
    ctx.fillStyle = pnlMid >= 0
      ? `rgba(74,222,128,${alpha})`
      : `rgba(248,113,113,${alpha})`
    ctx.fillRect(x + 0.5, isHovered ? y - 2 : y, bw - 1, isHovered ? barH + 2 : barH)

    // Hovered bin: draw bright outline
    if (isHovered) {
      ctx.strokeStyle = pnlMid >= 0 ? '#4ade80' : '#f87171'
      ctx.lineWidth = 1.5
      ctx.strokeRect(x + 0.5, y - 2, bw - 1, barH + 2)
    }
  })

  // Zero line
  const zeroX = pad.l + ((0 - min) / range) * innerW
  ctx.beginPath()
  ctx.strokeStyle = '#fde68a'
  ctx.lineWidth = 1
  ctx.setLineDash([3, 3])
  ctx.moveTo(zeroX, pad.t)
  ctx.lineTo(zeroX, pad.t + innerH)
  ctx.stroke()
  ctx.setLineDash([])

  ctx.fillStyle = '#4a7b8a'
  ctx.font = '9px monospace'
  ctx.fillText('P&L Distribution', 12, 12)
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

// VIX multiplier per index (BANKNIFTY is empirically ~35% more volatile than NIFTY)
const VIX_MULTIPLIER: Record<Underlying, number> = {
  NIFTY: 1.0,
  BANKNIFTY: 1.35,
  SENSEX: 1.0,
}

interface OptionsSimulatorProps {
  vixLevel?: number
}

export function OptionsSimulator({ vixLevel }: OptionsSimulatorProps) {
  const apiKey = useAuthStore((s) => s.apiKey)

  const [underlying, setUnderlying] = useState<Underlying>('NIFTY')
  const [expiry, setExpiry] = useState<string>('')
  const [expiries, setExpiries] = useState<string[]>([])
  const [activeStrategy, setActiveStrategy] = useState<string | null>(null)
  const [legs, setLegs] = useState<SimulatorLeg[]>([])
  const [simParams, setSimParams] = useState<SimParams>({
    sigma: 0.18,
    mu: 0,
    nPaths: 500,
    days: 7,
  })
  const [simResult, setSimResult] = useState<SimulationOutput | null>(null)
  const [isSimRunning, setIsSimRunning] = useState(false)
  const [greeks, setGreeks] = useState<Map<string, GreekData>>(new Map())
  const [activeSimView, setActiveSimView] = useState<'payoff' | 'montecarlo'>('payoff')
  // Track sigma source so we know when ATM IV has overridden VIX
  const [sigmaSource, setSigmaSource] = useState<'default' | 'vix' | 'atm-iv'>('default')
  const atmIvLoadedRef = useRef(false)

  const workerRef = useRef<Worker | null>(null)
  const pathCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const histCanvasRef = useRef<HTMLCanvasElement | null>(null)
  // Track which keys have been fetched to avoid re-fetch loops (greeks map in deps causes cascade)
  const fetchedGreekKeys = useRef<Set<string>>(new Set())

  // Hover interactivity
  const pathHoverStepRef = useRef<number | null>(null)
  const histHoverBinRef = useRef<number | null>(null)
  const pathRafRef = useRef<number>(0)
  const histRafRef = useRef<number>(0)

  const [pathTooltip, setPathTooltip] = useState<{
    x: number; y: number; dayFraction: number
    meanPrice: number
    top3Prices: Array<{ rank: number; price: number; color: string }>
  } | null>(null)

  const [histTooltip, setHistTooltip] = useState<{
    x: number; y: number
    pnlMin: number; pnlMax: number
    count: number; pct: number; isProfit: boolean
  } | null>(null)

  const optionExchange = useMemo(() => getOptionExchange(underlying), [underlying])
  const underlyingExchange = useMemo(() => getUnderlyingExchange(underlying), [underlying])

  // Live chain data
  const { data: chainData, isLoading } = useOptionChainLive(
    apiKey,
    underlying,
    underlyingExchange,
    optionExchange,
    expiry,
    16,
    { enabled: Boolean(apiKey && expiry) }
  )

  const spot = chainData?.underlying_ltp ?? 0
  const atmStrike = chainData?.atm_strike ?? 0
  const strikes = useMemo(
    () => (chainData?.chain ?? []).map((s) => s.strike).sort((a, b) => a - b),
    [chainData?.chain]
  )

  // Live VIX → sigma: update in real-time unless ATM IV has already been loaded
  useEffect(() => {
    if (!vixLevel || vixLevel <= 0) return
    if (atmIvLoadedRef.current) return  // ATM IV takes priority once fetched
    const sigma = (vixLevel * VIX_MULTIPLIER[underlying]) / 100
    setSimParams((p) => ({ ...p, sigma: parseFloat(sigma.toFixed(4)) }))
    setSigmaSource('vix')
  }, [vixLevel, underlying])

  // Fetch expiry list on underlying change; reset sigma to index default
  useEffect(() => {
    if (!apiKey) return
    atmIvLoadedRef.current = false
    setSigmaSource(vixLevel && vixLevel > 0 ? 'vix' : 'default')
    const defaultSigma = vixLevel && vixLevel > 0
      ? (vixLevel * VIX_MULTIPLIER[underlying]) / 100
      : INDEX_DEFAULTS[underlying].sigma
    setSimParams((p) => ({ ...p, sigma: parseFloat(defaultSigma.toFixed(4)), mu: INDEX_DEFAULTS[underlying].mu }))
    fetchedGreekKeys.current.clear()
    setGreeks(new Map())
    const exchange = underlying === 'SENSEX' ? 'BFO' : 'NFO'
    optionChainApi
      .getExpiries(apiKey, underlying, exchange, 'options')
      .then((res) => {
        if (res.status === 'success' && res.data.length > 0) {
          setExpiries(res.data)
          setExpiry(res.data[0])
        }
      })
      .catch(() => {})
  }, [apiKey, underlying])

  // Sync days-to-expiry into simParams when expiry changes; reset fetched keys cache
  useEffect(() => {
    if (!expiry) return
    setSimParams((p) => ({ ...p, days: daysToExpiry(expiry) }))
    fetchedGreekKeys.current.clear()
    setGreeks(new Map())
  }, [expiry])

  // Worker setup
  useEffect(() => {
    workerRef.current = new Worker(
      new URL('@/workers/simulationWorker.ts', import.meta.url),
      { type: 'module' }
    )
    workerRef.current.onmessage = (e: MessageEvent<SimulationOutput>) => {
      setSimResult(e.data)
      setIsSimRunning(false)
    }
    return () => {
      workerRef.current?.terminate()
      workerRef.current = null
    }
  }, [])

  // Greeks fetch for each leg
  useEffect(() => {
    if (!apiKey) return
    for (const leg of legs) {
      if (!leg.symbol) continue
      const key = `${leg.type}:${leg.strike}:${leg.expiry}`
      if (fetchedGreekKeys.current.has(key)) continue
      fetchedGreekKeys.current.add(key)
      apiClient
        .post<{ status: string; implied_volatility?: number; greeks?: { delta?: number; gamma?: number; theta?: number; vega?: number } }>(
          '/optiongreeks',
          { apikey: apiKey, symbol: leg.symbol, exchange: optionExchange }
        )
        .then((res) => {
          const d = res.data
          if (d.status === 'success') {
            setGreeks((prev) => {
              const next = new Map(prev)
              // API returns implied_volatility as percentage — convert to decimal
              const ivDecimal = Math.min(Math.max((d.implied_volatility ?? 0) / 100, 0), 2.0)
              next.set(key, {
                iv: ivDecimal,
                delta: d.greeks?.delta ?? 0,
                gamma: d.greeks?.gamma ?? 0,
                theta: d.greeks?.theta ?? 0,
                vega: d.greeks?.vega ?? 0,
              })
              return next
            })
          }
        })
        .catch(() => { fetchedGreekKeys.current.delete(key) })
    }
  }, [apiKey, legs, optionExchange])

  // ATM IV from CE greek at ATM strike
  const atmIv = useMemo(() => {
    if (!atmStrike) return null
    const atmRow = chainData?.chain.find((s) => s.strike === atmStrike)
    if (!atmRow?.ce?.symbol) return null
    const key = `CE:${atmStrike}:${expiry}`
    return greeks.get(key)?.iv ?? null
  }, [atmStrike, chainData?.chain, expiry, greeks])

  // Fetch ATM IV when chain loads (pre-fills sigma)
  useEffect(() => {
    if (!apiKey || !atmStrike || !expiry) return
    const atmRow = chainData?.chain.find((s) => s.strike === atmStrike)
    if (!atmRow?.ce?.symbol) return
    const key = `CE:${atmStrike}:${expiry}`
    if (fetchedGreekKeys.current.has(key)) return
    fetchedGreekKeys.current.add(key)
    apiClient
      .post<{ status: string; implied_volatility?: number; greeks?: { delta?: number; gamma?: number; theta?: number; vega?: number } }>(
        '/optiongreeks',
        { apikey: apiKey, symbol: atmRow.ce.symbol, exchange: optionExchange }
      )
      .then((res) => {
        const d = res.data
        if (d.status === 'success') {
          // API returns implied_volatility as a percentage (e.g. 18.5 = 18.5%)
          // Convert to decimal for simulation (0.185) and clamp to sane range
          const ivPct = d.implied_volatility ?? 18
          const ivDecimal = Math.min(Math.max(ivPct / 100, 0.01), 2.0)
          setGreeks((prev) => {
            const next = new Map(prev)
            next.set(key, {
              iv: ivDecimal,
              delta: d.greeks?.delta ?? 0,
              gamma: d.greeks?.gamma ?? 0,
              theta: d.greeks?.theta ?? 0,
              vega: d.greeks?.vega ?? 0,
            })
            return next
          })
          // ATM IV takes priority over VIX — mark as loaded so VIX effect won't overwrite
          atmIvLoadedRef.current = true
          setSigmaSource('atm-iv')
          setSimParams((p) => ({ ...p, sigma: parseFloat(ivDecimal.toFixed(4)) }))
        }
      })
      .catch(() => { fetchedGreekKeys.current.delete(key) })
  }, [apiKey, atmStrike, expiry, chainData?.chain, optionExchange])

  // Resolve strike from chain by ATM index + offset
  const resolveStrikeByOffset = useCallback(
    (_type: 'CE' | 'PE', offset: number): number => {
      if (!strikes.length || !atmStrike) return atmStrike || 0
      const atmIdx = strikes.findIndex((s) => s === atmStrike)
      if (atmIdx < 0) return atmStrike
      // For CE: positive offset = higher strike; for PE: negative offset = lower strike
      const idx = Math.min(Math.max(atmIdx + offset, 0), strikes.length - 1)
      return strikes[idx]
    },
    [strikes, atmStrike]
  )

  // Get LTP for a strike/type from chain
  const getLtp = useCallback(
    (type: 'CE' | 'PE', strike: number): number => {
      const row = chainData?.chain.find((s) => s.strike === strike)
      return (type === 'CE' ? row?.ce?.ltp : row?.pe?.ltp) ?? 0
    },
    [chainData?.chain]
  )

  // Get symbol for a strike/type from chain
  const getSymbol = useCallback(
    (type: 'CE' | 'PE', strike: number): string | undefined => {
      const row = chainData?.chain.find((s) => s.strike === strike)
      return (type === 'CE' ? row?.ce?.symbol : row?.pe?.symbol) ?? undefined
    },
    [chainData?.chain]
  )

  // Apply a strategy preset
  const applyStrategy = useCallback(
    (stratId: string) => {
      const strat = STRATEGIES.find((s) => s.id === stratId)
      if (!strat || !atmStrike) return
      const newLegs: SimulatorLeg[] = strat.legs.map((template) => {
        const strike = resolveStrikeByOffset(template.type, template.strikeOffset)
        const ltp = getLtp(template.type, strike)
        return {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          action: template.action,
          qty: template.qty,
          type: template.type,
          strike,
          expiry,
          symbol: getSymbol(template.type, strike),
          ltp,
          premium: ltp,
        }
      })
      setLegs(newLegs)
      setActiveStrategy(stratId)
      setSimResult(null)
    },
    [atmStrike, expiry, getLtp, getSymbol, resolveStrikeByOffset]
  )

  // Add a default BUY CE @ ATM leg
  const addLeg = useCallback(() => {
    const strike = atmStrike || (strikes[0] ?? 0)
    const ltp = getLtp('CE', strike)
    setLegs((prev) => [
      ...prev,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        action: 'buy',
        qty: 1,
        type: 'CE',
        strike,
        expiry,
        symbol: getSymbol('CE', strike),
        ltp,
        premium: ltp,
      },
    ])
    setActiveStrategy(null)
    setSimResult(null)
  }, [atmStrike, strikes, expiry, getLtp, getSymbol])

  const removeLeg = useCallback((id: string) => {
    setLegs((prev) => prev.filter((l) => l.id !== id))
    setActiveStrategy(null)
    setSimResult(null)
  }, [])

  const updateLeg = useCallback(
    (id: string, patch: Partial<SimulatorLeg>) => {
      setLegs((prev) =>
        prev.map((l) => {
          if (l.id !== id) return l
          const updated = { ...l, ...patch }
          // If strike or type changed, refresh ltp/symbol
          if (patch.strike !== undefined || patch.type !== undefined) {
            const type = patch.type ?? l.type
            const strike = patch.strike ?? l.strike
            updated.ltp = getLtp(type, strike)
            updated.symbol = getSymbol(type, strike)
            updated.premium = updated.ltp
          }
          return updated
        })
      )
      setActiveStrategy(null)
      setSimResult(null)
    },
    [getLtp, getSymbol]
  )

  // Net Greeks
  const netGreeks = useMemo(() => {
    let delta = 0, gamma = 0, theta = 0, vega = 0
    for (const leg of legs) {
      const key = `${leg.type}:${leg.strike}:${leg.expiry}`
      const g = greeks.get(key)
      if (!g) continue
      const sign = leg.action === 'buy' ? 1 : -1
      delta += sign * leg.qty * g.delta
      gamma += sign * leg.qty * g.gamma
      theta += sign * leg.qty * g.theta
      vega += sign * leg.qty * g.vega
    }
    return { delta, gamma, theta, vega }
  }, [legs, greeks])

  // Net premium (positive = received credit, negative = debit paid)
  const netPremium = useMemo(() => {
    return legs.reduce((acc, leg) => {
      const sign = leg.action === 'sell' ? 1 : -1
      return acc + sign * leg.qty * leg.premium
    }, 0)
  }, [legs])

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

  // Simple payoff breakevens at expiry (approximate for single-expiry strategies)
  const breakevens = useMemo(() => {
    if (!legs.length || !spot) return []
    const bes: number[] = []
    const step = spot * 0.001
    const low = spot * 0.8
    const high = spot * 1.2
    let prevPnl: number | null = null
    for (let s = low; s <= high; s += step) {
      const pnl = legs.reduce((acc, leg) => {
        const intrinsic =
          leg.type === 'CE' ? Math.max(s - leg.strike, 0) : Math.max(leg.strike - s, 0)
        const sign = leg.action === 'buy' ? 1 : -1
        return acc + sign * leg.qty * (intrinsic - leg.premium)
      }, 0)
      if (prevPnl !== null && Math.sign(pnl) !== Math.sign(prevPnl)) {
        bes.push(s)
      }
      prevPnl = pnl
    }
    return bes.slice(0, 4)
  }, [legs, spot])

  // Run simulation
  const runSim = useCallback(() => {
    if (!workerRef.current || !legs.length || !spot) return
    setIsSimRunning(true)
    const input: SimulationInput = {
      spot,
      sigma: simParams.sigma,
      mu: simParams.mu,
      nPaths: simParams.nPaths,
      days: simParams.days,
      legs: legs.map((l) => ({
        action: l.action,
        qty: l.qty,
        type: l.type,
        strike: l.strike,
        premium: l.premium,
      })),
    }
    workerRef.current.postMessage(input)
  }, [legs, spot, simParams])

  // Top 3 most profitable paths
  const top3 = useMemo(() => {
    if (!simResult?.finalPnls?.length) return []
    return simResult.finalPnls
      .map((pnl, i) => ({ pnl, i }))
      .sort((a, b) => b.pnl - a.pnl)
      .slice(0, 3)
      .map(({ pnl, i }, rank) => ({
        rank: rank + 1,
        pathIdx: i,
        finalPnl: pnl,
        finalSpot: simResult.paths[i]?.[simResult.paths[i].length - 1] ?? 0,
        path: simResult.paths[i] ?? [],
        color: TOP3_COLORS[rank],
      }))
  }, [simResult])

  const top3Indices = useMemo(() => top3.map((t) => t.pathIdx), [top3])

  // Draw canvases when simResult / top3 changes
  useEffect(() => {
    if (!simResult) return
    if (pathCanvasRef.current) drawPathCanvas(pathCanvasRef.current, simResult, breakevens, top3Indices)
    if (histCanvasRef.current) drawHistCanvas(histCanvasRef.current, simResult.finalPnls)
  }, [simResult, breakevens, top3Indices])

  // Redraw callbacks (called from mouse handlers via rAF)
  const redrawPath = useCallback(() => {
    if (!pathCanvasRef.current || !simResult) return
    drawPathCanvas(
      pathCanvasRef.current, simResult, breakevens, top3Indices,
      pathHoverStepRef.current ?? undefined,
    )
  }, [simResult, breakevens, top3Indices])

  const redrawHist = useCallback(() => {
    if (!histCanvasRef.current || !simResult) return
    drawHistCanvas(histCanvasRef.current, simResult.finalPnls, histHoverBinRef.current ?? undefined)
  }, [simResult])

  // Mouse handlers — path canvas
  const handlePathMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!simResult || !pathCanvasRef.current) return
    const canvas = pathCanvasRef.current
    const rect = canvas.getBoundingClientRect()
    const relX = e.clientX - rect.left
    const relY = e.clientY - rect.top
    const W = rect.width
    const steps = simResult.paths[0]?.length ?? 0
    if (steps < 2) return

    const step = Math.max(0, Math.min(steps - 1, Math.round(((relX - 10) / (W - 20)) * (steps - 1))))
    pathHoverStepRef.current = step
    cancelAnimationFrame(pathRafRef.current)
    pathRafRef.current = requestAnimationFrame(redrawPath)

    const meanPrice = simResult.paths.reduce((acc, p) => acc + (p[Math.min(step, p.length - 1)] ?? p[p.length - 1]), 0) / simResult.paths.length
    const dayFraction = (step / (steps - 1)) * simParams.days
    setPathTooltip({
      x: relX, y: relY, dayFraction, meanPrice,
      top3Prices: top3.map((t) => ({
        rank: t.rank,
        price: t.path[Math.min(step, t.path.length - 1)] ?? t.path[t.path.length - 1],
        color: t.color,
      })),
    })
  }, [simResult, top3, redrawPath, simParams.days])

  const handlePathLeave = useCallback(() => {
    pathHoverStepRef.current = null
    cancelAnimationFrame(pathRafRef.current)
    pathRafRef.current = requestAnimationFrame(redrawPath)
    setPathTooltip(null)
  }, [redrawPath])

  // Mouse handlers — histogram canvas
  const handleHistMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!simResult || !histCanvasRef.current) return
    const canvas = histCanvasRef.current
    const rect = canvas.getBoundingClientRect()
    const relX = e.clientX - rect.left
    const relY = e.clientY - rect.top
    const W = rect.width
    const pnls = simResult.finalPnls
    const min = Math.min(...pnls)
    const max = Math.max(...pnls)
    const range = max - min || 1
    const BINS = 40
    const innerW = W - 20
    const bw = innerW / BINS
    const binIdx = Math.max(0, Math.min(BINS - 1, Math.floor((relX - 10) / bw)))
    const binWidth = range / BINS
    const pnlMin = min + binIdx * binWidth
    const pnlMax = pnlMin + binWidth
    const count = pnls.filter((p) => {
      const b = Math.min(Math.floor((p - min) / binWidth), BINS - 1)
      return b === binIdx
    }).length
    histHoverBinRef.current = binIdx
    cancelAnimationFrame(histRafRef.current)
    histRafRef.current = requestAnimationFrame(redrawHist)
    setHistTooltip({
      x: relX, y: relY, pnlMin, pnlMax,
      count, pct: (count / pnls.length) * 100,
      isProfit: (pnlMin + pnlMax) / 2 >= 0,
    })
  }, [simResult, redrawHist])

  const handleHistLeave = useCallback(() => {
    histHoverBinRef.current = null
    cancelAnimationFrame(histRafRef.current)
    histRafRef.current = requestAnimationFrame(redrawHist)
    setHistTooltip(null)
  }, [redrawHist])

  // Stats from sim
  const simStats = useMemo(() => {
    if (!simResult?.finalPnls?.length) return null
    const pnls = simResult.finalPnls
    const sorted = [...pnls].sort((a, b) => a - b)
    const avg = pnls.reduce((a, b) => a + b, 0) / pnls.length
    const median = sorted[Math.floor(sorted.length / 2)]
    const winPct = (pnls.filter((p) => p > 0).length / pnls.length) * 100
    const maxLoss = sorted[0]
    const maxPayoff = sorted[sorted.length - 1]
    return { avg, median, winPct, maxLoss, maxPayoff, total: pnls.length }
  }, [simResult])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Options Strategy Simulator
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {/* Underlying toggle */}
          {(['NIFTY', 'BANKNIFTY', 'SENSEX'] as Underlying[]).map((u) => (
            <button
              key={u}
              onClick={() => {
                setUnderlying(u)
                setExpiry('')
                setExpiries([])
                setLegs([])
                setSimResult(null)
              }}
              className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-widest transition-colors ${
                underlying === u
                  ? 'bg-[#1a3a4d] text-[#93c5fd]'
                  : 'text-[#6b8797] hover:text-[#93c5fd]'
              }`}
            >
              {u}
            </button>
          ))}
          {/* Expiry select */}
          <select
            value={expiry}
            onChange={(e) => {
              setExpiry(e.target.value)
              setLegs([])
              setSimResult(null)
            }}
            className="rounded border border-[#1a3140] bg-[#09111a] px-2 py-0.5 text-[10px] text-[#d8eef6] focus:outline-none"
          >
            {expiries.length === 0 && <option value="">Loading…</option>}
            {expiries.map((ex) => (
              <option key={ex} value={ex}>
                {ex}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Spot / ATM / IV row */}
      <div className="mt-2 flex flex-wrap gap-4 text-[10px]">
        <span className="text-[#7fa2b1]">
          Spot{' '}
          <span className="font-semibold text-[#d8eef6]">{spot ? spot.toFixed(2) : '—'}</span>
        </span>
        <span className="text-[#7fa2b1]">
          ATM{' '}
          <span className="font-semibold text-[#fde68a]">{atmStrike || '—'}</span>
        </span>
        <span className="text-[#7fa2b1]">
          ATM IV{' '}
          <span className="font-semibold text-[#4ade80]">
            {atmIv !== null ? `${(atmIv * 100).toFixed(1)}%` : isLoading ? '…' : '—'}
          </span>
        </span>
        <span className="text-[#7fa2b1]">
          DTE{' '}
          <span className="font-semibold text-[#d8eef6]">{expiry ? daysToExpiry(expiry) : '—'}</span>
        </span>
      </div>

      {/* Strategy pills */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {STRATEGIES.map((s) => (
          <button
            key={s.id}
            onClick={() => applyStrategy(s.id)}
            title={s.description}
            className={`rounded-full border px-2.5 py-0.5 text-[9px] uppercase tracking-wider transition-colors ${
              activeStrategy === s.id
                ? 'border-[#2563eb] bg-[#1e3a5f] text-[#93c5fd]'
                : 'border-[#1f3340] bg-[#09111a] text-[#6b8797] hover:border-[#2a4a5a] hover:text-[#94a3b8]'
            }`}
          >
            {s.name}
          </button>
        ))}
      </div>

      {/* Leg table */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="border-b border-[#1f3340] text-left text-[#4a7b8a] uppercase tracking-widest">
              <th className="pb-1 pr-2">Action</th>
              <th className="pb-1 pr-2">Qty</th>
              <th className="pb-1 pr-2">Type</th>
              <th className="pb-1 pr-2">Strike</th>
              <th className="pb-1 pr-2 text-right">LTP</th>
              <th className="pb-1 pr-2 text-right">Intr.</th>
              <th className="pb-1 pr-2 text-right">TV</th>
              <th className="pb-1 pr-2 text-right">IV</th>
              <th className="pb-1 pr-2 text-right">Δ</th>
              <th className="pb-1 pr-2 text-right">Γ</th>
              <th className="pb-1 pr-2 text-right">Θ</th>
              <th className="pb-1 pr-2 text-right">ν</th>
              <th className="pb-1" />
            </tr>
          </thead>
          <tbody>
            {legs.map((leg) => {
              const gKey = `${leg.type}:${leg.strike}:${leg.expiry}`
              const g = greeks.get(gKey)
              return (
                <tr key={leg.id} className="border-b border-[#111e28]">
                  {/* Action */}
                  <td className="py-1 pr-2">
                    <button
                      onClick={() => updateLeg(leg.id, { action: leg.action === 'buy' ? 'sell' : 'buy' })}
                      className={`rounded px-2 py-0.5 text-[9px] uppercase font-semibold ${
                        leg.action === 'buy'
                          ? 'bg-[#064e3b] text-[#4ade80]'
                          : 'bg-[#4c0519] text-[#f87171]'
                      }`}
                    >
                      {leg.action}
                    </button>
                  </td>
                  {/* Qty */}
                  <td className="py-1 pr-2">
                    <input
                      type="number"
                      min={1}
                      value={leg.qty}
                      onChange={(e) => updateLeg(leg.id, { qty: Math.max(1, parseInt(e.target.value) || 1) })}
                      className="w-12 rounded border border-[#1a3140] bg-[#09111a] px-1 py-0.5 text-[10px] text-[#d8eef6] focus:outline-none"
                    />
                  </td>
                  {/* Type */}
                  <td className="py-1 pr-2">
                    <button
                      onClick={() => updateLeg(leg.id, { type: leg.type === 'CE' ? 'PE' : 'CE' })}
                      className={`rounded px-2 py-0.5 text-[9px] uppercase font-semibold ${
                        leg.type === 'CE'
                          ? 'bg-[#1e3a5f] text-[#60a5fa]'
                          : 'bg-[#3b1d3b] text-[#e879f9]'
                      }`}
                    >
                      {leg.type}
                    </button>
                  </td>
                  {/* Strike */}
                  <td className="py-1 pr-2">
                    <select
                      value={leg.strike}
                      onChange={(e) => updateLeg(leg.id, { strike: Number(e.target.value) })}
                      className="rounded border border-[#1a3140] bg-[#09111a] px-1 py-0.5 text-[10px] text-[#d8eef6] focus:outline-none"
                    >
                      {strikes.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </td>
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
                  <td className="py-1 pr-2 text-right text-[#4ade80]">
                    {g ? `${(g.iv * 100).toFixed(1)}%` : '—'}
                  </td>
                  {/* Delta */}
                  <td className="py-1 pr-2 text-right text-[#94a3b8]">{fmt2(g?.delta)}</td>
                  {/* Gamma */}
                  <td className="py-1 pr-2 text-right text-[#94a3b8]">{fmt2(g?.gamma)}</td>
                  {/* Theta */}
                  <td className="py-1 pr-2 text-right text-[#94a3b8]">{fmt2(g?.theta)}</td>
                  {/* Vega */}
                  <td className="py-1 pr-2 text-right text-[#94a3b8]">{fmt2(g?.vega)}</td>
                  {/* Remove */}
                  <td className="py-1">
                    <button
                      onClick={() => removeLeg(leg.id)}
                      className="text-[#4a7b8a] hover:text-[#f87171] transition-colors"
                      title="Remove leg"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        <button
          onClick={addLeg}
          className="mt-2 rounded border border-dashed border-[#1f3340] px-3 py-1 text-[10px] text-[#4a7b8a] hover:border-[#2a4a5a] hover:text-[#7fa2b1] transition-colors"
        >
          + Add Leg
        </button>
      </div>

      {/* Net Greeks row */}
      {legs.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-4 rounded-xl border border-[#1a3140] bg-[#09111a] px-3 py-2 text-[10px]">
          <span className="text-[#4a7b8a] uppercase tracking-wider">Net</span>
          <span className="text-[#94a3b8]">
            Δ <span className="font-semibold text-[#d8eef6]">{fmt2(netGreeks.delta)}</span>
          </span>
          <span className="text-[#94a3b8]">
            Γ <span className="font-semibold text-[#d8eef6]">{fmt2(netGreeks.gamma)}</span>
          </span>
          <span className="text-[#94a3b8]">
            Θ <span className="font-semibold text-[#d8eef6]">{fmt2(netGreeks.theta)}</span>
          </span>
          <span className="text-[#94a3b8]">
            ν <span className="font-semibold text-[#d8eef6]">{fmt2(netGreeks.vega)}</span>
          </span>
          <span className="ml-auto text-[#7fa2b1]">
            Premium:{' '}
            <span className={`font-semibold ${netPremium >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]'}`}>
              {netPremium >= 0 ? '+' : ''}
              {netPremium.toFixed(2)}
            </span>{' '}
            <span className="text-[#4a7b8a]">{netPremium >= 0 ? 'credit' : 'debit'}</span>
          </span>
          {breakevens.length > 0 && (
            <span className="text-[#7fa2b1]">
              BE:{' '}
              <span className="font-semibold text-[#fde68a]">{breakevens.map((b) => b.toFixed(0)).join(' / ')}</span>
            </span>
          )}
        </div>
      )}

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

      {/* Simulation controls */}
      {activeSimView === 'montecarlo' && (<>
      <div className="mt-4 rounded-xl border border-[#1a3140] bg-[#09111a] p-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[10px] uppercase tracking-widest text-[#4a7b8a]">Simulate</span>

          {/* σ — Volatility */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[9px] text-[#4a7b8a] flex items-center gap-1">
              σ Volatility
              {sigmaSource === 'atm-iv' && (
                <span className="rounded bg-[#064e3b] px-1 text-[8px] text-[#4ade80]">ATM IV ✓</span>
              )}
              {sigmaSource === 'vix' && (
                <span className="rounded bg-[#1e3a5f] px-1 text-[8px] text-[#93c5fd]">India VIX live</span>
              )}
              {sigmaSource === 'default' && (
                <span className="rounded bg-[#1c1c1f] px-1 text-[8px] text-[#52525b]">default</span>
              )}
            </span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                step={0.1}
                min={1}
                max={200}
                value={parseFloat((simParams.sigma * 100).toFixed(1))}
                onChange={(e) => setSimParams((p) => ({ ...p, sigma: Math.max(0.01, parseFloat(e.target.value) || 15) / 100 }))}
                className="w-14 rounded border border-[#1a3140] bg-[#0d141d] px-1 py-0.5 text-[10px] text-[#4ade80] focus:outline-none"
              />
              <span className="text-[10px] text-[#4a7b8a]">%</span>
            </div>
            <span className="text-[8px] text-[#2a4a5a]">{INDEX_DEFAULTS[underlying].sigmaHint}</span>
          </div>

          {/* μ — Drift */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[9px] text-[#4a7b8a]">
              μ Annual Drift
              <span className="ml-1 text-[#2a4a5a]">(risk-neutral = 0)</span>
            </span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                step={1}
                min={-100}
                max={100}
                value={parseFloat((simParams.mu * 100).toFixed(1))}
                onChange={(e) => setSimParams((p) => ({ ...p, mu: (parseFloat(e.target.value) || 0) / 100 }))}
                className="w-14 rounded border border-[#1a3140] bg-[#0d141d] px-1 py-0.5 text-[10px] text-[#d8eef6] focus:outline-none"
              />
              <span className="text-[10px] text-[#4a7b8a]">%</span>
            </div>
            {/* Quick presets */}
            <div className="flex gap-1 mt-0.5">
              {[
                { label: '0% neutral', value: 0 },
                { label: '+12% bull', value: 0.12 },
                { label: '-10% bear', value: -0.10 },
              ].map(({ label, value }) => (
                <button
                  key={label}
                  onClick={() => setSimParams((p) => ({ ...p, mu: value }))}
                  className={`rounded px-1.5 py-0.5 text-[8px] transition-colors ${
                    Math.abs(simParams.mu - value) < 0.001
                      ? 'bg-[#1e3a5f] text-[#93c5fd]'
                      : 'bg-[#111e28] text-[#4a7b8a] hover:text-[#7fa2b1]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Paths */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[9px] text-[#4a7b8a]">Paths</span>
            <input
              type="number"
              step={100}
              min={100}
              max={2000}
              value={simParams.nPaths}
              onChange={(e) => setSimParams((p) => ({ ...p, nPaths: Math.max(100, parseInt(e.target.value) || 500) }))}
              className="w-16 rounded border border-[#1a3140] bg-[#0d141d] px-1 py-0.5 text-[10px] text-[#d8eef6] focus:outline-none"
            />
            <span className="text-[8px] text-[#2a4a5a]">500 recommended</span>
          </div>

          {/* Days */}
          <div className="flex flex-col gap-0.5">
            <span className="text-[9px] text-[#4a7b8a]">Days to Expiry</span>
            <input
              type="number"
              step={1}
              min={1}
              max={365}
              value={simParams.days}
              onChange={(e) => setSimParams((p) => ({ ...p, days: Math.max(1, parseInt(e.target.value) || 7) }))}
              className="w-14 rounded border border-[#1a3140] bg-[#0d141d] px-1 py-0.5 text-[10px] text-[#d8eef6] focus:outline-none"
            />
            <span className="text-[8px] text-[#2a4a5a]">auto from expiry</span>
          </div>

          <Button
            onClick={runSim}
            disabled={isSimRunning || !legs.length || !spot}
            size="sm"
            className="ml-auto h-8 bg-[#1e3a5f] px-4 text-[11px] text-[#93c5fd] hover:bg-[#1d4ed8] disabled:opacity-40"
          >
            {isSimRunning ? '⏳ Running…' : '▶ Run Simulation'}
          </Button>
        </div>
      </div>

      {/* Charts area */}
      {(simResult || isSimRunning) && (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {/* Path canvas */}
          <div
            className="relative rounded-xl border border-[#1a3140] bg-[#09111a] p-0 overflow-hidden"
            style={{ height: 220 }}
          >
            <canvas
              ref={pathCanvasRef}
              className="w-full h-full cursor-crosshair"
              style={{ display: 'block' }}
              onMouseMove={handlePathMove}
              onMouseLeave={handlePathLeave}
            />
            {isSimRunning && (
              <div className="absolute inset-0 flex items-center justify-center text-[10px] text-[#4a7b8a]">
                Simulating…
              </div>
            )}
            {/* Path hover tooltip */}
            {pathTooltip && !isSimRunning && (
              <div
                className="pointer-events-none absolute z-10 rounded-lg border border-[#1f3340] bg-[#0d141d]/95 px-2.5 py-2 text-[9px] shadow-xl"
                style={{
                  left: pathTooltip.x > 160 ? pathTooltip.x - 130 : pathTooltip.x + 12,
                  top: Math.max(4, pathTooltip.y - 20),
                }}
              >
                <div className="text-[#4a7b8a] mb-1">
                  Day {pathTooltip.dayFraction.toFixed(1)}&nbsp;·&nbsp;Mean{' '}
                  <span className="text-[#d8eef6]">{pathTooltip.meanPrice.toFixed(0)}</span>
                </div>
                {pathTooltip.top3Prices.map(({ rank, price, color }) => (
                  <div key={rank} className="flex items-center gap-1.5">
                    <span style={{ color }} className="font-bold">#{rank}</span>
                    <span className="text-[#94a3b8]">{price.toFixed(0)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Histogram canvas + stats */}
          <div className="flex flex-col gap-2">
            <div
              className="relative rounded-xl border border-[#1a3140] bg-[#09111a] overflow-hidden"
              style={{ height: 130 }}
            >
              <canvas
                ref={histCanvasRef}
                className="w-full h-full cursor-crosshair"
                style={{ display: 'block' }}
                onMouseMove={handleHistMove}
                onMouseLeave={handleHistLeave}
              />
              {/* Histogram hover tooltip */}
              {histTooltip && !isSimRunning && (
                <div
                  className="pointer-events-none absolute z-10 rounded-lg border border-[#1f3340] bg-[#0d141d]/95 px-2.5 py-1.5 text-[9px] shadow-xl"
                  style={{
                    left: histTooltip.x > 120 ? histTooltip.x - 110 : histTooltip.x + 8,
                    top: Math.max(4, histTooltip.y - 50),
                  }}
                >
                  <div style={{ color: histTooltip.isProfit ? '#4ade80' : '#f87171' }} className="font-semibold">
                    {histTooltip.isProfit ? 'Profit' : 'Loss'} zone
                  </div>
                  <div className="text-[#94a3b8]">
                    {fmtPnl(histTooltip.pnlMin)} → {fmtPnl(histTooltip.pnlMax)}
                  </div>
                  <div className="text-[#d8eef6]">
                    {histTooltip.count} paths ({histTooltip.pct.toFixed(1)}%)
                  </div>
                </div>
              )}
            </div>

            {simStats && (
              <div className="grid grid-cols-3 gap-1.5">
                {[
                  { label: 'Avg P&L', value: fmtPnl(simStats.avg), color: simStats.avg >= 0 ? '#4ade80' : '#f87171' },
                  { label: 'Median', value: fmtPnl(simStats.median), color: simStats.median >= 0 ? '#4ade80' : '#f87171' },
                  { label: 'Win % (MC)', value: `${simStats.winPct.toFixed(1)}%`, color: simStats.winPct >= 50 ? '#4ade80' : '#f87171' },
                  { label: 'POP (CDF)', value: pop !== null ? `${(pop * 100).toFixed(1)}%` : '—', color: pop !== null && pop >= 0.5 ? '#4ade80' : '#f87171' },
                  { label: 'Max Loss', value: fmtPnl(simStats.maxLoss), color: '#f87171' },
                  { label: 'Max Payoff', value: fmtPnl(simStats.maxPayoff), color: '#4ade80' },
                  { label: 'Paths', value: simStats.total.toString(), color: '#94a3b8' },
                ].map(({ label, value, color }) => (
                  <div
                    key={label}
                    className="rounded-lg border border-[#1a3140] bg-[#09111a] px-2 py-1.5 text-center"
                  >
                    <div className="text-[8px] uppercase tracking-widest text-[#4a7b8a]">{label}</div>
                    <div className="mt-0.5 text-[11px] font-semibold" style={{ color }}>{value}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Top 3 profitable paths */}
      {top3.length > 0 && !isSimRunning && (
        <div className="mt-3 rounded-xl border border-[#1a3140] bg-[#09111a] p-3">
          <div className="mb-2 text-[9px] uppercase tracking-[0.24em] text-[#4a7b8a]">
            Top 3 Profitable Paths — tweak strategy to shift more paths into this zone
          </div>
          <div className="grid grid-cols-3 gap-2">
            {top3.map((t) => (
              <div
                key={t.rank}
                className="rounded-lg border px-2.5 py-2"
                style={{ borderColor: `${t.color}40`, background: `${t.color}08` }}
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="text-[11px] font-bold" style={{ color: t.color }}>
                    #{t.rank}
                  </span>
                  <span className="text-[9px] text-[#4a7b8a]">Best path</span>
                </div>
                <div className="text-[12px] font-semibold" style={{ color: t.color }}>
                  +{t.finalPnl.toFixed(0)}
                </div>
                <div className="mt-0.5 text-[9px] text-[#4a7b8a]">
                  Spot → <span className="text-[#94a3b8]">{t.finalSpot.toFixed(0)}</span>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-2 text-[8px] text-[#2a4a5a] leading-relaxed">
            Gold/silver/bronze lines on the chart show these exact paths.
            Hover the chart to see prices at each time step.
          </div>
        </div>
      )}
      </> )}

      {/* Empty state */}
      {!legs.length && (
        <div className="mt-4 rounded-2xl border border-dashed border-[#223847] bg-[#09111a] p-6 text-center text-[11px] text-[#6b8797]">
          Select a strategy or add legs manually, then click ▶ Run to simulate
        </div>
      )}
    </section>
  )
}
