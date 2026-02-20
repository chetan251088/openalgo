import { useCallback, useEffect, useMemo, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { useLivePrice } from '@/hooks/useLivePrice'
import { useAuthStore } from '@/stores/authStore'
import { useMultiBrokerStore } from '@/stores/multiBrokerStore'
import type { Position } from '@/types/trading'
import type { ActiveSide, ScalpingPosition } from '@/types/scalping'

const POSITION_POLL_MS = 1000

function parseNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, '').trim())
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function toFiniteNumber(value: unknown): number {
  return parseNumber(value) ?? 0
}

function deriveOptionSide(symbol: string | undefined): ActiveSide | null {
  const match = (symbol ?? '').trim().toUpperCase().match(/(CE|PE)$/)
  if (!match) return null
  return match[1] as ActiveSide
}

function toResolvedPnl(position: Position): number {
  const apiPnl = parseNumber(position.pnl)
  if (apiPnl !== null) return apiPnl

  const liveLtp = toFiniteNumber(position.ltp)
  const avgPrice = toFiniteNumber(position.average_price)
  const qty = Math.abs(toFiniteNumber(position.quantity))
  const isBuy = toFiniteNumber(position.quantity) > 0
  const pnlPoints = isBuy ? liveLtp - avgPrice : avgPrice - liveLtp
  return pnlPoints * qty
}

/**
 * Fetches option positions and enriches them with real-time P&L
 * using the same polling + live price path as the main Positions page.
 */
export function useScalpingPositions() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const unifiedMode = useMultiBrokerStore((s) => s.unifiedMode)

  const [rawPositions, setRawPositions] = useState<Position[]>([])
  const [isLoading, setIsLoading] = useState(false)

  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKey) return apiKey
    try {
      const resp = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await resp.json()
      if (data.status === 'success' && data.api_key) {
        setApiKey(data.api_key)
        return data.api_key
      }
    } catch {
      // ignore
    }
    return null
  }, [apiKey, setApiKey])

  const fetchPositions = useCallback(async () => {
    const resolvedApiKey = await ensureApiKey()
    if (!resolvedApiKey) {
      setIsLoading(false)
      return
    }

    setIsLoading(true)
    try {
      const response = await tradingApi.getPositions(resolvedApiKey)
      if (response.status === 'success' && response.data) {
        // Keep full broker positionbook for top P&L (open + closed, same semantics as /positions).
        // CE/PE strip rendering is still filtered to open option positions below.
        setRawPositions(Array.isArray(response.data) ? response.data : [])
      }
    } catch {
      // Keep last known positions on transient failures.
    } finally {
      setIsLoading(false)
    }
  }, [ensureApiKey])

  useEffect(() => {
    fetchPositions()
    const interval = setInterval(fetchPositions, POSITION_POLL_MS)
    return () => clearInterval(interval)
  }, [fetchPositions])

  const { data: enhancedPositions, isLive, isPaused } = useLivePrice(rawPositions, {
    enabled: !unifiedMode && rawPositions.length > 0,
    useMultiQuotesFallback: true,
    staleThreshold: 5000,
    multiQuotesRefreshInterval: 30000,
    pauseWhenHidden: true,
  })

  const pricedPositions = useMemo(
    () => (unifiedMode ? rawPositions : enhancedPositions),
    [unifiedMode, rawPositions, enhancedPositions]
  )

  const positions = useMemo<ScalpingPosition[]>(() => {
    return pricedPositions.flatMap((p) => {
      const side = deriveOptionSide(p.symbol)
      if (!side) return []

      const signedQty = toFiniteNumber(p.quantity)
      const qty = Math.abs(signedQty)
      if (qty === 0) return []

      const liveLtp = toFiniteNumber(p.ltp)
      const avgPrice = toFiniteNumber(p.average_price)
      const isBuy = signedQty > 0
      const pnlPoints = isBuy ? liveLtp - avgPrice : avgPrice - liveLtp
      const pnl = parseNumber(p.pnl)
      const resolvedPnl = pnl ?? (pnlPoints * qty)

      return [
        {
          symbol: p.symbol,
          exchange: p.exchange,
          side,
          action: isBuy ? 'BUY' : 'SELL',
          quantity: qty,
          avgPrice,
          ltp: liveLtp,
          pnl: resolvedPnl,
          pnlPoints,
          product: p.product as 'MIS' | 'NRML',
        },
      ]
    })
  }, [pricedPositions])

  const hasOpenPositions = useMemo(
    () => rawPositions.some((p) => toFiniteNumber(p.quantity) !== 0),
    [rawPositions]
  )

  const totalPnl = useMemo(
    () => pricedPositions.reduce((sum, p) => sum + toResolvedPnl(p), 0),
    [pricedPositions]
  )

  const getPositionForSide = useCallback(
    (side: ActiveSide): ScalpingPosition | undefined => {
      return positions.find((p) => p.side === side)
    },
    [positions]
  )

  const positionSymbols = useMemo(
    () =>
      rawPositions.map((p) => ({
        symbol: p.symbol,
        exchange: p.exchange,
      })),
    [rawPositions]
  )

  return {
    positions,
    totalPnl,
    isLive: !unifiedMode && isLive && hasOpenPositions,
    isPaused: !unifiedMode && isPaused,
    isLoading,
    refetch: fetchPositions,
    getPositionForSide,
    positionSymbols,
  }
}
