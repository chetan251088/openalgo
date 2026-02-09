import { useEffect, useRef } from 'react'
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
import { buildVirtualPosition, resolveEntryPrice } from '@/lib/scalpingVirtualPosition'
import { getCurrentZone } from '@/lib/marketClock'
import type { ActiveSide } from '@/types/scalping'
import type { PlaceOrderRequest } from '@/types/trading'
import type { MarketData } from '@/lib/MarketDataManager'

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
  return {
    ema9: calculateEMAValue(prices, 9),
    ema21: calculateEMAValue(prices, 21),
    supertrend: null as number | null,
    rsi: calculateRSIValue(prices, 14),
    vwap: null as number | null,
  }
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
  const getTPSLForSymbol = useVirtualOrderStore((s) => s.getTPSLForSymbol)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)

  // Tick history for momentum calculation (refs for speed)
  const indexTicksRef = useRef<number[]>([])
  const ceTicksRef = useRef<number[]>([])
  const peTicksRef = useRef<number[]>([])
  const lastProcessTimeRef = useRef(0)
  const executingRef = useRef(false)
  const lastDecisionSignatureRef = useRef<Record<ActiveSide, string>>({ CE: '', PE: '' })
  const lastDecisionPushAtRef = useRef<Record<ActiveSide, number>>({ CE: 0, PE: 0 })
  const lastSignalEmitRef = useRef<Record<ActiveSide, { at: number; sig: string }>>({
    CE: { at: 0, sig: '' },
    PE: { at: 0, sig: '' },
  })

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

    // Get market clock sensitivity
    const { sensitivity } = getCurrentZone(new Date(), false)

    // Check both sides for signals
    const effectiveOptionsContext =
      optionsContext && now - optionsContext.lastUpdated <= OPTIONS_CONTEXT_STALE_MS
        ? optionsContext
        : null

    for (const side of ['CE', 'PE'] as ActiveSide[]) {
      const symbol = side === 'CE' ? ceSymbol : peSymbol
      const ltp = side === 'CE' ? ceLtp : peLtp
      const ticks = side === 'CE' ? ceTicksRef.current : peTicksRef.current
      const sideOpen = Object.values(virtualTPSL).some((order) => order.side === side)

      if (!symbol || !ltp || ticks.length < 5) continue

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
        requestedLots: quantity,
        sideOpen,
        lastExitAtForSide: sideLastExitAt[side],
        reEntryCountForSide: sideEntryCount[side],
        lastTradePnl,
        killSwitch: killSwitch || lockProfitTriggered,
      }
      const decision = shouldEnterTrade(
        side, ltp, config, runtime, momentum, indicators,
        indexBias, effectiveOptionsContext, sensitivity, spread, depthInfo, ticks
      )

      // Persist trader-facing "why trade" diagnostics at low frequency.
      const signature = `${decision.enter}|${decision.blockedBy ?? ''}|${decision.reason}|${decision.score.toFixed(2)}|${decision.minScore.toFixed(2)}`
      if (
        signature !== lastDecisionSignatureRef.current[side] ||
        now - lastDecisionPushAtRef.current[side] >= 1000
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
        const signalSig = `${symbol}|${decision.reason}|${decision.score.toFixed(1)}`
        const prev = lastSignalEmitRef.current[side]
        const shouldEmit = signalSig !== prev.sig || now - prev.at >= 2500
        if (shouldEmit) {
          addGhostSignal(ghost)
          lastSignalEmitRef.current[side] = { at: now, sig: signalSig }
          toast.message(`Signal ${side} ${symbol.slice(-12)}`, {
            description: `${decision.score.toFixed(1)} / ${decision.minScore.toFixed(1)} | ${decision.reason}`,
          })
        }
      }

      if (mode === 'execute' && !replayMode && decision.enter && !executingRef.current) {
        // Execute mode: fire order
        executingRef.current = true
        const action: 'BUY' = 'BUY'
        const qty = quantity * lotSize
        const placeAutoEntry = async () => {
          try {
            if (!paperMode && !apiKey) return

            if (!paperMode && apiKey) {
              const order: PlaceOrderRequest = {
                apikey: apiKey,
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
              console.log(`[AutoTrade] ${action} ${symbol} id=${res.data?.orderid} score=${decision.score}`)
            } else {
              console.log(`[AutoTrade Paper] ${action} ${side} ${symbol} score=${decision.score}`)
            }

            const entryPrice = await resolveEntryPrice({
              symbol,
              exchange: optionExchange,
              preferredPrice: ltp,
              apiKey: paperMode ? null : apiKey,
            })
            if (entryPrice > 0) {
              const existingVirtual = getTPSLForSymbol(symbol)
              if (existingVirtual) removeVirtualTPSL(existingVirtual.id)
              setVirtualTPSL(
                buildVirtualPosition({
                  symbol,
                  exchange: optionExchange,
                  side,
                  action,
                  entryPrice,
                  quantity: qty,
                  tpPoints,
                  slPoints,
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
            executingRef.current = false
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
    enabled, tickData, mode, config, selectedCESymbol, selectedPESymbol,
    optionsContext, regime, consecutiveLosses, tradesCount, realizedPnl,
    lastTradeTime, tradesThisMinute, lastLossTime, lastTradePnl,
    sideEntryCount, sideLastExitAt, replayMode, killSwitch, lockProfitTriggered,
    apiKey, optionExchange, quantity, lotSize, product, tpPoints, slPoints, paperMode,
    selectedStrike, addGhostSignal, recordTrade, recordAutoEntry, pushDecision, pushExecutionSample, setRegime,
    virtualTPSL, setVirtualTPSL, getTPSLForSymbol, removeVirtualTPSL,
    underlying, indexExchange,
  ])
}
