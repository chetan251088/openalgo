import { useEffect, useState } from 'react'
import type {
  IntradayContext as IntradayContextType,
  GlobalContextData,
  AlertsData,
  JournalData,
  OptionsGreeksData,
  InstitutionalContextData,
  FundamentalsData,
  SectorContextData,
} from '@/api/market-pulse'
import {
  fetchIntradayContext,
  fetchGlobalContext,
  fetchAlerts,
  fetchJournal,
  fetchOptionsGreeks,
  fetchInstitutionalContext,
  fetchFundamentals,
  fetchSectorContext,
} from '@/api/market-pulse'

/**
 * Hook for fetching enhanced Market Pulse data from the progressive endpoints.
 * This supplements the existing useMarketPulse hook — does NOT replace it.
 */
export function useMarketPulseEnhanced(
  mode: 'swing' | 'day',
  equitySymbols: string[] = [],
) {
  const [intraday, setIntraday] = useState<Record<string, IntradayContextType> | null>(null)
  const [global, setGlobal] = useState<GlobalContextData | null>(null)
  const [greeks, setGreeks] = useState<Record<string, OptionsGreeksData> | null>(null)
  const [alerts, setAlerts] = useState<AlertsData | null>(null)
  const [journal, setJournal] = useState<JournalData | null>(null)
  const [institutional, setInstitutional] = useState<InstitutionalContextData | null>(null)
  const [fundamentals, setFundamentals] = useState<FundamentalsData | null>(null)
  const [sectors, setSectors] = useState<SectorContextData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function fetchAll() {
      setLoading(true)
      try {
        // Fire all fetches in parallel — each one is independent
        const [
          intradayData, globalData, greekData, alertData, journalData,
          instData, sectorData, fundData,
        ] = await Promise.allSettled([
          fetchIntradayContext(mode),
          fetchGlobalContext(),
          fetchOptionsGreeks(),
          fetchAlerts(),
          fetchJournal(30),
          fetchInstitutionalContext(),
          fetchSectorContext(),
          equitySymbols.length > 0 ? fetchFundamentals(equitySymbols) : Promise.resolve(null),
        ])

        if (cancelled) return

        if (intradayData.status === 'fulfilled') setIntraday(intradayData.value)
        if (globalData.status === 'fulfilled') setGlobal(globalData.value)
        if (greekData.status === 'fulfilled') setGreeks(greekData.value)
        if (alertData.status === 'fulfilled') setAlerts(alertData.value)
        if (journalData.status === 'fulfilled') setJournal(journalData.value)
        if (instData.status === 'fulfilled') setInstitutional(instData.value)
        if (sectorData.status === 'fulfilled') setSectors(sectorData.value)
        if (fundData.status === 'fulfilled') setFundamentals(fundData.value)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void fetchAll()

    // Poll every 20s for day mode, 60s for swing
    const interval = setInterval(
      () => void fetchAll(),
      mode === 'day' ? 20_000 : 60_000,
    )

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [mode, equitySymbols.join(',')])

  return { intraday, global, greeks, alerts, journal, institutional, fundamentals, sectors, loading }
}

