import { useCallback, useEffect, useRef, useState } from 'react'
import { MarketDataManager, type SymbolData } from '@/lib/MarketDataManager'
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
 *
 * Subscribes DIRECTLY to MarketDataManager (bypassing React state/rAF batching)
 * so that every tick immediately fires the onCandleUpdate callback without
 * waiting for a React render cycle.
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

  // Connection state (updated via direct addStateListener, no React render per tick)
  const [isConnected, setIsConnected] = useState(() => {
    return MarketDataManager.getInstance().getState().isConnected
  })
  const [isFallbackMode, setIsFallbackMode] = useState(() => {
    return MarketDataManager.getInstance().getState().isFallbackMode
  })

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

  // Direct subscription to MarketDataManager — bypasses React state/rAF batching.
  // Every WS tick fires applyTick immediately without waiting for a render cycle.
  useEffect(() => {
    if (!enabled || !symbol) return

    const manager = MarketDataManager.getInstance()
    const normalizedSymbol = symbol.trim().toUpperCase()
    const normalizedExchange = exchange.trim().toUpperCase()

    // Sync initial connection state
    const initial = manager.getState()
    setIsConnected(initial.isConnected)
    setIsFallbackMode(initial.isFallbackMode)

    // Track connection state changes (for REST fallback logic)
    const unsubscribeState = manager.addStateListener((state) => {
      setIsConnected(state.isConnected)
      setIsFallbackMode(state.isFallbackMode)
    })

    // Subscribe directly — callback fires on every WS tick, no React cycle overhead
    const unsubscribeTick = manager.subscribe(
      normalizedSymbol,
      normalizedExchange,
      mode,
      (data: SymbolData) => {
        const tickLtp = parseOptionalNumber(data.data?.ltp)
        if (tickLtp == null || tickLtp <= 0) return
        applyTick({
          ltp: tickLtp,
          volume: parseOptionalNumber(data.data?.volume),
          timestamp: data.lastUpdate ?? Date.now(),
          sourceKey: `${data.exchange.toUpperCase()}:${data.symbol.toUpperCase()}:WS`,
        })
      }
    )

    // Auto-connect if not already connected or paused
    const currentState = manager.getState()
    if (!currentState.isConnected && !currentState.isPaused) {
      void manager.connect()
    }

    return () => {
      unsubscribeState()
      unsubscribeTick()
    }
  }, [enabled, symbol, exchange, mode, applyTick])

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

        applyTick({
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
