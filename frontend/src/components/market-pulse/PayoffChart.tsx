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
