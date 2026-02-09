import { useEffect, useRef } from 'react'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'
import {
  calculateMomentum,
  detectRegime,
  shouldEnterTrade,
  calculateIndexBias,
  generateGhostSignal,
} from '@/lib/autoTradeEngine'
import { getCurrentZone } from '@/lib/marketClock'
import type { ActiveSide } from '@/types/scalping'
import type { PlaceOrderRequest } from '@/types/trading'
import type { MarketData } from '@/lib/MarketDataManager'

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
  const apiKey = useAuthStore((s) => s.apiKey)

  const activeSide = useScalpingStore((s) => s.activeSide)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const product = useScalpingStore((s) => s.product)
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
  const lastTradeTime = useAutoTradeStore((s) => s.lastTradeTime)
  const tradesThisMinute = useAutoTradeStore((s) => s.tradesThisMinute)
  const lastLossTime = useAutoTradeStore((s) => s.lastLossTime)

  const setRegime = useAutoTradeStore((s) => s.setRegime)
  const addGhostSignal = useAutoTradeStore((s) => s.addGhostSignal)
  const recordTrade = useAutoTradeStore((s) => s.recordTrade)

  // Tick history for momentum calculation (refs for speed)
  const ceTicksRef = useRef<number[]>([])
  const peTicksRef = useRef<number[]>([])
  const lastProcessTimeRef = useRef(0)
  const executingRef = useRef(false)

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
    const ceLtp = ceSymbol ? tickData.get(`${optionExchange}:${ceSymbol}`)?.data?.ltp : undefined
    const peLtp = peSymbol ? tickData.get(`${optionExchange}:${peSymbol}`)?.data?.ltp : undefined

    // Update tick history
    if (ceLtp) {
      ceTicksRef.current.push(ceLtp)
      if (ceTicksRef.current.length > 100) ceTicksRef.current = ceTicksRef.current.slice(-100)
    }
    if (peLtp) {
      peTicksRef.current.push(peLtp)
      if (peTicksRef.current.length > 100) peTicksRef.current = peTicksRef.current.slice(-100)
    }

    // Get market clock sensitivity
    const { sensitivity } = getCurrentZone(new Date(), false)

    // Check both sides for signals
    for (const side of ['CE', 'PE'] as ActiveSide[]) {
      const symbol = side === 'CE' ? ceSymbol : peSymbol
      const ltp = side === 'CE' ? ceLtp : peLtp
      const ticks = side === 'CE' ? ceTicksRef.current : peTicksRef.current

      if (!symbol || !ltp || ticks.length < 5) continue

      // Calculate momentum
      const momentum = calculateMomentum(ticks, config)

      // Placeholder indicators (from chart data in full implementation)
      const indicators = {
        ema9: null,
        ema21: null,
        supertrend: null,
        rsi: null,
        vwap: null,
      }

      // Index bias placeholder
      const indexBias = calculateIndexBias(indicators, config)

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
      const runtime = { consecutiveLosses, tradesCount, realizedPnl, lastTradeTime, tradesThisMinute, lastLossTime }
      const decision = shouldEnterTrade(
        side, ltp, config, runtime, momentum, indicators,
        indexBias, optionsContext, sensitivity, spread, depthInfo
      )

      if (mode === 'ghost') {
        // Ghost mode: generate signal only
        const ghost = generateGhostSignal(
          side, symbol, selectedStrike ?? 0, ltp, decision, regime, optionsContext?.pcr
        )
        if (ghost) {
          addGhostSignal(ghost)
        }
      } else if (mode === 'execute' && decision.enter && !executingRef.current) {
        // Execute mode: fire order
        executingRef.current = true
        const action = side === 'CE' ? 'BUY' : 'BUY' // Buy options for both sides

        if (paperMode) {
          console.log(`[AutoTrade Paper] ${action} ${side} ${symbol} score=${decision.score}`)
          recordTrade(0)
          executingRef.current = false
        } else if (apiKey) {
          const order: PlaceOrderRequest = {
            apikey: apiKey,
            strategy: 'Scalping-Auto',
            exchange: optionExchange,
            symbol,
            action,
            quantity: quantity * lotSize,
            pricetype: 'MARKET',
            product,
            price: 0,
            trigger_price: 0,
            disclosed_quantity: 0,
          }

          // Fire and forget for speed
          tradingApi.placeOrder(order)
            .then((res) => {
              if (res.status === 'success') {
                console.log(`[AutoTrade] ${action} ${symbol} id=${res.data?.orderid} score=${decision.score}`)
              }
            })
            .catch((err) => console.error('[AutoTrade] Order failed:', err))
            .finally(() => { executingRef.current = false })
        } else {
          executingRef.current = false
        }
      }
    }

    // Regime detection (less frequent, based on candle data)
    if (ceTicksRef.current.length >= config.regimeDetectionPeriod) {
      const candles = ceTicksRef.current.slice(-config.regimeDetectionPeriod).map((p, i, arr) => ({
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
    lastTradeTime, tradesThisMinute, lastLossTime,
    apiKey, optionExchange, quantity, lotSize, product, paperMode,
    selectedStrike, addGhostSignal, recordTrade, setRegime, activeSide,
  ])
}
