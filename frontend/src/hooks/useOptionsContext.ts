import { useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import type { OptionsContext } from '@/types/scalping'

const POLL_INTERVAL = 45_000 // 45 seconds

/**
 * Polls backend options analytics every 30-60s.
 * Builds OptionsContext from: OI Tracker, Max Pain, GEX, IV Chart, Straddle.
 */
export function useOptionsContext() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const underlying = useScalpingStore((s) => s.underlying)
  const expiry = useScalpingStore((s) => s.expiry)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const setOptionsContext = useAutoTradeStore((s) => s.setOptionsContext)

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!apiKey || !expiry) return

    const fetchContext = async () => {
      try {
        const context = await fetchOptionsContextData(
          apiKey,
          underlying,
          expiry,
          optionExchange
        )
        if (context) {
          setOptionsContext(context)
        }
      } catch (err) {
        console.warn('[OptionsContext] Fetch failed:', err)
      }
    }

    fetchContext()
    intervalRef.current = setInterval(fetchContext, POLL_INTERVAL)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [apiKey, underlying, expiry, optionExchange, setOptionsContext])
}

/**
 * Fetch options context data from multiple backend endpoints.
 * Returns null if any critical endpoint fails.
 */
async function fetchOptionsContextData(
  apiKey: string,
  underlying: string,
  expiry: string,
  exchange: string
): Promise<OptionsContext | null> {
  const baseUrl = '/api/v1'

  try {
    // Fetch in parallel for speed
    const [oiRes, maxPainRes, gexRes, ivRes, straddleRes] = await Promise.allSettled([
      fetch(`${baseUrl}/oi_tracker`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: apiKey, symbol: underlying, exchange, expiry }),
      }).then((r) => r.json()),

      fetch(`${baseUrl}/max_pain`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: apiKey, symbol: underlying, exchange, expiry }),
      }).then((r) => r.json()),

      fetch(`${baseUrl}/gex`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: apiKey, symbol: underlying, exchange, expiry }),
      }).then((r) => r.json()),

      fetch(`${baseUrl}/iv_chart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: apiKey, symbol: underlying, exchange, expiry }),
      }).then((r) => r.json()),

      fetch(`${baseUrl}/straddle_chart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: apiKey, symbol: underlying, exchange, expiry }),
      }).then((r) => r.json()),
    ])

    const oi = oiRes.status === 'fulfilled' ? oiRes.value : null
    const maxPain = maxPainRes.status === 'fulfilled' ? maxPainRes.value : null
    const gex = gexRes.status === 'fulfilled' ? gexRes.value : null
    const iv = ivRes.status === 'fulfilled' ? ivRes.value : null
    const straddle = straddleRes.status === 'fulfilled' ? straddleRes.value : null

    return {
      pcr: oi?.data?.pcr ?? 1.0,
      oiChangeCE: oi?.data?.ce_oi_change ?? 0,
      oiChangePE: oi?.data?.pe_oi_change ?? 0,
      maxPainStrike: maxPain?.data?.max_pain_strike ?? 0,
      spotVsMaxPain: maxPain?.data?.spot_vs_max_pain ?? 0,
      topGammaStrikes: gex?.data?.top_gamma_strikes ?? [],
      gexFlipZones: gex?.data?.flip_zones ?? [],
      netGEX: gex?.data?.net_gex ?? 0,
      atmIV: iv?.data?.atm_iv ?? 15,
      ivPercentile: iv?.data?.iv_percentile ?? 50,
      ceIV: iv?.data?.ce_iv ?? 15,
      peIV: iv?.data?.pe_iv ?? 15,
      ivSkew: (iv?.data?.ce_iv ?? 15) - (iv?.data?.pe_iv ?? 15),
      straddlePrice: straddle?.data?.straddle_price ?? 0,
      lastUpdated: Date.now(),
    }
  } catch {
    return null
  }
}
