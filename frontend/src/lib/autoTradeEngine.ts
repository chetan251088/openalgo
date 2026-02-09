import type { ActiveSide, MarketRegime, OptionsContext, GhostSignal, TrailingStage } from '@/types/scalping'
import type { AutoTradeConfigFields } from './scalpingPresets'

/**
 * Pure functions for auto-trade decision making.
 * No React, no side effects - just inputs in, decisions out.
 */

interface IndicatorSnapshot {
  ema9: number | null
  ema21: number | null
  supertrend: number | null
  rsi: number | null
  vwap: number | null
}

interface MomentumResult {
  direction: 'up' | 'down' | 'flat'
  count: number
  velocity: number
}

interface EntryDecision {
  enter: boolean
  reason: string
  score: number
}

interface ExitDecision {
  exit: boolean
  reason: string
}

interface TrailResult {
  newSL: number
  newStage: TrailingStage
}

// --- Momentum ---

export function calculateMomentum(
  ticks: number[],
  config: Pick<AutoTradeConfigFields, 'entryMomentumCount' | 'entryMomentumVelocity'>
): MomentumResult {
  if (ticks.length < 2) return { direction: 'flat', count: 0, velocity: 0 }

  const recent = ticks.slice(-Math.max(config.entryMomentumCount, 2))
  let upCount = 0
  let downCount = 0

  for (let i = 1; i < recent.length; i++) {
    if (recent[i] > recent[i - 1]) upCount++
    else if (recent[i] < recent[i - 1]) downCount++
  }

  const direction = upCount > downCount ? 'up' : downCount > upCount ? 'down' : 'flat'
  const velocity = recent.length >= 2 ? Math.abs(recent[recent.length - 1] - recent[0]) : 0

  return {
    direction,
    count: Math.max(upCount, downCount),
    velocity,
  }
}

// --- Regime Detection ---

export function detectRegime(
  candles: Array<{ high: number; low: number; close: number }>,
  config: Pick<AutoTradeConfigFields, 'regimeDetectionPeriod' | 'rangingThresholdPts'>
): MarketRegime {
  if (candles.length < config.regimeDetectionPeriod) return 'UNKNOWN'

  const recent = candles.slice(-config.regimeDetectionPeriod)
  const highs = recent.map((c) => c.high)
  const lows = recent.map((c) => c.low)
  const range = Math.max(...highs) - Math.min(...lows)

  // Calculate directional movement
  const closes = recent.map((c) => c.close)
  const netMove = Math.abs(closes[closes.length - 1] - closes[0])
  const avgBarRange =
    recent.reduce((s, c) => s + (c.high - c.low), 0) / recent.length

  if (range < config.rangingThresholdPts) return 'RANGING'
  if (netMove > range * 0.6) return 'TRENDING'
  if (avgBarRange > range * 0.15) return 'VOLATILE'
  return 'RANGING'
}

// --- No-Trade Zone ---

export function isNoTradeZone(
  prices: number[],
  rangePts: number,
  period: number
): boolean {
  if (prices.length < period) return false
  const recent = prices.slice(-period)
  const range = Math.max(...recent) - Math.min(...recent)
  return range < rangePts
}

// --- Index Bias ---

export function calculateIndexBias(
  indexIndicators: IndicatorSnapshot,
  config: Pick<AutoTradeConfigFields, 'indexBiasWeight'>
): { score: number; direction: 'bullish' | 'bearish' | 'neutral' } {
  let score = 0

  if (indexIndicators.ema9 !== null && indexIndicators.ema21 !== null) {
    if (indexIndicators.ema9 > indexIndicators.ema21) score += 1
    else score -= 1
  }

  if (indexIndicators.rsi !== null) {
    if (indexIndicators.rsi > 60) score += 1
    else if (indexIndicators.rsi < 40) score -= 1
  }

  if (indexIndicators.supertrend !== null && indexIndicators.ema9 !== null) {
    if (indexIndicators.ema9 > indexIndicators.supertrend) score += 1
    else score -= 1
  }

  const weightedScore = score * config.indexBiasWeight
  const direction = weightedScore > 0.3 ? 'bullish' : weightedScore < -0.3 ? 'bearish' : 'neutral'

  return { score: weightedScore, direction }
}

// --- Options Context Filters ---

export function optionsContextFilter(
  side: ActiveSide,
  context: OptionsContext | null,
  config: Pick<
    AutoTradeConfigFields,
    'optionsContextEnabled' | 'pcrBullishThreshold' | 'pcrBearishThreshold' | 'maxPainProximityFilter' | 'gexWallFilterEnabled'
  >
): { allowed: boolean; reason: string } {
  if (!config.optionsContextEnabled || !context) {
    return { allowed: true, reason: 'Context disabled' }
  }

  // PCR filter
  if (side === 'CE' && context.pcr > config.pcrBearishThreshold) {
    return { allowed: false, reason: `PCR ${context.pcr.toFixed(2)} too high for CE buy` }
  }
  if (side === 'PE' && context.pcr < config.pcrBullishThreshold) {
    return { allowed: false, reason: `PCR ${context.pcr.toFixed(2)} too low for PE buy` }
  }

  // Max pain proximity
  if (Math.abs(context.spotVsMaxPain) < config.maxPainProximityFilter) {
    return { allowed: false, reason: `Too close to max pain (${context.spotVsMaxPain.toFixed(0)} pts)` }
  }

  // GEX wall filter
  if (config.gexWallFilterEnabled && context.topGammaStrikes.length > 0) {
    // If net GEX is very positive, mean-reversion expected - avoid directional bets
    if (context.netGEX > 100) {
      return { allowed: false, reason: `High positive GEX (${context.netGEX.toFixed(0)}) - mean reversion likely` }
    }
  }

  return { allowed: true, reason: 'Context OK' }
}

// --- Context-Aware Trail Distance ---

export function getContextAwareTrailDistance(
  baseDistance: number,
  context: OptionsContext | null,
  config: Pick<AutoTradeConfigFields, 'optionsContextEnabled'>
): number {
  if (!config.optionsContextEnabled || !context) return baseDistance

  let adjusted = baseDistance

  // Widen SL when ATM IV is high
  if (context.atmIV > 20) {
    adjusted *= 1 + (context.atmIV - 20) * 0.02 // +2% per IV point above 20
  }

  // Tighten near max pain (convergence)
  if (Math.abs(context.spotVsMaxPain) < 100) {
    adjusted *= 0.85
  }

  return Math.max(2, Math.round(adjusted * 10) / 10)
}

// --- Options Early Exit ---

export function optionsEarlyExitCheck(
  side: ActiveSide,
  context: OptionsContext | null,
  config: Pick<AutoTradeConfigFields, 'optionsContextEnabled' | 'ivSpikeExitEnabled' | 'ivSpikeThreshold' | 'pcrBullishThreshold' | 'pcrBearishThreshold'>
): ExitDecision {
  if (!config.optionsContextEnabled || !context) {
    return { exit: false, reason: '' }
  }

  // IV spike exit
  if (config.ivSpikeExitEnabled) {
    const ivChange = side === 'CE' ? context.ceIV : context.peIV
    if (ivChange > config.ivSpikeThreshold) {
      return { exit: true, reason: `IV spike: ${ivChange.toFixed(1)}% (threshold: ${config.ivSpikeThreshold}%)` }
    }
  }

  // PCR flip against position
  if (side === 'CE' && context.pcr > config.pcrBearishThreshold) {
    return { exit: true, reason: `PCR flipped bearish: ${context.pcr.toFixed(2)}` }
  }
  if (side === 'PE' && context.pcr < config.pcrBullishThreshold) {
    return { exit: true, reason: `PCR flipped bullish: ${context.pcr.toFixed(2)}` }
  }

  return { exit: false, reason: '' }
}

// --- Imbalance Filter ---

export function checkImbalanceFilter(
  bidDepth: number,
  askDepth: number,
  side: ActiveSide,
  config: Pick<AutoTradeConfigFields, 'imbalanceFilterEnabled' | 'imbalanceThreshold'>
): { allowed: boolean; reason: string } {
  if (!config.imbalanceFilterEnabled) return { allowed: true, reason: 'Imbalance filter off' }
  if (bidDepth <= 0 || askDepth <= 0) return { allowed: true, reason: 'No depth data' }

  const ratio = side === 'CE' ? bidDepth / askDepth : askDepth / bidDepth
  if (ratio < config.imbalanceThreshold) {
    return {
      allowed: false,
      reason: `Depth imbalance ${ratio.toFixed(2)} < ${config.imbalanceThreshold} (${side === 'CE' ? 'weak bid' : 'weak ask'})`,
    }
  }
  return { allowed: true, reason: 'Depth OK' }
}

// --- Entry Decision ---

export function shouldEnterTrade(
  side: ActiveSide,
  _ltp: number,
  config: AutoTradeConfigFields,
  runtime: {
    consecutiveLosses: number
    tradesCount: number
    realizedPnl: number
    lastTradeTime: number
    tradesThisMinute: number
    lastLossTime: number
  },
  momentum: MomentumResult,
  indicators: IndicatorSnapshot,
  indexBias: { score: number; direction: string },
  optionsContext: OptionsContext | null,
  sensitivity: number,
  spread: number,
  depthInfo?: { totalBid: number; totalAsk: number }
): EntryDecision {
  let score = 0
  const reasons: string[] = []
  const now = Date.now()

  // Risk checks
  if (runtime.tradesCount >= config.maxTradesPerDay) {
    return { enter: false, reason: 'Max daily trades reached', score: 0 }
  }
  if (runtime.realizedPnl < 0 && Math.abs(runtime.realizedPnl) >= config.maxDailyLoss) {
    return { enter: false, reason: 'Daily loss limit reached', score: 0 }
  }
  if (runtime.consecutiveLosses >= config.coolingOffAfterLosses) {
    return { enter: false, reason: `Cooling off (${runtime.consecutiveLosses} losses)`, score: 0 }
  }

  // Per-trade max loss check (skip entry if last trade hit per-trade max loss)
  // This acts as a circuit breaker - consecutive large losses = step back
  if (config.perTradeMaxLoss > 0 && runtime.consecutiveLosses > 0) {
    // Already handled by coolingOffAfterLosses, but perTradeMaxLoss informs exit side
  }

  // Min gap between trades
  if (config.minGapMs > 0 && runtime.lastTradeTime > 0) {
    const elapsed = now - runtime.lastTradeTime
    if (elapsed < config.minGapMs) {
      return { enter: false, reason: `Min gap: ${Math.ceil((config.minGapMs - elapsed) / 1000)}s remaining`, score: 0 }
    }
  }

  // Max trades per minute
  if (config.maxTradesPerMinute > 0 && runtime.tradesThisMinute >= config.maxTradesPerMinute) {
    return { enter: false, reason: `Rate limit: ${runtime.tradesThisMinute}/${config.maxTradesPerMinute} trades/min`, score: 0 }
  }

  // Cooldown after loss
  if (config.cooldownAfterLossSec > 0 && runtime.lastLossTime > 0) {
    const elapsed = now - runtime.lastLossTime
    const cooldownMs = config.cooldownAfterLossSec * 1000
    if (elapsed < cooldownMs) {
      return { enter: false, reason: `Cooldown: ${Math.ceil((cooldownMs - elapsed) / 1000)}s after loss`, score: 0 }
    }
  }

  // Imbalance filter (depth-based)
  if (depthInfo) {
    const imbalance = checkImbalanceFilter(depthInfo.totalBid, depthInfo.totalAsk, side, config)
    if (!imbalance.allowed) {
      return { enter: false, reason: imbalance.reason, score: 0 }
    }
  }

  // Spread filter
  if (spread > config.entryMaxSpread) {
    return { enter: false, reason: `Spread too wide: ${spread.toFixed(1)}`, score: 0 }
  }

  // Momentum score
  const expectedDir = side === 'CE' ? 'up' : 'down'
  if (momentum.direction === expectedDir && momentum.count >= config.entryMomentumCount) {
    score += 3
    reasons.push(`Momentum ${momentum.direction} x${momentum.count}`)
  } else if (momentum.direction === expectedDir) {
    score += 1
    reasons.push(`Weak momentum ${momentum.direction}`)
  }

  // Velocity
  if (momentum.velocity >= config.entryMomentumVelocity) {
    score += 2
    reasons.push(`Velocity ${momentum.velocity.toFixed(1)}`)
  }

  // Indicator alignment
  if (indicators.ema9 !== null && indicators.ema21 !== null) {
    const emaAligned =
      (side === 'CE' && indicators.ema9 > indicators.ema21) ||
      (side === 'PE' && indicators.ema9 < indicators.ema21)
    if (emaAligned) {
      score += 1
      reasons.push('EMA aligned')
    }
  }

  if (indicators.rsi !== null) {
    const rsiAligned =
      (side === 'CE' && indicators.rsi > 50 && indicators.rsi < 80) ||
      (side === 'PE' && indicators.rsi < 50 && indicators.rsi > 20)
    if (rsiAligned) {
      score += 1
      reasons.push(`RSI ${indicators.rsi.toFixed(0)}`)
    }
  }

  // Index bias
  if (config.indexBiasEnabled) {
    const biasAligned =
      (side === 'CE' && indexBias.direction === 'bullish') ||
      (side === 'PE' && indexBias.direction === 'bearish')
    if (biasAligned) {
      score += 1
      reasons.push(`Index ${indexBias.direction}`)
    }
  }

  // Options context filter
  const ctxFilter = optionsContextFilter(side, optionsContext, config)
  if (!ctxFilter.allowed) {
    return { enter: false, reason: ctxFilter.reason, score }
  }

  // Apply sensitivity multiplier from market clock
  const adjustedMinScore = config.entryMinScore / (sensitivity * config.sensitivityMultiplier)

  return {
    enter: score >= adjustedMinScore,
    reason: reasons.join(', ') || 'No strong signals',
    score,
  }
}

// --- Trailing Stop ---

export function calculateTrailingStop(
  currentStage: TrailingStage,
  entry: number,
  current: number,
  highSinceEntry: number,
  isBuy: boolean,
  config: AutoTradeConfigFields,
  optionsContext: OptionsContext | null
): TrailResult {
  const profitPts = isBuy ? current - entry : entry - current
  const highProfitPts = isBuy ? highSinceEntry - entry : entry - highSinceEntry
  const contextTrailBase = getContextAwareTrailDistance(config.trailInitialSL, optionsContext, config)

  // Stage progression
  if (currentStage === 'INITIAL') {
    if (profitPts >= config.trailBreakevenTrigger) {
      const sl = entry + (isBuy ? config.breakevenBuffer : -config.breakevenBuffer)
      return { newSL: sl, newStage: 'BREAKEVEN' }
    }
    const sl = isBuy ? entry - contextTrailBase : entry + contextTrailBase
    return { newSL: sl, newStage: 'INITIAL' }
  }

  if (currentStage === 'BREAKEVEN') {
    if (highProfitPts >= config.trailLockTrigger) {
      const sl = isBuy
        ? entry + config.trailLockAmount
        : entry - config.trailLockAmount
      return { newSL: sl, newStage: 'LOCK' }
    }
    const sl = entry + (isBuy ? config.breakevenBuffer : -config.breakevenBuffer)
    return { newSL: sl, newStage: 'BREAKEVEN' }
  }

  if (currentStage === 'LOCK') {
    if (highProfitPts >= config.trailStartTrigger) {
      const sl = isBuy
        ? highSinceEntry - config.trailStepSize
        : highSinceEntry + config.trailStepSize // highSinceEntry = low for sells
      return { newSL: sl, newStage: 'TRAIL' }
    }
    const sl = isBuy
      ? entry + config.trailLockAmount
      : entry - config.trailLockAmount
    return { newSL: sl, newStage: 'LOCK' }
  }

  if (currentStage === 'TRAIL') {
    if (highProfitPts >= config.trailTightTrigger) {
      const sl = isBuy
        ? highSinceEntry - config.trailTightStep
        : highSinceEntry + config.trailTightStep
      return { newSL: sl, newStage: 'TIGHT' }
    }
    const sl = isBuy
      ? highSinceEntry - config.trailStepSize
      : highSinceEntry + config.trailStepSize
    return { newSL: sl, newStage: 'TRAIL' }
  }

  // TIGHT or ACCELERATED
  const sl = isBuy
    ? highSinceEntry - config.trailTightStep
    : highSinceEntry + config.trailTightStep
  return { newSL: sl, newStage: 'TIGHT' }
}

// --- Ghost Signal Generation ---

export function generateGhostSignal(
  side: ActiveSide,
  symbol: string,
  strike: number,
  _ltp: number,
  entryDecision: EntryDecision,
  regime: MarketRegime,
  pcr: number | undefined
): GhostSignal | null {
  if (!entryDecision.enter || entryDecision.score < 4) return null

  return {
    id: `ghost-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    timestamp: Date.now(),
    side,
    action: side === 'CE' ? 'BUY' : 'BUY',
    symbol,
    strike,
    score: entryDecision.score,
    reason: entryDecision.reason,
    regime,
    pcr,
  }
}
