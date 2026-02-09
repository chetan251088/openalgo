import { useCallback, useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { tradingApi, type HistoryCandleData } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useCandleBuilder } from '@/hooks/useCandleBuilder'
import { ChartOrderOverlay } from './ChartOrderOverlay'
import type { ActiveSide } from '@/types/scalping'
import type { Candle } from '@/lib/candleUtils'
import { cn } from '@/lib/utils'
import {
  calculateEMA,
  calculateSupertrend,
  calculateVWAP,
  type IndicatorPoint,
} from '@/lib/technicalIndicators'
import {
  formatIstHmFromEpoch,
  isWithinIndiaMarketHours,
  parseHistoryTimestampToEpochSeconds,
} from '@/lib/indiaMarketTime'

const INDICATOR_THROTTLE_MS = 120
const MAX_CANDLE_CACHE = 500
const HISTORY_LOOKBACK_DAYS = 1

interface OptionChartViewProps {
  side: ActiveSide
  showEma9: boolean
  showEma21: boolean
  showSupertrend: boolean
  showVwap: boolean
}

function toLineData(points: IndicatorPoint[]) {
  return points.map((p) => ({
    time: p.time as UTCTimestamp,
    value: p.value,
  }))
}

function getChartColors(isDark: boolean) {
  return {
    bg: 'transparent',
    text: isDark ? '#a1a1aa' : '#71717a',
    grid: isDark ? 'rgba(161, 161, 170, 0.06)' : 'rgba(0, 0, 0, 0.04)',
    border: isDark ? 'rgba(161, 161, 170, 0.12)' : 'rgba(0, 0, 0, 0.08)',
    crosshair: isDark ? 'rgba(161, 161, 170, 0.4)' : 'rgba(0, 0, 0, 0.3)',
    upColor: '#22c55e',
    downColor: '#ef4444',
    ema9: '#f59e0b',
    ema21: '#8b5cf6',
    supertrend: '#06b6d4',
    vwap: '#ec4899',
  }
}

function toHistoryInterval(intervalSec: number): string {
  switch (intervalSec) {
    case 30:
      // Some broker history backends reject 30s even though live candles support it.
      return '1m'
    case 60:
      return '1m'
    case 180:
      return '3m'
    case 300:
      return '5m'
    case 900:
      return '15m'
    case 1800:
      return '30m'
    case 3600:
      return '1h'
    default:
      return '1m'
  }
}

function formatYmd(date: Date): string {
  const y = date.getFullYear()
  const m = `${date.getMonth() + 1}`.padStart(2, '0')
  const d = `${date.getDate()}`.padStart(2, '0')
  return `${y}-${m}-${d}`
}

function parseNumeric(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim().length > 0) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function normalizeHistoryCandles(rows: HistoryCandleData[] | undefined): Candle[] {
  if (!rows?.length) return []

  const parsed: Candle[] = []
  for (const row of rows) {
    const timestamp =
      parseHistoryTimestampToEpochSeconds(row.timestamp) ??
      parseHistoryTimestampToEpochSeconds(row.time) ??
      parseHistoryTimestampToEpochSeconds(row.datetime) ??
      parseHistoryTimestampToEpochSeconds(row.date)
    const open = parseNumeric(row.open)
    const high = parseNumeric(row.high)
    const low = parseNumeric(row.low)
    const close = parseNumeric(row.close)
    const volume = parseNumeric(row.volume) ?? 0

    if (
      timestamp == null ||
      open == null ||
      high == null ||
      low == null ||
      close == null
    ) {
      continue
    }
    if (!isWithinIndiaMarketHours(timestamp, { includeClose: true })) continue

    parsed.push({
      time: timestamp as UTCTimestamp,
      open,
      high,
      low,
      close,
      volume,
    })
  }

  parsed.sort((a, b) => Number(a.time) - Number(b.time))

  const dedup = new Map<number, Candle>()
  for (const candle of parsed) dedup.set(Number(candle.time), candle)
  return Array.from(dedup.values()).slice(-MAX_CANDLE_CACHE)
}

function cloneCandles(candles: Candle[]): Candle[] {
  return candles.map((c) => ({ ...c }))
}

function mergeCandles(base: Candle[], overlay: Candle[]): Candle[] {
  const merged = new Map<number, Candle>()
  for (const candle of base) merged.set(Number(candle.time), candle)
  for (const candle of overlay) merged.set(Number(candle.time), candle)
  return Array.from(merged.values())
    .sort((a, b) => Number(a.time) - Number(b.time))
    .slice(-MAX_CANDLE_CACHE)
}

function toChartCandles(candles: Candle[]) {
  return candles.map((c) => ({
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }))
}

export function OptionChartView({
  side,
  showEma9,
  showEma21,
  showSupertrend,
  showVwap,
}: OptionChartViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const ema9SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const supertrendSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const candlesRef = useRef<Candle[]>([])
  const cacheRef = useRef<Map<string, Candle[]>>(new Map())
  const currentCacheKeyRef = useRef<string | null>(null)
  const historyLoadSeqRef = useRef(0)
  const indicatorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const indicatorConfigRef = useRef({
    showEma9,
    showEma21,
    showSupertrend,
    showVwap,
  })

  const apiKey = useAuthStore((s) => s.apiKey)
  const isDark = useThemeStore((s) => s.mode === 'dark')
  const activeSide = useScalpingStore((s) => s.activeSide)
  const setActiveSide = useScalpingStore((s) => s.setActiveSide)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const chartInterval = useScalpingStore((s) => s.chartInterval)
  const symbol = useScalpingStore((s) =>
    side === 'CE' ? s.selectedCESymbol : s.selectedPESymbol
  )

  const isActive = activeSide === side

  const refreshIndicators = useCallback(() => {
    const candles = candlesRef.current
    const cfg = indicatorConfigRef.current

    const closes: IndicatorPoint[] = candles.map((c) => ({
      time: c.time as number,
      value: c.close,
    }))

    if (ema9SeriesRef.current) {
      ema9SeriesRef.current.setData(cfg.showEma9 ? toLineData(calculateEMA(closes, 9)) : [])
    }
    if (ema21SeriesRef.current) {
      ema21SeriesRef.current.setData(cfg.showEma21 ? toLineData(calculateEMA(closes, 21)) : [])
    }
    if (supertrendSeriesRef.current) {
      supertrendSeriesRef.current.setData(
        cfg.showSupertrend ? toLineData(calculateSupertrend(candles, 10, 3).trend) : []
      )
    }
    if (vwapSeriesRef.current) {
      vwapSeriesRef.current.setData(cfg.showVwap ? toLineData(calculateVWAP(candles)) : [])
    }
  }, [])

  const scheduleIndicatorRefresh = useCallback(() => {
    if (indicatorTimerRef.current !== null) return
    indicatorTimerRef.current = setTimeout(() => {
      indicatorTimerRef.current = null
      refreshIndicators()
    }, INDICATOR_THROTTLE_MS)
  }, [refreshIndicators])

  const clearChartData = useCallback(() => {
    candlesRef.current = []
    candleSeriesRef.current?.setData([])
    ema9SeriesRef.current?.setData([])
    ema21SeriesRef.current?.setData([])
    supertrendSeriesRef.current?.setData([])
    vwapSeriesRef.current?.setData([])
  }, [])

  const applyCandles = useCallback(
    (candles: Candle[]) => {
      const next = candles.slice(-MAX_CANDLE_CACHE)
      candlesRef.current = cloneCandles(next)
      candleSeriesRef.current?.setData(toChartCandles(next))
      scheduleIndicatorRefresh()
    },
    [scheduleIndicatorRefresh]
  )

  // Handle chart click to set active side
  const handleClick = useCallback(() => {
    setActiveSide(side)
  }, [side, setActiveSide])

  // Candle update callback - ref-based, no re-render
  const handleCandleUpdate = useCallback(
    (candle: Candle, isNew: boolean) => {
      if (!candleSeriesRef.current) return

      const nextTime = Number(candle.time)
      const lastTime = candlesRef.current.length
        ? Number(candlesRef.current[candlesRef.current.length - 1].time)
        : null

      // Guard against out-of-order stale ticks during interval/symbol transitions.
      if (lastTime != null && Number.isFinite(lastTime) && Number.isFinite(nextTime) && nextTime < lastTime) {
        return
      }

      try {
        candleSeriesRef.current.update({
          time: candle.time,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        })
      } catch {
        // Never allow lightweight-charts update ordering errors to crash the view.
        return
      }

      if (isNew) {
        candlesRef.current.push(candle)
        if (candlesRef.current.length > 500) {
          candlesRef.current = candlesRef.current.slice(-500)
        }
      } else if (candlesRef.current.length > 0) {
        candlesRef.current[candlesRef.current.length - 1] = candle
      } else {
        candlesRef.current.push(candle)
      }

      const cacheKey = currentCacheKeyRef.current
      if (cacheKey) {
        cacheRef.current.set(cacheKey, cloneCandles(candlesRef.current))
      }

      scheduleIndicatorRefresh()
    },
    [scheduleIndicatorRefresh]
  )

  const { isConnected, reset: resetCandles, seed: seedCandles } = useCandleBuilder({
    symbol: symbol ?? '',
    exchange: optionExchange,
    intervalSec: chartInterval,
    enabled: !!symbol,
    useIndiaMarketHours: true,
    onCandleUpdate: handleCandleUpdate,
  })

  // Rehydrate candles from in-memory cache or short history on symbol/interval changes.
  useEffect(() => {
    const prevKey = currentCacheKeyRef.current
    if (prevKey && candlesRef.current.length > 0) {
      cacheRef.current.set(prevKey, cloneCandles(candlesRef.current))
    }

    if (!symbol) {
      currentCacheKeyRef.current = null
      resetCandles()
      clearChartData()
      return
    }

    const nextKey = `${optionExchange}:${symbol}:${chartInterval}`
    currentCacheKeyRef.current = nextKey

    const cached = cacheRef.current.get(nextKey)
    if (cached && cached.length > 0) {
      resetCandles()
      seedCandles(cached)
      applyCandles(cached)
      return
    }

    resetCandles()
    clearChartData()

    // First time for this strike+interval: warm chart using short historical snapshot.
    const requestSeq = ++historyLoadSeqRef.current
    const interval = toHistoryInterval(chartInterval)
    const endDate = new Date()
    const startDate = new Date(endDate.getTime())
    startDate.setDate(startDate.getDate() - HISTORY_LOOKBACK_DAYS)

    const loadHistory = async () => {
      if (!apiKey) {
        if (requestSeq === historyLoadSeqRef.current) clearChartData()
        return
      }

      try {
        const response = await tradingApi.getHistory(
          apiKey,
          symbol,
          optionExchange,
          interval,
          formatYmd(startDate),
          formatYmd(endDate)
        )

        if (requestSeq !== historyLoadSeqRef.current) return
        const historyCandles =
          response.status === 'success'
            ? normalizeHistoryCandles(response.data)
            : []

        const liveCandles =
          currentCacheKeyRef.current === nextKey ? cloneCandles(candlesRef.current) : []
        const mergedCandles = mergeCandles(historyCandles, liveCandles)

        if (mergedCandles.length > 0) {
          cacheRef.current.set(nextKey, cloneCandles(mergedCandles))
          seedCandles(mergedCandles)
          applyCandles(mergedCandles)
          return
        }
      } catch {
        // If history fetch fails, fall back to live-only mode.
      }

      if (requestSeq === historyLoadSeqRef.current) {
        clearChartData()
      }
    }

    void loadHistory()
  }, [
    symbol,
    optionExchange,
    chartInterval,
    apiKey,
    resetCandles,
    seedCandles,
    clearChartData,
    applyCandles,
  ])

  // Apply visibility toggles to indicator series and refresh data.
  useEffect(() => {
    indicatorConfigRef.current = { showEma9, showEma21, showSupertrend, showVwap }

    ema9SeriesRef.current?.applyOptions({ visible: showEma9 })
    ema21SeriesRef.current?.applyOptions({ visible: showEma21 })
    supertrendSeriesRef.current?.applyOptions({ visible: showSupertrend })
    vwapSeriesRef.current?.applyOptions({ visible: showVwap })

    scheduleIndicatorRefresh()
  }, [showEma9, showEma21, showSupertrend, showVwap, scheduleIndicatorRefresh])

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
          return formatIstHmFromEpoch(time)
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
      visible: indicatorConfigRef.current.showEma9,
    })

    const ema21 = chart.addSeries(LineSeries, {
      color: colors.ema21,
      lineWidth: 1,
      title: 'EMA21',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showEma21,
    })

    const supertrend = chart.addSeries(LineSeries, {
      color: colors.supertrend,
      lineWidth: 2,
      title: 'ST',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showSupertrend,
    })

    const vwap = chart.addSeries(LineSeries, {
      color: colors.vwap,
      lineWidth: 1,
      lineStyle: 2,
      title: 'VWAP',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showVwap,
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    ema9SeriesRef.current = ema9
    ema21SeriesRef.current = ema21
    supertrendSeriesRef.current = supertrend
    vwapSeriesRef.current = vwap

    if (candlesRef.current.length > 0) {
      candleSeries.setData(toChartCandles(candlesRef.current))
      scheduleIndicatorRefresh()
    }

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
      if (indicatorTimerRef.current !== null) {
        clearTimeout(indicatorTimerRef.current)
        indicatorTimerRef.current = null
      }
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      ema9SeriesRef.current = null
      ema21SeriesRef.current = null
      supertrendSeriesRef.current = null
      vwapSeriesRef.current = null
    }
  }, [isDark, scheduleIndicatorRefresh])

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

      {/* Indicator legend */}
      <div className="absolute top-5 right-2 flex items-center gap-1 pointer-events-none">
        {showEma9 && <span className="text-[9px] text-amber-500 font-mono">E9</span>}
        {showEma21 && <span className="text-[9px] text-violet-500 font-mono">E21</span>}
        {showSupertrend && <span className="text-[9px] text-cyan-500 font-mono">ST</span>}
        {showVwap && <span className="text-[9px] text-pink-500 font-mono">VW</span>}
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
