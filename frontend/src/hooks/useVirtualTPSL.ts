import { useEffect, useRef, useCallback } from 'react'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'
import type { PlaceOrderRequest } from '@/types/trading'
import type { MarketData } from '@/lib/MarketDataManager'

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
  const paperMode = useScalpingStore((s) => s.paperMode)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const product = useScalpingStore((s) => s.product)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const addSessionPnl = useScalpingStore((s) => s.addSessionPnl)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)
  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)

  // Use refs for hot-path data to avoid stale closures
  const apiKeyRef = useRef(apiKey)
  const paperModeRef = useRef(paperMode)
  const exchangeRef = useRef(optionExchange)
  const productRef = useRef(product)
  const lotSizeRef = useRef(lotSize)

  useEffect(() => { apiKeyRef.current = apiKey }, [apiKey])
  useEffect(() => { paperModeRef.current = paperMode }, [paperMode])
  useEffect(() => { exchangeRef.current = optionExchange }, [optionExchange])
  useEffect(() => { productRef.current = product }, [product])
  useEffect(() => { lotSizeRef.current = lotSize }, [lotSize])

  // Track which orders are currently being executed (prevent double-fire)
  const executingRef = useRef<Set<string>>(new Set())

  const fireCloseOrder = useCallback(
    async (symbol: string, qty: number, action: 'BUY' | 'SELL', reason: string) => {
      // Close = opposite action
      const closeAction = action === 'BUY' ? 'SELL' : 'BUY'

      if (paperModeRef.current) {
        console.log(`[Paper TP/SL] ${reason}: ${closeAction} ${symbol} qty=${qty}`)
        return true
      }

      if (!apiKeyRef.current) return false

      const order: PlaceOrderRequest = {
        apikey: apiKeyRef.current,
        strategy: 'Scalping',
        exchange: exchangeRef.current,
        symbol,
        action: closeAction,
        quantity: qty,
        pricetype: 'MARKET',
        product: productRef.current,
      }

      try {
        const res = await tradingApi.placeOrder(order)
        if (res.status === 'success') {
          console.log(`[Scalping TP/SL] ${reason}: ${closeAction} ${symbol} id=${res.data?.orderid}`)
          return true
        }
      } catch (err) {
        console.error(`[Scalping TP/SL] ${reason} order failed:`, err)
      }
      return false
    },
    []
  )

  const fireTriggerEntry = useCallback(
    async (symbol: string, qty: number, action: 'BUY' | 'SELL') => {
      if (paperModeRef.current) {
        console.log(`[Paper Trigger] Entry: ${action} ${symbol} qty=${qty}`)
        return true
      }

      if (!apiKeyRef.current) return false

      const order: PlaceOrderRequest = {
        apikey: apiKeyRef.current,
        strategy: 'Scalping',
        exchange: exchangeRef.current,
        symbol,
        action,
        quantity: qty,
        pricetype: 'MARKET',
        product: productRef.current,
      }

      try {
        const res = await tradingApi.placeOrder(order)
        if (res.status === 'success') {
          console.log(`[Scalping Trigger] Entry: ${action} ${symbol} id=${res.data?.orderid}`)
          return true
        }
      } catch (err) {
        console.error('[Scalping Trigger] Entry failed:', err)
      }
      return false
    },
    []
  )

  // Process ticks - check virtual TP/SL and trigger orders
  useEffect(() => {
    if (!tickData) return

    // Check virtual TP/SL
    for (const order of Object.values(virtualTPSL)) {
      const symbolData = tickData.get(order.symbol)
      const ltp = symbolData?.data?.ltp
      if (!ltp || executingRef.current.has(order.id)) continue

      const isBuy = order.action === 'BUY'

      // TP check
      if (order.tpPrice !== null) {
        const tpHit = isBuy ? ltp >= order.tpPrice : ltp <= order.tpPrice
        if (tpHit) {
          executingRef.current.add(order.id)
          const pnl = isBuy
            ? (ltp - order.entryPrice) * order.quantity
            : (order.entryPrice - ltp) * order.quantity

          fireCloseOrder(order.symbol, order.quantity, order.action, `TP hit at ${ltp}`).then(
            (ok) => {
              if (ok) {
                removeVirtualTPSL(order.id)
                addSessionPnl(pnl)
                incrementTradeCount()
              }
              executingRef.current.delete(order.id)
            }
          )
          continue
        }
      }

      // SL check
      if (order.slPrice !== null) {
        const slHit = isBuy ? ltp <= order.slPrice : ltp >= order.slPrice
        if (slHit) {
          executingRef.current.add(order.id)
          const pnl = isBuy
            ? (ltp - order.entryPrice) * order.quantity
            : (order.entryPrice - ltp) * order.quantity

          fireCloseOrder(order.symbol, order.quantity, order.action, `SL hit at ${ltp}`).then(
            (ok) => {
              if (ok) {
                removeVirtualTPSL(order.id)
                addSessionPnl(pnl)
                incrementTradeCount()
              }
              executingRef.current.delete(order.id)
            }
          )
        }
      }
    }

    // Check trigger orders
    for (const trigger of Object.values(triggerOrders)) {
      const symbolData = tickData.get(trigger.symbol)
      const ltp = symbolData?.data?.ltp
      if (!ltp || executingRef.current.has(trigger.id)) continue

      const triggered =
        trigger.direction === 'above'
          ? ltp >= trigger.triggerPrice
          : ltp <= trigger.triggerPrice

      if (triggered) {
        executingRef.current.add(trigger.id)

        fireTriggerEntry(trigger.symbol, trigger.quantity, trigger.action).then((ok) => {
          if (ok) {
            removeTriggerOrder(trigger.id)
            incrementTradeCount()

            // Auto-set virtual TP/SL for the triggered entry
            if (trigger.tpPoints > 0 || trigger.slPoints > 0) {
              const isBuy = trigger.action === 'BUY'
              setVirtualTPSL({
                id: `tpsl-${Date.now()}`,
                symbol: trigger.symbol,
                exchange: trigger.exchange,
                side: trigger.side,
                action: trigger.action,
                entryPrice: ltp,
                quantity: trigger.quantity,
                tpPrice:
                  trigger.tpPoints > 0
                    ? isBuy
                      ? ltp + trigger.tpPoints
                      : ltp - trigger.tpPoints
                    : null,
                slPrice:
                  trigger.slPoints > 0
                    ? isBuy
                      ? ltp - trigger.slPoints
                      : ltp + trigger.slPoints
                    : null,
                tpPoints: trigger.tpPoints,
                slPoints: trigger.slPoints,
                createdAt: Date.now(),
              })
            }
          }
          executingRef.current.delete(trigger.id)
        })
      }
    }
  }, [
    tickData,
    virtualTPSL,
    triggerOrders,
    fireCloseOrder,
    fireTriggerEntry,
    removeVirtualTPSL,
    removeTriggerOrder,
    setVirtualTPSL,
    addSessionPnl,
    incrementTradeCount,
  ])
}
