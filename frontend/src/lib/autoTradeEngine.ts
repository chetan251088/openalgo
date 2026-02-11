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

export interface DecisionCheck {
  id: string
  label: string
  pass: boolean
  value?: string
}

export interface EntryDecision {
  enter: boolean
  reason: string
  score: number
  minScore: number
  checks: DecisionCheck[]
  blockedBy?: string
  spread: number
  depthRatio: number | null
  expectedSlippage: number
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
    requestedLots?: number
    sideOpen?: boolean
    lastExitAtForSide?: number
    reEntryCountForSide?: number
    lastTradePnl?: number | null
    killSwitch?: boolean
  },
  momentum: MomentumResult,
  indicators: IndicatorSnapshot,
  indexBias: { score: number; direction: string },
  optionsContext: OptionsContext | null,
  sensitivity: number,
  spread: number,
  depthInfo?: { totalBid: number; totalAsk: number },
  recentPrices: number[] = []
): EntryDecision {
  let score = 0
  const reasons: string[] = []
  const now = Date.now()
  const checks: DecisionCheck[] = []
  const depthRatio =
    depthInfo && depthInfo.totalBid > 0 && depthInfo.totalAsk > 0
      ? side === 'CE'
        ? depthInfo.totalBid / depthInfo.totalAsk
        : depthInfo.totalAsk / depthInfo.totalBid
      : null

  const effectiveSensitivity = config.respectHotZones ? sensitivity : 1
  const sensitivityFactor = Math.max(
    0.25,
    effectiveSensitivity * Math.max(0.1, config.sensitivityMultiplier)
  )
  // Keep score-gate realistic for current scoring weights (max practical score ~= 8).
  const adjustedMinScore = Math.min(8, Math.max(1, config.entryMinScore / sensitivityFactor))

  const addCheck = (id: string, label: string, pass: boolean, value?: string) => {
    checks.push({ id, label, pass, value })
  }

  const block = (reason: string, blockedBy: string): EntryDecision => ({
    enter: false,
    reason,
    score,
    minScore: Number(adjustedMinScore.toFixed(2)),
    checks,
    blockedBy,
    spread,
    depthRatio,
    expectedSlippage: Math.max(0, spread / 2),
  })

  // Risk checks
  const withinDailyTrades = runtime.tradesCount < config.maxTradesPerDay
  addCheck('daily-trades', 'Daily trade cap', withinDailyTrades, `${runtime.tradesCount}/${config.maxTradesPerDay}`)
  if (!withinDailyTrades) {
    return block('Max daily trades reached', 'daily-trades')
  }

  const withinDailyLoss = !(runtime.realizedPnl < 0 && Math.abs(runtime.realizedPnl) >= config.maxDailyLoss)
  addCheck('daily-loss', 'Daily loss limit', withinDailyLoss, `${runtime.realizedPnl.toFixed(0)}/${-config.maxDailyLoss}`)
  if (!withinDailyLoss) {
    return block('Daily loss limit reached', 'daily-loss')
  }

  const withinConsecutiveLosses = runtime.consecutiveLosses < config.coolingOffAfterLosses
  addCheck('cooling-off', 'Consecutive-loss cooling off', withinConsecutiveLosses, `${runtime.consecutiveLosses}/${config.coolingOffAfterLosses}`)
  if (!withinConsecutiveLosses) {
    return block(`Cooling off (${runtime.consecutiveLosses} losses)`, 'cooling-off')
  }

  const killSwitchOff = !runtime.killSwitch
  addCheck('kill-switch', 'Kill switch', killSwitchOff)
  if (!killSwitchOff) {
    return block('Kill switch active', 'kill-switch')
  }

  const hotZoneAllowed = !config.respectHotZones || effectiveSensitivity > 0
  addCheck(
    'hot-zone',
    'Market hot-zone timing',
    hotZoneAllowed,
    config.respectHotZones ? effectiveSensitivity.toFixed(2) : 'OFF'
  )
  if (!hotZoneAllowed) {
    return block('Outside configured market hot-zone timing', 'hot-zone')
  }

  const noOpenPositionOnSide = !runtime.sideOpen
  addCheck('side-open', 'No open position on side', noOpenPositionOnSide)
  if (!noOpenPositionOnSide) {
    return block(`Position already open on ${side}`, 'side-open')
  }

  const reEntryCountForSide = runtime.reEntryCountForSide ?? 0
  const lastExitAtForSide = runtime.lastExitAtForSide ?? 0
  if (!config.reEntryEnabled && reEntryCountForSide > 0) {
    addCheck('reentry-enabled', 'Re-entry enabled', false, 'OFF')
    return block(`Re-entry disabled for ${side}`, 'reentry-enabled')
  }

  addCheck('reentry-count', 'Re-entry cap', config.reEntryMaxPerSide <= 0 || reEntryCountForSide < config.reEntryMaxPerSide, `${reEntryCountForSide}/${config.reEntryMaxPerSide}`)
  if (config.reEntryEnabled && config.reEntryMaxPerSide > 0 && reEntryCountForSide >= config.reEntryMaxPerSide) {
    return block(`Re-entry cap reached for ${side}`, 'reentry-count')
  }

  if (config.reEntryEnabled && config.reEntryDelaySec > 0 && lastExitAtForSide > 0) {
    const elapsed = now - lastExitAtForSide
    const delayMs = config.reEntryDelaySec * 1000
    const pass = elapsed >= delayMs
    addCheck('reentry-delay', 'Re-entry delay', pass, pass ? `${config.reEntryDelaySec}s` : `${Math.ceil((delayMs - elapsed) / 1000)}s left`)
    if (!pass) {
      return block(`Re-entry delay active: ${Math.ceil((delayMs - elapsed) / 1000)}s`, 'reentry-delay')
    }
  }

  if (config.maxPositionSize > 0 && (runtime.requestedLots ?? 0) > config.maxPositionSize) {
    addCheck('max-position-size', 'Max position size', false, `${runtime.requestedLots}/${config.maxPositionSize}`)
    return block(`Requested lots ${runtime.requestedLots} exceeds max ${config.maxPositionSize}`, 'max-position-size')
  }
  addCheck('max-position-size', 'Max position size', true, `${runtime.requestedLots ?? 0}/${config.maxPositionSize}`)

  if (config.perTradeMaxLoss > 0 && runtime.lastTradePnl != null) {
    const pass = runtime.lastTradePnl > -config.perTradeMaxLoss
    addCheck('per-trade-loss', 'Per-trade max loss', pass, `${runtime.lastTradePnl.toFixed(0)}/${-config.perTradeMaxLoss}`)
    if (!pass) {
      return block('Last trade breached per-trade max loss', 'per-trade-loss')
    }
  } else {
    addCheck('per-trade-loss', 'Per-trade max loss', true)
  }

  // Min gap between trades
  if (config.minGapMs > 0 && runtime.lastTradeTime > 0) {
    const elapsed = now - runtime.lastTradeTime
    const pass = elapsed >= config.minGapMs
    addCheck('min-gap', 'Minimum gap between entries', pass, pass ? `${elapsed}ms` : `${config.minGapMs - elapsed}ms left`)
    if (elapsed < config.minGapMs) {
      return block(`Min gap: ${Math.ceil((config.minGapMs - elapsed) / 1000)}s remaining`, 'min-gap')
    }
  } else {
    addCheck('min-gap', 'Minimum gap between entries', true)
  }

  // Max trades per minute
  addCheck('per-minute-rate', 'Per-minute trade rate', config.maxTradesPerMinute <= 0 || runtime.tradesThisMinute < config.maxTradesPerMinute, `${runtime.tradesThisMinute}/${config.maxTradesPerMinute}`)
  if (config.maxTradesPerMinute > 0 && runtime.tradesThisMinute >= config.maxTradesPerMinute) {
    return block(`Rate limit: ${runtime.tradesThisMinute}/${config.maxTradesPerMinute} trades/min`, 'per-minute-rate')
  }

  // Cooldown after loss
  if (config.cooldownAfterLossSec > 0 && runtime.lastLossTime > 0) {
    const elapsed = now - runtime.lastLossTime
    const cooldownMs = config.cooldownAfterLossSec * 1000
    const pass = elapsed >= cooldownMs
    addCheck('loss-cooldown', 'Cooldown after loss', pass, pass ? `${config.cooldownAfterLossSec}s` : `${Math.ceil((cooldownMs - elapsed) / 1000)}s left`)
    if (elapsed < cooldownMs) {
      return block(`Cooldown: ${Math.ceil((cooldownMs - elapsed) / 1000)}s after loss`, 'loss-cooldown')
    }
  } else {
    addCheck('loss-cooldown', 'Cooldown after loss', true)
  }

  // Imbalance filter (depth-based)
  if (depthInfo) {
    const imbalance = checkImbalanceFilter(depthInfo.totalBid, depthInfo.totalAsk, side, config)
    addCheck('depth-imbalance', 'Depth imbalance', imbalance.allowed, imbalance.reason)
    if (!imbalance.allowed) {
      return block(imbalance.reason, 'depth-imbalance')
    }
  } else {
    addCheck('depth-imbalance', 'Depth imbalance', true, 'No depth data')
  }

  // Spread filter
  addCheck('spread', 'Spread gate', spread <= config.entryMaxSpread, `${spread.toFixed(2)}/${config.entryMaxSpread}`)
  if (spread > config.entryMaxSpread) {
    return block(`Spread too wide: ${spread.toFixed(1)}`, 'spread')
  }

  const inNoTradeZone =
    config.noTradeZoneEnabled &&
    isNoTradeZone(recentPrices, config.noTradeZoneRangePts, config.noTradeZonePeriod)
  addCheck(
    'no-trade-zone',
    'No-trade zone filter',
    !inNoTradeZone,
    config.noTradeZoneEnabled
      ? `${config.noTradeZonePeriod} bars/${config.noTradeZoneRangePts}pts`
      : 'OFF'
  )
  if (
    inNoTradeZone
  ) {
    return block(`No-trade zone (${config.noTradeZonePeriod} candles in ${config.noTradeZoneRangePts} pts range)`, 'no-trade-zone')
  }

  // Momentum score
  const expectedDir = side === 'CE' ? 'up' : 'down'
  addCheck('momentum-direction', 'Momentum alignment', momentum.direction === expectedDir, `${momentum.direction} x${momentum.count}`)
  if (momentum.direction === expectedDir && momentum.count >= config.entryMomentumCount) {
    score += 3
    reasons.push(`Momentum ${momentum.direction} x${momentum.count}`)
  } else if (momentum.direction === expectedDir) {
    score += 1
    reasons.push(`Weak momentum ${momentum.direction}`)
  }

  // Velocity
  addCheck('velocity', 'Velocity threshold', momentum.velocity >= config.entryMomentumVelocity, `${momentum.velocity.toFixed(2)}/${config.entryMomentumVelocity}`)
  if (momentum.velocity >= config.entryMomentumVelocity) {
    score += 2
    reasons.push(`Velocity ${momentum.velocity.toFixed(1)}`)
  }

  // Indicator alignment
  let emaAligned = false
  if (indicators.ema9 !== null && indicators.ema21 !== null) {
    emaAligned =
      (side === 'CE' && indicators.ema9 > indicators.ema21) ||
      (side === 'PE' && indicators.ema9 < indicators.ema21)
    if (emaAligned) {
      score += 1
      reasons.push('EMA aligned')
    }
  }
  addCheck('ema', 'EMA alignment', emaAligned, indicators.ema9 != null && indicators.ema21 != null ? `${indicators.ema9.toFixed(2)}/${indicators.ema21.toFixed(2)}` : 'NA')

  let rsiAligned = false
  if (indicators.rsi !== null) {
    rsiAligned =
      (side === 'CE' && indicators.rsi > 50 && indicators.rsi < 80) ||
      (side === 'PE' && indicators.rsi < 50 && indicators.rsi > 20)
    if (rsiAligned) {
      score += 1
      reasons.push(`RSI ${indicators.rsi.toFixed(0)}`)
    }
  }
  addCheck('rsi', 'RSI alignment', rsiAligned, indicators.rsi != null ? indicators.rsi.toFixed(1) : 'NA')

  // Index bias
  let biasAligned = false
  if (config.indexBiasEnabled) {
    biasAligned =
      (side === 'CE' && indexBias.direction === 'bullish') ||
      (side === 'PE' && indexBias.direction === 'bearish')
    if (biasAligned) {
      score += 1
      reasons.push(`Index ${indexBias.direction}`)
    }
  }
  addCheck('index-bias', 'Index bias alignment', config.indexBiasEnabled ? biasAligned : true, config.indexBiasEnabled ? `${indexBias.direction}` : 'OFF')

  // Options context filter
  const ctxFilter = optionsContextFilter(side, optionsContext, config)
  addCheck('options-context', 'Options context filter', ctxFilter.allowed, ctxFilter.reason)
  if (!ctxFilter.allowed) {
    return block(ctxFilter.reason, 'options-context')
  }

  addCheck('score-gate', 'Final score gate', score >= adjustedMinScore, `${score.toFixed(1)}/${adjustedMinScore.toFixed(1)}`)

  return {
    enter: score >= adjustedMinScore,
    reason: reasons.join(', ') || 'No strong signals',
    score,
    minScore: Number(adjustedMinScore.toFixed(2)),
    checks,
    spread,
    depthRatio,
    expectedSlippage: Math.max(0, spread / 2),
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
    const breakevenTrigger =
      config.breakevenTriggerPts > 0 ? config.breakevenTriggerPts : config.trailBreakevenTrigger
    if (profitPts >= breakevenTrigger) {
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
