import { useState, useEffect } from 'react'
import {
  getCurrentZone,
  getMinutesToNextZone,
  isExpiryDate,
  isMarketOpen,
  formatISTTime,
} from '@/lib/marketClock'
import { useScalpingStore } from '@/stores/scalpingStore'
import type { MarketClockZone } from '@/types/scalping'

interface MarketClockState {
  currentTime: string
  currentZone: MarketClockZone | null
  sensitivity: number
  nextZone: MarketClockZone | null
  minutesToNext: number
  isExpiryDay: boolean
  isOpen: boolean
}

/**
 * Real-time market clock with zone awareness.
 * Updates every second.
 */
export function useMarketClock(): MarketClockState {
  const expiry = useScalpingStore((s) => s.expiry)

  const [state, setState] = useState<MarketClockState>(() => {
    const now = new Date()
    const isExpiry = isExpiryDate(expiry, now)
    const { zone, sensitivity } = getCurrentZone(now, isExpiry)
    const { nextZone, minutesUntil } = getMinutesToNextZone(now, isExpiry)
    return {
      currentTime: formatISTTime(now),
      currentZone: zone,
      sensitivity,
      nextZone,
      minutesToNext: minutesUntil,
      isExpiryDay: isExpiry,
      isOpen: isMarketOpen(now),
    }
  })

  useEffect(() => {
    const update = () => {
      const now = new Date()
      const isExpiry = isExpiryDate(expiry, now)
      const { zone, sensitivity } = getCurrentZone(now, isExpiry)
      const { nextZone, minutesUntil } = getMinutesToNextZone(now, isExpiry)
      setState({
        currentTime: formatISTTime(now),
        currentZone: zone,
        sensitivity,
        nextZone,
        minutesToNext: minutesUntil,
        isExpiryDay: isExpiry,
        isOpen: isMarketOpen(now),
      })
    }

    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [expiry])

  return state
}
