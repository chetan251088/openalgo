// frontend/src/components/market-pulse/PayoffChart.tsx
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

interface PayoffData {
  spotPrices: number[]
  expiryPnl: number[]
  targetPnl: number[]    // empty when targetDte === 0
  xLo: number
  xHi: number
  yLo: number
  yHi: number
  sigmaAdj: number
  T_target: number
  targetDte: number
}

interface HoverTooltip {
  x: number
  y: number
  spotPrice: number
  expiryPnl: number
  targetPnl: number | null
}

// ---------------------------------------------------------------------------
// Canvas helper
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
// Computation — separated from drawing for hover reuse
// ---------------------------------------------------------------------------

function computePayoffData(
  legs: BsmLeg[],
  spot: number,
  sigma: number,
  targetDte: number,
  ivOffsetPct: number,
): PayoffData {
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

    expiryPnl.push(
      legs.reduce((acc, leg) => {
        const intr = leg.type === 'CE' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0)
        return acc + (leg.action === 'buy' ? 1 : -1) * leg.qty * (intr - leg.premium)
      }, 0),
    )

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
  // Dynamic Y padding: 20% of full range, minimum 100 points
  const ySpan = rawMax - rawMin || 1
  const yPad = Math.max(ySpan * 0.20, 100)

  return {
    spotPrices, expiryPnl, targetPnl,
    xLo, xHi,
    yLo: rawMin - yPad,
    yHi: rawMax + yPad,
    sigmaAdj, T_target, targetDte,
  }
}

// ---------------------------------------------------------------------------
// Max Profit / Max Loss — wide-range expiry scan
// ---------------------------------------------------------------------------

interface MaxStats {
  maxPnl: number | null   // null = unlimited
  minPnl: number | null   // null = unlimited
}

function calcMaxStats(legs: BsmLeg[], spot: number): MaxStats {
  if (!legs.length || !spot) return { maxPnl: null, minPnl: null }

  const SAMPLES = 1000
  const xLo = spot * 0.04        // near-zero (put-heavy strategies show max here)
  const xHi = spot * 4.0         // far OTM call strikes
  const logStep = Math.log(xHi / xLo) / SAMPLES

  const pnlAt = (S: number) =>
    legs.reduce((acc, leg) => {
      const intr = leg.type === 'CE' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0)
      return acc + (leg.action === 'buy' ? 1 : -1) * leg.qty * (intr - leg.premium)
    }, 0)

  const values: number[] = []
  for (let i = 0; i <= SAMPLES; i++) {
    values.push(pnlAt(xLo * Math.exp(logStep * i)))
  }

  const maxVal = Math.max(...values)
  const minVal = Math.min(...values)

  // Detect unlimited: check if slope at both edges is still growing
  // Threshold: > 0.5% of spot per step means it's accelerating at the boundary
  const thresh = spot * 0.005
  const rightSlope = values[SAMPLES] - values[SAMPLES - 5]
  const leftSlope  = values[0] - values[5]   // positive = rising as S→0

  const unlimitedProfit = rightSlope > thresh || leftSlope > thresh
  const unlimitedLoss   = rightSlope < -thresh || leftSlope < -thresh

  return {
    maxPnl: unlimitedProfit ? null : maxVal,
    minPnl: unlimitedLoss   ? null : minVal,
  }
}

// ---------------------------------------------------------------------------
// Canvas draw — accepts precomputed data + optional hover index
// ---------------------------------------------------------------------------

const PAD = { l: 20, r: 20, t: 22, b: 20 } as const

function drawPayoffFromData(
  canvas: HTMLCanvasElement,
  data: PayoffData,
  breakevens: number[],
  spot: number,
  dte: number,
  hoverIdx?: number,
) {
  const ctx = setupCanvas(canvas)
  const W = canvas.getBoundingClientRect().width
  const H = canvas.getBoundingClientRect().height

  ctx.fillStyle = '#09111a'
  ctx.fillRect(0, 0, W, H)

  const { spotPrices, expiryPnl, targetPnl, xLo, xHi, yLo, yHi, sigmaAdj, T_target, targetDte } = data

  if (!spotPrices.length) return

  const yRange = yHi - yLo || 1
  const iW = W - PAD.l - PAD.r
  const iH = H - PAD.t - PAD.b

  const toX = (S: number) => PAD.l + ((S - xLo) / (xHi - xLo)) * iW
  const toY = (pnl: number) => PAD.t + iH - ((pnl - yLo) / yRange) * iH
  const clampX = (x: number) => Math.max(PAD.l, Math.min(PAD.l + iW, x))

  // SD cones
  const coneDte = targetDte > 0 ? targetDte : dte
  const cone = calcSDCone(spot, sigmaAdj, coneDte)
  const lo2X = clampX(toX(cone.spotLo2))
  const lo1X = clampX(toX(cone.spotLo1))
  const hi1X = clampX(toX(cone.spotHi1))
  const hi2X = clampX(toX(cone.spotHi2))

  ctx.fillStyle = 'rgba(148,163,184,0.05)'
  ctx.fillRect(lo2X, PAD.t, lo1X - lo2X, iH)
  ctx.fillRect(hi1X, PAD.t, hi2X - hi1X, iH)
  ctx.fillStyle = 'rgba(148,163,184,0.11)'
  ctx.fillRect(lo1X, PAD.t, clampX(toX(spot)) - lo1X, iH)
  ctx.fillRect(clampX(toX(spot)), PAD.t, hi1X - clampX(toX(spot)), iH)

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

  // Expiry payoff (yellow — on top)
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

  // Y-axis ticks (3–4 price labels for quick reading)
  const tickCount = 4
  ctx.fillStyle = 'rgba(100,140,160,0.6)'
  ctx.font = '8px monospace'
  ctx.textAlign = 'right'
  for (let t = 0; t <= tickCount; t++) {
    const pnlVal = yLo + (yHi - yLo) * (t / tickCount)
    const ty = toY(pnlVal)
    if (ty < PAD.t || ty > PAD.t + iH) continue
    const label = pnlVal >= 0 ? `+${pnlVal.toFixed(0)}` : pnlVal.toFixed(0)
    ctx.fillText(label, PAD.l + iW, ty + 3)
  }
  ctx.textAlign = 'left'

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

  // Hover crosshair
  if (hoverIdx !== undefined) {
    const hx = toX(spotPrices[hoverIdx])

    // Vertical dashed line
    ctx.beginPath()
    ctx.setLineDash([3, 3])
    ctx.strokeStyle = 'rgba(148,194,209,0.55)'
    ctx.lineWidth = 1
    ctx.moveTo(hx, PAD.t)
    ctx.lineTo(hx, PAD.t + iH)
    ctx.stroke()
    ctx.setLineDash([])

    // Dot on expiry curve
    const ePnl = expiryPnl[hoverIdx]
    ctx.beginPath()
    ctx.arc(hx, toY(ePnl), 4, 0, Math.PI * 2)
    ctx.fillStyle = '#fde68a'
    ctx.fill()
    ctx.strokeStyle = '#09111a'
    ctx.lineWidth = 1.5
    ctx.stroke()

    // Dot on T+n curve (if visible)
    if (targetPnl.length && T_target > 0) {
      const tPnl = targetPnl[hoverIdx]
      ctx.beginPath()
      ctx.arc(hx, toY(tPnl), 4, 0, Math.PI * 2)
      ctx.fillStyle = '#22d3ee'
      ctx.fill()
      ctx.strokeStyle = '#09111a'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }
  }

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
    const [tooltip, setTooltip] = useState<HoverTooltip | null>(null)

    const canvasRef    = useRef<HTMLCanvasElement | null>(null)
    const rafRef       = useRef<number>(0)
    const hoverRafRef  = useRef<number>(0)
    const payoffDataRef = useRef<PayoffData | null>(null)
    const hoverIdxRef  = useRef<number | null>(null)

    // Keep targetDte clamped when dte prop changes (expiry switch)
    useEffect(() => {
      setTargetDte((prev) => Math.min(prev, Math.max(0, dte)))
    }, [dte])

    const redraw = useCallback(() => {
      if (!canvasRef.current || !spot) return
      const data = computePayoffData(legs, spot, sigma, targetDte, ivOffsetPct)
      payoffDataRef.current = data
      cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => {
        if (!canvasRef.current || !payoffDataRef.current) return
        drawPayoffFromData(
          canvasRef.current, payoffDataRef.current, breakevens,
          spot, dte, hoverIdxRef.current ?? undefined,
        )
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

    // Mouse handlers
    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current
      const data = payoffDataRef.current
      if (!canvas || !data) return

      const rect = canvas.getBoundingClientRect()
      const relX = e.clientX - rect.left
      const relY = e.clientY - rect.top
      const iW = rect.width - PAD.l - PAD.r
      const { xLo, xHi, spotPrices, expiryPnl, targetPnl, T_target } = data

      // Map pixel → spot index
      const S = xLo + ((relX - PAD.l) / iW) * (xHi - xLo)
      const idx = Math.max(0, Math.min(spotPrices.length - 1,
        Math.round((S - xLo) / (xHi - xLo) * (spotPrices.length - 1))
      ))

      hoverIdxRef.current = idx
      cancelAnimationFrame(hoverRafRef.current)
      hoverRafRef.current = requestAnimationFrame(() => {
        if (!canvasRef.current || !payoffDataRef.current) return
        drawPayoffFromData(
          canvasRef.current, payoffDataRef.current, breakevens,
          spot, dte, idx,
        )
      })

      setTooltip({
        x: relX,
        y: relY,
        spotPrice: spotPrices[idx],
        expiryPnl: expiryPnl[idx],
        targetPnl: T_target > 0 && targetPnl.length ? targetPnl[idx] : null,
      })
    }, [breakevens, spot, dte])

    const handleMouseLeave = useCallback(() => {
      hoverIdxRef.current = null
      setTooltip(null)
      cancelAnimationFrame(hoverRafRef.current)
      hoverRafRef.current = requestAnimationFrame(() => {
        if (!canvasRef.current || !payoffDataRef.current) return
        drawPayoffFromData(
          canvasRef.current, payoffDataRef.current, breakevens,
          spot, dte, undefined,
        )
      })
    }, [breakevens, spot, dte])

    // Max / Min stats — scanned over a wide price range at expiry
    const maxStats = useMemo(() => calcMaxStats(legs, spot), [legs, spot])

    const fmtPnl = (v: number | null, sign: 1 | -1) => {
      if (v === null) return 'Unlimited'
      const raw = sign === 1 ? v : v   // already correct from calcMaxStats
      return `${raw >= 0 ? '+' : ''}${raw.toFixed(0)}`
    }

    const riskReward = useMemo(() => {
      if (maxStats.maxPnl === null || maxStats.minPnl === null) return null
      if (maxStats.maxPnl <= 0 || maxStats.minPnl >= 0) return null
      const reward = maxStats.maxPnl
      const risk   = Math.abs(maxStats.minPnl)
      return (reward / risk).toFixed(2)
    }, [maxStats])

    return (
      <div className="flex flex-col gap-2">
        {/* Canvas */}
        <div
          className="relative overflow-hidden rounded-xl border border-[#1a3140] bg-[#09111a]"
          style={{ height: 220 }}
        >
          <canvas
            ref={canvasRef}
            className="w-full h-full cursor-crosshair"
            style={{ display: 'block' }}
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
          />

          {/* Hover tooltip */}
          {tooltip && (
            <div
              className="pointer-events-none absolute z-10 rounded-lg border border-[#1f3340] bg-[#0d141d]/95 px-2.5 py-2 text-[9px] shadow-xl"
              style={{
                left: tooltip.x > 160 ? tooltip.x - 130 : tooltip.x + 12,
                top: Math.max(4, tooltip.y - 20),
              }}
            >
              <div className="text-[#4a7b8a] mb-1">
                Spot <span className="text-[#d8eef6] font-semibold">{tooltip.spotPrice.toFixed(0)}</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-[#fde68a] inline-block" />
                <span className="text-[#7fa2b1]">Expiry</span>
                <span
                  className="font-semibold ml-1"
                  style={{ color: tooltip.expiryPnl >= 0 ? '#4ade80' : '#f87171' }}
                >
                  {tooltip.expiryPnl >= 0 ? '+' : ''}{tooltip.expiryPnl.toFixed(1)}
                </span>
              </div>
              {tooltip.targetPnl !== null && (
                <div className="flex items-center gap-1 mt-0.5">
                  <span className="w-2 h-2 rounded-full bg-[#22d3ee] inline-block" />
                  <span className="text-[#7fa2b1]">T+{targetDte}d</span>
                  <span
                    className="font-semibold ml-1"
                    style={{ color: tooltip.targetPnl >= 0 ? '#4ade80' : '#f87171' }}
                  >
                    {tooltip.targetPnl >= 0 ? '+' : ''}{tooltip.targetPnl.toFixed(1)}
                  </span>
                </div>
              )}
            </div>
          )}

          {!legs.length && (
            <div className="absolute inset-0 flex items-center justify-center text-[10px] text-[#4a7b8a]">
              Add legs to see payoff chart
            </div>
          )}
        </div>

        {/* Max Profit / Max Loss / Risk:Reward strip */}
        {legs.length > 0 && (
          <div className="grid grid-cols-3 gap-1.5">
            <div className="rounded-lg border border-[#1a3140] bg-[#09111a] px-2.5 py-1.5 text-center">
              <div className="text-[8px] uppercase tracking-widest text-[#4a7b8a]">Max Profit</div>
              <div className="mt-0.5 text-[11px] font-semibold text-[#4ade80]">
                {maxStats.maxPnl === null ? 'Unlimited ↑' : fmtPnl(maxStats.maxPnl, 1)}
              </div>
            </div>
            <div className="rounded-lg border border-[#1a3140] bg-[#09111a] px-2.5 py-1.5 text-center">
              <div className="text-[8px] uppercase tracking-widest text-[#4a7b8a]">Max Loss</div>
              <div className="mt-0.5 text-[11px] font-semibold text-[#f87171]">
                {maxStats.minPnl === null ? 'Unlimited ↓' : fmtPnl(maxStats.minPnl, -1)}
              </div>
            </div>
            <div className="rounded-lg border border-[#1a3140] bg-[#09111a] px-2.5 py-1.5 text-center">
              <div className="text-[8px] uppercase tracking-widest text-[#4a7b8a]">Risk:Reward</div>
              <div className="mt-0.5 text-[11px] font-semibold text-[#fbbf24]">
                {riskReward !== null
                  ? `1 : ${riskReward}`
                  : maxStats.maxPnl === null && maxStats.minPnl === null
                    ? '∞ : ∞'
                    : maxStats.maxPnl === null
                      ? '∞ : 1'
                      : maxStats.minPnl === null
                        ? '1 : ∞'
                        : '—'}
              </div>
            </div>
          </div>
        )}

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
                  {ivOffsetPct > 0 ? '+' : ''}{ivOffsetPct}%
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
