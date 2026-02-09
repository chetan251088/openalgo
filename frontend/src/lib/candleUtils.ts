import type { UTCTimestamp } from 'lightweight-charts'

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

/**
 * Merge a new tick into the current candle (or create a new one).
 * Candle interval in seconds. Returns [updatedCandle, isNewCandle].
 */
export function mergeTickIntoCandle(
  tick: Tick,
  currentCandle: Candle | null,
  intervalSec: number
): [Candle, boolean] {
  const candleTime = Math.floor(tick.timestamp / 1000 / intervalSec) * intervalSec as UTCTimestamp
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
