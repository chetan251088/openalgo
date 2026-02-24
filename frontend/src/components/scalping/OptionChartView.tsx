import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { useMarketData } from '@/hooks/useMarketData'
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

interface OptionChartViewProps {
  side: ActiveSide
  showEma9: boolean
  showEma21: boolean
  showSupertrend: boolean
  showVwap: boolean
  showOrderFlow: boolean
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

type FlowDominance = 'BUY' | 'SELL' | 'BAL'
type FlowBiasAction = 'BUY_CE' | 'BUY_PE' | 'HOLD'

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

interface FlowState {
  lastLtp: number | null
  lastVolume: number | null
  lastBidSize: number | null
  lastAskSize: number | null
  lastTickAt: number
  cumulativeDelta: number
  buckets: FlowTickBucket[]
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

function deriveFlowBias(side: ActiveSide, flow: OrderFlowStats): { action: FlowBiasAction; label: string } {
  const totalFlow = Math.max(0, flow.buyFlow + flow.sellFlow)
  if (totalFlow <= 0) return { action: 'HOLD', label: 'HOLD FOR NOW' }

  const imbalance = Math.abs(flow.delta) / Math.max(1, totalFlow)
  const directionAligned =
    flow.delta === 0
      ? false
      : Math.sign(flow.delta) === Math.sign(flow.cumulativeDelta) &&
        Math.abs(flow.cumulativeDelta) >= Math.abs(flow.delta) * 0.35

  const minActivity = flow.flowSource === 'VOL' ? 1_000 : 250
  const minImbalance = flow.flowSource === 'VOL' ? 0.12 : 0.22

  if (!directionAligned || totalFlow < minActivity || imbalance < minImbalance) {
    return { action: 'HOLD', label: 'HOLD FOR NOW' }
  }

  const sideBuying = flow.delta > 0 && flow.cumulativeDelta > 0
  const sideSelling = flow.delta < 0 && flow.cumulativeDelta < 0

  if (sideBuying) {
    return side === 'CE'
      ? { action: 'BUY_CE', label: 'BUY CE NOW' }
      : { action: 'BUY_PE', label: 'BUY PE NOW' }
  }
  if (sideSelling) {
    return side === 'CE'
      ? { action: 'BUY_PE', label: 'BUY PE NOW' }
      : { action: 'BUY_CE', label: 'BUY CE NOW' }
  }
  return { action: 'HOLD', label: 'HOLD FOR NOW' }
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
  showEma9,
  showEma21,
  showSupertrend,
  showVwap,
  showOrderFlow,
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
  const flowStateRef = useRef<FlowState>({
    lastLtp: null,
    lastVolume: null,
    lastBidSize: null,
    lastAskSize: null,
    lastTickAt: 0,
    cumulativeDelta: 0,
    buckets: [],
    lastEmitAt: 0,
  })
  const [orderFlow, setOrderFlow] = useState<OrderFlowStats>(EMPTY_ORDER_FLOW)
  const [flowClock, setFlowClock] = useState(() => Date.now())
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

  const isActive = activeSide === side
  const orderFlowSymbols = useMemo(
    () => (showOrderFlow && symbol ? [{ symbol, exchange: optionExchange }] : []),
    [showOrderFlow, symbol, optionExchange]
  )
  const { data: orderFlowQuoteData, isFallbackMode: isQuoteFallbackMode } = useMarketData({
    symbols: orderFlowSymbols,
    mode: 'Quote',
    enabled: showOrderFlow && !!symbol,
  })
  const { data: orderFlowLtpData, isFallbackMode: isLtpFallbackMode } = useMarketData({
    symbols: orderFlowSymbols,
    mode: 'LTP',
    enabled: showOrderFlow && !!symbol,
  })
  const { data: orderFlowDepthData, isFallbackMode: isDepthFallbackMode } = useMarketData({
    symbols: orderFlowSymbols,
    mode: 'Depth',
    enabled: showOrderFlow && !!symbol,
  })
  const flowDataKey = useMemo(
    () =>
      showOrderFlow && symbol
        ? `${optionExchange.toUpperCase()}:${symbol.toUpperCase()}`
        : null,
    [optionExchange, showOrderFlow, symbol]
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
  const flowBias = useMemo(() => deriveFlowBias(side, orderFlow), [side, orderFlow])
  const isFlowStale = useMemo(() => {
    if (!showOrderFlow || !symbol || lastFlowTickAt <= 0) return false
    const staleThreshold = isOrderFlowFallbackMode
      ? ORDER_FLOW_FALLBACK_STALE_MS
      : ORDER_FLOW_STALE_MS
    return flowClock - lastFlowTickAt > staleThreshold
  }, [flowClock, isOrderFlowFallbackMode, lastFlowTickAt, showOrderFlow, symbol])

  useEffect(() => {
    if (!showOrderFlow || !symbol) return
    const timer = setInterval(() => setFlowClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [showOrderFlow, symbol])

  useEffect(() => {
    flowStateRef.current = {
      lastLtp: null,
      lastVolume: null,
      lastBidSize: null,
      lastAskSize: null,
      lastTickAt: 0,
      cumulativeDelta: 0,
      buckets: [],
      lastEmitAt: 0,
    }
    setOrderFlow(EMPTY_ORDER_FLOW)
  }, [showOrderFlow, symbol, optionExchange])

  useEffect(() => {
    if (!showOrderFlow || !symbol || !flowDataKey) return
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
    }

    const minTs = now - ORDER_FLOW_WINDOW_MS
    flowState.buckets = flowState.buckets.filter((bucket) => bucket.ts >= minTs)
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
  }, [flowDataKey, orderFlowDepthData, orderFlowLtpData, orderFlowQuoteData, showOrderFlow, symbol])

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
    },
    [scheduleIndicatorRefresh]
  )

  const { isConnected, reset: resetCandles, seed: seedCandles } = useCandleBuilder({
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

      {/* Order-flow overlay */}
      {showOrderFlow && symbol && (
        <div className="absolute top-7 left-2 pointer-events-none rounded border border-border/70 bg-background/85 backdrop-blur-[1px] px-2 py-1.5 text-[10px] font-mono leading-tight">
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="text-muted-foreground">FLOW 30s</span>
            <span className={cn(
              orderFlow.flowSource === 'VOL' ? 'text-emerald-500' : 'text-amber-500'
            )}>
              {orderFlow.flowSource}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <span className="text-green-500">B {formatFlowNumber(orderFlow.buyFlow)}</span>
            <span className="text-red-500 text-right">S {formatFlowNumber(orderFlow.sellFlow)}</span>
            <span className={cn(
              orderFlow.delta > 0 ? 'text-green-500' : orderFlow.delta < 0 ? 'text-red-500' : 'text-muted-foreground'
            )}>
              D {orderFlow.delta >= 0 ? '+' : ''}{formatFlowNumber(orderFlow.delta)}
            </span>
            <span className={cn(
              orderFlow.cumulativeDelta > 0 ? 'text-green-500' : orderFlow.cumulativeDelta < 0 ? 'text-red-500' : 'text-muted-foreground',
              'text-right'
            )}>
              CD {orderFlow.cumulativeDelta >= 0 ? '+' : ''}{formatFlowNumber(orderFlow.cumulativeDelta)}
            </span>
            <span className={cn(
              orderFlow.dominance === 'BUY' ? 'text-green-500' : orderFlow.dominance === 'SELL' ? 'text-red-500' : 'text-muted-foreground'
            )}>
              {orderFlow.dominance}
            </span>
            <span className={cn(
              flowBias.action === 'BUY_CE'
                ? 'text-green-500'
                : flowBias.action === 'BUY_PE'
                  ? 'text-red-500'
                  : 'text-amber-400',
              'text-right'
            )}>
              {flowBias.label}
            </span>
            {(orderFlow.depthImbalancePct != null || orderFlow.spread != null) && (
              <span className={cn(
                orderFlow.depthImbalancePct == null
                  ? 'text-muted-foreground'
                  : orderFlow.depthImbalancePct >= 0
                    ? 'text-green-500'
                    : 'text-red-500',
                'text-right'
              )}>
                DEPTH {orderFlow.depthImbalancePct == null ? 'NA' : `${orderFlow.depthImbalancePct >= 0 ? '+' : ''}${orderFlow.depthImbalancePct.toFixed(0)}%`}
              </span>
            )}
            {(orderFlow.depthImbalancePct != null || orderFlow.spread != null) && (
              <span className="text-muted-foreground col-span-2">
                SPR {orderFlow.spread == null ? 'NA' : orderFlow.spread.toFixed(2)}
              </span>
            )}
            {orderFlow.depthImbalancePct == null && orderFlow.spread == null && (
              <span
                className={cn(
                  'col-span-2 text-right',
                  isFlowStale ? 'text-amber-500' : 'text-muted-foreground'
                )}
              >
                {isFlowStale ? 'WS stale / no recent ticks' : 'No L2 quote'}
              </span>
            )}
          </div>
        </div>
      )}

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
