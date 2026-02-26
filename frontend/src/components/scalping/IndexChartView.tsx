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
import {
  calculateEMA,
  calculateSupertrend,
  calculateVWAP,
  type IndicatorPoint,
} from '@/lib/technicalIndicators'
import type { Candle } from '@/lib/candleUtils'
import {
  formatIstHmFromEpoch,
  isWithinIndiaMarketHours,
  parseHistoryTimestampToEpochSeconds,
} from '@/lib/indiaMarketTime'
import { createSteppedAutoscaleProvider } from '@/lib/chartAutoscale'

const INDICATOR_THROTTLE_MS = 120
const MAX_CANDLE_CACHE = 500
const HISTORY_LOOKBACK_DAYS = 1
const INDEX_PRICE_STEP = 50
const INDEX_MIN_PRICE_SPAN = 200

interface IndexChartViewProps {
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
    case 86400:
      return 'D'
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

function normalizeCandleTime(value: unknown): UTCTimestamp | null {
  const timestamp = parseHistoryTimestampToEpochSeconds(value)
  return timestamp == null ? null : (timestamp as UTCTimestamp)
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

    parsed.push({
      time: timestamp as UTCTimestamp,
      open,
      high,
      low,
      close,
      volume,
    })
  }

  if (parsed.length === 0) return []
  parsed.sort((a, b) => Number(a.time) - Number(b.time))

  const dedup = new Map<number, Candle>()
  for (const candle of parsed) dedup.set(Number(candle.time), candle)
  const deduped = Array.from(dedup.values()).slice(-MAX_CANDLE_CACHE)
  const marketHoursOnly = deduped.filter((candle) =>
    isWithinIndiaMarketHours(Number(candle.time), { includeClose: true })
  )

  // Some broker payloads use alternate timestamp semantics; avoid blank charts if strict market-hour filtering drops all rows.
  return (marketHoursOnly.length > 0 ? marketHoursOnly : deduped).slice(-MAX_CANDLE_CACHE)
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
  const normalized: Array<{ time: UTCTimestamp; open: number; high: number; low: number; close: number }> = []
  for (const candle of candles) {
    const time = normalizeCandleTime(candle.time)
    if (time == null) continue
    normalized.push({
      time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })
  }
  normalized.sort((a, b) => Number(a.time) - Number(b.time))
  return normalized
}

function normalizeRuntimeCandles(candles: Candle[]): Candle[] {
  if (!candles.length) return []

  const dedup = new Map<number, Candle>()
  for (const candle of candles) {
    const time = normalizeCandleTime(candle.time)
    const open = parseNumeric(candle.open)
    const high = parseNumeric(candle.high)
    const low = parseNumeric(candle.low)
    const close = parseNumeric(candle.close)
    const volume = parseNumeric(candle.volume) ?? 0

    if (
      time == null ||
      open == null ||
      high == null ||
      low == null ||
      close == null
    ) {
      continue
    }

    dedup.set(Number(time), {
      time,
      open,
      high,
      low,
      close,
      volume,
    })
  }

  return Array.from(dedup.values())
    .sort((a, b) => Number(a.time) - Number(b.time))
    .slice(-MAX_CANDLE_CACHE)
}

export function IndexChartView({
  showEma9,
  showEma21,
  showSupertrend,
  showVwap,
}: IndexChartViewProps) {
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
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const isDark = useThemeStore((s) => s.mode === 'dark')
  const underlying = useScalpingStore((s) => s.underlying)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const chartInterval = useScalpingStore((s) => s.chartInterval)

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
      const next = normalizeRuntimeCandles(candles)
      candlesRef.current = cloneCandles(next)
      candleSeriesRef.current?.setData(toChartCandles(next))
      scheduleIndicatorRefresh()
    },
    [scheduleIndicatorRefresh]
  )

  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKey) return apiKey
    try {
      const response = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await response.json()
      if (data.status === 'success' && data.api_key) {
        setApiKey(data.api_key)
        return data.api_key
      }
    } catch {
      // no-op
    }
    return null
  }, [apiKey, setApiKey])

  // Candle update callback - fires on every tick, updates chart via ref
  const handleCandleUpdate = useCallback(
    (candle: Candle, isNew: boolean) => {
      if (!candleSeriesRef.current) return

      const time = normalizeCandleTime(candle.time)
      const open = parseNumeric(candle.open)
      const high = parseNumeric(candle.high)
      const low = parseNumeric(candle.low)
      const close = parseNumeric(candle.close)
      const volume = parseNumeric(candle.volume) ?? 0

      if (
        time == null ||
        open == null ||
        high == null ||
        low == null ||
        close == null
      ) {
        return
      }

      const normalizedCandle: Candle = {
        time,
        open,
        high,
        low,
        close,
        volume,
      }

      const nextTime = Number(normalizedCandle.time)
      const lastTime = candlesRef.current.length
        ? Number(candlesRef.current[candlesRef.current.length - 1].time)
        : null

      // Guard against out-of-order stale ticks during interval/symbol transitions.
      if (lastTime != null && Number.isFinite(lastTime) && Number.isFinite(nextTime) && nextTime < lastTime) {
        return
      }

      // Update candle on chart directly (no React re-render)
      try {
        candleSeriesRef.current.update({
          time: normalizedCandle.time,
          open: normalizedCandle.open,
          high: normalizedCandle.high,
          low: normalizedCandle.low,
          close: normalizedCandle.close,
        })
      } catch {
        const rebuilt = normalizeRuntimeCandles([...candlesRef.current, normalizedCandle])
        candlesRef.current = rebuilt
        candleSeriesRef.current.setData(toChartCandles(rebuilt))
        scheduleIndicatorRefresh()
        return
      }

      if (isNew) {
        candlesRef.current.push(normalizedCandle)
        if (candlesRef.current.length > MAX_CANDLE_CACHE) {
          candlesRef.current = candlesRef.current.slice(-MAX_CANDLE_CACHE)
        }
      } else if (candlesRef.current.length > 0) {
        candlesRef.current[candlesRef.current.length - 1] = normalizedCandle
      } else {
        candlesRef.current.push(normalizedCandle)
      }

      const cacheKey = currentCacheKeyRef.current
      if (cacheKey) {
        cacheRef.current.set(cacheKey, cloneCandles(candlesRef.current))
      }

      scheduleIndicatorRefresh()
    },
    [scheduleIndicatorRefresh]
  )

  const { isConnected, isFallbackMode, reset: resetCandles, seed: seedCandles } = useCandleBuilder({
    symbol: underlying,
    exchange: indexExchange,
    intervalSec: chartInterval,
    mode: 'LTP',
    enabled: !!underlying,
    useIndiaMarketHours: false,
    onCandleUpdate: handleCandleUpdate,
  })

  // Rehydrate candles from in-memory cache or short history on symbol/interval changes.
  useEffect(() => {
    const prevKey = currentCacheKeyRef.current
    if (prevKey && candlesRef.current.length > 0) {
      cacheRef.current.set(prevKey, cloneCandles(candlesRef.current))
    }

    if (!underlying) {
      currentCacheKeyRef.current = null
      resetCandles()
      clearChartData()
      return
    }

    const nextKey = `${indexExchange}:${underlying}:${chartInterval}`
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

    // First time for this index+interval: warm chart using short historical snapshot.
    const requestSeq = ++historyLoadSeqRef.current
    const interval = toHistoryInterval(chartInterval)
    const endDate = new Date()
    const startDate = new Date(endDate.getTime())
    startDate.setDate(startDate.getDate() - HISTORY_LOOKBACK_DAYS)

    const loadHistory = async () => {
      const resolvedApiKey = await ensureApiKey()
      if (!resolvedApiKey) {
        if (requestSeq === historyLoadSeqRef.current) clearChartData()
        return
      }

      try {
        const buildSnapshotCandle = async (): Promise<Candle | null> => {
          try {
            const quoteResp = await tradingApi.getQuotes(resolvedApiKey, underlying, indexExchange)
            if (quoteResp.status !== 'success' || !quoteResp.data) return null
            const ltp = parseNumeric(quoteResp.data.ltp)
            if (ltp == null || ltp <= 0) return null
            const nowSec = Math.floor(Date.now() / 1000)
            const aligned = Math.floor(nowSec / chartInterval) * chartInterval
            return {
              time: aligned as UTCTimestamp,
              open: ltp,
              high: ltp,
              low: ltp,
              close: ltp,
              volume: parseNumeric(quoteResp.data.volume) ?? 0,
            }
          } catch {
            return null
          }
        }

        const fetchHistoryCandles = async (source: 'api' | 'db'): Promise<Candle[]> => {
          try {
            const response = await tradingApi.getHistory(
              resolvedApiKey,
              underlying,
              indexExchange,
              interval,
              formatYmd(startDate),
              formatYmd(endDate),
              source
            )
            return response.status === 'success' ? normalizeHistoryCandles(response.data) : []
          } catch {
            return []
          }
        }

        const apiHistoryCandles = await fetchHistoryCandles('api')
        const historyCandles =
          apiHistoryCandles.length > 0
            ? apiHistoryCandles
            : await fetchHistoryCandles('db')

        if (requestSeq !== historyLoadSeqRef.current) return

        const liveCandles =
          currentCacheKeyRef.current === nextKey ? cloneCandles(candlesRef.current) : []
        const mergedCandles = mergeCandles(historyCandles, liveCandles)

        if (mergedCandles.length > 0) {
          cacheRef.current.set(nextKey, cloneCandles(mergedCandles))
          seedCandles(mergedCandles)
          applyCandles(mergedCandles)
          return
        }

        const snapshotCandle = await buildSnapshotCandle()
        if (snapshotCandle && requestSeq === historyLoadSeqRef.current) {
          const seeded = [snapshotCandle]
          cacheRef.current.set(nextKey, cloneCandles(seeded))
          seedCandles(seeded)
          applyCandles(seeded)
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
    underlying,
    indexExchange,
    chartInterval,
    ensureApiKey,
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
    const steppedAutoscale = createSteppedAutoscaleProvider(
      INDEX_PRICE_STEP,
      INDEX_MIN_PRICE_SPAN
    )

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
      autoscaleInfoProvider: steppedAutoscale,
    })

    const ema9 = chart.addSeries(LineSeries, {
      color: colors.ema9,
      lineWidth: 1,
      title: 'EMA9',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showEma9,
      autoscaleInfoProvider: steppedAutoscale,
    })

    const ema21 = chart.addSeries(LineSeries, {
      color: colors.ema21,
      lineWidth: 1,
      title: 'EMA21',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showEma21,
      autoscaleInfoProvider: steppedAutoscale,
    })

    const supertrend = chart.addSeries(LineSeries, {
      color: colors.supertrend,
      lineWidth: 2,
      title: 'ST',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showSupertrend,
      autoscaleInfoProvider: steppedAutoscale,
    })

    const vwap = chart.addSeries(LineSeries, {
      color: colors.vwap,
      lineWidth: 1,
      lineStyle: 2,
      title: 'VWAP',
      lastValueVisible: false,
      priceLineVisible: false,
      visible: indicatorConfigRef.current.showVwap,
      autoscaleInfoProvider: steppedAutoscale,
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
    <div className="relative h-full w-full min-w-0 min-h-0 overflow-hidden">
      <div ref={containerRef} className="h-full w-full" />
      {/* Index label */}
      <div className="absolute top-1 left-2 flex items-center gap-1.5 pointer-events-none">
        <span className="text-xs font-bold text-foreground/80">{underlying}</span>
        {!isConnected && !isFallbackMode && (
          <span className="text-[10px] text-yellow-500">Connecting...</span>
        )}
        {!isConnected && isFallbackMode && (
          <span className="text-[10px] text-blue-500">REST fallback</span>
        )}
      </div>
      <div className="absolute top-1 right-2 flex items-center gap-1 pointer-events-none">
        {showEma9 && <span className="text-[9px] text-amber-500 font-mono">E9</span>}
        {showEma21 && <span className="text-[9px] text-violet-500 font-mono">E21</span>}
        {showSupertrend && <span className="text-[9px] text-cyan-500 font-mono">ST</span>}
        {showVwap && <span className="text-[9px] text-pink-500 font-mono">VW</span>}
      </div>
    </div>
  )
}
