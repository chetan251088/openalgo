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

  const prevSLRef = useRef<number | null>(null)

  useEffect(() => {
    const entries = Object.values(virtualTPSL)
    if (entries.length === 0) return

    // Monitor the first active position
    const active = entries[0]
    if (!active || active.slPrice == null) return

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
          ? Math.max(highSinceEntry || active.entryPrice, ltp)
          : Math.min(highSinceEntry || active.entryPrice, ltp)

        if (currentHigh !== highSinceEntry) {
          setHighSinceEntry(currentHigh)
        }

        // Calculate trailing stop
        const result = calculateTrailingStop(
          trailStage,
          active.entryPrice,
          ltp,
          currentHigh,
          isBuy,
          config,
          optionsContext
        )

        // Update stage if changed
        if (result.newStage !== trailStage) {
          setTrailStage(result.newStage)
        }

        // Update SL if changed
        const roundedSL = Math.round(result.newSL / 0.05) * 0.05
        if (prevSLRef.current == null || Math.abs(roundedSL - prevSLRef.current) >= 0.05) {
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
    virtualTPSL, optionExchange, config, trailStage, highSinceEntry,
    optionsContext, setTrailStage, setHighSinceEntry, updateVirtualTPSL,
  ])
}
