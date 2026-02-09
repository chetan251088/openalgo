import { useEffect, useRef } from 'react'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { calculateTrailingStop } from '@/lib/autoTradeEngine'
import { MarketDataManager } from '@/lib/MarketDataManager'

/**
 * Monitors live tick data for active virtual TP/SL positions and applies
 * the 5-stage trailing stop logic from calculateTrailingStop().
 *
 * When trailing SL moves, updates the virtualOrderStore SL price,
 * which ChartOrderOverlay picks up and re-renders the SL line.
 */
export function useTrailingMonitor() {
  const optionExchange = useScalpingStore((s) => s.optionExchange)

  const config = useAutoTradeStore((s) => s.config)
  const trailStage = useAutoTradeStore((s) => s.trailStage)
  const highSinceEntry = useAutoTradeStore((s) => s.highSinceEntry)
  const optionsContext = useAutoTradeStore((s) => s.optionsContext)
  const setTrailStage = useAutoTradeStore((s) => s.setTrailStage)
  const setHighSinceEntry = useAutoTradeStore((s) => s.setHighSinceEntry)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const updateVirtualTPSL = useVirtualOrderStore((s) => s.updateVirtualTPSL)

  // Keep runtime/config values in refs so subscription callback doesn't force resubscribe loops.
  const configRef = useRef(config)
  const trailStageRef = useRef(trailStage)
  const highSinceEntryRef = useRef(highSinceEntry)
  const optionsContextRef = useRef(optionsContext)
  const prevSLRef = useRef<number | null>(null)

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    trailStageRef.current = trailStage
  }, [trailStage])

  useEffect(() => {
    highSinceEntryRef.current = highSinceEntry
  }, [highSinceEntry])

  useEffect(() => {
    optionsContextRef.current = optionsContext
  }, [optionsContext])

  const active = Object.values(virtualTPSL)[0]
  const activeKey = active
    ? `${active.id}:${active.symbol}:${active.entryPrice}:${active.action}`
    : ''

  useEffect(() => {
    if (!active || active.slPrice == null) return

    // Initialize refs for this active position lifecycle.
    prevSLRef.current = active.slPrice
    if (!highSinceEntryRef.current || highSinceEntryRef.current <= 0) {
      highSinceEntryRef.current = active.entryPrice
    }

    const isBuy = active.action === 'BUY'
    const mdm = MarketDataManager.getInstance()

    const unsubscribe = mdm.subscribe(
      active.symbol,
      optionExchange,
      'LTP',
      (data) => {
        const ltp = data.data.ltp
        if (!ltp || ltp <= 0) return

        // Update high water mark
        const currentHigh = isBuy
          ? Math.max(highSinceEntryRef.current || active.entryPrice, ltp)
          : Math.min(highSinceEntryRef.current || active.entryPrice, ltp)

        if (currentHigh !== highSinceEntryRef.current) {
          highSinceEntryRef.current = currentHigh
          setHighSinceEntry(currentHigh)
        }

        // Calculate trailing stop
        const currentStage = trailStageRef.current
        const result = calculateTrailingStop(
          currentStage,
          active.entryPrice,
          ltp,
          currentHigh,
          isBuy,
          configRef.current,
          optionsContextRef.current
        )

        // Update stage if changed
        if (result.newStage !== currentStage) {
          trailStageRef.current = result.newStage
          setTrailStage(result.newStage)
        }

        // Update SL only on meaningful changes to avoid update loops.
        const roundedSL = Math.round(result.newSL / 0.05) * 0.05
        const currentSL = useVirtualOrderStore.getState().virtualTPSL[active.id]?.slPrice ?? null
        const slChangedFromStore = currentSL == null || Math.abs(roundedSL - currentSL) >= 0.05
        const slChangedFromPrev = prevSLRef.current == null || Math.abs(roundedSL - prevSLRef.current) >= 0.05

        if (slChangedFromStore && slChangedFromPrev) {
          prevSLRef.current = roundedSL
          updateVirtualTPSL(active.id, { slPrice: roundedSL })
        }
      }
    )

    return () => {
      unsubscribe()
      prevSLRef.current = null
    }
  }, [
    active,
    activeKey,
    optionExchange,
    setTrailStage,
    setHighSinceEntry,
    updateVirtualTPSL,
  ])
}
