import { useCallback, useMemo, useRef, useEffect } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useOptionChainLive } from '@/hooks/useOptionChainLive'
import { ScalpingChainRow } from './ScalpingChainRow'
import { Badge } from '@/components/ui/badge'

export function OptionChainPanel() {
  const apiKey = useAuthStore((s) => s.apiKey)

  const underlying = useScalpingStore((s) => s.underlying)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const expiry = useScalpingStore((s) => s.expiry)
  const selectedStrike = useScalpingStore((s) => s.selectedStrike)
  const chainStrikeCount = useScalpingStore((s) => s.chainStrikeCount)

  const setSelectedStrike = useScalpingStore((s) => s.setSelectedStrike)
  const setSelectedSymbols = useScalpingStore((s) => s.setSelectedSymbols)
  const setLotSize = useScalpingStore((s) => s.setLotSize)

  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const atmRowRef = useRef<HTMLDivElement>(null)
  const lotSizeUpdated = useRef(false)

  const {
    data: chainData,
    isLoading,
    isStreaming,
    error,
    streamingSymbols,
  } = useOptionChainLive(
    apiKey,
    underlying,
    indexExchange,
    optionExchange,
    expiry,
    chainStrikeCount,
    {
      enabled: !!apiKey && !!expiry,
      oiRefreshInterval: 0,
      wsMode: 'Quote',
    }
  )

  // Extract lot size from chain data (once per expiry change)
  useEffect(() => {
    if (!chainData?.chain?.length) return
    if (lotSizeUpdated.current) return

    const firstRow = chainData.chain[0]
    const apiLotSize = firstRow.ce?.lotsize ?? firstRow.pe?.lotsize
    if (apiLotSize && apiLotSize > 0) {
      setLotSize(apiLotSize)
      lotSizeUpdated.current = true
    }
  }, [chainData, setLotSize])

  // Reset lot size flag when expiry or underlying changes
  useEffect(() => {
    lotSizeUpdated.current = false
  }, [underlying, expiry])

  // Auto-scroll to ATM on first load
  useEffect(() => {
    if (chainData?.chain && atmRowRef.current && scrollContainerRef.current) {
      const container = scrollContainerRef.current
      const atm = atmRowRef.current
      const containerRect = container.getBoundingClientRect()
      const atmRect = atm.getBoundingClientRect()
      const offset = atmRect.top - containerRect.top - containerRect.height / 2 + atmRect.height / 2
      container.scrollTop += offset
    }
  }, [chainData?.atm_strike])

  const handleSelectStrike = useCallback(
    (strike: number, ceSymbol: string | null, peSymbol: string | null) => {
      setSelectedStrike(strike)
      setSelectedSymbols(ceSymbol, peSymbol)
    },
    [setSelectedStrike, setSelectedSymbols]
  )

  // Compute max OI and max Volume across all strikes for heatmap scaling
  const { maxOI, maxVol } = useMemo(() => {
    if (!chainData?.chain?.length) return { maxOI: 1, maxVol: 1 }
    let mOI = 0
    let mVol = 0
    for (const row of chainData.chain) {
      if (row.ce?.oi && row.ce.oi > mOI) mOI = row.ce.oi
      if (row.pe?.oi && row.pe.oi > mOI) mOI = row.pe.oi
      if (row.ce?.volume && row.ce.volume > mVol) mVol = row.ce.volume
      if (row.pe?.volume && row.pe.volume > mVol) mVol = row.pe.volume
    }
    return { maxOI: mOI || 1, maxVol: mVol || 1 }
  }, [chainData?.chain])

  const spotPrice = chainData?.underlying_ltp
  const atmStrike = chainData?.atm_strike

  return (
    <div className="flex flex-col h-full bg-card">
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1 border-b shrink-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-bold">{underlying}</span>
          <Badge variant="outline" className="text-[10px] h-4 px-1">
            {chainStrikeCount} strikes
          </Badge>
          {spotPrice != null && (
            <span className="text-sm tabular-nums font-mono text-muted-foreground">
              {spotPrice.toFixed(2)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {isStreaming && (
            <Badge variant="outline" className="text-[10px] h-4 px-1 gap-0.5 text-green-500 border-green-500/30">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              {streamingSymbols}
            </Badge>
          )}
          {isLoading && (
            <Badge variant="outline" className="text-[10px] h-4 px-1">
              Loading...
            </Badge>
          )}
        </div>
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-[1fr_1fr_1fr_50px_1fr_1fr_1fr] items-center gap-0 px-0 py-0.5 text-[10px] text-muted-foreground font-medium border-b bg-muted/30 shrink-0">
        <div className="text-right px-1.5">OI (L)</div>
        <div className="text-right px-1.5">Vol (L)</div>
        <div className="text-right px-1.5">CE LTP</div>
        <div className="text-center">Strike</div>
        <div className="text-left px-1.5">PE LTP</div>
        <div className="text-left px-1.5">Vol (L)</div>
        <div className="text-left px-1.5">OI (L)</div>
      </div>

      {/* Chain rows */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto overflow-x-hidden">
        {error && (
          <div className="p-4 text-center text-sm text-destructive">{error}</div>
        )}

        {!error && !isLoading && chainData?.chain?.length === 0 && (
          <div className="p-4 text-center text-sm text-muted-foreground">
            No data available
          </div>
        )}

        {chainData?.chain?.map((row) => {
          const isATM = row.strike === atmStrike
          return (
            <div key={row.strike} ref={isATM ? atmRowRef : undefined}>
              <ScalpingChainRow
                strike={row.strike}
                ce={row.ce}
                pe={row.pe}
                isATM={isATM}
                isSelected={row.strike === selectedStrike}
                maxOI={maxOI}
                maxVol={maxVol}
                onSelectStrike={handleSelectStrike}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}
