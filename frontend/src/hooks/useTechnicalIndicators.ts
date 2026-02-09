import { useMemo } from 'react'
import type { Candle } from '@/lib/candleUtils'
import {
  calculateEMA,
  calculateSupertrend,
  calculateVWAP,
  calculateRSI,
  type IndicatorPoint,
} from '@/lib/technicalIndicators'

interface IndicatorConfig {
  ema9: boolean
  ema21: boolean
  supertrend: boolean
  vwap: boolean
  rsi: boolean
}

interface IndicatorResults {
  ema9: IndicatorPoint[]
  ema21: IndicatorPoint[]
  supertrendLine: IndicatorPoint[]
  vwap: IndicatorPoint[]
  rsi: IndicatorPoint[]
}

const DEFAULT_CONFIG: IndicatorConfig = {
  ema9: true,
  ema21: true,
  supertrend: true,
  vwap: true,
  rsi: false,
}

/**
 * Computes technical indicators from candle data.
 * Memoized - only recomputes when candles change.
 */
export function useTechnicalIndicators(
  candles: Candle[],
  config: Partial<IndicatorConfig> = {}
): IndicatorResults {
  const cfg = { ...DEFAULT_CONFIG, ...config }

  return useMemo(() => {
    const closes: IndicatorPoint[] = candles.map((c) => ({
      time: c.time as number,
      value: c.close,
    }))

    return {
      ema9: cfg.ema9 ? calculateEMA(closes, 9) : [],
      ema21: cfg.ema21 ? calculateEMA(closes, 21) : [],
      supertrendLine: cfg.supertrend ? calculateSupertrend(candles, 10, 3).trend : [],
      vwap: cfg.vwap ? calculateVWAP(candles) : [],
      rsi: cfg.rsi ? calculateRSI(closes, 14) : [],
    }
  }, [candles, cfg.ema9, cfg.ema21, cfg.supertrend, cfg.vwap, cfg.rsi])
}
