import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { tradingApi, type HistoryCandleData } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useCandleBuilder } from '@/hooks/useCandleBuilder'
import { useMarketData } from '@/hooks/useMarketData'
import { ChartOrderOverlay } from './ChartOrderOverlay'
import type { ActiveSide, OptionsContext } from '@/types/scalping'
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
import { createSteppedAutoscaleProvider } from '@/lib/chartAutoscale'

const INDICATOR_THROTTLE_MS = 120
const MAX_CANDLE_CACHE = 500
const HISTORY_LOOKBACK_DAYS = 1
const OPTION_PRICE_STEP = 10
const OPTION_MIN_PRICE_SPAN = 40
const ORDER_FLOW_WINDOW_MS = 30_000
const ORDER_FLOW_EMIT_THROTTLE_MS = 120
const ORDER_FLOW_STALE_MS = 5_000
const ORDER_FLOW_FALLBACK_STALE_MS = 12_000
const FOOTPRINT_DEFAULT_LOOKBACK_BARS = 36

interface OptionChartViewProps {
  side: ActiveSide
  prominence: 'primary' | 'secondary'
  showEma9: boolean
  showEma21: boolean
  showSupertrend: boolean
  showVwap: boolean
  showOrderFlow: boolean
  showFootprints: boolean
  footprintDensity: 'sparse' | 'balanced' | 'all'
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

function formatFlowNumber(value: number): string {
  if (!Number.isFinite(value)) return '0'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  if (abs >= 100) return value.toFixed(0)
  if (abs >= 10) return value.toFixed(1)
  if (abs > 0) return value.toFixed(2)
  return '0'
}

function formatSignedFlowNumber(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '0'
  return `${value > 0 ? '+' : ''}${formatFlowNumber(value)}`
}

function getLastIndicatorValue(points: IndicatorPoint[]): number | null {
  if (points.length === 0) return null
  const value = points[points.length - 1]?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

type FlowDominance = 'BUY' | 'SELL' | 'BAL'
type FlowBiasAction = 'BUY_CE' | 'BUY_PE' | 'HOLD'
type UnifiedSignalConfidence = 'LOW' | 'MED' | 'HIGH'
type FootprintDensity = 'sparse' | 'balanced' | 'all'

interface OrderFlowStats {
  buyFlow: number
  sellFlow: number
  delta: number
  cumulativeDelta: number
  dominance: FlowDominance
  depthImbalancePct: number | null
  spread: number | null
  flowSource: 'VOL' | 'EST'
  lastUpdate: number
}

interface FlowTickBucket {
  ts: number
  buy: number
  sell: number
}

interface CandleFootprintAccumulator {
  buy: number
  sell: number
}

interface CandleFootprint {
  time: UTCTimestamp
  buy: number
  sell: number
  delta: number
  total: number
  high: number
  low: number
  source: 'FLOW' | 'EST'
}

interface RenderableFootprint extends CandleFootprint {
  x: number
  y: number
  yTop: number
  height: number
  alpha: number
}

interface FlowStatItem {
  label: string
  value: string
  tone?: 'positive' | 'negative' | 'neutral' | 'accent'
}

interface UnifiedFlowSignal {
  action: FlowBiasAction
  label: string
  confidence: UnifiedSignalConfidence
  score: number
  components: {
    flow: number
    footprints: number
    context: number
  }
}

interface ChartSignalMarker {
  time: UTCTimestamp
  position: 'aboveBar' | 'belowBar'
  color: string
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
  text: string
  size: number
}

interface FlowState {
  lastLtp: number | null
  lastVolume: number | null
  lastBidSize: number | null
  lastAskSize: number | null
  lastTickAt: number
  cumulativeDelta: number
  buckets: FlowTickBucket[]
  candleFlow: Map<number, CandleFootprintAccumulator>
  lastEmitAt: number
}

const EMPTY_ORDER_FLOW: OrderFlowStats = {
  buyFlow: 0,
  sellFlow: 0,
  delta: 0,
  cumulativeDelta: 0,
  dominance: 'BAL',
  depthImbalancePct: null,
  spread: null,
  flowSource: 'EST',
  lastUpdate: 0,
}

const FOOTPRINT_DENSITY_CONFIG: Record<
  FootprintDensity,
  { maxVisibleBars: number; minSpacingPx: number }
> = {
  sparse: { maxVisibleBars: 36, minSpacingPx: 28 },
  balanced: { maxVisibleBars: 96, minSpacingPx: 14 },
  all: { maxVisibleBars: 1200, minSpacingPx: 0 },
}

function parseFlowLtp(payload: Record<string, unknown>): number | null {
  return (
    parseNumeric(payload.ltp) ??
    parseNumeric(payload.last_price) ??
    parseNumeric(payload.lp) ??
    parseNumeric(payload.price) ??
    parseNumeric(payload.close)
  )
}

function parseFlowVolume(payload: Record<string, unknown>): number | null {
  return (
    parseNumeric(payload.volume) ??
    parseNumeric(payload.total_volume) ??
    parseNumeric(payload.v) ??
    parseNumeric(payload.ttq)
  )
}

function parseFlowBidPrice(payload: Record<string, unknown>): number | null {
  return (
    parseNumeric(payload.bid_price) ??
    parseNumeric(payload.bid) ??
    parseNumeric(payload.bp) ??
    parseNumeric(payload.best_bid_price) ??
    parseNumeric(payload.bp1)
  )
}

function parseFlowAskPrice(payload: Record<string, unknown>): number | null {
  return (
    parseNumeric(payload.ask_price) ??
    parseNumeric(payload.ask) ??
    parseNumeric(payload.sp) ??
    parseNumeric(payload.best_ask_price) ??
    parseNumeric(payload.sp1)
  )
}

function parseFlowBidSize(payload: Record<string, unknown>): number | null {
  return (
    parseNumeric(payload.bid_size) ??
    parseNumeric(payload.bid_qty) ??
    parseNumeric(payload.total_buy_quantity) ??
    parseNumeric(payload.total_buy_qty) ??
    parseNumeric(payload.totalbuyqty) ??
    parseNumeric(payload.buy_quantity) ??
    parseNumeric(payload.tbq) ??
    parseNumeric(payload.bq1)
  )
}

function parseFlowAskSize(payload: Record<string, unknown>): number | null {
  return (
    parseNumeric(payload.ask_size) ??
    parseNumeric(payload.ask_qty) ??
    parseNumeric(payload.total_sell_quantity) ??
    parseNumeric(payload.total_sell_qty) ??
    parseNumeric(payload.totalsellqty) ??
    parseNumeric(payload.sell_quantity) ??
    parseNumeric(payload.tsq) ??
    parseNumeric(payload.sq1)
  )
}

function parseDepthLevelPrice(level: unknown): number {
  if (!level || typeof level !== 'object') return 0
  const row = level as Record<string, unknown>
  const price = parseNumeric(row.price) ?? parseNumeric(row.px)
  return price != null && price > 0 ? price : 0
}

function parseDepthLevelQuantity(level: unknown): number {
  if (!level || typeof level !== 'object') return 0
  const row = level as Record<string, unknown>
  const quantity =
    parseNumeric(row.quantity) ??
    parseNumeric(row.qty) ??
    parseNumeric(row.size) ??
    parseNumeric(row.volume)
  return quantity != null && quantity > 0 ? quantity : 0
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function parseVisibleRangeTime(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  return parseNumeric(row.timestamp) ?? parseNumeric(row.time) ?? null
}

function calculateFootprintBias(footprints: CandleFootprint[]): number {
  if (!footprints.length) return 0

  let weighted = 0
  let weightSum = 0
  for (let i = 0; i < footprints.length; i += 1) {
    const row = footprints[i]
    const weight = 0.55 + (i / Math.max(1, footprints.length - 1)) * 0.45
    const rowScore = clamp(row.delta / Math.max(1, row.total), -1, 1)
    weighted += rowScore * weight
    weightSum += weight
  }
  if (weightSum <= 0) return 0
  return clamp((weighted / weightSum) * 1.1, -1, 1)
}

function calculateContextBias(context: OptionsContext | null): number {
  if (!context) return 0

  // PCR: lower PCR tends to support upside (CE), higher PCR supports downside (PE)
  const pcrCentered = clamp((1 - context.pcr) * 1.5, -1, 1)

  // OI shift: PE-heavy OI build interpreted as support (CE bias), CE-heavy as resistance (PE bias)
  const oiTotal = Math.abs(context.oiChangeCE) + Math.abs(context.oiChangePE)
  const oiBias =
    oiTotal > 0 ? clamp((context.oiChangePE - context.oiChangeCE) / oiTotal, -1, 1) : 0

  // Spot vs MaxPain: below max-pain => slight CE bias, above => slight PE bias
  const maxPainBias = clamp((-context.spotVsMaxPain) / 180, -0.55, 0.55)

  let combined = pcrCentered * 0.5 + oiBias * 0.35 + maxPainBias * 0.15

  // Very high absolute GEX often dampens directional follow-through; reduce conviction.
  const gexDamp =
    Math.abs(context.netGEX) >= 1_000_000 ? 0.75 : Math.abs(context.netGEX) >= 300_000 ? 0.85 : 1
  combined *= gexDamp

  return clamp(combined, -1, 1)
}

function deriveUnifiedFlowSignal(
  side: ActiveSide,
  flow: OrderFlowStats,
  footprints: CandleFootprint[],
  context: OptionsContext | null
): UnifiedFlowSignal {
  const totalFlow = Math.max(0, flow.buyFlow + flow.sellFlow)
  const sideDirection = side === 'CE' ? 1 : -1
  const flowScore =
    totalFlow > 0 ? clamp((flow.delta / Math.max(1, totalFlow)) * 1.35, -1, 1) : 0
  const normalizedFlowScore = flowScore * sideDirection
  const footprintScore = calculateFootprintBias(footprints) * sideDirection
  const contextScore = calculateContextBias(context)

  const combined = clamp(
    normalizedFlowScore * 0.45 + footprintScore * 0.3 + contextScore * 0.25,
    -1,
    1
  )
  const magnitude = Math.abs(combined)

  let action: FlowBiasAction = 'HOLD'
  if (magnitude >= 0.16) {
    action = combined > 0 ? 'BUY_CE' : 'BUY_PE'
  }

  const confidence: UnifiedSignalConfidence =
    magnitude >= 0.56 ? 'HIGH' : magnitude >= 0.34 ? 'MED' : 'LOW'
  const label =
    action === 'BUY_CE'
      ? `BUY CE (${confidence})`
      : action === 'BUY_PE'
        ? `BUY PE (${confidence})`
        : `HOLD (${confidence})`

  return {
    action,
    label,
    confidence,
    score: combined,
    components: {
      flow: normalizedFlowScore,
      footprints: footprintScore,
      context: contextScore,
    },
  }
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

export function OptionChartView({
  side,
  prominence,
  showEma9,
  showEma21,
  showSupertrend,
  showVwap,
  showOrderFlow,
  showFootprints,
  footprintDensity,
}: OptionChartViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const ema9SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const supertrendSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const ema9PriceLineRef = useRef<IPriceLine | null>(null)
  const ema21PriceLineRef = useRef<IPriceLine | null>(null)
  const supertrendPriceLineRef = useRef<IPriceLine | null>(null)
  const vwapPriceLineRef = useRef<IPriceLine | null>(null)
  const candlesRef = useRef<Candle[]>([])
  const cacheRef = useRef<Map<string, Candle[]>>(new Map())
  const currentCacheKeyRef = useRef<string | null>(null)
  const historyLoadSeqRef = useRef(0)
  const indicatorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flowStateRef = useRef<FlowState>({
    lastLtp: null,
    lastVolume: null,
    lastBidSize: null,
    lastAskSize: null,
    lastTickAt: 0,
    cumulativeDelta: 0,
    buckets: [],
    candleFlow: new Map<number, CandleFootprintAccumulator>(),
    lastEmitAt: 0,
  })
  const chartMarkersRef = useRef<ChartSignalMarker[]>([])
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<any> | null>(null)
  const [orderFlow, setOrderFlow] = useState<OrderFlowStats>(EMPTY_ORDER_FLOW)
  const [footprints, setFootprints] = useState<CandleFootprint[]>([])
  const [flowClock, setFlowClock] = useState(() => Date.now())
  const [footprintViewportTick, setFootprintViewportTick] = useState(0)
  const indicatorConfigRef = useRef({
    showEma9,
    showEma21,
    showSupertrend,
    showVwap,
  })

  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const isDark = useThemeStore((s) => s.mode === 'dark')
  const activeSide = useScalpingStore((s) => s.activeSide)
  const setActiveSide = useScalpingStore((s) => s.setActiveSide)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const chartInterval = useScalpingStore((s) => s.chartInterval)
  const symbol = useScalpingStore((s) =>
    side === 'CE' ? s.selectedCESymbol : s.selectedPESymbol
  )

  const ghostSignals = useAutoTradeStore((s) => s.ghostSignals)
  const executionSamples = useAutoTradeStore((s) => s.executionSamples)
  const optionsContext = useAutoTradeStore((s) => s.optionsContext)

  const syncIndicatorPriceLine = useCallback(
    (
      seriesRef: MutableRefObject<ISeriesApi<'Line'> | null>,
      lineRef: MutableRefObject<IPriceLine | null>,
      enabled: boolean,
      price: number | null,
      color: string
    ) => {
      const series = seriesRef.current
      if (!series) return

      if (!enabled || price == null) {
        if (lineRef.current) {
          try {
            series.removePriceLine(lineRef.current)
          } catch {
            // no-op
          }
          lineRef.current = null
        }
        return
      }

      const options = {
        price,
        color,
        lineWidth: 1 as const,
        lineStyle: 2 as const,
        axisLabelVisible: true,
        title: '',
      }

      if (lineRef.current) {
        lineRef.current.applyOptions(options)
      } else {
        lineRef.current = series.createPriceLine(options)
      }
    },
    []
  )

  const clearIndicatorPriceLines = useCallback(() => {
    const refs = [
      [ema9SeriesRef, ema9PriceLineRef],
      [ema21SeriesRef, ema21PriceLineRef],
      [supertrendSeriesRef, supertrendPriceLineRef],
      [vwapSeriesRef, vwapPriceLineRef],
    ] as const

    for (const [seriesRef, lineRef] of refs) {
      const series = seriesRef.current
      if (series && lineRef.current) {
        try {
          series.removePriceLine(lineRef.current)
        } catch {
          // no-op
        }
      }
      lineRef.current = null
    }
  }, [])

  const isPrimary = prominence === 'primary'
  const isActive = activeSide === side
  const orderFlowEnabled = showOrderFlow || (showFootprints && isPrimary)
  const orderFlowSymbols = useMemo(
    () => (orderFlowEnabled && symbol ? [{ symbol, exchange: optionExchange }] : []),
    [orderFlowEnabled, symbol, optionExchange]
  )
  const { data: orderFlowQuoteData, isFallbackMode: isQuoteFallbackMode } = useMarketData({
    symbols: orderFlowSymbols,
    mode: 'Quote',
    enabled: orderFlowEnabled && !!symbol,
  })
  const { data: orderFlowLtpData, isFallbackMode: isLtpFallbackMode } = useMarketData({
    symbols: orderFlowSymbols,
    mode: 'LTP',
    enabled: orderFlowEnabled && !!symbol,
  })
  const { data: orderFlowDepthData, isFallbackMode: isDepthFallbackMode } = useMarketData({
    symbols: orderFlowSymbols,
    mode: 'Depth',
    enabled: orderFlowEnabled && !!symbol,
  })
  const flowDataKey = useMemo(
    () =>
      orderFlowEnabled && symbol
        ? `${optionExchange.toUpperCase()}:${symbol.toUpperCase()}`
        : null,
    [optionExchange, orderFlowEnabled, symbol]
  )
  const isOrderFlowFallbackMode =
    isQuoteFallbackMode || isLtpFallbackMode || isDepthFallbackMode
  const lastFlowTickAt = useMemo(() => {
    if (!flowDataKey) return 0
    const quoteTs = orderFlowQuoteData.get(flowDataKey)?.lastUpdate ?? 0
    const ltpTs = orderFlowLtpData.get(flowDataKey)?.lastUpdate ?? 0
    const depthTs = orderFlowDepthData.get(flowDataKey)?.lastUpdate ?? 0
    return Math.max(0, quoteTs, ltpTs, depthTs)
  }, [flowDataKey, orderFlowDepthData, orderFlowLtpData, orderFlowQuoteData])
  const footprintConfig = FOOTPRINT_DENSITY_CONFIG[footprintDensity]
  const isFlowStale = useMemo(() => {
    if (!orderFlowEnabled || !symbol || lastFlowTickAt <= 0) return false
    const staleThreshold = isOrderFlowFallbackMode
      ? ORDER_FLOW_FALLBACK_STALE_MS
      : ORDER_FLOW_STALE_MS
    return flowClock - lastFlowTickAt > staleThreshold
  }, [flowClock, isOrderFlowFallbackMode, lastFlowTickAt, orderFlowEnabled, symbol])

  const refreshFootprints = useCallback(() => {
    if (!showFootprints || !symbol || !isPrimary) {
      setFootprints((prev) => (prev.length === 0 ? prev : []))
      return
    }

    const allCandles = candlesRef.current
    if (!allCandles.length) {
      setFootprints((prev) => (prev.length === 0 ? prev : []))
      return
    }

    let sourceCandles = allCandles
    const chart = chartRef.current
    const visibleRange = chart?.timeScale().getVisibleRange()
    const visibleFrom = parseVisibleRangeTime(visibleRange?.from)
    const visibleTo = parseVisibleRangeTime(visibleRange?.to)

    if (visibleFrom != null && visibleTo != null && visibleTo >= visibleFrom) {
      const visibleCandles = allCandles.filter((c) => {
        const t = Number(c.time)
        return t >= visibleFrom && t <= visibleTo
      })
      if (visibleCandles.length > 0) sourceCandles = visibleCandles
    }

    const maxBars = Math.max(0, footprintConfig.maxVisibleBars)
    if (maxBars > 0 && sourceCandles.length > maxBars) {
      sourceCandles = sourceCandles.slice(-maxBars)
    }

    if (sourceCandles.length === 0) {
      sourceCandles = allCandles.slice(-FOOTPRINT_DEFAULT_LOOKBACK_BARS)
    }

    const rows: CandleFootprint[] = []
    const candleFlow = flowStateRef.current.candleFlow

    for (const candle of sourceCandles) {
      const time = Number(candle.time)
      const flow = candleFlow.get(time)
      let buy = flow?.buy ?? 0
      let sell = flow?.sell ?? 0
      let source: 'FLOW' | 'EST' = flow ? 'FLOW' : 'EST'

      if ((buy + sell <= 0) && candle.volume > 0) {
        const range = Math.max(0.0001, candle.high - candle.low)
        const body = candle.close - candle.open
        const bodyRatio = Math.min(1, Math.abs(body) / range)
        const directionalBias = 0.5 + Math.sign(body) * (0.1 + bodyRatio * 0.2)
        const buyShare = Math.max(0.2, Math.min(0.8, directionalBias))
        buy = candle.volume * buyShare
        sell = Math.max(0, candle.volume - buy)
        source = 'EST'
      }

      const total = buy + sell
      if (!Number.isFinite(total) || total <= 0) continue

      rows.push({
        time: candle.time as UTCTimestamp,
        buy,
        sell,
        delta: buy - sell,
        total,
        high: candle.high,
        low: candle.low,
        source,
      })
    }

    setFootprints(rows)
  }, [footprintConfig.maxVisibleBars, isPrimary, showFootprints, symbol])

  const renderableFootprints = useMemo(() => {
    if (!showFootprints || !symbol || footprints.length === 0) return []
    const chart = chartRef.current
    const series = candleSeriesRef.current
    if (!chart || !series) return []

    const positioned: RenderableFootprint[] = []
    for (const row of footprints) {
      const x = chart.timeScale().timeToCoordinate(row.time)
      const yHigh = series.priceToCoordinate(row.high)
      const yLow = series.priceToCoordinate(row.low)
      if (x == null || yHigh == null || yLow == null) continue

      const candleHeight = Math.max(12, yLow - yHigh)
      const y = yHigh + Math.min(22, Math.max(6, candleHeight * 0.08))
      const intensity = Math.min(1, Math.abs(row.delta) / Math.max(1, row.total))
      const alpha = 0.14 + intensity * 0.24
      positioned.push({ ...row, x, y, yTop: yHigh, height: candleHeight, alpha })
    }

    positioned.sort((a, b) => Number(a.time) - Number(b.time))
    if (footprintConfig.minSpacingPx <= 0) return positioned

    const filtered: RenderableFootprint[] = []
    let lastX = -Infinity
    for (const row of positioned) {
      if (row.x - lastX < footprintConfig.minSpacingPx) continue
      filtered.push(row)
      lastX = row.x
    }

    return filtered
  }, [
    footprintConfig.minSpacingPx,
    showFootprints,
    symbol,
    footprints,
    footprintViewportTick,
  ])

  const unifiedFlowSignal = useMemo(
    () => deriveUnifiedFlowSignal(side, orderFlow, footprints, optionsContext),
    [side, orderFlow, footprints, optionsContext]
  )
  const footprintDisplayMode = useMemo(() => {
    if (!showFootprints || !symbol || !isPrimary || footprints.length === 0) return 'off'
    const chartWidth = containerRef.current?.clientWidth ?? 0
    if (chartWidth <= 0) return 'off'
    const visibleBars = Math.max(1, footprints.length)
    const avgSpacing = chartWidth / visibleBars
    return avgSpacing >= 24 ? 'detail' : 'heat'
  }, [footprintViewportTick, footprints.length, isPrimary, showFootprints, symbol])
  const showDetailedFootprints = footprintDisplayMode === 'detail'
  const showFootprintHeat = footprintDisplayMode === 'heat'
  const statsItems = useMemo<FlowStatItem[]>(() => {
    if (!showOrderFlow || !symbol) return []

    const depthValue =
      orderFlow.depthImbalancePct == null
        ? isFlowStale
          ? 'STALE'
          : 'NA'
        : `${orderFlow.depthImbalancePct >= 0 ? '+' : ''}${orderFlow.depthImbalancePct.toFixed(0)}%`

    return [
      {
        label: 'FLOW',
        value: `${orderFlow.flowSource} ${formatSignedFlowNumber(orderFlow.delta)}`,
        tone: orderFlow.delta > 0 ? 'positive' : orderFlow.delta < 0 ? 'negative' : 'neutral',
      },
      {
        label: 'OI',
        value: `${unifiedFlowSignal.components.context >= 0 ? '+' : ''}${unifiedFlowSignal.components.context.toFixed(2)}`,
        tone:
          unifiedFlowSignal.components.context > 0
            ? 'positive'
            : unifiedFlowSignal.components.context < 0
              ? 'negative'
              : 'neutral',
      },
      {
        label: 'UNI',
        value: unifiedFlowSignal.label,
        tone:
          unifiedFlowSignal.action === 'BUY_CE'
            ? 'positive'
            : unifiedFlowSignal.action === 'BUY_PE'
              ? 'negative'
              : 'accent',
      },
      {
        label: 'DOM',
        value: orderFlow.dominance,
        tone:
          orderFlow.dominance === 'BUY'
            ? 'positive'
            : orderFlow.dominance === 'SELL'
              ? 'negative'
              : 'neutral',
      },
      {
        label: 'DEP',
        value: depthValue,
        tone:
          orderFlow.depthImbalancePct == null
            ? isFlowStale
              ? 'accent'
              : 'neutral'
            : orderFlow.depthImbalancePct >= 0
              ? 'positive'
              : 'negative',
      },
      {
        label: 'SPR',
        value: orderFlow.spread == null ? 'NA' : orderFlow.spread.toFixed(2),
        tone: 'neutral',
      },
    ]
  }, [isFlowStale, orderFlow, showOrderFlow, symbol, unifiedFlowSignal])

  useEffect(() => {
    refreshFootprints()
  }, [refreshFootprints])

  useEffect(() => {
    if (!orderFlowEnabled || !symbol) return
    const timer = setInterval(() => setFlowClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [orderFlowEnabled, symbol])

  useEffect(() => {
    flowStateRef.current = {
      lastLtp: null,
      lastVolume: null,
      lastBidSize: null,
      lastAskSize: null,
      lastTickAt: 0,
      cumulativeDelta: 0,
      buckets: [],
      candleFlow: new Map<number, CandleFootprintAccumulator>(),
      lastEmitAt: 0,
    }
    setOrderFlow(EMPTY_ORDER_FLOW)
    setFootprints([])
  }, [orderFlowEnabled, symbol, optionExchange, chartInterval])

  useEffect(() => {
    if (!orderFlowEnabled || !symbol || !flowDataKey) return
    const quoteTick = orderFlowQuoteData.get(flowDataKey)
    const ltpTick = orderFlowLtpData.get(flowDataKey)
    const depthTick = orderFlowDepthData.get(flowDataKey)
    const candidates = [quoteTick, ltpTick, depthTick].filter(
      (tick): tick is NonNullable<typeof tick> => !!tick
    )
    const latestTick = candidates.reduce((latest, tick) => {
      if (!latest) return tick
      return (tick.lastUpdate ?? 0) >= (latest.lastUpdate ?? 0) ? tick : latest
    }, candidates[0] ?? null)
    if (!quoteTick?.data && !ltpTick?.data && !depthTick?.data) return
    const mergedData: Record<string, unknown> = {
      ...(quoteTick?.data ?? {}),
      ...(ltpTick?.data ?? {}),
      ...(depthTick?.data ?? {}),
    }

    const ltp = parseFlowLtp({
      ...mergedData,
      ...(latestTick?.data ?? {}),
    })
    if (ltp == null || ltp <= 0) return

    const now = Math.max(Date.now(), latestTick?.lastUpdate ?? 0)
    const flowState = flowStateRef.current
    const prevLtp = flowState.lastLtp
    const hasFreshTick = now > flowState.lastTickAt
    const currentVolume = parseFlowVolume(mergedData)
    const previousVolume = flowState.lastVolume
    let depthBuyLevels: unknown[] = []
    let depthSellLevels: unknown[] = []
    if (mergedData.depth && typeof mergedData.depth === 'object') {
      const depth = mergedData.depth as { buy?: unknown; sell?: unknown }
      depthBuyLevels = Array.isArray(depth.buy) ? depth.buy : []
      depthSellLevels = Array.isArray(depth.sell) ? depth.sell : []
    }

    const topDepthBidPrice = depthBuyLevels.length > 0 ? parseDepthLevelPrice(depthBuyLevels[0]) : 0
    const topDepthAskPrice = depthSellLevels.length > 0 ? parseDepthLevelPrice(depthSellLevels[0]) : 0
    const bid = parseFlowBidPrice(mergedData) ?? (topDepthBidPrice > 0 ? topDepthBidPrice : null)
    const ask = parseFlowAskPrice(mergedData) ?? (topDepthAskPrice > 0 ? topDepthAskPrice : null)

    const topDepthBidSize = depthBuyLevels.length > 0 ? parseDepthLevelQuantity(depthBuyLevels[0]) : 0
    const topDepthAskSize = depthSellLevels.length > 0 ? parseDepthLevelQuantity(depthSellLevels[0]) : 0
    const quoteBidSize = parseFlowBidSize(mergedData)
    const quoteAskSize = parseFlowAskSize(mergedData)
    const bidSize =
      quoteBidSize != null && quoteBidSize > 0
        ? quoteBidSize
        : topDepthBidSize > 0
          ? topDepthBidSize
          : null
    const askSize =
      quoteAskSize != null && quoteAskSize > 0
        ? quoteAskSize
        : topDepthAskSize > 0
          ? topDepthAskSize
          : null

    let buyAdd = 0
    let sellAdd = 0
    let usedEstimatedFlow = false
    let usedVolumeFlow = false
    let volumeDelta = 0

    if (
      currentVolume != null &&
      previousVolume != null &&
      Number.isFinite(currentVolume) &&
      Number.isFinite(previousVolume) &&
      currentVolume >= previousVolume
    ) {
      volumeDelta = Math.max(0, currentVolume - previousVolume)
    } else if (currentVolume != null && previousVolume == null) {
      volumeDelta = 0
    }

    if (volumeDelta > 0) {
      usedVolumeFlow = true
      if (prevLtp != null && ltp > prevLtp) {
        buyAdd = volumeDelta
      } else if (prevLtp != null && ltp < prevLtp) {
        sellAdd = volumeDelta
      } else {
        if (bid != null && ask != null && ask > bid) {
          const mid = (bid + ask) / 2
          if (ltp >= mid) buyAdd = volumeDelta
          else sellAdd = volumeDelta
        } else {
          buyAdd = volumeDelta * 0.5
          sellAdd = volumeDelta * 0.5
          usedEstimatedFlow = true
        }
      }
    }

    // No reliable traded-volume delta: infer aggressor pressure from order-book consumption/replenishment + LTP drift.
    if (buyAdd <= 0 && sellAdd <= 0) {
      const prevBidSize = flowState.lastBidSize
      const prevAskSize = flowState.lastAskSize

      const askConsumed =
        prevAskSize != null && askSize != null ? Math.max(0, prevAskSize - askSize) : 0
      const bidConsumed =
        prevBidSize != null && bidSize != null ? Math.max(0, prevBidSize - bidSize) : 0
      const bidReplenished =
        prevBidSize != null && bidSize != null ? Math.max(0, bidSize - prevBidSize) : 0
      const askReplenished =
        prevAskSize != null && askSize != null ? Math.max(0, askSize - prevAskSize) : 0

      let buyPressure = askConsumed + bidReplenished * 0.35
      let sellPressure = bidConsumed + askReplenished * 0.35

      if (prevLtp != null && ltp > prevLtp) buyPressure += 1
      if (prevLtp != null && ltp < prevLtp) sellPressure += 1

      const totalPressure = buyPressure + sellPressure
      if (totalPressure > 0) {
        usedEstimatedFlow = true
        const scaled = Math.max(1, Math.min(12, Math.sqrt(totalPressure)))

        if (buyPressure > sellPressure) {
          buyAdd = scaled
        } else if (sellPressure > buyPressure) {
          sellAdd = scaled
        } else if (bidSize != null && askSize != null && bidSize + askSize > 0) {
          const imbalance = (bidSize - askSize) / (bidSize + askSize)
          if (imbalance > 0) buyAdd = Math.max(1, scaled * Math.abs(imbalance))
          else if (imbalance < 0) sellAdd = Math.max(1, scaled * Math.abs(imbalance))
        }
      }
    }

    // Final fallback for pure LTP feeds where order-book sizes are unavailable.
    if (buyAdd <= 0 && sellAdd <= 0 && prevLtp != null) {
      usedEstimatedFlow = true
      if (ltp > prevLtp) buyAdd = 1
      else if (ltp < prevLtp) sellAdd = 1
    }

    // If feed is alive but prints flat ticks (no depth + no cumulative volume),
    // keep a small balanced heartbeat so FLOW doesn't appear frozen.
    if (buyAdd <= 0 && sellAdd <= 0 && hasFreshTick) {
      usedEstimatedFlow = true
      buyAdd = 0.35
      sellAdd = 0.35
    }

    flowState.lastLtp = ltp
    flowState.lastTickAt = now
    if (currentVolume != null && Number.isFinite(currentVolume) && currentVolume >= 0) {
      flowState.lastVolume = currentVolume
    }
    if (bidSize != null && Number.isFinite(bidSize) && bidSize >= 0) {
      flowState.lastBidSize = bidSize
    }
    if (askSize != null && Number.isFinite(askSize) && askSize >= 0) {
      flowState.lastAskSize = askSize
    }

    if (buyAdd > 0 || sellAdd > 0) {
      flowState.buckets.push({ ts: now, buy: buyAdd, sell: sellAdd })
      flowState.cumulativeDelta += buyAdd - sellAdd

      const candleTime = Math.floor(now / 1000 / chartInterval) * chartInterval
      const current = flowState.candleFlow.get(candleTime) ?? { buy: 0, sell: 0 }
      current.buy += buyAdd
      current.sell += sellAdd
      flowState.candleFlow.set(candleTime, current)
    }

    const minTs = now - ORDER_FLOW_WINDOW_MS
    flowState.buckets = flowState.buckets.filter((bucket) => bucket.ts >= minTs)
    const minCandleTime = Math.floor(now / 1000) - chartInterval * (MAX_CANDLE_CACHE + 2)
    for (const candleTime of flowState.candleFlow.keys()) {
      if (candleTime < minCandleTime) flowState.candleFlow.delete(candleTime)
    }
    const buyFlow = flowState.buckets.reduce((sum, bucket) => sum + bucket.buy, 0)
    const sellFlow = flowState.buckets.reduce((sum, bucket) => sum + bucket.sell, 0)
    const delta = buyFlow - sellFlow
    const dominance: FlowDominance =
      delta > 0 ? 'BUY' : delta < 0 ? 'SELL' : 'BAL'

    let depthBid = 0
    let depthAsk = 0
    if (depthBuyLevels.length || depthSellLevels.length) {
      depthBid = depthBuyLevels.slice(0, 5).reduce((sum: number, level) => sum + parseDepthLevelQuantity(level), 0)
      depthAsk = depthSellLevels.slice(0, 5).reduce((sum: number, level) => sum + parseDepthLevelQuantity(level), 0)
    } else {
      depthBid = bidSize && bidSize > 0 ? bidSize : 0
      depthAsk = askSize && askSize > 0 ? askSize : 0
    }

    const depthTotal = depthBid + depthAsk
    const depthImbalancePct =
      depthTotal > 0 ? ((depthBid - depthAsk) / depthTotal) * 100 : null

    const spread = bid != null && ask != null && ask >= bid ? ask - bid : null

    if (now - flowState.lastEmitAt < ORDER_FLOW_EMIT_THROTTLE_MS) return
    flowState.lastEmitAt = now

    setOrderFlow({
      buyFlow,
      sellFlow,
      delta,
      cumulativeDelta: flowState.cumulativeDelta,
      dominance,
      depthImbalancePct,
      spread,
      flowSource: usedVolumeFlow && !usedEstimatedFlow ? 'VOL' : 'EST',
      lastUpdate: now,
    })

    refreshFootprints()
  }, [
    chartInterval,
    flowDataKey,
    orderFlowDepthData,
    orderFlowEnabled,
    orderFlowLtpData,
    orderFlowQuoteData,
    refreshFootprints,
    symbol,
  ])

  const refreshIndicators = useCallback(() => {
    const candles = candlesRef.current
    const cfg = indicatorConfigRef.current
    const colors = getChartColors(isDark)

    const closes: IndicatorPoint[] = candles.map((c) => ({
      time: c.time as number,
      value: c.close,
    }))

    const ema9Data = cfg.showEma9 ? calculateEMA(closes, 9) : []
    const ema21Data = cfg.showEma21 ? calculateEMA(closes, 21) : []
    const supertrendData = cfg.showSupertrend ? calculateSupertrend(candles, 10, 3).trend : []
    const vwapData = cfg.showVwap ? calculateVWAP(candles) : []

    if (ema9SeriesRef.current) {
      ema9SeriesRef.current.setData(toLineData(ema9Data))
    }
    if (ema21SeriesRef.current) {
      ema21SeriesRef.current.setData(toLineData(ema21Data))
    }
    if (supertrendSeriesRef.current) {
      supertrendSeriesRef.current.setData(toLineData(supertrendData))
    }
    if (vwapSeriesRef.current) {
      vwapSeriesRef.current.setData(toLineData(vwapData))
    }

    syncIndicatorPriceLine(
      ema9SeriesRef,
      ema9PriceLineRef,
      cfg.showEma9,
      getLastIndicatorValue(ema9Data),
      colors.ema9
    )
    syncIndicatorPriceLine(
      ema21SeriesRef,
      ema21PriceLineRef,
      cfg.showEma21,
      getLastIndicatorValue(ema21Data),
      colors.ema21
    )
    syncIndicatorPriceLine(
      supertrendSeriesRef,
      supertrendPriceLineRef,
      cfg.showSupertrend,
      getLastIndicatorValue(supertrendData),
      colors.supertrend
    )
    syncIndicatorPriceLine(
      vwapSeriesRef,
      vwapPriceLineRef,
      cfg.showVwap,
      getLastIndicatorValue(vwapData),
      colors.vwap
    )
  }, [isDark, syncIndicatorPriceLine])

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
    clearIndicatorPriceLines()
    setFootprints([])
  }, [clearIndicatorPriceLines])

  const applyChartMarkers = useCallback(() => {
    try {
      markersPluginRef.current?.setMarkers(chartMarkersRef.current)
    } catch {
      // plugin may not be ready yet
    }
  }, [])

  // Sync ghost signals and filled/exited executions as chart markers.
  // CE signals: green arrow below bar | PE signals: red arrow above bar.
  // Actual fills are drawn larger. Exits get an amber square.
  useEffect(() => {
    if (!symbol) return
    const isCE = side === 'CE'
    const markers: ChartSignalMarker[] = []

    // Ghost signals detected by the engine (shown in both ghost and execute modes)
    for (const sig of ghostSignals.slice(-40)) {
      if (sig.side !== side || sig.symbol !== symbol) continue
      const aligned = (Math.floor(sig.timestamp / 1000 / chartInterval) * chartInterval) as UTCTimestamp
      markers.push({
        time: aligned,
        position: isCE ? 'belowBar' : 'aboveBar',
        color: isCE ? '#22c55e99' : '#ef444499',
        shape: isCE ? 'arrowUp' : 'arrowDown',
        text: sig.score.toFixed(1),
        size: 1,
      })
    }

    // Execution samples: filled orders and exits (execute mode only)
    for (const samp of executionSamples.slice(-20)) {
      if (samp.side !== side || samp.symbol !== symbol) continue
      const aligned = (Math.floor(samp.timestamp / 1000 / chartInterval) * chartInterval) as UTCTimestamp
      if (samp.status === 'filled') {
        markers.push({
          time: aligned,
          position: isCE ? 'belowBar' : 'aboveBar',
          color: isCE ? '#22c55e' : '#ef4444',
          shape: isCE ? 'arrowUp' : 'arrowDown',
          text: 'IN',
          size: 2,
        })
      } else if (samp.status === 'exited') {
        markers.push({
          time: aligned,
          position: isCE ? 'aboveBar' : 'belowBar',
          color: '#f59e0b',
          shape: 'square',
          text: 'OUT',
          size: 1,
        })
      }
    }

    // setMarkers requires the array to be sorted by time
    markers.sort((a, b) => Number(a.time) - Number(b.time))
    chartMarkersRef.current = markers
    applyChartMarkers()
  }, [ghostSignals, executionSamples, side, symbol, chartInterval, applyChartMarkers])

  const applyCandles = useCallback(
    (candles: Candle[]) => {
      const next = normalizeRuntimeCandles(candles)
      candlesRef.current = cloneCandles(next)
      candleSeriesRef.current?.setData(toChartCandles(next))
      scheduleIndicatorRefresh()
      refreshFootprints()
    },
    [refreshFootprints, scheduleIndicatorRefresh]
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

  // Handle chart click to set active side
  const handleClick = useCallback(() => {
    setActiveSide(side)
  }, [side, setActiveSide])

  // Candle update callback - ref-based, no re-render
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
        if (candlesRef.current.length > 500) {
          candlesRef.current = candlesRef.current.slice(-500)
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
      refreshFootprints()
    },
    [refreshFootprints, scheduleIndicatorRefresh]
  )

  const { isConnected, isFallbackMode, reset: resetCandles, seed: seedCandles } = useCandleBuilder({
    symbol: symbol ?? '',
    exchange: optionExchange,
    intervalSec: chartInterval,
    mode: 'LTP',
    enabled: !!symbol,
    useIndiaMarketHours: false,
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
      chartMarkersRef.current = []
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
      const resolvedApiKey = await ensureApiKey()
      if (!resolvedApiKey) {
        if (requestSeq === historyLoadSeqRef.current) clearChartData()
        return
      }

      try {
        const buildSnapshotCandle = async (): Promise<Candle | null> => {
          try {
            const quoteResp = await tradingApi.getQuotes(resolvedApiKey, symbol, optionExchange)
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
              symbol,
              optionExchange,
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
    symbol,
    optionExchange,
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
      OPTION_PRICE_STEP,
      OPTION_MIN_PRICE_SPAN
    )

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
    const requestFootprintRepaint = () => {
      setFootprintViewportTick((tick) => tick + 1)
    }

    if (candlesRef.current.length > 0) {
      candleSeries.setData(toChartCandles(candlesRef.current))
      scheduleIndicatorRefresh()
      requestFootprintRepaint()
    }

    // Create the v5 markers plugin and restore any accumulated markers
    const markersPlugin = createSeriesMarkers(candleSeries)
    markersPluginRef.current = markersPlugin
    if (chartMarkersRef.current.length > 0) {
      try { markersPlugin.setMarkers(chartMarkersRef.current) } catch { /* no-op */ }
    }

    chart.timeScale().subscribeVisibleTimeRangeChange(requestFootprintRepaint)
    chart.timeScale().subscribeVisibleLogicalRangeChange(requestFootprintRepaint)

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (chartRef.current && container) {
        const w = container.offsetWidth
        const h = container.offsetHeight
        if (w > 0 && h > 0) {
          chartRef.current.applyOptions({ width: w, height: h })
          requestFootprintRepaint()
        }
      }
    })
    ro.observe(container)

    return () => {
      if (indicatorTimerRef.current !== null) {
        clearTimeout(indicatorTimerRef.current)
        indicatorTimerRef.current = null
      }
      clearIndicatorPriceLines()
      ro.disconnect()
      chart.timeScale().unsubscribeVisibleTimeRangeChange(requestFootprintRepaint)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(requestFootprintRepaint)
      try { markersPluginRef.current?.detach() } catch { /* no-op */ }
      markersPluginRef.current = null
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      ema9SeriesRef.current = null
      ema21SeriesRef.current = null
      supertrendSeriesRef.current = null
      vwapSeriesRef.current = null
    }
  }, [clearIndicatorPriceLines, isDark, scheduleIndicatorRefresh])

  return (
    <div
      className={cn(
        'flex h-full w-full min-w-0 min-h-0 flex-col overflow-hidden bg-background/30',
        prominence === 'secondary' && 'bg-background/10'
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-3 py-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={cn(
              'text-xs font-bold uppercase tracking-[0.16em]',
              side === 'CE' ? 'text-emerald-400' : 'text-rose-400'
            )}
          >
            {side}
          </span>
          <span className="truncate text-[10px] font-mono text-foreground/90">
            {symbol ?? 'Select a strike'}
          </span>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em]',
            isActive
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-border/60 bg-background/60 text-muted-foreground'
          )}
        >
          {isActive ? 'Active' : 'Click to focus'}
        </span>
      </div>

      {statsItems.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 border-b border-border/50 px-2 py-1">
          {statsItems.map((item) => (
            <span
              key={`${item.label}-${item.value}`}
              className={cn(
                'rounded-md border px-1.5 py-0.5 text-[9px] font-mono leading-none',
                item.tone === 'positive' && 'border-emerald-500/25 bg-emerald-500/10 text-emerald-300',
                item.tone === 'negative' && 'border-rose-500/25 bg-rose-500/10 text-rose-300',
                item.tone === 'accent' && 'border-sky-500/25 bg-sky-500/10 text-sky-300',
                (!item.tone || item.tone === 'neutral') && 'border-border/60 bg-background/60 text-muted-foreground'
              )}
            >
              <span className="text-[8px] uppercase tracking-[0.12em] text-muted-foreground/80">
                {item.label}
              </span>{' '}
              {item.value}
            </span>
          ))}
        </div>
      )}

      {/* biome-ignore lint/a11y/useKeyWithClickEvents: chart click for active side */}
      <div
        className={cn(
          'relative min-h-0 flex-1 overflow-hidden cursor-pointer',
          isActive && 'ring-1 ring-primary/20'
        )}
        onClick={handleClick}
      >
        <div ref={containerRef} className="h-full w-full" />

        <ChartOrderOverlay
          chartRef={chartRef}
          seriesRef={candleSeriesRef}
          side={side}
          containerRef={containerRef}
        />

        {showDetailedFootprints && symbol && renderableFootprints.length > 0 && (
          <div className="absolute inset-0 pointer-events-none">
            {renderableFootprints.map((row) => (
              <div
                key={`fp-detail-${Number(row.time)}`}
                className={cn(
                  'absolute min-w-[52px] rounded border px-1 py-0.5 text-[8px] font-mono leading-tight shadow-sm',
                  row.delta >= 0 ? 'border-emerald-400/60' : 'border-rose-400/60'
                )}
                style={{
                  left: `${row.x}px`,
                  top: `${row.y}px`,
                  transform: 'translate(-50%, 0)',
                  backgroundColor:
                    row.delta >= 0
                      ? `rgba(16, 185, 129, ${row.alpha.toFixed(3)})`
                      : `rgba(244, 63, 94, ${row.alpha.toFixed(3)})`,
                }}
              >
                <div className="text-white/90">B {formatFlowNumber(row.buy)}</div>
                <div className="text-white/90">S {formatFlowNumber(row.sell)}</div>
                <div className={cn(row.delta >= 0 ? 'text-emerald-100' : 'text-rose-100')}>
                  Δ {formatSignedFlowNumber(row.delta)}
                </div>
              </div>
            ))}
          </div>
        )}

        {showFootprintHeat && symbol && renderableFootprints.length > 0 && (
          <div className="absolute inset-0 pointer-events-none">
            {renderableFootprints.map((row) => (
              <div
                key={`fp-heat-${Number(row.time)}`}
                className="absolute rounded-sm"
                style={{
                  left: `${row.x}px`,
                  top: `${row.yTop}px`,
                  width: '10px',
                  height: `${row.height}px`,
                  transform: 'translateX(-50%)',
                  backgroundColor:
                    row.delta >= 0
                      ? `rgba(16, 185, 129, ${(row.alpha * 0.55).toFixed(3)})`
                      : `rgba(244, 63, 94, ${(row.alpha * 0.55).toFixed(3)})`,
                }}
              />
            ))}
          </div>
        )}

        {showOrderFlow && symbol && (
          <div className="absolute left-2 top-2 pointer-events-none">
            <div className="inline-flex items-center gap-2 rounded-full border border-border/70 bg-background/82 px-2.5 py-1 text-[10px] font-mono shadow-sm backdrop-blur-[1px]">
              <span
                className={cn(
                  unifiedFlowSignal.action === 'BUY_CE'
                    ? 'text-emerald-400'
                    : unifiedFlowSignal.action === 'BUY_PE'
                      ? 'text-rose-400'
                      : 'text-amber-300'
                )}
              >
                {unifiedFlowSignal.label}
              </span>
              <span className="text-muted-foreground/70">|</span>
              <span
                className={cn(
                  orderFlow.delta > 0
                    ? 'text-emerald-400'
                    : orderFlow.delta < 0
                      ? 'text-rose-400'
                      : 'text-muted-foreground'
                )}
              >
                Δ {formatSignedFlowNumber(orderFlow.delta)}
              </span>
            </div>
          </div>
        )}

        {symbol && !isConnected && !isFallbackMode && (
          <div className="absolute bottom-1 right-2 pointer-events-none">
            <span className="text-[10px] text-yellow-500">Connecting...</span>
          </div>
        )}
        {symbol && !isConnected && isFallbackMode && (
          <div className="absolute bottom-1 right-2 pointer-events-none">
            <span className="text-[10px] text-blue-500">REST fallback</span>
          </div>
        )}

        {!symbol && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <span className="text-xs text-muted-foreground/40">
              Click a strike in the chain
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
