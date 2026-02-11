import { useEffect, useMemo, useRef } from 'react'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { calculateTrailingStop } from '@/lib/autoTradeEngine'
import { MarketDataManager } from '@/lib/MarketDataManager'
import type { TrailingStage, VirtualTPSL } from '@/types/scalping'

/**
 * Multi-position trailing monitor.
 * Each active virtual TP/SL position gets an independent trailing state.
 */
export function useTrailingMonitor() {
  const config = useAutoTradeStore((s) => s.config)
  const optionsContext = useAutoTradeStore((s) => s.optionsContext)
  const setTrailStage = useAutoTradeStore((s) => s.setTrailStage)
  const setHighSinceEntry = useAutoTradeStore((s) => s.setHighSinceEntry)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const updateVirtualTPSL = useVirtualOrderStore((s) => s.updateVirtualTPSL)

  const configRef = useRef(config)
  const optionsContextRef = useRef(optionsContext)
  const stageByOrderRef = useRef<Record<string, TrailingStage>>({})
  const highByOrderRef = useRef<Record<string, number>>({})
  const prevSLByOrderRef = useRef<Record<string, number>>({})
  const unsubscribeByOrderRef = useRef<Record<string, () => void>>({})

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    optionsContextRef.current = optionsContext
  }, [optionsContext])

  const trailingOrders = useMemo(
    () =>
      Object.values(virtualTPSL).filter(
        (order) => order.managedBy === 'auto' && order.slPrice != null
      ),
    [virtualTPSL]
  )

  useEffect(() => {
    const mdm = MarketDataManager.getInstance()
    const nextIds = new Set(trailingOrders.map((order) => order.id))

    // Unsubscribe removed orders.
    for (const [id, unsubscribe] of Object.entries(unsubscribeByOrderRef.current)) {
      if (!nextIds.has(id)) {
        unsubscribe()
        delete unsubscribeByOrderRef.current[id]
        delete stageByOrderRef.current[id]
        delete highByOrderRef.current[id]
        delete prevSLByOrderRef.current[id]
      }
    }

    for (const order of trailingOrders) {
      if (unsubscribeByOrderRef.current[order.id]) continue

      stageByOrderRef.current[order.id] = 'INITIAL'
      highByOrderRef.current[order.id] = order.entryPrice
      prevSLByOrderRef.current[order.id] = order.slPrice ?? order.entryPrice

      unsubscribeByOrderRef.current[order.id] = mdm.subscribe(
        order.symbol,
        order.exchange,
        'LTP',
        (payload) => {
          const ltp = payload.data.ltp
          if (!ltp || ltp <= 0) return

          const liveOrder = useVirtualOrderStore.getState().virtualTPSL[order.id] as VirtualTPSL | undefined
          if (!liveOrder || liveOrder.slPrice == null) return

          const isBuy = liveOrder.action === 'BUY'
          const currentHighRef = highByOrderRef.current[order.id] ?? liveOrder.entryPrice
          const currentHigh = isBuy
            ? Math.max(currentHighRef, ltp)
            : Math.min(currentHighRef, ltp)

          if (currentHigh !== currentHighRef) {
            highByOrderRef.current[order.id] = currentHigh
            setHighSinceEntry(currentHigh)
          }

          const currentStage = stageByOrderRef.current[order.id] ?? 'INITIAL'
          const result = calculateTrailingStop(
            currentStage,
            liveOrder.entryPrice,
            ltp,
            currentHigh,
            isBuy,
            configRef.current,
            optionsContextRef.current
          )

          if (result.newStage !== currentStage) {
            stageByOrderRef.current[order.id] = result.newStage
            setTrailStage(result.newStage)
          }

          // Round to option tick and update only on meaningful changes.
          const roundedSL = Math.round(result.newSL / 0.05) * 0.05
          const storeSL = liveOrder.slPrice
          const prevSL = prevSLByOrderRef.current[order.id]
          const monotonicSL = isBuy
            ? Math.max(storeSL ?? roundedSL, roundedSL)
            : Math.min(storeSL ?? roundedSL, roundedSL)
          const slChangedFromStore = storeSL == null || Math.abs(monotonicSL - storeSL) >= 0.05
          const slChangedFromPrev = prevSL == null || Math.abs(monotonicSL - prevSL) >= 0.05

          if (slChangedFromStore && slChangedFromPrev) {
            prevSLByOrderRef.current[order.id] = monotonicSL
            updateVirtualTPSL(order.id, { slPrice: monotonicSL })
          }
        }
      )
    }

    return () => {
      // Only clean all subscriptions on unmount.
      // Diff cleanup above handles incremental order changes.
    }
  }, [trailingOrders, setHighSinceEntry, setTrailStage, updateVirtualTPSL])

  useEffect(
    () => () => {
      for (const unsubscribe of Object.values(unsubscribeByOrderRef.current)) {
        unsubscribe()
      }
      unsubscribeByOrderRef.current = {}
      stageByOrderRef.current = {}
      highByOrderRef.current = {}
      prevSLByOrderRef.current = {}
    },
    []
  )
}
