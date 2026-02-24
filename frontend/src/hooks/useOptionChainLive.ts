import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { OptionChainResponse, OptionStrike } from '@/types/option-chain'
import { useOptionChainPolling } from './useOptionChainPolling'
import { useMarketData, type SymbolData } from './useMarketData'

// Index symbols that use NSE_INDEX/BSE_INDEX for quotes (matches backend lists)
const NSE_INDEX_SYMBOLS = new Set([
  'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY',
  'NIFTYNXT50', 'NIFTYIT', 'NIFTYPHARMA', 'NIFTYBANK',
])
const BSE_INDEX_SYMBOLS = new Set(['SENSEX', 'BANKEX', 'SENSEX50'])

function getUnderlyingExchange(symbol: string, optionExchange: string): string {
  if (NSE_INDEX_SYMBOLS.has(symbol)) return 'NSE_INDEX'
  if (BSE_INDEX_SYMBOLS.has(symbol)) return 'BSE_INDEX'
  return optionExchange === 'BFO' ? 'BSE' : 'NSE'
}

// Round price to nearest tick size (e.g., 0.05 for options)
// Fixes broker WebSocket data that may not be aligned to tick size
function roundToTickSize(price: number | undefined, tickSize: number | undefined): number | undefined {
  if (price === undefined || price === null) return undefined
  if (!tickSize || tickSize <= 0) return price
  // Round to nearest tick and fix floating point precision
  return Number((Math.round(price / tickSize) * tickSize).toFixed(2))
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim().length > 0) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function getExchangeLookupCandidates(exchange: string): string[] {
  const normalized = (exchange || '').trim().toUpperCase()
  const candidates = new Set<string>()
  if (normalized) candidates.add(normalized)

  if (normalized === 'NSE_INDEX') candidates.add('NSE')
  else if (normalized === 'BSE_INDEX') candidates.add('BSE')
  else if (normalized === 'NSE') candidates.add('NSE_INDEX')
  else if (normalized === 'BSE') candidates.add('BSE_INDEX')

  return Array.from(candidates)
}

function getSymbolDataFromWs(
  wsData: Map<string, SymbolData>,
  symbolIndex: Map<string, SymbolData[]>,
  symbol: string,
  preferredExchange: string
): SymbolData | undefined {
  const symbolKey = (symbol || '').trim().toUpperCase()
  if (!symbolKey) return undefined

  const exchangeCandidates = getExchangeLookupCandidates(preferredExchange)
  for (const exchange of exchangeCandidates) {
    const direct = wsData.get(`${exchange}:${symbolKey}`)
    if (direct) return direct
  }

  const symbolCandidates = symbolIndex.get(symbolKey)
  if (!symbolCandidates || symbolCandidates.length === 0) {
    return undefined
  }

  for (const exchange of exchangeCandidates) {
    const matched = symbolCandidates.find((entry) => entry.exchange.toUpperCase() === exchange)
    if (matched) return matched
  }

  return symbolCandidates[0]
}

function resolveAtmStrike(
  chain: OptionStrike[],
  underlyingLtp: number | null,
  fallbackAtmStrike: number
): number {
  if (!chain.length || underlyingLtp == null || underlyingLtp <= 0) {
    return fallbackAtmStrike
  }

  let nearest = chain[0].strike
  let bestDistance = Math.abs(chain[0].strike - underlyingLtp)
  for (const row of chain) {
    const distance = Math.abs(row.strike - underlyingLtp)
    if (distance < bestDistance) {
      nearest = row.strike
      bestDistance = distance
    }
  }
  return nearest
}

interface UseOptionChainLiveOptions {
  enabled: boolean
  /** Polling interval for OI/Volume data in ms (default: 30000) */
  oiRefreshInterval?: number
  /** WebSocket mode: Quote gives LTP+volume updates, LTP is lighter */
  wsMode?: 'LTP' | 'Quote'
  /** Pause WebSocket and polling when tab is hidden (default: true) */
  pauseWhenHidden?: boolean
}

/** Merge throttle interval in ms */
const MERGE_INTERVAL = 100

/**
 * Hook for real-time option chain data using hybrid approach:
 * - WebSocket for real-time LTP updates (LTP mode for performance)
 * - REST polling for OI/Volume data (less frequent)
 *
 * Performance optimizations:
 * - LTP mode instead of Depth (chain only shows LTP, not bid/ask)
 * - Merge throttled to 100ms via ref + setTimeout
 * - rAF-batched WebSocket updates from useMarketData
 */
export function useOptionChainLive(
  apiKey: string | null,
  underlying: string,
  exchange: string,
  optionExchange: string,
  expiryDate: string,
  strikeCount: number,
  options: UseOptionChainLiveOptions = { enabled: true, oiRefreshInterval: 30000, pauseWhenHidden: true }
) {
  const optionExchangeKey = optionExchange.toUpperCase()
  const underlyingSymbolKey = underlying.toUpperCase()

  const {
    enabled,
    oiRefreshInterval = 30000,
    wsMode = 'LTP',
    pauseWhenHidden = true,
  } = options

  // Track merged data with WebSocket updates
  const [mergedData, setMergedData] = useState<OptionChainResponse | null>(null)
  const [lastLtpUpdate, setLastLtpUpdate] = useState<Date | null>(null)

  // Polling for OI/Volume/Greeks (less frequent)
  const {
    data: polledData,
    isLoading,
    isConnected: isPollingConnected,
    isPaused: isPollingPaused,
    error,
    lastUpdate: lastPollUpdate,
    refetch,
  } = useOptionChainPolling(apiKey, underlying, exchange, expiryDate, strikeCount, {
    enabled,
    refreshInterval: oiRefreshInterval,
    pauseWhenHidden,
  })

  // Build symbol list from polled data for WebSocket subscription
  // Includes both option symbols AND underlying index for real-time spot price
  const wsSymbols = useMemo(() => {
    const symbols: Array<{ symbol: string; exchange: string }> = []

    // Add underlying symbol for real-time spot price
    // Use correct exchange based on whether it's an index or stock
    const underlyingExch = getUnderlyingExchange(underlying, optionExchange)
    symbols.push({ symbol: underlying, exchange: underlyingExch })

    // Add all option symbols
    if (polledData?.chain) {
      for (const strike of polledData.chain) {
        if (strike.ce?.symbol) {
          symbols.push({ symbol: strike.ce.symbol, exchange: optionExchange })
        }
        if (strike.pe?.symbol) {
          symbols.push({ symbol: strike.pe.symbol, exchange: optionExchange })
        }
      }
    }

    return symbols
  }, [polledData?.chain, optionExchange, underlying])

  // WebSocket for real-time LTP updates (LTP mode = less data per tick)
  const {
    data: wsData,
    isConnected: isWsConnected,
    isAuthenticated: isWsAuthenticated,
    isPaused: isWsPaused,
  } = useMarketData({
    symbols: wsSymbols,
    mode: wsMode,
    enabled: enabled && wsSymbols.length > 0,
  })

  // Track last LTP update time using ref to avoid triggering effect loops
  const lastLtpUpdateRef = useRef<number>(0)

  // Throttled merge: accumulate wsData changes, merge at most every MERGE_INTERVAL ms
  const mergeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latestWsDataRef = useRef(wsData)
  const latestPolledDataRef = useRef(polledData)

  // Keep refs in sync
  latestWsDataRef.current = wsData
  latestPolledDataRef.current = polledData

  // The actual merge function (called from throttled timer)
  const doMerge = useMemo(() => {
    return () => {
      mergeTimerRef.current = null
      const currentPolled = latestPolledDataRef.current
      const currentWs = latestWsDataRef.current

      if (!currentPolled) {
        setMergedData(null)
        return
      }

      // If no WebSocket data yet, use polled data as-is
      if (currentWs.size === 0) {
        setMergedData(currentPolled)
        return
      }

      const wsSymbolIndex = new Map<string, SymbolData[]>()
      for (const [, symbolData] of currentWs) {
        const symbolKey = symbolData.symbol.toUpperCase()
        const existing = wsSymbolIndex.get(symbolKey)
        if (existing) existing.push(symbolData)
        else wsSymbolIndex.set(symbolKey, [symbolData])
      }

      // Create merged chain with WebSocket LTP updates
      const mergedChain: OptionStrike[] = currentPolled.chain.map((strike) => {
        let ceChanged = false
        let peChanged = false
        let ceLtp = strike.ce?.ltp
        let peLtp = strike.pe?.ltp
        let ceVolume = strike.ce?.volume
        let peVolume = strike.pe?.volume
        let ceOi = strike.ce?.oi
        let peOi = strike.pe?.oi

        // Update CE LTP from WebSocket
        if (strike.ce?.symbol) {
          const wsSymbolData = getSymbolDataFromWs(
            currentWs,
            wsSymbolIndex,
            strike.ce.symbol,
            optionExchangeKey
          )
          const wsPayload = wsSymbolData?.data
          if (wsPayload?.ltp !== undefined) {
            const rounded = roundToTickSize(wsPayload.ltp, strike.ce.tick_size)
            if (rounded !== undefined && rounded !== strike.ce.ltp) {
              ceLtp = rounded
              ceChanged = true
            }
          }
          if (wsPayload?.volume !== undefined && wsPayload.volume !== strike.ce.volume) {
            ceVolume = wsPayload.volume
            ceChanged = true
          }
          const wsOi = wsPayload?.oi ?? wsPayload?.open_interest
          if (wsOi !== undefined && wsOi !== strike.ce.oi) {
            ceOi = wsOi
            ceChanged = true
          }
        }

        // Update PE LTP from WebSocket
        if (strike.pe?.symbol) {
          const wsSymbolData = getSymbolDataFromWs(
            currentWs,
            wsSymbolIndex,
            strike.pe.symbol,
            optionExchangeKey
          )
          const wsPayload = wsSymbolData?.data
          if (wsPayload?.ltp !== undefined) {
            const rounded = roundToTickSize(wsPayload.ltp, strike.pe.tick_size)
            if (rounded !== undefined && rounded !== strike.pe.ltp) {
              peLtp = rounded
              peChanged = true
            }
          }
          if (wsPayload?.volume !== undefined && wsPayload.volume !== strike.pe.volume) {
            peVolume = wsPayload.volume
            peChanged = true
          }
          const wsOi = wsPayload?.oi ?? wsPayload?.open_interest
          if (wsOi !== undefined && wsOi !== strike.pe.oi) {
            peOi = wsOi
            peChanged = true
          }
        }

        // Only create new object if something actually changed
        if (!ceChanged && !peChanged) return strike

        const newStrike = { ...strike }
        if (ceChanged && strike.ce) {
          newStrike.ce = {
            ...strike.ce,
            ...(ceLtp !== undefined ? { ltp: ceLtp } : {}),
            ...(ceVolume !== undefined ? { volume: ceVolume } : {}),
            ...(ceOi !== undefined ? { oi: ceOi } : {}),
          }
        }
        if (peChanged && strike.pe) {
          newStrike.pe = {
            ...strike.pe,
            ...(peLtp !== undefined ? { ltp: peLtp } : {}),
            ...(peVolume !== undefined ? { volume: peVolume } : {}),
            ...(peOi !== undefined ? { oi: peOi } : {}),
          }
        }
        return newStrike
      })

      // Check if any LTP was updated
      let hasLtpUpdate = false
      for (const [, symbolData] of currentWs) {
        if (symbolData.lastUpdate && symbolData.lastUpdate > lastLtpUpdateRef.current) {
          hasLtpUpdate = true
          lastLtpUpdateRef.current = symbolData.lastUpdate
          break
        }
      }

      if (hasLtpUpdate) {
        setLastLtpUpdate(new Date())
      }

      // Get real-time underlying spot price from WebSocket
      const underlyingExch = getUnderlyingExchange(underlyingSymbolKey, optionExchangeKey).toUpperCase()
      const underlyingWsData = getSymbolDataFromWs(
        currentWs,
        wsSymbolIndex,
        underlyingSymbolKey,
        underlyingExch
      )
      const underlyingLtp =
        toFiniteNumber(underlyingWsData?.data?.ltp) ??
        toFiniteNumber(currentPolled.underlying_ltp) ??
        currentPolled.underlying_ltp
      const atmStrike = resolveAtmStrike(
        mergedChain,
        toFiniteNumber(underlyingLtp),
        currentPolled.atm_strike
      )

      setMergedData({
        ...currentPolled,
        underlying_ltp: underlyingLtp,
        atm_strike: atmStrike,
        chain: mergedChain,
      })
    }
  }, [optionExchangeKey, underlyingSymbolKey])

  const clearMergeTimer = useCallback(() => {
    if (mergeTimerRef.current !== null) {
      clearTimeout(mergeTimerRef.current)
      mergeTimerRef.current = null
    }
  }, [])

  // Trigger throttled merge when wsData or polledData changes
  useEffect(() => {
    if (!polledData) {
      clearMergeTimer()
      setMergedData(null)
      return
    }

    // If no WS data, set polled immediately (first load)
    if (wsData.size === 0) {
      clearMergeTimer()
      setMergedData(polledData)
      return
    }

    // Schedule merge if not already scheduled
    if (mergeTimerRef.current === null) {
      mergeTimerRef.current = setTimeout(doMerge, MERGE_INTERVAL)
    }
  }, [clearMergeTimer, polledData, wsData, doMerge])

  // Cleanup pending merge timer on unmount.
  useEffect(() => clearMergeTimer, [clearMergeTimer])

  // Determine streaming status
  const isStreaming = isWsConnected && isWsAuthenticated && wsSymbols.length > 0
  const isPaused = isPollingPaused || isWsPaused

  // Combined last update (use LTP update if more recent)
  const lastUpdate = useMemo(() => {
    if (!lastPollUpdate && !lastLtpUpdate) return null
    if (!lastPollUpdate) return lastLtpUpdate
    if (!lastLtpUpdate) return lastPollUpdate
    return lastLtpUpdate > lastPollUpdate ? lastLtpUpdate : lastPollUpdate
  }, [lastPollUpdate, lastLtpUpdate])

  return {
    data: mergedData,
    isLoading,
    isConnected: isPollingConnected,
    isStreaming,
    isPaused,
    error,
    lastUpdate,
    streamingSymbols: wsSymbols.length,
    refetch,
  }
}
