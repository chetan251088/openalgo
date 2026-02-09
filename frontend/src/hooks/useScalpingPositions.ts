import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { tradingApi } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import type { Position } from '@/types/trading'
import type { ScalpingPosition, ActiveSide } from '@/types/scalping'
import type { MarketData } from '@/lib/MarketDataManager'

/**
 * Fetches option positions and enriches with live P&L from tick data.
 * Filters to only show positions matching current option exchange.
 */
export function useScalpingPositions(
  tickData: Map<string, { data: MarketData }> | null
) {
  const apiKey = useAuthStore((s) => s.apiKey)
  const optionExchange = useScalpingStore((s) => s.optionExchange)

  // Fetch positions every 5s, also refetchable via queryClient
  const {
    data: positionData,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['scalping-positions', optionExchange],
    queryFn: () => tradingApi.getPositions(apiKey!),
    enabled: !!apiKey,
    refetchInterval: 5000,
    staleTime: 2000,
  })

  // Filter to option positions on the current exchange
  const rawPositions = useMemo<Position[]>(() => {
    if (!positionData?.data) return []
    return positionData.data.filter(
      (p) => p.exchange === optionExchange && p.quantity !== 0
    )
  }, [positionData, optionExchange])

  // Enrich with live tick data and classify CE/PE
  const positions = useMemo<ScalpingPosition[]>(() => {
    return rawPositions.map((p) => {
      const liveLtp = tickData?.get(p.symbol)?.data?.ltp ?? p.ltp
      const side: ActiveSide = p.symbol.endsWith('CE') ? 'CE' : 'PE'
      const isBuy = p.quantity > 0
      const qty = Math.abs(p.quantity)
      const pnlPoints = isBuy
        ? liveLtp - p.average_price
        : p.average_price - liveLtp
      const pnl = pnlPoints * qty

      return {
        symbol: p.symbol,
        exchange: p.exchange,
        side,
        action: isBuy ? 'BUY' : 'SELL',
        quantity: qty,
        avgPrice: p.average_price,
        ltp: liveLtp,
        pnl,
        pnlPoints,
        product: p.product as 'MIS' | 'NRML',
      }
    })
  }, [rawPositions, tickData])

  // Total P&L across all positions
  const totalPnl = useMemo(
    () => positions.reduce((sum, p) => sum + p.pnl, 0),
    [positions]
  )

  // Get position for a specific side
  const getPositionForSide = useCallback(
    (side: ActiveSide): ScalpingPosition | undefined => {
      return positions.find((p) => p.side === side)
    },
    [positions]
  )

  // Symbols that have open positions (for subscribing to live data)
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
    isLoading,
    refetch,
    getPositionForSide,
    positionSymbols,
  }
}
