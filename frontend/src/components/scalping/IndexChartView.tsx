import { useCallback, useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from 'lightweight-charts'
import { useThemeStore } from '@/stores/themeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useCandleBuilder } from '@/hooks/useCandleBuilder'
import { useTechnicalIndicators } from '@/hooks/useTechnicalIndicators'
import type { Candle } from '@/lib/candleUtils'

function getChartColors(isDark: boolean) {
  return {
    bg: 'transparent',
    text: isDark ? '#a1a1aa' : '#71717a',
    grid: isDark ? 'rgba(161, 161, 170, 0.08)' : 'rgba(0, 0, 0, 0.06)',
    border: isDark ? 'rgba(161, 161, 170, 0.15)' : 'rgba(0, 0, 0, 0.1)',
    crosshair: isDark ? 'rgba(161, 161, 170, 0.4)' : 'rgba(0, 0, 0, 0.3)',
    upColor: '#22c55e',
    downColor: '#ef4444',
    ema9: '#f59e0b',
    ema21: '#8b5cf6',
    supertrend: '#06b6d4',
    vwap: '#ec4899',
  }
}

export function IndexChartView() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const ema9SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const supertrendSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const candlesRef = useRef<Candle[]>([])

  const isDark = useThemeStore((s) => s.mode === 'dark')
  const underlying = useScalpingStore((s) => s.underlying)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const chartInterval = useScalpingStore((s) => s.chartInterval)

  const indicators = useTechnicalIndicators(candlesRef.current)

  // Candle update callback - fires on every tick, updates chart via ref
  const handleCandleUpdate = useCallback((candle: Candle, isNew: boolean) => {
    if (!candleSeriesRef.current) return

    // Update candle on chart directly (no React re-render)
    candleSeriesRef.current.update({
      time: candle.time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })

    if (isNew) {
      // Store candle for indicator computation
      candlesRef.current.push(candle)
      if (candlesRef.current.length > 500) {
        candlesRef.current = candlesRef.current.slice(-500)
      }
    } else if (candlesRef.current.length > 0) {
      candlesRef.current[candlesRef.current.length - 1] = candle
    } else {
      candlesRef.current.push(candle)
    }
  }, [])

  const { isConnected, reset: resetCandles } = useCandleBuilder({
    symbol: underlying,
    exchange: indexExchange,
    intervalSec: chartInterval,
    enabled: !!underlying,
    onCandleUpdate: handleCandleUpdate,
  })

  // Reset candle data when interval changes
  useEffect(() => {
    candlesRef.current = []
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
        fontSize: 11,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      rightPriceScale: {
        borderColor: colors.border,
        scaleMargins: { top: 0.05, bottom: 0.05 },
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

    const ema9 = chart.addSeries(LineSeries, {
      color: colors.ema9,
      lineWidth: 1,
      title: 'EMA9',
      lastValueVisible: false,
      priceLineVisible: false,
    })

    const ema21 = chart.addSeries(LineSeries, {
      color: colors.ema21,
      lineWidth: 1,
      title: 'EMA21',
      lastValueVisible: false,
      priceLineVisible: false,
    })

    const supertrend = chart.addSeries(LineSeries, {
      color: colors.supertrend,
      lineWidth: 2,
      title: 'ST',
      lastValueVisible: false,
      priceLineVisible: false,
    })

    const vwap = chart.addSeries(LineSeries, {
      color: colors.vwap,
      lineWidth: 1,
      lineStyle: 2,
      title: 'VWAP',
      lastValueVisible: false,
      priceLineVisible: false,
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    ema9SeriesRef.current = ema9
    ema21SeriesRef.current = ema21
    supertrendSeriesRef.current = supertrend
    vwapSeriesRef.current = vwap

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
      ema9SeriesRef.current = null
      ema21SeriesRef.current = null
      supertrendSeriesRef.current = null
      vwapSeriesRef.current = null
    }
  }, [isDark, underlying, indexExchange, chartInterval])

  // Update indicator overlays when computed
  useEffect(() => {
    if (ema9SeriesRef.current && indicators.ema9.length > 0) {
      ema9SeriesRef.current.setData(
        indicators.ema9.map((p) => ({ time: p.time as import('lightweight-charts').UTCTimestamp, value: p.value }))
      )
    }
    if (ema21SeriesRef.current && indicators.ema21.length > 0) {
      ema21SeriesRef.current.setData(
        indicators.ema21.map((p) => ({ time: p.time as import('lightweight-charts').UTCTimestamp, value: p.value }))
      )
    }
    if (supertrendSeriesRef.current && indicators.supertrendLine.length > 0) {
      supertrendSeriesRef.current.setData(
        indicators.supertrendLine.map((p) => ({ time: p.time as import('lightweight-charts').UTCTimestamp, value: p.value }))
      )
    }
    if (vwapSeriesRef.current && indicators.vwap.length > 0) {
      vwapSeriesRef.current.setData(
        indicators.vwap.map((p) => ({ time: p.time as import('lightweight-charts').UTCTimestamp, value: p.value }))
      )
    }
  }, [indicators])

  return (
    <div className="relative h-full w-full min-w-0 min-h-0 overflow-hidden">
      <div ref={containerRef} className="h-full w-full" />
      {/* Index label */}
      <div className="absolute top-1 left-2 flex items-center gap-1.5 pointer-events-none">
        <span className="text-xs font-bold text-foreground/80">{underlying}</span>
        {!isConnected && (
          <span className="text-[10px] text-yellow-500">Connecting...</span>
        )}
      </div>
    </div>
  )
}
