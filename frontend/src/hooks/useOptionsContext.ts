import { useCallback, useEffect, useRef } from 'react'
import type { OptionChainResponse, OptionData, OptionStrike } from '@/types/option-chain'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useAuthStore } from '@/stores/authStore'
import type { OptionsContext } from '@/types/scalping'

const UPDATE_THROTTLE_MS = 300
const KEEPALIVE_UPDATE_MS = 2_000
const DEFAULT_IV = 15
const STALE_CHAIN_MS = 8_000
const FALLBACK_FETCH_INTERVAL_MS = 15_000

function toNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number') return Number.isFinite(value) ? value : fallback
  if (typeof value === 'string' && value.trim().length > 0) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : fallback
  }
  return fallback
}

function readNumericField(source: unknown, field: string): number | null {
  if (!source || typeof source !== 'object') return null
  const value = (source as Record<string, unknown>)[field]
  const parsed = toNumber(value, Number.NaN)
  return Number.isFinite(parsed) ? parsed : null
}

function getAtmRow(chain: OptionStrike[], atmStrike: number, spotPrice: number): OptionStrike | null {
  if (!chain.length) return null
  const exact = chain.find((row) => row.strike === atmStrike)
  if (exact) return exact

  const reference = Number.isFinite(spotPrice) && spotPrice > 0 ? spotPrice : atmStrike
  let best = chain[0]
  let bestDistance = Math.abs(chain[0].strike - reference)
  for (let i = 1; i < chain.length; i++) {
    const distance = Math.abs(chain[i].strike - reference)
    if (distance < bestDistance) {
      best = chain[i]
      bestDistance = distance
    }
  }
  return best
}

function getStrikeTotalOi(row: OptionStrike): number {
  return toNumber(row.ce?.oi, 0) + toNumber(row.pe?.oi, 0)
}

function computeTopWallStrikes(chain: OptionStrike[]): number[] {
  return [...chain]
    .map((row) => ({
      strike: row.strike,
      score:
        Math.abs(toNumber(readNumericField(row, 'net_gex'), 0)) ||
        getStrikeTotalOi(row),
    }))
    .filter((item) => Number.isFinite(item.strike) && item.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 5)
    .map((item) => item.strike)
}

function computeMaxPainStrike(chain: OptionStrike[], fallbackStrike: number): number {
  if (!chain.length) return fallbackStrike

  let bestStrike = fallbackStrike
  let minPain = Number.POSITIVE_INFINITY

  for (const candidate of chain) {
    const target = candidate.strike
    let totalPain = 0

    for (const row of chain) {
      const strike = row.strike
      const ceOi = toNumber(row.ce?.oi, 0)
      const peOi = toNumber(row.pe?.oi, 0)

      if (target > strike) {
        totalPain += (target - strike) * ceOi
      } else if (target < strike) {
        totalPain += (strike - target) * peOi
      }
    }

    if (totalPain < minPain) {
      minPain = totalPain
      bestStrike = target
    }
  }

  return bestStrike
}

function isSameTopStrikes(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

function hasMeaningfulDelta(previous: OptionsContext | null, next: OptionsContext): boolean {
  if (!previous) return true

  if (Math.abs(previous.pcr - next.pcr) >= 0.01) return true
  if (Math.abs(previous.maxPainStrike - next.maxPainStrike) >= 1) return true
  if (Math.abs(previous.spotVsMaxPain - next.spotVsMaxPain) >= 1) return true
  if (Math.abs(previous.straddlePrice - next.straddlePrice) >= 0.1) return true
  if (Math.abs(previous.netGEX - next.netGEX) >= 1) return true
  if (Math.abs(previous.ceIV - next.ceIV) >= 0.1) return true
  if (Math.abs(previous.peIV - next.peIV) >= 0.1) return true
  if (Math.abs(previous.ivPercentile - next.ivPercentile) >= 0.5) return true
  if (!isSameTopStrikes(previous.topGammaStrikes, next.topGammaStrikes)) return true

  return false
}

function extractIv(option: OptionData | null | undefined, previousFallback: number): number {
  const ivFromPayload = readNumericField(option, 'iv')
  if (ivFromPayload != null && ivFromPayload > 0) return ivFromPayload
  return previousFallback
}

function normalizeExpiryForApi(expiry: string): string {
  return expiry.replace(/-/g, '').toUpperCase()
}

function buildContextFromChain(
  chainData: OptionChainResponse,
  previous: OptionsContext | null,
  lastUpdate: number
): OptionsContext | null {
  const chain = Array.isArray(chainData.chain) ? chainData.chain : []
  if (!chain.length) return null

  const spotPrice = toNumber(chainData.underlying_ltp, 0)
  const atmStrike = toNumber(chainData.atm_strike, 0)
  const atmRow = getAtmRow(chain, atmStrike, spotPrice)

  const totals = chain.reduce(
    (acc, row) => {
      acc.ceOi += toNumber(row.ce?.oi, 0)
      acc.peOi += toNumber(row.pe?.oi, 0)
      acc.ceVol += toNumber(row.ce?.volume, 0)
      acc.peVol += toNumber(row.pe?.volume, 0)
      return acc
    },
    { ceOi: 0, peOi: 0, ceVol: 0, peVol: 0 }
  )

  const pcr = totals.ceOi > 0 ? totals.peOi / totals.ceOi : 1
  const maxPainStrike = computeMaxPainStrike(chain, atmRow?.strike ?? atmStrike)
  const spotVsMaxPain = spotPrice - maxPainStrike
  const topGammaStrikes = computeTopWallStrikes(chain)

  const netGexRaw = readNumericField(chainData, 'total_net_gex')
  const netGEX = netGexRaw != null ? netGexRaw : 0

  const previousCeIv = previous?.ceIV ?? DEFAULT_IV
  const previousPeIv = previous?.peIV ?? DEFAULT_IV
  const ceIV = extractIv(atmRow?.ce, previousCeIv)
  const peIV = extractIv(atmRow?.pe, previousPeIv)
  const atmIV = (ceIV + peIV) / 2
  const ivSkew = ceIV - peIV
  const ivPercentile =
    readNumericField(chainData, 'iv_percentile') ??
    previous?.ivPercentile ??
    50

  const straddlePrice =
    toNumber(atmRow?.ce?.ltp, 0) + toNumber(atmRow?.pe?.ltp, 0)

  return {
    pcr,
    oiChangeCE: totals.ceOi,
    oiChangePE: totals.peOi,
    maxPainStrike,
    spotVsMaxPain,
    topGammaStrikes,
    gexFlipZones: [],
    netGEX,
    atmIV,
    ivPercentile,
    ceIV,
    peIV,
    ivSkew,
    straddlePrice,
    lastUpdated: lastUpdate > 0 ? lastUpdate : Date.now(),
  }
}

/**
 * WS-first options context:
 * - Uses live option-chain snapshot (WS-overlaid quotes/OI/volume) from scalping store
 * - Avoids periodic REST polling for OI/MaxPain/GEX/IV/Straddle endpoints
 * - Publishes throttled context updates for auto-trade/risk filters
 */
export function useOptionsContext() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const underlying = useScalpingStore((s) => s.underlying)
  const expiry = useScalpingStore((s) => s.expiry)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const chainStrikeCount = useScalpingStore((s) => s.chainStrikeCount)
  const chainData = useScalpingStore((s) => s.optionChainData)
  const chainLastUpdate = useScalpingStore((s) => s.optionChainLastUpdate)
  const chainIsStreaming = useScalpingStore((s) => s.optionChainIsStreaming)

  const currentContext = useAutoTradeStore((s) => s.optionsContext)
  const setOptionsContext = useAutoTradeStore((s) => s.setOptionsContext)

  const lastPushRef = useRef(0)
  const fallbackFetchInFlightRef = useRef(false)
  const lastFallbackFetchRef = useRef(0)
  const fallbackIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!chainData || !Array.isArray(chainData.chain) || chainData.chain.length === 0) return

    const now = Date.now()
    if (now - lastPushRef.current < UPDATE_THROTTLE_MS) return

    const next = buildContextFromChain(
      chainData,
      currentContext,
      chainLastUpdate || now
    )
    if (!next) return

    const changed = hasMeaningfulDelta(currentContext, next)
    const keepaliveExpired =
      !currentContext || now - currentContext.lastUpdated >= KEEPALIVE_UPDATE_MS

    if (!changed && !keepaliveExpired) return

    setOptionsContext(next)
    lastPushRef.current = now
  }, [chainData, chainLastUpdate, currentContext, setOptionsContext])

  const fetchFallbackContext = useCallback(async () => {
    if (fallbackFetchInFlightRef.current) return
    if (!apiKey || !underlying || !expiry || !indexExchange) return

    const now = Date.now()
    if (now - lastFallbackFetchRef.current < FALLBACK_FETCH_INTERVAL_MS) return

    const hasFreshChain =
      !!chainData &&
      Array.isArray(chainData.chain) &&
      chainData.chain.length > 0 &&
      (chainIsStreaming || now - (chainLastUpdate || 0) <= STALE_CHAIN_MS)
    if (hasFreshChain) return

    fallbackFetchInFlightRef.current = true
    lastFallbackFetchRef.current = now
    try {
      const response = await fetch('/api/v1/optionchain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          apikey: apiKey,
          underlying,
          exchange: indexExchange,
          expiry_date: normalizeExpiryForApi(expiry),
          strike_count: chainStrikeCount,
        }),
      })

      if (!response.ok) return

      const payload = (await response.json()) as OptionChainResponse
      if (payload.status !== 'success' || !Array.isArray(payload.chain) || payload.chain.length === 0) return

      const next = buildContextFromChain(payload, currentContext, Date.now())
      if (!next) return

      const changed = hasMeaningfulDelta(currentContext, next)
      const keepaliveExpired = !currentContext || Date.now() - currentContext.lastUpdated >= KEEPALIVE_UPDATE_MS
      if (!changed && !keepaliveExpired) return

      setOptionsContext(next)
      lastPushRef.current = Date.now()
    } catch {
      // Keep silent; WS stream remains primary and this is best-effort fallback.
    } finally {
      fallbackFetchInFlightRef.current = false
    }
  }, [
    apiKey,
    underlying,
    expiry,
    indexExchange,
    chainStrikeCount,
    chainData,
    chainIsStreaming,
    chainLastUpdate,
    currentContext,
    setOptionsContext,
  ])

  useEffect(() => {
    const hasFreshChain =
      !!chainData &&
      Array.isArray(chainData.chain) &&
      chainData.chain.length > 0 &&
      (chainIsStreaming || Date.now() - (chainLastUpdate || 0) <= STALE_CHAIN_MS)

    if (hasFreshChain) {
      if (fallbackIntervalRef.current) {
        clearInterval(fallbackIntervalRef.current)
        fallbackIntervalRef.current = null
      }
      return
    }

    void fetchFallbackContext()
    if (fallbackIntervalRef.current) clearInterval(fallbackIntervalRef.current)
    fallbackIntervalRef.current = setInterval(() => {
      void fetchFallbackContext()
    }, FALLBACK_FETCH_INTERVAL_MS)

    return () => {
      if (fallbackIntervalRef.current) {
        clearInterval(fallbackIntervalRef.current)
        fallbackIntervalRef.current = null
      }
    }
  }, [chainData, chainIsStreaming, chainLastUpdate, fetchFallbackContext])
}
