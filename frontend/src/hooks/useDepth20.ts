import { useState, useEffect, useRef, useCallback } from 'react'
import { tradingApi, type DepthData } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'

interface DepthAnalytics {
  totalBidQty: number
  totalAskQty: number
  bidAskRatio: number
  spread: number
  largestBidWall: { price: number; qty: number } | null
  largestAskWall: { price: number; qty: number } | null
  imbalanceScore: number // -100 to +100 (positive = more bids)
}

interface UseDepthResult {
  depth: DepthData | null
  analytics: DepthAnalytics | null
  levels: 5 | 20
  isLoading: boolean
}

/**
 * Broker-aware depth subscription.
 * Polls 5-level depth via REST API every 2 seconds.
 * (20-level depth via WebSocket to be added in future for Dhan)
 */
export function useDepth20(symbol: string | null, exchange: string): UseDepthResult {
  const apiKey = useAuthStore((s) => s.apiKey)
  const [depth, setDepth] = useState<DepthData | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchDepth = useCallback(async () => {
    if (!symbol || !apiKey) return

    try {
      const res = await tradingApi.getDepth(apiKey, symbol, exchange)
      if (res.status === 'success' && res.data) {
        setDepth(res.data)
      }
    } catch {
      // Silently fail - depth is non-critical
    }
    setIsLoading(false)
  }, [symbol, exchange, apiKey])

  useEffect(() => {
    if (!symbol) {
      setDepth(null)
      return
    }

    setIsLoading(true)
    fetchDepth()

    intervalRef.current = setInterval(fetchDepth, 2000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [symbol, fetchDepth])

  // Compute analytics
  const analytics: DepthAnalytics | null = depth
    ? computeAnalytics(depth)
    : null

  return {
    depth,
    analytics,
    levels: 5, // REST API always returns 5 levels
    isLoading,
  }
}

function computeAnalytics(depth: DepthData): DepthAnalytics {
  const bids = depth.bids || []
  const asks = depth.asks || []

  const totalBidQty = bids.reduce((s, l) => s + l.quantity, 0)
  const totalAskQty = asks.reduce((s, l) => s + l.quantity, 0)
  const bidAskRatio =
    totalAskQty > 0 ? totalBidQty / totalAskQty : totalBidQty > 0 ? 999 : 1

  const spread =
    asks.length > 0 && bids.length > 0 ? asks[0].price - bids[0].price : 0

  const largestBidWall =
    bids.length > 0
      ? bids.reduce((max, l) => (l.quantity > max.quantity ? l : max), bids[0])
      : null
  const largestBidWallResult = largestBidWall
    ? { price: largestBidWall.price, qty: largestBidWall.quantity }
    : null

  const largestAskWall =
    asks.length > 0
      ? asks.reduce((max, l) => (l.quantity > max.quantity ? l : max), asks[0])
      : null
  const largestAskWallResult = largestAskWall
    ? { price: largestAskWall.price, qty: largestAskWall.quantity }
    : null

  const total = totalBidQty + totalAskQty
  const imbalanceScore = total > 0 ? ((totalBidQty - totalAskQty) / total) * 100 : 0

  return {
    totalBidQty,
    totalAskQty,
    bidAskRatio,
    spread,
    largestBidWall: largestBidWallResult,
    largestAskWall: largestAskWallResult,
    imbalanceScore,
  }
}
