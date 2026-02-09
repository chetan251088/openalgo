import { useCallback, useEffect, useRef } from 'react'
import { useMarketData } from './useMarketData'
import { mergeTickIntoCandle, type Candle, type Tick } from '@/lib/candleUtils'
import { isWithinIndiaMarketHours } from '@/lib/indiaMarketTime'

interface UseCandleBuilderOptions {
  symbol: string
  exchange: string
  intervalSec?: number
  enabled?: boolean
  useIndiaMarketHours?: boolean
  maxCandles?: number
  onCandleUpdate?: (candle: Candle, isNew: boolean) => void
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
  enabled = true,
  useIndiaMarketHours = false,
  maxCandles = 500,
  onCandleUpdate,
}: UseCandleBuilderOptions) {
  const candlesRef = useRef<Candle[]>([])
  const currentCandleRef = useRef<Candle | null>(null)
  const lastProcessedTickKeyRef = useRef<string | null>(null)
  const callbackRef = useRef(onCandleUpdate)
  callbackRef.current = onCandleUpdate

  const symbols = enabled && symbol
    ? [{ symbol, exchange }]
    : []

  const { data: wsData, isConnected } = useMarketData({
    symbols,
    mode: 'LTP',
    enabled: enabled && !!symbol,
  })

  // Process tick data when it arrives
  useEffect(() => {
    if (!enabled || !symbol || wsData.size === 0) return

    const key = `${exchange}:${symbol}`
    const symbolData = wsData.get(key)
    if (!symbolData?.data?.ltp) return

    const tickTimestamp = symbolData.lastUpdate
    if (!tickTimestamp || !Number.isFinite(tickTimestamp)) return
    const tickEpochSeconds = Math.floor(tickTimestamp / 1000)
    if (useIndiaMarketHours && !isWithinIndiaMarketHours(tickEpochSeconds)) return

    // Ignore stale/cached ticks being replayed on dependency changes (e.g. timeframe switch).
    const tickKey = `${key}:${tickTimestamp}`
    if (lastProcessedTickKeyRef.current === tickKey) return
    lastProcessedTickKeyRef.current = tickKey

    const tick: Tick = {
      price: symbolData.data.ltp,
      volume: symbolData.data.volume,
      timestamp: tickTimestamp,
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
    callbackRef.current?.(candle, isNew)
  }, [wsData, symbol, exchange, intervalSec, enabled, maxCandles, useIndiaMarketHours])

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
  }
}
