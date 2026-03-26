import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchMarketPulse, type MarketPulseData } from '@/api/market-pulse'
import { usePageVisibility } from '@/hooks/usePageVisibility'

const SWING_POLL_INTERVAL = 45_000
const DAY_POLL_INTERVAL = 15_000

export function useMarketPulse() {
  const [mode, setMode] = useState<'swing' | 'day'>('swing')
  const [isManualRefreshing, setIsManualRefreshing] = useState(false)
  const { isVisible, wasHidden } = usePageVisibility()
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['market-pulse', mode] as const, [mode])
  const pollInterval = mode === 'day' ? DAY_POLL_INTERVAL : SWING_POLL_INTERVAL
  const staleTime = mode === 'day' ? 15_000 : 30_000

  const query = useQuery<MarketPulseData>({
    queryKey,
    queryFn: () => fetchMarketPulse(mode),
    placeholderData: (previousData) => previousData,
    refetchInterval: pollInterval,
    refetchIntervalInBackground: mode === 'day',
    refetchOnWindowFocus: true,
    staleTime,
    retry: 2,
  })

  useEffect(() => {
    if (!isVisible || !wasHidden) {
      return
    }

    setIsManualRefreshing(false)
    void queryClient.invalidateQueries({ queryKey })
  }, [isVisible, queryClient, queryKey, wasHidden])

  const refresh = useCallback(async () => {
    if (isManualRefreshing) {
      return
    }

    setIsManualRefreshing(true)
    try {
      const freshData = await fetchMarketPulse(mode, true)
      queryClient.setQueryData(queryKey, freshData)
    } catch (error) {
      console.error('Market Pulse refresh failed', error)
      await queryClient.invalidateQueries({ queryKey })
    } finally {
      setIsManualRefreshing(false)
    }
  }, [isManualRefreshing, mode, queryClient, queryKey])

  const secondsAgo = query.dataUpdatedAt
    ? Math.round((Date.now() - query.dataUpdatedAt) / 1000)
    : null

  return {
    data: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching || isManualRefreshing,
    error: query.error,
    mode,
    setMode,
    refresh,
    secondsAgo,
  }
}
