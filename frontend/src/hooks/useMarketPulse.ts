import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { fetchMarketPulse, type MarketPulseData } from '@/api/market-pulse'

const POLL_INTERVAL = 45_000 // 45 seconds

export function useMarketPulse() {
  const [mode, setMode] = useState<'swing' | 'day'>('swing')
  const queryClient = useQueryClient()

  const { data, isLoading, error, dataUpdatedAt } = useQuery<MarketPulseData>({
    queryKey: ['market-pulse', mode],
    queryFn: () => fetchMarketPulse(mode),
    refetchInterval: POLL_INTERVAL,
    staleTime: 30_000,
    retry: 2,
  })

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['market-pulse', mode] })
  }, [queryClient, mode])

  const secondsAgo = dataUpdatedAt
    ? Math.round((Date.now() - dataUpdatedAt) / 1000)
    : null

  return {
    data,
    isLoading,
    error,
    mode,
    setMode,
    refresh,
    secondsAgo,
  }
}
