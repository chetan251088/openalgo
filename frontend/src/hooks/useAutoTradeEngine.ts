import { useCallback, useEffect, useRef } from 'react'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'
import { telegramApi } from '@/api/telegram'
import { toast } from 'sonner'
import {
  calculateMomentum,
  detectRegime,
  shouldEnterTrade,
  calculateIndexBias,
  generateGhostSignal,
} from '@/lib/autoTradeEngine'
import {
  buildVirtualPosition,
  extractOrderId,
  resolveFilledOrderPrice,
  resolveEntryPrice,
} from '@/lib/scalpingVirtualPosition'
import { getCurrentZone } from '@/lib/marketClock'
import type { ActiveSide } from '@/types/scalping'
import type { PlaceOrderRequest } from '@/types/trading'
import type { MarketData } from '@/lib/MarketDataManager'

interface VolumeFlowStats {
  latestDelta: number | null
  avgDelta: number | null
  ratio: number | null
}

interface VolumeSignalSnapshot {
  indexDelta: number | null
  indexDeltaRatio: number | null
  optionDelta: number | null
  optionDeltaRatio: number | null
  oppositeDelta: number | null
  sideDominanceRatio: number | null
}

const ADAPTIVE_SCALPER_MAX_EXTRA_LOTS = 2

function calculateEMAValue(prices: number[], period: number): number | null {
  if (prices.length < period) return null
  const k = 2 / (period + 1)
  let ema = prices.slice(0, period).reduce((sum, value) => sum + value, 0) / period
  for (let i = period; i < prices.length; i++) {
    ema = prices[i] * k + ema * (1 - k)
  }
  return Number.isFinite(ema) ? ema : null
}

function calculateRSIValue(prices: number[], period = 14): number | null {
  if (prices.length < period + 1) return null

  let gains = 0
  let losses = 0
  for (let i = 1; i <= period; i++) {
    const diff = prices[i] - prices[i - 1]
    if (diff > 0) gains += diff
    else losses += -diff
  }

  let avgGain = gains / period
  let avgLoss = losses / period

  for (let i = period + 1; i < prices.length; i++) {
    const diff = prices[i] - prices[i - 1]
    const gain = diff > 0 ? diff : 0
    const loss = diff < 0 ? -diff : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
  }

  if (avgLoss === 0) return 100
  const rs = avgGain / avgLoss
  const rsi = 100 - 100 / (1 + rs)
  return Number.isFinite(rsi) ? rsi : null
}

function buildIndicatorSnapshot(prices: number[]) {
  const ema9 = calculateEMAValue(prices, 9)
  const ema12 = calculateEMAValue(prices, 12)
  const ema21 = calculateEMAValue(prices, 21)
  const ema26 = calculateEMAValue(prices, 26)
  const macdLine = ema12 !== null && ema26 !== null ? ema12 - ema26 : null
  return {
    ema9,
    ema21,
    supertrend: null as number | null,
    rsi: calculateRSIValue(prices, 14),
    vwap: null as number | null,
    macdLine,
  }
}

function toNonNegativeNumber(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  return value
}

function pushMetricValue(history: number[], value: number, maxSize = 360): void {
  history.push(value)
  if (history.length > maxSize) history.splice(0, history.length - maxSize)
}

function computeVolumeFlow(cumulativeVolumes: number[], lookbackTicks: number): VolumeFlowStats {
  if (cumulativeVolumes.length < 3) return { latestDelta: null, avgDelta: null, ratio: null }

  const windowSize = Math.max(3, lookbackTicks + 1)
  const recent = cumulativeVolumes.slice(-windowSize)
  if (recent.length < 3) return { latestDelta: null, avgDelta: null, ratio: null }

  const deltas: number[] = []
  for (let i = 1; i < recent.length; i++) {
    const diff = recent[i] - recent[i - 1]
    deltas.push(diff > 0 && Number.isFinite(diff) ? diff : 0)
  }
  if (deltas.length < 2) return { latestDelta: null, avgDelta: null, ratio: null }

  const latestDelta = deltas[deltas.length - 1]
  const baseline = deltas.slice(0, -1)
  const baselineAvg = baseline.length > 0
    ? baseline.reduce((sum, value) => sum + value, 0) / baseline.length
    : 0
  const ratio = baselineAvg > 0 ? latestDelta / baselineAvg : null

  return {
    latestDelta,
    avgDelta: baselineAvg > 0 ? baselineAvg : null,
    ratio,
  }
}

function isTrailingActiveStage(stage: unknown): boolean {
  return stage === 'TRAIL' || stage === 'TIGHT' || stage === 'ACCELERATED'
}

/**
 * Auto-trade engine hook.
 * - Execute mode: processes ticks, fires orders when conditions met
 * - Ghost mode: processes ticks, generates GhostSignals only
 *
 * Uses refs for zero-React tick path.
 */
export function useAutoTradeEngine(
  tickData: Map<string, { data: MarketData }> | null
) {
  const OPTIONS_CONTEXT_STALE_MS = 20_000

  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)

  const underlying = useScalpingStore((s) => s.underlying)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const product = useScalpingStore((s) => s.product)
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const selectedStrike = useScalpingStore((s) => s.selectedStrike)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const enabled = useAutoTradeStore((s) => s.enabled)
  const mode = useAutoTradeStore((s) => s.mode)
  const config = useAutoTradeStore((s) => s.config)
  const activePresetId = useAutoTradeStore((s) => s.activePresetId)
  const optionsContext = useAutoTradeStore((s) => s.optionsContext)
  const regime = useAutoTradeStore((s) => s.regime)
  const consecutiveLosses = useAutoTradeStore((s) => s.consecutiveLosses)
  const tradesCount = useAutoTradeStore((s) => s.tradesCount)
  const realizedPnl = useAutoTradeStore((s) => s.realizedPnl)
  const lastTradePnl = useAutoTradeStore((s) => s.lastTradePnl)
  const lastTradeTime = useAutoTradeStore((s) => s.lastTradeTime)
  const tradesThisMinute = useAutoTradeStore((s) => s.tradesThisMinute)
  const lastLossTime = useAutoTradeStore((s) => s.lastLossTime)
  const sideEntryCount = useAutoTradeStore((s) => s.sideEntryCount)
  const sideLastExitAt = useAutoTradeStore((s) => s.sideLastExitAt)
  const replayMode = useAutoTradeStore((s) => s.replayMode)
  const killSwitch = useAutoTradeStore((s) => s.killSwitch)
  const lockProfitTriggered = useAutoTradeStore((s) => s.lockProfitTriggered)

  const setRegime = useAutoTradeStore((s) => s.setRegime)
  const addGhostSignal = useAutoTradeStore((s) => s.addGhostSignal)
  const recordTrade = useAutoTradeStore((s) => s.recordTrade)
  const recordAutoEntry = useAutoTradeStore((s) => s.recordAutoEntry)
  const pushDecision = useAutoTradeStore((s) => s.pushDecision)
  const pushExecutionSample = useAutoTradeStore((s) => s.pushExecutionSample)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)

  // Tick history for momentum calculation (refs for speed)
  const indexTicksRef = useRef<number[]>([])
  const ceTicksRef = useRef<number[]>([])
  const peTicksRef = useRef<number[]>([])
  const indexVolumeRef = useRef<number[]>([])
  const ceVolumeRef = useRef<number[]>([])
  const peVolumeRef = useRef<number[]>([])
  const lastProcessTimeRef = useRef(0)
  const executingRef = useRef<Record<ActiveSide, boolean>>({ CE: false, PE: false })
  const lastDecisionSignatureRef = useRef<Record<ActiveSide, string>>({ CE: '', PE: '' })
  const lastDecisionPushAtRef = useRef<Record<ActiveSide, number>>({ CE: 0, PE: 0 })
  const lastSignalEmitRef = useRef<Record<ActiveSide, { at: number; sig: string }>>({
    CE: { at: 0, sig: '' },
    PE: { at: 0, sig: '' },
  })
  const lastMissingApiKeyWarnAtRef = useRef(0)

  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKey) return apiKey
    try {
      const resp = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await resp.json()
      if (data.status === 'success' && data.api_key) {
        setApiKey(data.api_key)
        return data.api_key as string
      }
    } catch (err) {
      console.error('[AutoTrade] Failed to fetch API key:', err)
    }
    return null
  }, [apiKey, setApiKey])

  // Process ticks - runs every ~100ms via effect dependencies
  useEffect(() => {
    if (!enabled || !tickData) return

    // Throttle to avoid excessive processing
    const now = Date.now()
    if (now - lastProcessTimeRef.current < 100) return
    lastProcessTimeRef.current = now

    // Get current prices
    const ceSymbol = selectedCESymbol
    const peSymbol = selectedPESymbol
    const indexLtp = tickData.get(`${indexExchange}:${underlying}`)?.data?.ltp
    const ceLtp = ceSymbol ? tickData.get(`${optionExchange}:${ceSymbol}`)?.data?.ltp : undefined
    const peLtp = peSymbol ? tickData.get(`${optionExchange}:${peSymbol}`)?.data?.ltp : undefined
    const indexVolume = toNonNegativeNumber(tickData.get(`${indexExchange}:${underlying}`)?.data?.volume)
    const ceVolume = ceSymbol ? toNonNegativeNumber(tickData.get(`${optionExchange}:${ceSymbol}`)?.data?.volume) : null
    const peVolume = peSymbol ? toNonNegativeNumber(tickData.get(`${optionExchange}:${peSymbol}`)?.data?.volume) : null

    // Update tick history
    if (indexLtp) {
      indexTicksRef.current.push(indexLtp)
      if (indexTicksRef.current.length > 300) indexTicksRef.current = indexTicksRef.current.slice(-300)
    }
    if (ceLtp) {
      ceTicksRef.current.push(ceLtp)
      if (ceTicksRef.current.length > 300) ceTicksRef.current = ceTicksRef.current.slice(-300)
    }
    if (peLtp) {
      peTicksRef.current.push(peLtp)
      if (peTicksRef.current.length > 300) peTicksRef.current = peTicksRef.current.slice(-300)
    }
    if (indexVolume != null) pushMetricValue(indexVolumeRef.current, indexVolume)
    if (ceVolume != null) pushMetricValue(ceVolumeRef.current, ceVolume)
    if (peVolume != null) pushMetricValue(peVolumeRef.current, peVolume)

    const volumeLookbackTicks = Math.max(5, Number(config.volumeLookbackTicks) || 20)
    const indexVolumeFlow = computeVolumeFlow(indexVolumeRef.current, volumeLookbackTicks)
    const ceVolumeFlow = computeVolumeFlow(ceVolumeRef.current, volumeLookbackTicks)
    const peVolumeFlow = computeVolumeFlow(peVolumeRef.current, volumeLookbackTicks)

    // Get market clock sensitivity
    const isExpiryDayPreset = activePresetId === 'expiry'
    const { sensitivity } = getCurrentZone(new Date(), isExpiryDayPreset)

    // Check both sides for signals
    const effectiveOptionsContext =
      optionsContext && now - optionsContext.lastUpdated <= OPTIONS_CONTEXT_STALE_MS
        ? optionsContext
        : null

    for (const side of ['CE', 'PE'] as ActiveSide[]) {
      const symbol = side === 'CE' ? ceSymbol : peSymbol
      const ltp = side === 'CE' ? ceLtp : peLtp
      const ticks = side === 'CE' ? ceTicksRef.current : peTicksRef.current
      const sideOrders = Object.values(virtualTPSL).filter((order) => order.side === side)
      const sideAutoOrders = sideOrders.filter((order) => order.managedBy === 'auto')
      const sideOpen = sideOrders.length > 0
      const openSideQty = sideOrders.reduce((sum, order) => sum + Math.max(0, order.quantity || 0), 0)
      const openSideLotsRaw = lotSize > 0 ? openSideQty / lotSize : 0
      const openSideLots = Number.isFinite(openSideLotsRaw) ? openSideLotsRaw : 0
      const sideVolumeFlow = side === 'CE' ? ceVolumeFlow : peVolumeFlow
      const oppositeVolumeFlow = side === 'CE' ? peVolumeFlow : ceVolumeFlow
      const sideDominanceRatio =
        sideVolumeFlow.latestDelta != null && oppositeVolumeFlow.latestDelta != null
          ? sideVolumeFlow.latestDelta / Math.max(1, oppositeVolumeFlow.latestDelta)
          : null
      const volumeFlow: VolumeSignalSnapshot = {
        indexDelta: indexVolumeFlow.latestDelta,
        indexDeltaRatio: indexVolumeFlow.ratio,
        optionDelta: sideVolumeFlow.latestDelta,
        optionDeltaRatio: sideVolumeFlow.ratio,
        oppositeDelta: oppositeVolumeFlow.latestDelta,
        sideDominanceRatio,
      }

      if (!symbol || !ltp || ticks.length < 5) continue

      let sideUnrealizedPnl = 0
      for (const order of sideOrders) {
        const orderKey = `${order.exchange}:${order.symbol}`
        const orderLtp = tickData.get(orderKey)?.data?.ltp ?? (order.symbol === symbol ? ltp : undefined)
        if (!orderLtp || orderLtp <= 0) continue
        sideUnrealizedPnl +=
          order.action === 'BUY'
            ? (orderLtp - order.entryPrice) * order.quantity
            : (order.entryPrice - orderLtp) * order.quantity
      }

      const trailingStarted = sideAutoOrders.some((order) => isTrailingActiveStage(order.trailStage))
      const pyramidLotsCap = quantity + ADAPTIVE_SCALPER_MAX_EXTRA_LOTS
      const pyramidRemainingLots = Math.max(0, Math.floor(pyramidLotsCap - openSideLots + 1e-6))
      const canPyramidOnProfit =
        activePresetId === 'adaptive-scalper' &&
        sideOpen &&
        sideAutoOrders.length > 0 &&
        trailingStarted &&
        sideUnrealizedPnl > 0 &&
        pyramidRemainingLots > 0
      const entryLots = canPyramidOnProfit
        ? Math.max(1, Math.min(quantity, pyramidRemainingLots))
        : quantity

      // Calculate momentum
      const momentum = calculateMomentum(ticks, config)

      // Lightweight indicator snapshot from recent tick stream.
      const indicators = buildIndicatorSnapshot(ticks)

      // Index bias from dedicated index tick stream.
      const indexIndicators = buildIndicatorSnapshot(indexTicksRef.current)
      const indexBias = calculateIndexBias(indexIndicators, config)

      // Spread estimate (bid-ask from tick data if available)
      const symbolKey = `${optionExchange}:${symbol}`
      const bidPrice = tickData.get(symbolKey)?.data?.bid_price
      const askPrice = tickData.get(symbolKey)?.data?.ask_price
      const spread = bidPrice && askPrice ? askPrice - bidPrice : 2

      // Depth info for imbalance filter (sum bid/ask qty from depth levels)
      const depthData = tickData.get(symbolKey)?.data?.depth
      let depthInfo: { totalBid: number; totalAsk: number } | undefined
      if (depthData?.buy?.length && depthData?.sell?.length) {
        const totalBid = depthData.buy.reduce((sum, l) => sum + (l.quantity || 0), 0)
        const totalAsk = depthData.sell.reduce((sum, l) => sum + (l.quantity || 0), 0)
        if (totalBid > 0 && totalAsk > 0) depthInfo = { totalBid, totalAsk }
      }

      // Entry decision
      const runtime = {
        consecutiveLosses,
        tradesCount,
        realizedPnl,
        lastTradeTime,
        tradesThisMinute,
        lastLossTime,
        requestedLots: canPyramidOnProfit ? openSideLots + entryLots : quantity,
        sideOpen,
        openSideLots,
        allowEntryWithOpenSide: canPyramidOnProfit,
        lastExitAtForSide: sideLastExitAt[side],
        reEntryCountForSide: sideEntryCount[side],
        lastTradePnl,
        killSwitch,
        lockProfitTriggered,
      }
      const decision = shouldEnterTrade(
        side, ltp, config, runtime, momentum, indicators,
        indexBias, effectiveOptionsContext, sensitivity, spread, depthInfo, ticks, volumeFlow, regime
      )

      // Persist trader-facing "why trade" diagnostics at low frequency.
      // Use a stable signature: score rounded to nearest 0.5, minScore to 1dp,
      // reason truncated to 40 chars. This prevents the velocity float (e.g.
      // "Velocity 5.23" → "5.24" each tick) from looking like a new decision
      // and flooding the panel every 100 ms.
      const sigScore = (Math.round(decision.score * 2) / 2).toFixed(1)
      const sigReason = decision.reason.slice(0, 40)
      const signature = `${decision.enter}|${decision.blockedBy ?? ''}|${sigScore}|${decision.minScore.toFixed(1)}|${sigReason}`
      if (
        signature !== lastDecisionSignatureRef.current[side] ||
        now - lastDecisionPushAtRef.current[side] >= 5000
      ) {
        pushDecision({
          timestamp: now,
          side,
          symbol,
          enter: decision.enter,
          score: decision.score,
          minScore: decision.minScore,
          reason: decision.reason,
          blockedBy: decision.blockedBy,
          spread: decision.spread,
          depthRatio: decision.depthRatio,
          expectedSlippage: decision.expectedSlippage,
          checks: decision.checks,
          regime,
        })
        lastDecisionSignatureRef.current[side] = signature
        lastDecisionPushAtRef.current[side] = now
      }

      // Signal generation is always active while engine is enabled:
      // - ghost mode: signal only
      // - execute mode: signal + auto execution
      const ghost = generateGhostSignal(
        side, symbol, selectedStrike ?? 0, ltp, decision, regime, effectiveOptionsContext?.pcr
      )
      if (ghost) {
        // Use a stable sig: round score to integer + first 40 chars of reason.
        // Velocity/count in the reason change every tick → cause false "new signal"
        // if we include them verbatim. With this sig a signal only re-emits when
        // the score bracket or the dominant reason actually changes.
        const sigReason = decision.reason.slice(0, 40)
        const signalSig = `${symbol}|${Math.round(decision.score)}|${sigReason}`
        const prev = lastSignalEmitRef.current[side]
        // Minimum 4 seconds between any same-side signals (even if sig changes).
        // A second emit of a different sig is allowed after 2 seconds.
        const msSinceLast = now - prev.at
        const shouldEmit =
          msSinceLast >= 4000 ||
          (signalSig !== prev.sig && msSinceLast >= 2000)
        if (shouldEmit) {
          addGhostSignal(ghost)
          lastSignalEmitRef.current[side] = { at: now, sig: signalSig }
          toast.message(`Signal ${side} ${symbol.slice(-12)}`, {
            description: `${decision.score.toFixed(1)} / ${decision.minScore.toFixed(1)} | ${decision.reason}`,
          })
        }
      }

      if (mode === 'execute' && !replayMode && decision.enter && !executingRef.current[side]) {
        // Execute mode: fire order
        executingRef.current[side] = true
        const action: 'BUY' = 'BUY'
        const qty = entryLots * lotSize
        const placeAutoEntry = async () => {
          const isExecutionAllowed = () => {
            const runtimeState = useAutoTradeStore.getState()
            return (
              runtimeState.enabled &&
              runtimeState.mode === 'execute' &&
              !runtimeState.replayMode &&
              !runtimeState.killSwitch
            )
          }

          try {
            if (!isExecutionAllowed()) {
              pushExecutionSample({
                timestamp: Date.now(),
                side,
                symbol,
                spread: decision.spread,
                expectedSlippage: decision.expectedSlippage,
                status: 'rejected',
                reason: 'Execution skipped: mode/risk changed',
              })
              return
            }

            const executionApiKey = paperMode ? null : await ensureApiKey()
            if (!paperMode && !executionApiKey) {
              pushExecutionSample({
                timestamp: Date.now(),
                side,
                symbol,
                spread: decision.spread,
                expectedSlippage: decision.expectedSlippage,
                status: 'rejected',
                reason: 'Missing API key',
              })
              if (Date.now() - lastMissingApiKeyWarnAtRef.current > 5000) {
                lastMissingApiKeyWarnAtRef.current = Date.now()
                toast.error('Auto execute blocked: API key missing')
              }
              return
            }

            let brokerOrderId: string | null = null

            if (!paperMode && executionApiKey) {
              const order: PlaceOrderRequest = {
                apikey: executionApiKey,
                strategy: 'Scalping-Auto',
                exchange: optionExchange,
                symbol,
                action,
                quantity: qty,
                pricetype: 'MARKET',
                product,
                price: 0,
                trigger_price: 0,
                disclosed_quantity: 0,
              }

              const res = await tradingApi.placeOrder(order)
              if (res.status !== 'success') {
                pushExecutionSample({
                  timestamp: Date.now(),
                  side,
                  symbol,
                  spread: decision.spread,
                  expectedSlippage: decision.expectedSlippage,
                  status: 'rejected',
                  reason: 'Order rejected',
                })
                return
              }
              brokerOrderId = extractOrderId(res)
              console.log(
                `[AutoTrade] ${action} ${symbol} id=${brokerOrderId ?? 'n/a'} score=${decision.score}`
              )
            } else {
              console.log(`[AutoTrade Paper] ${action} ${side} ${symbol} score=${decision.score}`)
            }

            if (!isExecutionAllowed()) {
              pushExecutionSample({
                timestamp: Date.now(),
                side,
                symbol,
                spread: decision.spread,
                expectedSlippage: decision.expectedSlippage,
                status: 'rejected',
                reason: 'Execution skipped before virtual attach: mode/risk changed',
              })
              return
            }

            const entryPrice = paperMode
              ? await resolveEntryPrice({
                  symbol,
                  exchange: optionExchange,
                  preferredPrice: ltp,
                  apiKey: null,
                })
              : await resolveFilledOrderPrice({
                  symbol,
                  exchange: optionExchange,
                  orderId: brokerOrderId,
                  preferredPrice: ltp,
                  apiKey: executionApiKey,
                })
            const fallbackTpPoints = Math.max(2, Number(config.trailLockTrigger) || 8)
            const fallbackSlPoints = Math.max(2, Number(config.trailInitialSL) || 5)
            const resolvedTpPoints = tpPoints > 0 ? tpPoints : fallbackTpPoints
            const resolvedSlPoints = slPoints > 0 ? slPoints : fallbackSlPoints

            if (entryPrice > 0) {
              setVirtualTPSL(
                buildVirtualPosition({
                  symbol,
                  exchange: optionExchange,
                  side,
                  action,
                  entryPrice,
                  quantity: qty,
                  tpPoints: resolvedTpPoints,
                  slPoints: resolvedSlPoints,
                  managedBy: 'auto',
                  autoEntryScore: decision.score,
                  autoEntryReason: decision.reason,
                })
              )
            }

            recordTrade(0)
            recordAutoEntry(side)
            pushExecutionSample({
              timestamp: Date.now(),
              side,
              symbol,
              spread: decision.spread,
              expectedSlippage: Math.max(0, Math.abs((entryPrice > 0 ? entryPrice : ltp) - ltp)),
              status: 'filled',
              reason: decision.reason,
            })

            if (config.telegramAlertsEntry) {
              void telegramApi.sendBroadcast({
                message: `[AUTO ENTRY] ${side} ${symbol} qty=${qty} score=${decision.score.toFixed(1)} reason=${decision.reason}`,
              }).catch(() => {})
            }
          } catch (err) {
            pushExecutionSample({
              timestamp: Date.now(),
              side,
              symbol,
              spread: decision.spread,
              expectedSlippage: decision.expectedSlippage,
              status: 'rejected',
              reason: 'Order request failed',
            })
            console.error('[AutoTrade] Order failed:', err)
          } finally {
            executingRef.current[side] = false
          }
        }

        void placeAutoEntry()
      }
    }

    // Regime detection (less frequent, based on candle data)
    const regimeTicks =
      indexTicksRef.current.length >= config.regimeDetectionPeriod
        ? indexTicksRef.current
        : ceTicksRef.current
    if (regimeTicks.length >= config.regimeDetectionPeriod) {
      const candles = regimeTicks.slice(-config.regimeDetectionPeriod).map((p, i, arr) => ({
        high: Math.max(p, arr[Math.max(0, i - 1)] ?? p),
        low: Math.min(p, arr[Math.max(0, i - 1)] ?? p),
        close: p,
      }))
      const newRegime = detectRegime(candles, config)
      if (newRegime !== regime) {
        setRegime(newRegime)
      }
    }
  }, [
    enabled, tickData, mode, config, activePresetId, selectedCESymbol, selectedPESymbol,
    optionsContext, regime, consecutiveLosses, tradesCount, realizedPnl,
    lastTradeTime, tradesThisMinute, lastLossTime, lastTradePnl,
    sideEntryCount, sideLastExitAt, replayMode, killSwitch, lockProfitTriggered,
    ensureApiKey, optionExchange, quantity, lotSize, product, tpPoints, slPoints, paperMode,
    selectedStrike, addGhostSignal, recordTrade, recordAutoEntry, pushDecision, pushExecutionSample, setRegime,
    virtualTPSL, setVirtualTPSL,
    underlying, indexExchange,
  ])
}
