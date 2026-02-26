import { useCallback, useEffect, useRef } from 'react'
import { useMarketData, type SymbolData } from './useMarketData'
import { mergeTickIntoCandle, type Candle, type Tick } from '@/lib/candleUtils'
import { isWithinIndiaMarketHours } from '@/lib/indiaMarketTime'
import type { SubscriptionMode } from '@/lib/MarketDataManager'
import { tradingApi } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'

type CandleFeedMode = Extract<SubscriptionMode, 'LTP' | 'Quote'>
const REST_FALLBACK_STALE_MS = 4000
const REST_FALLBACK_POLL_MS = 3000

interface UseCandleBuilderOptions {
  symbol: string
  exchange: string
  intervalSec?: number
  mode?: CandleFeedMode
  enabled?: boolean
  useIndiaMarketHours?: boolean
  maxCandles?: number
  onCandleUpdate?: (candle: Candle, isNew: boolean) => void
}

function getExchangeLookupCandidates(exchange: string): string[] {
  const normalized = exchange.trim().toUpperCase()
  const candidates = new Set<string>()
  if (normalized) candidates.add(normalized)

  if (normalized === 'NSE_INDEX') candidates.add('NSE')
  else if (normalized === 'BSE_INDEX') candidates.add('BSE')
  else if (normalized === 'NSE') candidates.add('NSE_INDEX')
  else if (normalized === 'BSE') candidates.add('BSE_INDEX')

  return Array.from(candidates)
}

function getSymbolDataFromWs(
  wsData: Map<string, SymbolData>,
  symbol: string,
  preferredExchange: string
): SymbolData | undefined {
  const symbolKey = symbol.trim().toUpperCase()
  if (!symbolKey) return undefined

  const exchangeCandidates = getExchangeLookupCandidates(preferredExchange)
  for (const exchange of exchangeCandidates) {
    const direct = wsData.get(`${exchange}:${symbolKey}`)
    if (direct) return direct
  }

  const symbolMatches: SymbolData[] = []
  for (const [, entry] of wsData) {
    if (entry.symbol.trim().toUpperCase() === symbolKey) {
      symbolMatches.push(entry)
    }
  }
  if (symbolMatches.length === 0) return undefined

  const preferred = preferredExchange.trim().toUpperCase()
  const fuzzyMatch = symbolMatches.find((entry) => {
    const entryExchange = entry.exchange.trim().toUpperCase()
    return (
      entryExchange === preferred ||
      entryExchange.includes(preferred) ||
      preferred.includes(entryExchange)
    )
  })
  return fuzzyMatch ?? symbolMatches[0]
}

function parseOptionalNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value === 'string') {
    const parsed = Number(value.trim())
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

/**
 * Builds OHLC candles from real-time WebSocket ticks.
 * Uses useMarketData for subscription and fires onCandleUpdate callback
 * on every tick (ref-based, no React re-renders per tick).
 */
export function useCandleBuilder({
  symbol,
  exchange,
  intervalSec = 1,
  mode = 'LTP',
  enabled = true,
  useIndiaMarketHours = false,
  maxCandles = 500,
  onCandleUpdate,
}: UseCandleBuilderOptions) {
  const candlesRef = useRef<Candle[]>([])
  const currentCandleRef = useRef<Candle | null>(null)
  const lastProcessedTickKeyRef = useRef<string | null>(null)
  const lastTickAtRef = useRef(0)
  const restFallbackInFlightRef = useRef(false)
  const callbackRef = useRef(onCandleUpdate)
  callbackRef.current = onCandleUpdate

  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    const existing = useAuthStore.getState().apiKey
    if (existing) return existing

    try {
      const response = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await response.json()
      if (data.status === 'success' && data.api_key) {
        useAuthStore.getState().setApiKey(data.api_key)
        return data.api_key as string
      }
    } catch {
      // ignore
    }
    return null
  }, [])

  const applyTick = useCallback(
    (params: {
      ltp: number
      volume?: number | null
      timestamp: number
      sourceKey: string
    }): boolean => {
      if (!enabled) return false
      if (!Number.isFinite(params.ltp) || params.ltp <= 0) return false
      if (!Number.isFinite(params.timestamp) || params.timestamp <= 0) return false

      const tickEpochSeconds = Math.floor(params.timestamp / 1000)
      if (useIndiaMarketHours && !isWithinIndiaMarketHours(tickEpochSeconds)) return false

      const tickKey = `${params.sourceKey}:${params.timestamp}:${params.ltp}:${params.volume ?? ''}`
      if (lastProcessedTickKeyRef.current === tickKey) return false
      lastProcessedTickKeyRef.current = tickKey

      const tick: Tick = {
        price: params.ltp,
        volume: params.volume ?? undefined,
        timestamp: params.timestamp,
      }

      const [candle, isNew] = mergeTickIntoCandle(tick, currentCandleRef.current, intervalSec, {
        alignToIndiaSession: useIndiaMarketHours,
      })

      if (isNew && currentCandleRef.current) {
        // Finalize previous candle
        candlesRef.current.push(currentCandleRef.current)
        if (candlesRef.current.length > maxCandles) {
          candlesRef.current = candlesRef.current.slice(-maxCandles)
        }
      }

      currentCandleRef.current = candle
      lastTickAtRef.current = Date.now()
      callbackRef.current?.(candle, isNew)
      return true
    },
    [enabled, intervalSec, maxCandles, useIndiaMarketHours]
  )

  const symbols = enabled && symbol
    ? [{ symbol, exchange }]
    : []

  const { data: wsData, isConnected, isFallbackMode } = useMarketData({
    symbols,
    mode,
    enabled: enabled && !!symbol,
  })

  // Process tick data when it arrives
  useEffect(() => {
    if (!enabled || !symbol || wsData.size === 0) return

    const symbolData = getSymbolDataFromWs(wsData, symbol, exchange)
    if (!symbolData?.data?.ltp) return

    const tickTimestamp = parseOptionalNumber(symbolData.lastUpdate)
    const tickLtp = parseOptionalNumber(symbolData.data.ltp)
    if (tickTimestamp == null || tickLtp == null) return

    void applyTick({
      ltp: tickLtp,
      volume: parseOptionalNumber(symbolData.data.volume),
      timestamp: tickTimestamp,
      sourceKey: `${symbolData.exchange.toUpperCase()}:${symbolData.symbol.toUpperCase()}:WS`,
    })
  }, [wsData, symbol, exchange, applyTick, enabled])

  // Auto-heal stalled chart streams: when symbol ticks stop, keep candles alive from quotes API.
  useEffect(() => {
    if (!enabled || !symbol) return

    let cancelled = false
    const normalizedSymbol = symbol.trim().toUpperCase()
    const normalizedExchange = exchange.trim().toUpperCase()

    const pollFallbackQuote = async () => {
      if (cancelled || restFallbackInFlightRef.current) return

      const lastTickAt = lastTickAtRef.current
      const ageMs = lastTickAt > 0 ? Date.now() - lastTickAt : Number.POSITIVE_INFINITY
      const shouldFallback =
        !isConnected || isFallbackMode || ageMs >= REST_FALLBACK_STALE_MS
      if (!shouldFallback) return

      restFallbackInFlightRef.current = true
      try {
        const apiKey = await ensureApiKey()
        if (!apiKey || cancelled) return

        const response = await tradingApi.getQuotes(apiKey, normalizedSymbol, normalizedExchange)
        if (cancelled || response.status !== 'success' || !response.data) return

        const quoteLtp = parseOptionalNumber(response.data.ltp)
        if (quoteLtp == null || quoteLtp <= 0) return

        void applyTick({
          ltp: quoteLtp,
          volume: parseOptionalNumber(response.data.volume),
          timestamp: Date.now(),
          sourceKey: `${normalizedExchange}:${normalizedSymbol}:REST`,
        })
      } catch {
        // Keep silent: this is best-effort self-healing fallback.
      } finally {
        restFallbackInFlightRef.current = false
      }
    }

    void pollFallbackQuote()
    const intervalId = setInterval(() => {
      void pollFallbackQuote()
    }, REST_FALLBACK_POLL_MS)

    return () => {
      cancelled = true
      clearInterval(intervalId)
      restFallbackInFlightRef.current = false
    }
  }, [enabled, symbol, exchange, isConnected, isFallbackMode, ensureApiKey, applyTick])

  const getCandles = useCallback((): Candle[] => {
    const all = [...candlesRef.current]
    if (currentCandleRef.current) all.push(currentCandleRef.current)
    return all
  }, [])

  const reset = useCallback(() => {
    candlesRef.current = []
    currentCandleRef.current = null
    // Allow one fresh rebuild after symbol/interval reset, even if the last tick timestamp is unchanged.
    lastProcessedTickKeyRef.current = null
    lastTickAtRef.current = 0
  }, [])

  const seed = useCallback(
    (candles: Candle[]) => {
      if (!candles?.length) {
        candlesRef.current = []
        currentCandleRef.current = null
        return
      }

      const clipped = candles.slice(-maxCandles)
      if (clipped.length === 1) {
        candlesRef.current = []
        currentCandleRef.current = clipped[0]
        return
      }

      candlesRef.current = clipped.slice(0, -1)
      currentCandleRef.current = clipped[clipped.length - 1]
    },
    [maxCandles]
  )

  return {
    getCandles,
    reset,
    seed,
    isConnected,
    isFallbackMode,
  }
}
