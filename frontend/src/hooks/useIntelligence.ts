import { useEffect, useRef, useCallback } from 'react'
import { useIntelligenceStore } from '@/stores/intelligenceStore'
import { webClient } from '@/api/client'

const POLL_INTERVAL_MS = 60_000  // 60s — keep light to protect scalping tick latency (<5ms)
const MARKET_START_HOUR = 9
const MARKET_END_HOUR = 16

function isMarketHours(): boolean {
  const now = new Date()
  const hour = now.getHours()
  return hour >= MARKET_START_HOUR && hour < MARKET_END_HOUR
}

export function useIntelligence(enabled: boolean = true) {
  const { setIntelligence, setLoading, setError } = useIntelligenceStore()
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchIntelligence = useCallback(async () => {
    if (!enabled) return
    try {
      setLoading(true)
      const response = await webClient.get('/intelligence/status')
      if (response.data?.status === 'success') {
        setIntelligence(response.data.data)
      }
    } catch (err: any) {
      if (err?.response?.status !== 401) {
        setError(err?.message ?? 'Failed to fetch intelligence')
      }
    }
  }, [enabled, setIntelligence, setLoading, setError])

  const refreshIntelligence = useCallback(
    async (params?: {
      news?: any[]
      market_data?: Record<string, any>
      symbols?: string[]
    }) => {
      try {
        setLoading(true)
        const response = await webClient.post('/intelligence/refresh', params ?? {})
        if (response.data?.status === 'success') {
          setIntelligence(response.data.data)
        }
      } catch (err: any) {
        setError(err?.message ?? 'Failed to refresh intelligence')
      }
    },
    [setIntelligence, setLoading, setError]
  )

  useEffect(() => {
    if (!enabled) return

    fetchIntelligence()

    intervalRef.current = setInterval(() => {
      if (isMarketHours()) {
        fetchIntelligence()
      }
    }, POLL_INTERVAL_MS)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [enabled, fetchIntelligence])

  return {
    ...useIntelligenceStore(),
    refreshIntelligence,
  }
}
