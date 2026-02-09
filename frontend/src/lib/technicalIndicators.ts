import type { Candle } from './candleUtils'

export interface IndicatorPoint {
  time: number // UTCTimestamp
  value: number
}

/**
 * Exponential Moving Average
 */
export function calculateEMA(closes: IndicatorPoint[], period: number): IndicatorPoint[] {
  if (closes.length < period) return []
  const k = 2 / (period + 1)
  const result: IndicatorPoint[] = []

  // SMA for the first value
  let sum = 0
  for (let i = 0; i < period; i++) {
    sum += closes[i].value
  }
  let ema = sum / period
  result.push({ time: closes[period - 1].time, value: ema })

  for (let i = period; i < closes.length; i++) {
    ema = closes[i].value * k + ema * (1 - k)
    result.push({ time: closes[i].time, value: ema })
  }
  return result
}

/**
 * Supertrend indicator
 */
export function calculateSupertrend(
  candles: Candle[],
  period: number = 10,
  multiplier: number = 3
): { upper: IndicatorPoint[]; lower: IndicatorPoint[]; trend: IndicatorPoint[] } {
  if (candles.length < period + 1) {
    return { upper: [], lower: [], trend: [] }
  }

  const atr = calculateATR(candles, period)
  const upper: IndicatorPoint[] = []
  const lower: IndicatorPoint[] = []
  const trend: IndicatorPoint[] = []

  if (atr.length === 0) return { upper, lower, trend }

  // Align candles with ATR (ATR starts at index period)
  const startIdx = candles.length - atr.length

  let prevUpperBand = 0
  let prevLowerBand = 0
  let prevTrend = 1 // 1 = bullish, -1 = bearish

  for (let i = 0; i < atr.length; i++) {
    const candle = candles[startIdx + i]
    const hl2 = (candle.high + candle.low) / 2
    const atrVal = atr[i].value

    let upperBand = hl2 + multiplier * atrVal
    let lowerBand = hl2 - multiplier * atrVal

    // Carry forward bands
    if (i > 0) {
      if (lowerBand > prevLowerBand || candles[startIdx + i - 1].close < prevLowerBand) {
        // keep lowerBand
      } else {
        lowerBand = prevLowerBand
      }

      if (upperBand < prevUpperBand || candles[startIdx + i - 1].close > prevUpperBand) {
        // keep upperBand
      } else {
        upperBand = prevUpperBand
      }
    }

    let currentTrend = prevTrend
    if (i > 0) {
      if (prevTrend === 1 && candle.close < lowerBand) {
        currentTrend = -1
      } else if (prevTrend === -1 && candle.close > upperBand) {
        currentTrend = 1
      }
    }

    const time = candle.time as number
    upper.push({ time, value: upperBand })
    lower.push({ time, value: lowerBand })
    trend.push({ time, value: currentTrend === 1 ? lowerBand : upperBand })

    prevUpperBand = upperBand
    prevLowerBand = lowerBand
    prevTrend = currentTrend
  }

  return { upper, lower, trend }
}

/**
 * Average True Range
 */
export function calculateATR(candles: Candle[], period: number = 14): IndicatorPoint[] {
  if (candles.length < period + 1) return []
  const result: IndicatorPoint[] = []

  // Calculate true ranges
  const tr: number[] = []
  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].high
    const low = candles[i].low
    const prevClose = candles[i - 1].close
    tr.push(Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose)))
  }

  // First ATR is SMA
  let atr = 0
  for (let i = 0; i < period; i++) {
    atr += tr[i]
  }
  atr /= period
  result.push({ time: candles[period].time as number, value: atr })

  // Subsequent ATRs use exponential smoothing
  for (let i = period; i < tr.length; i++) {
    atr = (atr * (period - 1) + tr[i]) / period
    result.push({ time: candles[i + 1].time as number, value: atr })
  }

  return result
}

/**
 * Volume Weighted Average Price (intraday)
 */
export function calculateVWAP(candles: Candle[]): IndicatorPoint[] {
  if (candles.length === 0) return []

  const result: IndicatorPoint[] = []
  let cumTypicalPriceVolume = 0
  let cumVolume = 0

  for (const candle of candles) {
    const typicalPrice = (candle.high + candle.low + candle.close) / 3
    const vol = candle.volume || 1
    cumTypicalPriceVolume += typicalPrice * vol
    cumVolume += vol
    result.push({
      time: candle.time as number,
      value: cumVolume > 0 ? cumTypicalPriceVolume / cumVolume : typicalPrice,
    })
  }

  return result
}

/**
 * Relative Strength Index
 */
export function calculateRSI(closes: IndicatorPoint[], period: number = 14): IndicatorPoint[] {
  if (closes.length < period + 1) return []

  const result: IndicatorPoint[] = []
  const gains: number[] = []
  const losses: number[] = []

  for (let i = 1; i < closes.length; i++) {
    const diff = closes[i].value - closes[i - 1].value
    gains.push(diff > 0 ? diff : 0)
    losses.push(diff < 0 ? -diff : 0)
  }

  let avgGain = 0
  let avgLoss = 0
  for (let i = 0; i < period; i++) {
    avgGain += gains[i]
    avgLoss += losses[i]
  }
  avgGain /= period
  avgLoss /= period

  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss
  result.push({
    time: closes[period].time,
    value: 100 - 100 / (1 + rs),
  })

  for (let i = period; i < gains.length; i++) {
    avgGain = (avgGain * (period - 1) + gains[i]) / period
    avgLoss = (avgLoss * (period - 1) + losses[i]) / period
    const rsI = avgLoss === 0 ? 100 : avgGain / avgLoss
    result.push({
      time: closes[i + 1].time,
      value: 100 - 100 / (1 + rsI),
    })
  }

  return result
}
