import { useCallback, useEffect, useRef } from 'react'
import { useMarketData } from './useMarketData'
import { mergeTickIntoCandle, type Candle, type Tick } from '@/lib/candleUtils'

interface UseCandleBuilderOptions {
  symbol: string
  exchange: string
  intervalSec?: number
  enabled?: boolean
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
  maxCandles = 500,
  onCandleUpdate,
}: UseCandleBuilderOptions) {
  const candlesRef = useRef<Candle[]>([])
  const currentCandleRef = useRef<Candle | null>(null)
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

    const tick: Tick = {
      price: symbolData.data.ltp,
      volume: symbolData.data.volume,
      timestamp: symbolData.lastUpdate ?? Date.now(),
    }

    const [candle, isNew] = mergeTickIntoCandle(tick, currentCandleRef.current, intervalSec)

    if (isNew && currentCandleRef.current) {
      // Finalize previous candle
      candlesRef.current.push(currentCandleRef.current)
      if (candlesRef.current.length > maxCandles) {
        candlesRef.current = candlesRef.current.slice(-maxCandles)
      }
    }

    currentCandleRef.current = candle
    callbackRef.current?.(candle, isNew)
  }, [wsData, symbol, exchange, intervalSec, enabled, maxCandles])

  const getCandles = useCallback((): Candle[] => {
    const all = [...candlesRef.current]
    if (currentCandleRef.current) all.push(currentCandleRef.current)
    return all
  }, [])

  const reset = useCallback(() => {
    candlesRef.current = []
    currentCandleRef.current = null
  }, [])

  return {
    getCandles,
    reset,
    isConnected,
  }
}
