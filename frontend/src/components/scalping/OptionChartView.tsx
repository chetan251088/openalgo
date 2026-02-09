import { useCallback, useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from 'lightweight-charts'
import { useThemeStore } from '@/stores/themeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useCandleBuilder } from '@/hooks/useCandleBuilder'
import { ChartOrderOverlay } from './ChartOrderOverlay'
import type { ActiveSide } from '@/types/scalping'
import type { Candle } from '@/lib/candleUtils'
import { cn } from '@/lib/utils'

function getChartColors(isDark: boolean) {
  return {
    bg: 'transparent',
    text: isDark ? '#a1a1aa' : '#71717a',
    grid: isDark ? 'rgba(161, 161, 170, 0.06)' : 'rgba(0, 0, 0, 0.04)',
    border: isDark ? 'rgba(161, 161, 170, 0.12)' : 'rgba(0, 0, 0, 0.08)',
    crosshair: isDark ? 'rgba(161, 161, 170, 0.4)' : 'rgba(0, 0, 0, 0.3)',
    upColor: '#22c55e',
    downColor: '#ef4444',
  }
}

interface OptionChartViewProps {
  side: ActiveSide
}

export function OptionChartView({ side }: OptionChartViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  const isDark = useThemeStore((s) => s.mode === 'dark')
  const activeSide = useScalpingStore((s) => s.activeSide)
  const setActiveSide = useScalpingStore((s) => s.setActiveSide)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const chartInterval = useScalpingStore((s) => s.chartInterval)
  const symbol = useScalpingStore((s) =>
    side === 'CE' ? s.selectedCESymbol : s.selectedPESymbol
  )

  const isActive = activeSide === side

  // Handle chart click to set active side
  const handleClick = useCallback(() => {
    setActiveSide(side)
  }, [side, setActiveSide])

  // Candle update callback - ref-based, no re-render
  const handleCandleUpdate = useCallback((candle: Candle, _isNew: boolean) => {
    if (!candleSeriesRef.current) return
    candleSeriesRef.current.update({
      time: candle.time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })
  }, [])

  const { isConnected, reset: resetCandles } = useCandleBuilder({
    symbol: symbol ?? '',
    exchange: optionExchange,
    intervalSec: chartInterval,
    enabled: !!symbol,
    onCandleUpdate: handleCandleUpdate,
  })

  // Reset candle data when interval changes
  useEffect(() => {
    resetCandles()
  }, [chartInterval, resetCandles])

  // Initialize chart
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const colors = getChartColors(isDark)

    const chart = createChart(container, {
      width: container.offsetWidth,
      height: container.offsetHeight,
      layout: {
        background: { type: ColorType.Solid, color: colors.bg },
        textColor: colors.text,
        fontFamily: 'ui-monospace, SFMono-Regular, monospace',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      rightPriceScale: {
        borderColor: colors.border,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: colors.border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: number) => {
          const d = new Date(time * 1000)
          const ist = new Date(d.getTime() + 5.5 * 60 * 60 * 1000)
          return `${ist.getUTCHours().toString().padStart(2, '0')}:${ist.getUTCMinutes().toString().padStart(2, '0')}`
        },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { width: 1, color: colors.crosshair, style: 2 },
        horzLine: { width: 1, color: colors.crosshair, style: 2 },
      },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: colors.upColor,
      downColor: colors.downColor,
      borderVisible: false,
      wickUpColor: colors.upColor,
      wickDownColor: colors.downColor,
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (chartRef.current && container) {
        const w = container.offsetWidth
        const h = container.offsetHeight
        if (w > 0 && h > 0) {
          chartRef.current.applyOptions({ width: w, height: h })
        }
      }
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
    }
  }, [isDark, chartInterval])

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: chart click for active side
    <div
      className={cn(
        'relative h-full w-full min-w-0 min-h-0 overflow-hidden cursor-pointer',
        isActive && 'ring-1 ring-primary/40'
      )}
      onClick={handleClick}
    >
      <div ref={containerRef} className="h-full w-full" />

      {/* Order overlay: tracking line, entry/TP/SL lines, draggable overlays */}
      <ChartOrderOverlay
        chartRef={chartRef}
        seriesRef={candleSeriesRef}
        side={side}
        containerRef={containerRef}
      />

      {/* Side label */}
      <div className="absolute top-1 left-2 flex items-center gap-1.5 pointer-events-none">
        <span
          className={cn(
            'text-xs font-bold',
            side === 'CE' ? 'text-green-500' : 'text-red-500'
          )}
        >
          {side}
        </span>
        {symbol && (
          <span className="text-[10px] text-muted-foreground font-mono">
            {symbol}
          </span>
        )}
        {!symbol && (
          <span className="text-[10px] text-muted-foreground/50">
            Select a strike
          </span>
        )}
      </div>

      {/* Active indicator */}
      {isActive && (
        <div className="absolute top-1 right-2 pointer-events-none">
          <span className="text-[10px] font-medium text-primary">ACTIVE</span>
        </div>
      )}

      {/* Connection status */}
      {symbol && !isConnected && (
        <div className="absolute bottom-1 right-2 pointer-events-none">
          <span className="text-[10px] text-yellow-500">Connecting...</span>
        </div>
      )}

      {/* Placeholder when no symbol selected */}
      {!symbol && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <span className="text-xs text-muted-foreground/40">
            Click a strike in the chain
          </span>
        </div>
      )}
    </div>
  )
}
