import { useEffect, useMemo, useRef } from 'react'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { calculateTrailingStop } from '@/lib/autoTradeEngine'
import { MarketDataManager } from '@/lib/MarketDataManager'
import type { TrailingStage, VirtualTPSL } from '@/types/scalping'

const ADAPTIVE_SCALPER_TRAIL_DISTANCE = 2
const OPTION_TICK_SIZE = 0.05

function roundToOptionTick(price: number): number {
  return Math.round(price / OPTION_TICK_SIZE) * OPTION_TICK_SIZE
}

function shouldTrackTrailing(order: VirtualTPSL): boolean {
  if (order.slPrice == null) return false
  return (
    order.managedBy === 'auto' ||
    order.managedBy === 'manual' ||
    order.managedBy === 'hotkey' ||
    order.managedBy === 'trigger'
  )
}

function shouldUseCrossEntryTrail(order: VirtualTPSL, activePresetId: string | null): boolean {
  if (
    order.managedBy === 'manual' ||
    order.managedBy === 'hotkey' ||
    order.managedBy === 'trigger'
  ) {
    return true
  }
  return order.managedBy === 'auto' && activePresetId === 'adaptive-scalper'
}

function buildTrailingPriceUpdate(liveOrder: VirtualTPSL, nextSL: number): Partial<VirtualTPSL> {
  const updates: Partial<VirtualTPSL> = { slPrice: nextSL }
  if (liveOrder.tpPrice == null || liveOrder.slPrice == null) return updates

  const slDelta = nextSL - liveOrder.slPrice
  if (Math.abs(slDelta) < OPTION_TICK_SIZE) return updates

  const shiftedTP = roundToOptionTick(liveOrder.tpPrice + slDelta)
  const isBuy = liveOrder.action === 'BUY'
  const nextTP = isBuy
    ? Math.max(liveOrder.tpPrice, shiftedTP)
    : Math.min(liveOrder.tpPrice, shiftedTP)
  if (Math.abs(nextTP - liveOrder.tpPrice) >= OPTION_TICK_SIZE) {
    updates.tpPrice = nextTP
  }
  return updates
}

/**
 * Multi-position trailing monitor.
 * Each active virtual TP/SL position gets an independent trailing state.
 */
export function useTrailingMonitor() {
  const config = useAutoTradeStore((s) => s.config)
  const activePresetId = useAutoTradeStore((s) => s.activePresetId)
  const optionsContext = useAutoTradeStore((s) => s.optionsContext)
  const setTrailStage = useAutoTradeStore((s) => s.setTrailStage)
  const setHighSinceEntry = useAutoTradeStore((s) => s.setHighSinceEntry)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const updateVirtualTPSL = useVirtualOrderStore((s) => s.updateVirtualTPSL)

  const configRef = useRef(config)
  const activePresetIdRef = useRef(activePresetId)
  const optionsContextRef = useRef(optionsContext)
  const stageByOrderRef = useRef<Record<string, TrailingStage>>({})
  const highByOrderRef = useRef<Record<string, number>>({})
  const prevSLByOrderRef = useRef<Record<string, number>>({})
  const unsubscribeByOrderRef = useRef<Record<string, () => void>>({})

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    activePresetIdRef.current = activePresetId
  }, [activePresetId])

  useEffect(() => {
    optionsContextRef.current = optionsContext
  }, [optionsContext])

  const trailingOrders = useMemo(
    () =>
      Object.values(virtualTPSL).filter((order) => shouldTrackTrailing(order)),
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

      stageByOrderRef.current[order.id] = order.trailStage ?? 'INITIAL'
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

          const isAutoManaged = liveOrder.managedBy === 'auto'
          const isBuy = liveOrder.action === 'BUY'
          const currentHighRef = highByOrderRef.current[order.id] ?? liveOrder.entryPrice
          const currentHigh = isBuy
            ? Math.max(currentHighRef, ltp)
            : Math.min(currentHighRef, ltp)

          if (currentHigh !== currentHighRef) {
            highByOrderRef.current[order.id] = currentHigh
            if (isAutoManaged) setHighSinceEntry(currentHigh)
          }

          if (shouldUseCrossEntryTrail(liveOrder, activePresetIdRef.current)) {
            const crossedEntry = isBuy ? ltp > liveOrder.entryPrice : ltp < liveOrder.entryPrice
            if (!crossedEntry) return

            if (stageByOrderRef.current[order.id] !== 'TRAIL') {
              stageByOrderRef.current[order.id] = 'TRAIL'
              if (isAutoManaged) setTrailStage('TRAIL')
              updateVirtualTPSL(order.id, { trailStage: 'TRAIL' })
            }

            const rawSL = isBuy
              ? ltp - ADAPTIVE_SCALPER_TRAIL_DISTANCE
              : ltp + ADAPTIVE_SCALPER_TRAIL_DISTANCE
            const roundedSL = roundToOptionTick(rawSL)
            const storeSL = liveOrder.slPrice
            const prevSL = prevSLByOrderRef.current[order.id]
            const monotonicSL = isBuy
              ? Math.max(storeSL ?? roundedSL, roundedSL)
              : Math.min(storeSL ?? roundedSL, roundedSL)
            const slChangedFromStore = storeSL == null || Math.abs(monotonicSL - storeSL) >= OPTION_TICK_SIZE
            const slChangedFromPrev = prevSL == null || Math.abs(monotonicSL - prevSL) >= OPTION_TICK_SIZE

            if (slChangedFromStore && slChangedFromPrev) {
              prevSLByOrderRef.current[order.id] = monotonicSL
              updateVirtualTPSL(order.id, buildTrailingPriceUpdate(liveOrder, monotonicSL))
            }
            return
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
            if (isAutoManaged) setTrailStage(result.newStage)
            updateVirtualTPSL(order.id, { trailStage: result.newStage })
          }

          // Round to option tick and update only on meaningful changes.
          const roundedSL = roundToOptionTick(result.newSL)
          const storeSL = liveOrder.slPrice
          const prevSL = prevSLByOrderRef.current[order.id]
          const monotonicSL = isBuy
            ? Math.max(storeSL ?? roundedSL, roundedSL)
            : Math.min(storeSL ?? roundedSL, roundedSL)
          const slChangedFromStore = storeSL == null || Math.abs(monotonicSL - storeSL) >= OPTION_TICK_SIZE
          const slChangedFromPrev = prevSL == null || Math.abs(monotonicSL - prevSL) >= OPTION_TICK_SIZE

          if (slChangedFromStore && slChangedFromPrev) {
            prevSLByOrderRef.current[order.id] = monotonicSL
            updateVirtualTPSL(order.id, buildTrailingPriceUpdate(liveOrder, monotonicSL))
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
