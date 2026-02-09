import { useCallback, useEffect, useRef, useState } from 'react'
import {
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type MouseEventParams,
} from 'lightweight-charts'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { cn } from '@/lib/utils'
import type { ActiveSide } from '@/types/scalping'

const TICK_SIZE = 0.05

function roundToTick(price: number): number {
  return Math.round(price / TICK_SIZE) * TICK_SIZE
}

interface ChartOrderOverlayProps {
  chartRef: React.RefObject<IChartApi | null>
  seriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>
  side: ActiveSide
  containerRef: React.RefObject<HTMLDivElement | null>
}

interface OverlayPos {
  y: number
  price: number
}

export function ChartOrderOverlay({
  chartRef,
  seriesRef,
  side,
  containerRef,
}: ChartOrderOverlayProps) {
  const activeSide = useScalpingStore((s) => s.activeSide)
  const orderType = useScalpingStore((s) => s.orderType)
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const symbol = useScalpingStore((s) =>
    side === 'CE' ? s.selectedCESymbol : s.selectedPESymbol
  )

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const updateVirtualTPSL = useVirtualOrderStore((s) => s.updateVirtualTPSL)

  // Price line refs
  const trackingLineRef = useRef<IPriceLine | null>(null)
  const entryLineRef = useRef<IPriceLine | null>(null)
  const tpLineRef = useRef<IPriceLine | null>(null)
  const slLineRef = useRef<IPriceLine | null>(null)

  // Overlay position state for draggable divs
  const [tpOverlay, setTpOverlay] = useState<OverlayPos | null>(null)
  const [slOverlay, setSlOverlay] = useState<OverlayPos | null>(null)
  const [entryOverlay, setEntryOverlay] = useState<OverlayPos | null>(null)

  // Drag state
  const dragRef = useRef<{
    type: 'tp' | 'sl'
    startY: number
    startPrice: number
    tpslId: string | null
  } | null>(null)

  const isActive = activeSide === side
  const isLimitOrTrigger = orderType === 'LIMIT' || orderType === 'TRIGGER'

  // Find active virtualTPSL for this symbol
  const activeTPSL = symbol
    ? Object.values(virtualTPSL).find((o) => o.symbol === symbol)
    : undefined

  // ——— Helper: remove a price line safely ———
  const removeLine = useCallback(
    (lineRef: React.MutableRefObject<IPriceLine | null>) => {
      if (lineRef.current && seriesRef.current) {
        try {
          seriesRef.current.removePriceLine(lineRef.current)
        } catch {
          // line may already be removed
        }
        lineRef.current = null
      }
    },
    [seriesRef]
  )

  // ——— Helper: create or update a price line ———
  const upsertLine = useCallback(
    (
      lineRef: React.MutableRefObject<IPriceLine | null>,
      price: number,
      color: string,
      style: LineStyle,
      width: 1 | 2 | 3 | 4,
      title: string
    ) => {
      if (!seriesRef.current) return
      if (lineRef.current) {
        lineRef.current.applyOptions({ price, color, lineStyle: style, lineWidth: width, title })
      } else {
        lineRef.current = seriesRef.current.createPriceLine({
          price,
          color,
          lineStyle: style,
          lineWidth: width,
          title,
          axisLabelVisible: true,
        })
      }
    },
    [seriesRef]
  )

  // ——— Helper: convert price → Y coordinate ———
  const priceToY = useCallback(
    (price: number): number | null => {
      if (!seriesRef.current) return null
      const coord = seriesRef.current.priceToCoordinate(price)
      return coord
    },
    [seriesRef]
  )

  // ——— Update overlay positions from price lines ———
  const updateOverlayPositions = useCallback(() => {
    if (!seriesRef.current) return

    if (entryLineRef.current) {
      const price = entryLineRef.current.options().price
      const y = priceToY(price)
      if (y != null) setEntryOverlay({ y, price })
    } else {
      setEntryOverlay(null)
    }

    if (tpLineRef.current) {
      const price = tpLineRef.current.options().price
      const y = priceToY(price)
      if (y != null) setTpOverlay({ y, price })
    } else {
      setTpOverlay(null)
    }

    if (slLineRef.current) {
      const price = slLineRef.current.options().price
      const y = priceToY(price)
      if (y != null) setSlOverlay({ y, price })
    } else {
      setSlOverlay(null)
    }
  }, [priceToY])

  // ——— A. Crosshair tracking line (LIMIT/TRIGGER) ———
  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series) return
    if (!isActive || !isLimitOrTrigger) {
      removeLine(trackingLineRef)
      return
    }

    const handler = (param: MouseEventParams) => {
      if (!param.point || !series) {
        removeLine(trackingLineRef)
        return
      }

      const price = series.coordinateToPrice(param.point.y)
      if (price == null) {
        removeLine(trackingLineRef)
        return
      }

      const rounded = roundToTick(price as number)
      const color = '#a1a1aa'
      upsertLine(trackingLineRef, rounded, color, LineStyle.Dashed, 1, rounded.toFixed(2))
    }

    chart.subscribeCrosshairMove(handler)
    return () => {
      chart.unsubscribeCrosshairMove(handler)
      removeLine(trackingLineRef)
    }
  }, [chartRef, seriesRef, isActive, isLimitOrTrigger, removeLine, upsertLine])

  // ——— B. Click to place entry (LIMIT/TRIGGER) ———
  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series) return
    if (!isActive || !isLimitOrTrigger) return

    const handler = (param: MouseEventParams) => {
      if (!param.point || !series) return

      const price = series.coordinateToPrice(param.point.y)
      if (price == null) return

      const rounded = roundToTick(price as number)
      setLimitPrice(rounded)

      // Remove tracking line
      removeLine(trackingLineRef)

      // Place entry line
      const entryColor = orderType === 'LIMIT' ? '#06b6d4' : '#ffa500'
      const label = orderType === 'LIMIT' ? `LIMIT @ ${rounded.toFixed(2)}` : `TRIGGER @ ${rounded.toFixed(2)}`
      upsertLine(entryLineRef, rounded, entryColor, LineStyle.Dashed, 2, label)

      // Place TP/SL lines
      const tpPrice = roundToTick(rounded + tpPoints)
      const slPrice = roundToTick(rounded - slPoints)
      upsertLine(tpLineRef, tpPrice, '#00ff88', LineStyle.Dashed, 1, `TP @ ${tpPrice.toFixed(2)}`)
      upsertLine(slLineRef, slPrice, '#ff4560', LineStyle.Dashed, 1, `SL @ ${slPrice.toFixed(2)}`)

      updateOverlayPositions()
    }

    chart.subscribeClick(handler)
    return () => {
      chart.unsubscribeClick(handler)
    }
  }, [
    chartRef, seriesRef, isActive, isLimitOrTrigger, orderType,
    tpPoints, slPoints, setLimitPrice, removeLine, upsertLine, updateOverlayPositions,
  ])

  // ——— C. Draw lines from active virtualTPSL ———
  useEffect(() => {
    if (!seriesRef.current || !activeTPSL) return

    const entryColor = activeTPSL.action === 'BUY' ? '#00ff88' : '#ff4560'
    const label = `${activeTPSL.action} @ ${activeTPSL.entryPrice.toFixed(2)}`
    upsertLine(entryLineRef, activeTPSL.entryPrice, entryColor, LineStyle.Dotted, 2, label)

    if (activeTPSL.tpPrice != null) {
      upsertLine(tpLineRef, activeTPSL.tpPrice, '#00ff88', LineStyle.Dashed, 1, `TP @ ${activeTPSL.tpPrice.toFixed(2)}`)
    }
    if (activeTPSL.slPrice != null) {
      upsertLine(slLineRef, activeTPSL.slPrice, '#ff4560', LineStyle.Dashed, 1, `SL @ ${activeTPSL.slPrice.toFixed(2)}`)
    }

    updateOverlayPositions()
  }, [activeTPSL, seriesRef, upsertLine, updateOverlayPositions])

  // ——— D. Update overlay Y positions on chart scroll/zoom ———
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const handler = () => updateOverlayPositions()

    chart.subscribeCrosshairMove(handler)
    const ts = chart.timeScale()
    ts.subscribeVisibleLogicalRangeChange(handler)

    return () => {
      chart.unsubscribeCrosshairMove(handler)
      ts.unsubscribeVisibleLogicalRangeChange(handler)
    }
  }, [chartRef, updateOverlayPositions])

  // ——— E. Cleanup lines when symbol changes ———
  useEffect(() => {
    return () => {
      removeLine(trackingLineRef)
      removeLine(entryLineRef)
      removeLine(tpLineRef)
      removeLine(slLineRef)
      setTpOverlay(null)
      setSlOverlay(null)
      setEntryOverlay(null)
    }
  }, [symbol, removeLine])

  // ——— F. Cleanup when order type switches to MARKET ———
  useEffect(() => {
    if (orderType === 'MARKET') {
      // Remove pending LIMIT/TRIGGER lines (but not position lines)
      if (!activeTPSL) {
        removeLine(entryLineRef)
        removeLine(tpLineRef)
        removeLine(slLineRef)
        setTpOverlay(null)
        setSlOverlay(null)
        setEntryOverlay(null)
      }
    }
  }, [orderType, activeTPSL, removeLine])

  // ——— G. Drag handlers for TP/SL overlays ———
  const handleDragStart = useCallback(
    (type: 'tp' | 'sl', e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const overlay = type === 'tp' ? tpOverlay : slOverlay
      if (!overlay) return

      dragRef.current = {
        type,
        startY: e.clientY,
        startPrice: overlay.price,
        tpslId: activeTPSL?.id ?? null,
      }

      const handleMove = (ev: MouseEvent) => {
        const drag = dragRef.current
        if (!drag || !seriesRef.current) return

        const deltaY = ev.clientY - drag.startY
        const startCoord = seriesRef.current.priceToCoordinate(drag.startPrice)
        if (startCoord == null) return

        const newCoord = (startCoord as number) + deltaY
        const newPrice = seriesRef.current.coordinateToPrice(newCoord)
        if (newPrice == null) return

        const rounded = roundToTick(newPrice as number)
        const lineRef = drag.type === 'tp' ? tpLineRef : slLineRef
        const label = drag.type === 'tp' ? `TP @ ${rounded.toFixed(2)}` : `SL @ ${rounded.toFixed(2)}`

        if (lineRef.current) {
          lineRef.current.applyOptions({ price: rounded, title: label })
        }

        const y = seriesRef.current.priceToCoordinate(rounded)
        if (y != null) {
          if (drag.type === 'tp') setTpOverlay({ y: y as number, price: rounded })
          else setSlOverlay({ y: y as number, price: rounded })
        }
      }

      const handleUp = () => {
        const drag = dragRef.current
        if (drag && drag.tpslId) {
          // Persist dragged price to virtualOrderStore
          const overlay = drag.type === 'tp' ? tpOverlay : slOverlay
          if (overlay) {
            const updates =
              drag.type === 'tp'
                ? { tpPrice: overlay.price, tpPoints: activeTPSL ? overlay.price - activeTPSL.entryPrice : tpPoints }
                : { slPrice: overlay.price, slPoints: activeTPSL ? activeTPSL.entryPrice - overlay.price : slPoints }
            updateVirtualTPSL(drag.tpslId, updates)
          }
        }
        dragRef.current = null
        document.removeEventListener('mousemove', handleMove)
        document.removeEventListener('mouseup', handleUp)
      }

      document.addEventListener('mousemove', handleMove)
      document.addEventListener('mouseup', handleUp)
    },
    [tpOverlay, slOverlay, activeTPSL, tpPoints, slPoints, seriesRef, updateVirtualTPSL]
  )

  // ——— H. Remove TP or SL overlay on close button ———
  const handleRemoveOverlay = useCallback(
    (type: 'tp' | 'sl') => {
      const lineRef = type === 'tp' ? tpLineRef : slLineRef
      removeLine(lineRef)
      if (type === 'tp') setTpOverlay(null)
      else setSlOverlay(null)

      if (activeTPSL) {
        const updates =
          type === 'tp'
            ? { tpPrice: null, tpPoints: 0 }
            : { slPrice: null, slPoints: 0 }
        updateVirtualTPSL(activeTPSL.id, updates)
      }
    },
    [removeLine, activeTPSL, updateVirtualTPSL]
  )

  // Don't render overlays if no chart container
  if (!containerRef.current) return null

  return (
    <>
      {/* TP Draggable Overlay */}
      {tpOverlay && (
        <div
          className={cn(
            'absolute left-0 right-[60px] z-[15] flex items-center pointer-events-auto',
            dragRef.current?.type === 'tp' && 'opacity-80'
          )}
          style={{ top: tpOverlay.y - 10 }}
        >
          <div
            className="flex items-center gap-1 cursor-ns-resize select-none"
            onMouseDown={(e) => handleDragStart('tp', e)}
          >
            <span className="text-[10px] font-mono px-1 py-0.5 rounded-sm bg-green-500/15 text-green-400">
              TP @ {tpOverlay.price.toFixed(2)}
            </span>
            <button
              type="button"
              className="text-[10px] text-green-400/60 hover:text-green-400 px-0.5"
              onClick={(e) => {
                e.stopPropagation()
                handleRemoveOverlay('tp')
              }}
            >
              x
            </button>
          </div>
        </div>
      )}

      {/* SL Draggable Overlay */}
      {slOverlay && (
        <div
          className={cn(
            'absolute left-0 right-[60px] z-[15] flex items-center pointer-events-auto',
            dragRef.current?.type === 'sl' && 'opacity-80'
          )}
          style={{ top: slOverlay.y - 10 }}
        >
          <div
            className="flex items-center gap-1 cursor-ns-resize select-none"
            onMouseDown={(e) => handleDragStart('sl', e)}
          >
            <span className="text-[10px] font-mono px-1 py-0.5 rounded-sm bg-red-500/15 text-red-400">
              SL @ {slOverlay.price.toFixed(2)}
            </span>
            <button
              type="button"
              className="text-[10px] text-red-400/60 hover:text-red-400 px-0.5"
              onClick={(e) => {
                e.stopPropagation()
                handleRemoveOverlay('sl')
              }}
            >
              x
            </button>
          </div>
        </div>
      )}

      {/* Entry Overlay with PnL */}
      {entryOverlay && activeTPSL && (
        <div
          className="absolute left-0 right-[60px] z-[15] flex items-center pointer-events-none"
          style={{ top: entryOverlay.y - 10 }}
        >
          <span
            className={cn(
              'text-[10px] font-mono px-1 py-0.5 rounded-sm',
              activeTPSL.action === 'BUY'
                ? 'bg-green-500/15 text-green-400'
                : 'bg-red-500/15 text-red-400'
            )}
          >
            {activeTPSL.action} @ {entryOverlay.price.toFixed(2)}
          </span>
        </div>
      )}
    </>
  )
}
