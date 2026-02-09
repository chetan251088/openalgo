import type { UTCTimestamp } from 'lightweight-charts'
import { alignToIndiaMarketInterval } from '@/lib/indiaMarketTime'

export interface Candle {
  time: UTCTimestamp
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Tick {
  price: number
  volume?: number
  timestamp: number // ms epoch
}

interface MergeTickOptions {
  alignToIndiaSession?: boolean
}

/**
 * Merge a new tick into the current candle (or create a new one).
 * Candle interval in seconds. Returns [updatedCandle, isNewCandle].
 */
export function mergeTickIntoCandle(
  tick: Tick,
  currentCandle: Candle | null,
  intervalSec: number,
  options: MergeTickOptions = {}
): [Candle, boolean] {
  const tickEpochSeconds = Math.floor(tick.timestamp / 1000)
  const candleEpochSeconds = options.alignToIndiaSession
    ? alignToIndiaMarketInterval(tickEpochSeconds, intervalSec)
    : Math.floor(tickEpochSeconds / intervalSec) * intervalSec
  const candleTime = candleEpochSeconds as UTCTimestamp
  const vol = tick.volume ?? 0

  if (!currentCandle || currentCandle.time !== candleTime) {
    // New candle
    return [
      {
        time: candleTime,
        open: tick.price,
        high: tick.price,
        low: tick.price,
        close: tick.price,
        volume: vol,
      },
      true,
    ]
  }

  // Update existing candle
  return [
    {
      ...currentCandle,
      high: Math.max(currentCandle.high, tick.price),
      low: Math.min(currentCandle.low, tick.price),
      close: tick.price,
      volume: currentCandle.volume + vol,
    },
    false,
  ]
}

/**
 * Build candle array from an array of ticks.
 */
export function buildCandlesFromTicks(ticks: Tick[], intervalSec: number): Candle[] {
  const candles: Candle[] = []
  let current: Candle | null = null

  for (const tick of ticks) {
    const [candle, isNew] = mergeTickIntoCandle(tick, current, intervalSec)
    if (isNew && current) {
      candles.push(current)
    }
    current = candle
  }

  if (current) candles.push(current)
  return candles
}
