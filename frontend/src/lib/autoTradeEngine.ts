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
  macdLine?: number | null
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

interface VolumeSignalSnapshot {
  indexDelta: number | null
  indexDeltaRatio: number | null
  optionDelta: number | null
  optionDeltaRatio: number | null
  oppositeDelta: number | null
  sideDominanceRatio: number | null
}

export type FlowBiasAction = 'BUY_CE' | 'BUY_PE' | 'HOLD'
export type UnifiedSignalConfidence = 'LOW' | 'MED' | 'HIGH'

export interface UnifiedFlowSignal {
  action: FlowBiasAction
  label: string
  confidence: UnifiedSignalConfidence
  score: number
  components: {
    flow: number
    footprints: number
    context: number
  }
}

// --- Momentum ---

export function calculateMomentum(
  ticks: number[],
  config: Pick<AutoTradeConfigFields, 'entryMomentumCount' | 'entryMomentumVelocity'>
): MomentumResult {
  if (ticks.length < 2) return { direction: 'flat', count: 0, velocity: 0 }

  // +1 so we get entryMomentumCount comparisons (N ticks → N-1 diffs).
  // Without this, count can only reach entryMomentumCount-1, making the
  // "count >= entryMomentumCount" strong-momentum check always false.
  const recent = ticks.slice(-Math.max(config.entryMomentumCount + 1, 2))
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

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function calculateFootprintBias(
  footprints: Array<{ buy: number; sell: number; delta: number; total: number }>
): number {
  if (!footprints.length) return 0

  let weighted = 0
  let weightSum = 0
  for (let i = 0; i < footprints.length; i += 1) {
    const row = footprints[i]
    const weight = 0.55 + (i / Math.max(1, footprints.length - 1)) * 0.45
    const rowScore = clamp(row.delta / Math.max(1, row.total), -1, 1)
    weighted += rowScore * weight
    weightSum += weight
  }
  if (weightSum <= 0) return 0
  return clamp((weighted / weightSum) * 1.1, -1, 1)
}

function calculateContextBias(context: OptionsContext | null): number {
  if (!context) return 0

  const pcrCentered = clamp((1 - context.pcr) * 1.5, -1, 1)
  const oiTotal = Math.abs(context.oiChangeCE) + Math.abs(context.oiChangePE)
  const oiBias =
    oiTotal > 0 ? clamp((context.oiChangePE - context.oiChangeCE) / oiTotal, -1, 1) : 0
  const maxPainBias = clamp((-context.spotVsMaxPain) / 180, -0.55, 0.55)

  let combined = pcrCentered * 0.5 + oiBias * 0.35 + maxPainBias * 0.15
  const gexDamp =
    Math.abs(context.netGEX) >= 1_000_000 ? 0.75 : Math.abs(context.netGEX) >= 300_000 ? 0.85 : 1
  combined *= gexDamp

  return clamp(combined, -1, 1)
}

export function calculateUnifiedFlowSignal(
  side: ActiveSide,
  flow: { buyFlow: number; sellFlow: number; delta: number; cumulativeDelta: number },
  footprints: Array<{ buy: number; sell: number; delta: number; total: number }>,
  context: OptionsContext | null
): UnifiedFlowSignal {
  const totalFlow = Math.max(0, flow.buyFlow + flow.sellFlow)
  let flowScore = totalFlow > 0 ? clamp((flow.delta / Math.max(1, totalFlow)) * 1.35, -1, 1) : 0
  const cumulativeAligned =
    flow.delta !== 0 &&
    flow.cumulativeDelta !== 0 &&
    Math.sign(flow.delta) === Math.sign(flow.cumulativeDelta)
  flowScore = clamp(flowScore * (cumulativeAligned ? 1.05 : 0.85), -1, 1)

  const sideDirection = side === 'CE' ? 1 : -1
  flowScore *= sideDirection
  const footprintScore = calculateFootprintBias(footprints) * sideDirection
  const contextScore = calculateContextBias(context)
  const combined = clamp(flowScore * 0.45 + footprintScore * 0.3 + contextScore * 0.25, -1, 1)
  const magnitude = Math.abs(combined)

  let action: FlowBiasAction = 'HOLD'
  if (magnitude >= 0.16) {
    action = combined > 0 ? 'BUY_CE' : 'BUY_PE'
  }

  const confidence: UnifiedSignalConfidence =
    magnitude >= 0.56 ? 'HIGH' : magnitude >= 0.34 ? 'MED' : 'LOW'
  const label =
    action === 'BUY_CE'
      ? `BUY CE (${confidence})`
      : action === 'BUY_PE'
        ? `BUY PE (${confidence})`
        : `HOLD (${confidence})`

  return {
    action,
    label,
    confidence,
    score: combined,
    components: {
      flow: flowScore,
      footprints: footprintScore,
      context: contextScore,
    },
  }
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
    openSideLots?: number
    allowEntryWithOpenSide?: boolean
    lastExitAtForSide?: number
    reEntryCountForSide?: number
    lastTradePnl?: number | null
    killSwitch?: boolean
    lockProfitTriggered?: boolean
  },
  momentum: MomentumResult,
  indicators: IndicatorSnapshot,
  indexBias: { score: number; direction: string },
  optionsContext: OptionsContext | null,
  sensitivity: number,
  spread: number,
  depthInfo?: { totalBid: number; totalAsk: number },
  recentPrices: number[] = [],
  volumeFlow?: VolumeSignalSnapshot,
  regime?: MarketRegime,
  unifiedSignal?: UnifiedFlowSignal | null
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
  // Regime penalty raises the score bar in flat/unknown markets.
  // Capped at 0.75 for RANGING because the regime detector runs on raw LTP
  // ticks (not real candles), causing TRENDING/VOLATILE to rarely fire and
  // RANGING to be the near-constant default — a 1.5 penalty would
  // systematically over-block entries.  UNKNOWN gets 0.25 (insufficient data).
  const regimePenalty =
    regime === 'RANGING' ? 0.75 :
    regime === 'UNKNOWN' ? 0.25 :
    0
  // Keep score-gate realistic for current scoring weights.
  const adjustedMinScore = Math.min(10, Math.max(1, config.entryMinScore / sensitivityFactor) + regimePenalty)

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

  const lockProfitFree = !runtime.lockProfitTriggered
  addCheck('lock-profit', 'Lock-profit protection', lockProfitFree)
  if (!lockProfitFree) {
    return block('Lock-profit triggered: new entries blocked', 'lock-profit')
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

  const openSideLots = Math.max(0, runtime.openSideLots ?? 0)
  const canEnterWithOpenSide = runtime.allowEntryWithOpenSide === true
  const noOpenPositionOnSide = !runtime.sideOpen || canEnterWithOpenSide
  addCheck(
    'side-open',
    'No open position on side',
    noOpenPositionOnSide,
    runtime.sideOpen
      ? canEnterWithOpenSide
        ? `Pyramid allowed (${openSideLots.toFixed(1)} lots open)`
        : `${openSideLots.toFixed(1)} lots open`
      : '0 lots'
  )
  if (!noOpenPositionOnSide) {
    return block(`Position already open on ${side}`, 'side-open')
  }

  const reEntryCountForSide = runtime.reEntryCountForSide ?? 0
  const lastExitAtForSide = runtime.lastExitAtForSide ?? 0
  const reEntryEnabled = config.reEntryEnabled === true
  addCheck('reentry-setting', 'Re-entry', true, reEntryEnabled ? 'ON' : 'OFF (first entry only)')

  if (!reEntryEnabled) {
    const firstEntryOnlyPass = reEntryCountForSide === 0
    addCheck('reentry-first', 'First entry only', firstEntryOnlyPass, `${reEntryCountForSide}/1`)
    if (!firstEntryOnlyPass) {
      return block(`Re-entry disabled for ${side}`, 'reentry-first')
    }
  } else {
    addCheck(
      'reentry-count',
      'Re-entry cap',
      config.reEntryMaxPerSide <= 0 || reEntryCountForSide < config.reEntryMaxPerSide,
      `${reEntryCountForSide}/${config.reEntryMaxPerSide}`
    )
    if (config.reEntryMaxPerSide > 0 && reEntryCountForSide >= config.reEntryMaxPerSide) {
      return block(`Re-entry cap reached for ${side}`, 'reentry-count')
    }

    if (config.reEntryDelaySec > 0 && lastExitAtForSide > 0) {
      const elapsed = now - lastExitAtForSide
      const delayMs = config.reEntryDelaySec * 1000
      const pass = elapsed >= delayMs
      addCheck(
        'reentry-delay',
        'Re-entry delay',
        pass,
        pass ? `${config.reEntryDelaySec}s` : `${Math.ceil((delayMs - elapsed) / 1000)}s left`
      )
      if (!pass) {
        return block(`Re-entry delay active: ${Math.ceil((delayMs - elapsed) / 1000)}s`, 'reentry-delay')
      }
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

  // Volume influence (index + option-side + side-vs-opposite participation)
  if (config.volumeInfluenceEnabled) {
    const indexMinRatio = Math.max(0.1, config.indexVolumeMinRatio || 1)
    const optionMinRatio = Math.max(0.1, config.optionVolumeMinRatio || 1)
    const dominanceMinRatio = Math.max(0.1, config.sideVolumeDominanceRatio || 1)
    const volumeScoreWeight = Math.max(0, config.volumeScoreWeight || 0)

    const indexRatio = volumeFlow?.indexDeltaRatio ?? null
    const optionRatio = volumeFlow?.optionDeltaRatio ?? null
    const dominanceRatio = volumeFlow?.sideDominanceRatio ?? null
    const indexDelta = volumeFlow?.indexDelta
    const optionDelta = volumeFlow?.optionDelta
    const oppositeDelta = volumeFlow?.oppositeDelta

    const indexPass = indexRatio == null ? true : indexRatio >= indexMinRatio
    const optionPass = optionRatio == null ? true : optionRatio >= optionMinRatio
    const dominancePass = dominanceRatio == null ? true : dominanceRatio >= dominanceMinRatio

    addCheck(
      'volume-index',
      'Index volume flow',
      indexPass,
      indexRatio == null
        ? 'NA'
        : `${indexRatio.toFixed(2)}x/${indexMinRatio.toFixed(2)}x d:${(indexDelta ?? 0).toFixed(0)}`
    )
    addCheck(
      'volume-option',
      `${side} volume flow`,
      optionPass,
      optionRatio == null
        ? 'NA'
        : `${optionRatio.toFixed(2)}x/${optionMinRatio.toFixed(2)}x d:${(optionDelta ?? 0).toFixed(0)}`
    )
    addCheck(
      'volume-dominance',
      `${side} vs opposite volume`,
      dominancePass,
      dominanceRatio == null
        ? 'NA'
        : `${Math.min(dominanceRatio, 20).toFixed(2)}x/${dominanceMinRatio.toFixed(2)}x opp:${(oppositeDelta ?? 0).toFixed(0)}`
    )

    // Hard-block only on genuinely dead volume (< 50% of the configured minimum).
    // For Indian index options (NIFTY/SENSEX), NSE/BSE WebSocket sends cumulative
    // day-volume. Delta between two consecutive ticks can be 0 on quiet ticks,
    // making the ratio temporarily very low. We avoid blocking on minor shortfalls
    // and only gate out truly inactive sessions.
    // Side-dominance (CE vs PE) is used for scoring only — NIFTY always has both
    // sides heavily traded due to institutional hedging.
    const weakOptionFlow =
      optionRatio != null &&
      optionRatio < optionMinRatio * 0.5
    if (weakOptionFlow) {
      return block(`Very low ${side} volume (${optionRatio.toFixed(2)}x baseline)`, 'volume-option')
    }

    if (indexRatio != null && indexPass && volumeScoreWeight > 0) {
      score += 0.5 * volumeScoreWeight
      reasons.push(`IdxVol x${Math.min(indexRatio, 99).toFixed(2)}`)
    }
    if (optionRatio != null && optionPass && volumeScoreWeight > 0) {
      score += 1.0 * volumeScoreWeight
      reasons.push(`${side}Vol x${Math.min(optionRatio, 99).toFixed(2)}`)
    }
    // Cap the dominance display at 20x — when one side has near-zero delta
    // (e.g. PE volume delta = 1 lot) the ratio explodes to 10000+, which is
    // a measurement artifact, not a trading signal.
    if (dominanceRatio != null && dominancePass && volumeScoreWeight > 0) {
      score += 0.75 * volumeScoreWeight
      reasons.push(`${side}>Opp x${Math.min(dominanceRatio, 20).toFixed(2)}`)
    }
  } else {
    addCheck('volume-influence', 'Volume influence', true, 'OFF')
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
  // Strong momentum: allow 1 "bad tick" in the window (4/5 same direction).
  // Options LTP is noisy; requiring all ticks to align (5/5) means strong
  // momentum almost never fires even in clearly trending conditions.
  const strongMomentumThreshold = Math.max(2, config.entryMomentumCount - 1)
  addCheck('momentum-direction', 'Momentum alignment', momentum.direction === expectedDir, `${momentum.direction} x${momentum.count}`)
  if (momentum.direction === expectedDir && momentum.count >= strongMomentumThreshold) {
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

  // MACD alignment — require meaningful separation between EMA12 and EMA26.
  // On tick-level data, MACD oscillates within ±0.1 even in flat markets
  // (EMA12/EMA26 never fully converge on a price series with tiny tick noise).
  // A threshold of 0.1 filters the constant zero-cross noise while still
  // capturing real directional moves.
  const macdSignificant = indicators.macdLine != null && Math.abs(indicators.macdLine) >= 0.1
  let macdAligned = false
  if (macdSignificant) {
    macdAligned =
      (side === 'CE' && indicators.macdLine! > 0) ||
      (side === 'PE' && indicators.macdLine! < 0)
    if (macdAligned) {
      score += 1
      reasons.push(`MACD ${indicators.macdLine! > 0 ? '+' : ''}${indicators.macdLine!.toFixed(2)}`)
    }
  }
  addCheck('macd', 'MACD alignment', macdAligned, indicators.macdLine != null ? indicators.macdLine.toFixed(2) : 'NA')

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

  // Unified FLOW + Footprints + OI signal gate
  if (config.unifiedFlowSignalEnabled) {
    if (unifiedSignal) {
      const expectedAction: FlowBiasAction = side === 'CE' ? 'BUY_CE' : 'BUY_PE'
      const scoreAbs = Math.abs(unifiedSignal.score)
      const strongSignal = scoreAbs >= Math.max(0.05, config.unifiedFlowMinScore)
      const directionMatch =
        !strongSignal ||
        unifiedSignal.action === expectedAction ||
        unifiedSignal.action === 'HOLD'

      addCheck(
        'unified-flow',
        'Unified flow signal',
        directionMatch,
        `${unifiedSignal.label} s:${unifiedSignal.score >= 0 ? '+' : ''}${unifiedSignal.score.toFixed(2)}`
      )

      if (
        config.unifiedFlowHardBlock &&
        strongSignal &&
        unifiedSignal.action !== 'HOLD' &&
        unifiedSignal.action !== expectedAction
      ) {
        return block(`Unified flow opposes ${side}: ${unifiedSignal.label}`, 'unified-flow')
      }

      const weight = Math.max(0, config.unifiedFlowScoreWeight)
      if (weight > 0 && strongSignal) {
        if (unifiedSignal.action === expectedAction) {
          const bonus = scoreAbs * weight * 2
          score += bonus
          reasons.push(`Uni ${unifiedSignal.confidence}`)
        } else if (unifiedSignal.action === 'HOLD') {
          const penalty = Math.min(1.5, scoreAbs * weight)
          score -= penalty
          reasons.push('UniHold')
        } else {
          const penalty = scoreAbs * weight * 1.5
          score -= penalty
          reasons.push('UniOpp')
        }
      }
    } else {
      addCheck('unified-flow', 'Unified flow signal', true, 'NA')
    }
  } else {
    addCheck('unified-flow', 'Unified flow signal', true, 'OFF')
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
  if (!entryDecision.enter || entryDecision.score < 7) return null

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
