import { useEffect, useRef, useState } from 'react'
import ScalpingDashboard from '@/pages/scalping/ScalpingDashboard'
import { MarketDataManager } from '@/lib/MarketDataManager'
import { useMultiBrokerStore, type DataFeedMode } from '@/stores/multiBrokerStore'

export default function ScalpingUnifiedDashboard() {
  const setUnifiedMode = useMultiBrokerStore((s) => s.setUnifiedMode)
  const dataFeed = useMultiBrokerStore((s) => s.dataFeed)
  const [ready, setReady] = useState(false)
  const lastFeedRef = useRef<DataFeedMode | null>(null)

  useEffect(() => {
    const manager = MarketDataManager.getInstance()
    setUnifiedMode(true)
    setReady(true)

    return () => {
      setReady(false)
      setUnifiedMode(false)
      manager.disconnect()
    }
  }, [setUnifiedMode])

  useEffect(() => {
    if (!ready) return

    const manager = MarketDataManager.getInstance()
    const previousFeed = lastFeedRef.current
    manager.setUnifiedFeedMode(dataFeed)
    if (previousFeed && previousFeed !== dataFeed) {
      manager.disconnect()
    }
    lastFeedRef.current = dataFeed
  }, [dataFeed, ready])

  if (!ready) return null
  return <ScalpingDashboard />
}
