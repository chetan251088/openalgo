import { useEffect, useRef, useCallback } from 'react'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'
import { telegramApi } from '@/api/telegram'
import {
  buildVirtualPosition,
  extractOrderId,
  resolveFilledOrderPrice,
} from '@/lib/scalpingVirtualPosition'
import { optionsEarlyExitCheck } from '@/lib/autoTradeEngine'
import type { PlaceOrderRequest } from '@/types/trading'
import type { MarketData } from '@/lib/MarketDataManager'

const AUTO_OPTIONS_EARLY_EXIT_GRACE_MS = 3000

/**
 * Monitors live ticks and fires MARKET close orders when
 * virtual TP/SL or trigger prices are hit.
 *
 * Uses refs to avoid re-renders per tick.
 */
export function useVirtualTPSL(
  tickData: Map<string, { data: MarketData }> | null
) {
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const paperMode = useScalpingStore((s) => s.paperMode)
  const product = useScalpingStore((s) => s.product)
  const trailDistancePoints = useScalpingStore((s) => s.trailDistancePoints)
  const addSessionPnl = useScalpingStore((s) => s.addSessionPnl)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)

  const autoConfig = useAutoTradeStore((s) => s.config)
  const activePresetId = useAutoTradeStore((s) => s.activePresetId)
  const optionsContext = useAutoTradeStore((s) => s.optionsContext)
  const recordAutoExit = useAutoTradeStore((s) => s.recordAutoExit)
  const recordTradeOutcome = useAutoTradeStore((s) => s.recordTradeOutcome)
  const pushExecutionSample = useAutoTradeStore((s) => s.pushExecutionSample)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)
  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)

  // Use refs for hot-path data to avoid stale closures
  const apiKeyRef = useRef(apiKey)
  const paperModeRef = useRef(paperMode)
  const productRef = useRef(product)

  useEffect(() => { apiKeyRef.current = apiKey }, [apiKey])
  useEffect(() => { paperModeRef.current = paperMode }, [paperMode])
  useEffect(() => { productRef.current = product }, [product])

  // Track which orders are currently being executed (prevent double-fire)
  const executingRef = useRef<Set<string>>(new Set())

  // Time-based auto square-off tracking (fires once per trading day)
  const squareOffFiredRef = useRef(false)
  const squareOffDateRef = useRef('')

  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKeyRef.current) return apiKeyRef.current
    try {
      const resp = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await resp.json()
      if (data.status === 'success' && data.api_key) {
        apiKeyRef.current = data.api_key
        setApiKey(data.api_key)
        return data.api_key
      }
    } catch (err) {
      console.error('[Scalping] Failed to fetch API key:', err)
    }
    console.warn('[Scalping] No API key available — generate one at /apikey')
    return null
  }, [setApiKey])

  const fireCloseOrder = useCallback(
    async (symbol: string, exchange: string, qty: number, action: 'BUY' | 'SELL', reason: string) => {
      // Close = opposite action
      const closeAction = action === 'BUY' ? 'SELL' : 'BUY'

      if (paperModeRef.current) {
        console.log(`[Paper TP/SL] ${reason}: ${closeAction} ${symbol} qty=${qty}`)
        return true
      }

      const key = await ensureApiKey()
      if (!key) return false

      const order: PlaceOrderRequest = {
        apikey: key,
        strategy: 'Scalping',
        exchange,
        symbol,
        action: closeAction,
        quantity: qty,
        pricetype: 'MARKET',
        product: productRef.current,
        price: 0,
        trigger_price: 0,
        disclosed_quantity: 0,
      }

      try {
        const res = await tradingApi.placeOrder(order)
        if (res.status === 'success') {
          const brokerOrderId = extractOrderId(res)
          console.log(`[Scalping TP/SL] ${reason}: ${closeAction} ${symbol} id=${brokerOrderId ?? 'n/a'}`)
          return true
        }
      } catch (err) {
        console.error(`[Scalping TP/SL] ${reason} order failed:`, err)
      }
      try {
        const closeRes = await tradingApi.closePosition(symbol, exchange, productRef.current)
        if (closeRes.status === 'success') {
          console.log(`[Scalping TP/SL] ${reason}: closePosition fallback ${symbol}`)
          return true
        }
      } catch (err) {
        console.error(`[Scalping TP/SL] ${reason} closePosition fallback failed:`, err)
      }
      return false
    },
    [ensureApiKey]
  )

  const fireTriggerEntry = useCallback(
    async (
      symbol: string,
      exchange: string,
      qty: number,
      action: 'BUY' | 'SELL'
    ): Promise<{ ok: boolean; orderId: string | null }> => {
      if (paperModeRef.current) {
        console.log(`[Paper Trigger] Entry: ${action} ${symbol} qty=${qty}`)
        return { ok: true, orderId: null }
      }

      const key = await ensureApiKey()
      if (!key) return { ok: false, orderId: null }

      const order: PlaceOrderRequest = {
        apikey: key,
        strategy: 'Scalping',
        exchange,
        symbol,
        action,
        quantity: qty,
        pricetype: 'MARKET',
        product: productRef.current,
        price: 0,
        trigger_price: 0,
        disclosed_quantity: 0,
      }

      try {
        const res = await tradingApi.placeOrder(order)
        if (res.status === 'success') {
          const brokerOrderId = extractOrderId(res)
          console.log(`[Scalping Trigger] Entry: ${action} ${symbol} id=${brokerOrderId ?? 'n/a'}`)
          return { ok: true, orderId: brokerOrderId }
        }
      } catch (err) {
        console.error('[Scalping Trigger] Entry failed:', err)
      }
      return { ok: false, orderId: null }
    },
    [ensureApiKey]
  )

  // Process ticks - check virtual TP/SL and trigger orders
  useEffect(() => {
    if (!tickData) return

    const closeVirtualOrder = (order: (typeof virtualTPSL)[string], ltp: number, reason: string) => {
      executingRef.current.add(order.id)
      const isBuy = order.action === 'BUY'
      const pnl = isBuy
        ? (ltp - order.entryPrice) * order.quantity
        : (order.entryPrice - ltp) * order.quantity

      fireCloseOrder(order.symbol, order.exchange, order.quantity, order.action, `${reason} at ${ltp}`).then(
        (ok) => {
          if (ok) {
            removeVirtualTPSL(order.id)
            addSessionPnl(pnl)
            incrementTradeCount()

            if (order.managedBy === 'auto') {
              recordAutoExit(order.side, pnl)
              recordTradeOutcome(pnl)
              pushExecutionSample({
                timestamp: Date.now(),
                side: order.side,
                symbol: order.symbol,
                spread: 0,
                expectedSlippage: 0,
                status: 'exited',
                reason,
              })
              if (autoConfig.telegramAlertsExit) {
                void telegramApi.sendBroadcast({
                  message: `[AUTO EXIT] ${order.side} ${order.symbol} pnl=${pnl.toFixed(0)} reason=${reason}`,
                }).catch(() => {})
              }
            }
          }
          executingRef.current.delete(order.id)
        }
      )
    }

    // Check virtual TP/SL
    for (const order of Object.values(virtualTPSL)) {
      const symbolKey = `${order.exchange}:${order.symbol}`
      const symbolData = tickData.get(symbolKey) ?? tickData.get(order.symbol)
      const ltp = symbolData?.data?.ltp
      if (!ltp || executingRef.current.has(order.id)) continue

      const isBuy = order.action === 'BUY'
      const pnl = isBuy
        ? (ltp - order.entryPrice) * order.quantity
        : (order.entryPrice - ltp) * order.quantity

      // Auto-managed protection checks (safe: does not touch manual-only positions)
      if (order.managedBy === 'auto') {
        if (autoConfig.perTradeMaxLoss > 0 && pnl <= -autoConfig.perTradeMaxLoss) {
          closeVirtualOrder(order, ltp, `Per-trade max loss ${autoConfig.perTradeMaxLoss}`)
          continue
        }

        const orderAgeMs = Math.max(0, Date.now() - (order.createdAt || 0))
        if (orderAgeMs >= AUTO_OPTIONS_EARLY_EXIT_GRACE_MS) {
          const effectiveAutoConfig =
            activePresetId === 'adaptive-scalper'
              ? { ...autoConfig, ivSpikeExitEnabled: false }
              : autoConfig
          const earlyExit = optionsEarlyExitCheck(order.side, optionsContext, effectiveAutoConfig)
          if (earlyExit.exit) {
            closeVirtualOrder(order, ltp, `Options early-exit: ${earlyExit.reason}`)
            continue
          }
        }
      }

      // TP check
      if (order.tpPrice !== null) {
        const tpHit = isBuy ? ltp >= order.tpPrice : ltp <= order.tpPrice
        if (tpHit) {
          closeVirtualOrder(order, ltp, 'TP hit')
          continue
        }
      }

      // SL check
      if (order.slPrice !== null) {
        const slHit = isBuy ? ltp <= order.slPrice : ltp >= order.slPrice
        if (slHit) {
          closeVirtualOrder(order, ltp, 'SL hit')
        }
      }
    }

    // Check trigger orders
    for (const trigger of Object.values(triggerOrders)) {
      const symbolKey = `${trigger.exchange}:${trigger.symbol}`
      const symbolData = tickData.get(symbolKey) ?? tickData.get(trigger.symbol)
      const ltp = symbolData?.data?.ltp
      if (!ltp || executingRef.current.has(trigger.id)) continue

      const triggered =
        trigger.direction === 'above'
          ? ltp >= trigger.triggerPrice
          : ltp <= trigger.triggerPrice

      if (triggered) {
        executingRef.current.add(trigger.id)

        fireTriggerEntry(trigger.symbol, trigger.exchange, trigger.quantity, trigger.action).then(
          async ({ ok, orderId }) => {
            try {
              if (ok) {
                removeTriggerOrder(trigger.id)
                incrementTradeCount()

                // Triggered MARKET entry should always create/update a virtual position line.
                if (ltp > 0) {
                  const key = apiKeyRef.current
                  const entryPrice = paperModeRef.current
                    ? ltp
                    : await resolveFilledOrderPrice({
                        symbol: trigger.symbol,
                        exchange: trigger.exchange,
                        orderId,
                        preferredPrice: ltp,
                        apiKey: key,
                      })
                  if (entryPrice > 0) {
                    setVirtualTPSL(
                      buildVirtualPosition({
                        symbol: trigger.symbol,
                        exchange: trigger.exchange,
                        side: trigger.side,
                        action: trigger.action,
                        entryPrice,
                        quantity: trigger.quantity,
                        tpPoints: trigger.tpPoints,
                        slPoints: trigger.slPoints,
                        trailDistancePoints: trigger.trailDistancePoints ?? trailDistancePoints,
                        managedBy: 'trigger',
                      })
                    )
                  }
                }
              }
            } finally {
              executingRef.current.delete(trigger.id)
            }
          }
        )
      }
    }
    // Time-based auto square-off: close all positions before market close
    // Normal day: 15:15 IST | Expiry day: 14:45 IST
    const nowIST = new Date()
    const istTotalMins = ((nowIST.getUTCHours() * 60 + nowIST.getUTCMinutes()) + 330) % (24 * 60)
    const dateIST = new Date(nowIST.getTime() + 330 * 60 * 1000).toISOString().slice(0, 10)

    if (squareOffDateRef.current !== dateIST) {
      squareOffDateRef.current = dateIST
      squareOffFiredRef.current = false
    }

    const squareOffMinutes = activePresetId === 'expiry' ? 14 * 60 + 45 : 15 * 60 + 15
    const hasOpenOrders = Object.keys(virtualTPSL).length > 0

    if (!squareOffFiredRef.current && istTotalMins >= squareOffMinutes && hasOpenOrders) {
      squareOffFiredRef.current = true
      for (const order of Object.values(virtualTPSL)) {
        if (!executingRef.current.has(order.id)) {
          const symbolKey = `${order.exchange}:${order.symbol}`
          const ltp =
            tickData.get(symbolKey)?.data?.ltp ??
            tickData.get(order.symbol)?.data?.ltp ??
            order.entryPrice
          closeVirtualOrder(order, ltp, 'Auto square-off before market close')
        }
      }
    }
  }, [
    tickData,
    virtualTPSL,
    triggerOrders,
    fireCloseOrder,
    fireTriggerEntry,
    removeTriggerOrder,
    setVirtualTPSL,
    removeVirtualTPSL,
    addSessionPnl,
    incrementTradeCount,
    trailDistancePoints,
    autoConfig,
    activePresetId,
    optionsContext,
    recordAutoExit,
    recordTradeOutcome,
    pushExecutionSample,
  ])
}
