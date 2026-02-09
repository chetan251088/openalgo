import { Badge } from '@/components/ui/badge'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useDepth20 } from '@/hooks/useDepth20'

export function DepthScoutTab() {
  const activeSide = useScalpingStore((s) => s.activeSide)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const symbol = useScalpingStore((s) =>
    s.activeSide === 'CE' ? s.selectedCESymbol : s.selectedPESymbol
  )

  const { depth, analytics, levels, isLoading } = useDepth20(symbol, optionExchange)

  if (!symbol) {
    return (
      <div className="flex-1 flex items-center justify-center p-4">
        <span className="text-xs text-muted-foreground">Select a strike to view depth</span>
      </div>
    )
  }

  return (
    <div className="p-2 space-y-3 text-xs">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className={activeSide === 'CE' ? 'text-green-500 font-bold' : 'text-red-500 font-bold'}>
            {activeSide}
          </span>
          <span className="font-mono text-muted-foreground">{symbol.slice(-10)}</span>
        </div>
        <Badge variant="outline" className="text-[10px] h-4">
          {levels}-Level
        </Badge>
      </div>

      {/* Depth bars */}
      {isLoading && !depth ? (
        <div className="text-muted-foreground text-center py-4">Loading depth...</div>
      ) : depth ? (
        <>
          {/* Order book */}
          <div className="space-y-0.5">
            <div className="grid grid-cols-[1fr_60px_60px_1fr] gap-0.5 text-[10px] text-muted-foreground font-medium mb-1">
              <span className="text-right">Bid Qty</span>
              <span className="text-center">Bid</span>
              <span className="text-center">Ask</span>
              <span>Ask Qty</span>
            </div>

            {Array.from({ length: Math.max((depth.bids?.length ?? 0), (depth.asks?.length ?? 0)) }).map((_, i) => {
              const bid = depth.bids?.[i]
              const ask = depth.asks?.[i]
              const maxQty = Math.max(
                ...(depth.bids?.map((b) => b.quantity) ?? [0]),
                ...(depth.asks?.map((a) => a.quantity) ?? [0])
              )

              return (
                <div key={i} className="grid grid-cols-[1fr_60px_60px_1fr] gap-0.5 items-center">
                  {/* Bid bar */}
                  <div className="flex items-center justify-end">
                    <div className="relative h-4 w-full">
                      {bid && (
                        <>
                          <div
                            className="absolute right-0 top-0 h-full bg-green-500/20 rounded-l"
                            style={{ width: `${maxQty > 0 ? (bid.quantity / maxQty) * 100 : 0}%` }}
                          />
                          <span className="absolute right-1 top-0 h-full flex items-center text-[10px] tabular-nums text-green-600">
                            {bid.quantity.toLocaleString()}
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Bid price */}
                  <span className="text-center text-[10px] font-mono tabular-nums text-green-500">
                    {bid?.price.toFixed(2) ?? '--'}
                  </span>

                  {/* Ask price */}
                  <span className="text-center text-[10px] font-mono tabular-nums text-red-500">
                    {ask?.price.toFixed(2) ?? '--'}
                  </span>

                  {/* Ask bar */}
                  <div className="flex items-center">
                    <div className="relative h-4 w-full">
                      {ask && (
                        <>
                          <div
                            className="absolute left-0 top-0 h-full bg-red-500/20 rounded-r"
                            style={{ width: `${maxQty > 0 ? (ask.quantity / maxQty) * 100 : 0}%` }}
                          />
                          <span className="absolute left-1 top-0 h-full flex items-center text-[10px] tabular-nums text-red-600">
                            {ask.quantity.toLocaleString()}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Analytics */}
          {analytics && (
            <div className="border-t pt-2 space-y-1.5">
              <div className="text-[10px] font-medium text-muted-foreground uppercase">Analytics</div>

              {/* Bid/Ask ratio */}
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Bid/Ask Ratio</span>
                <span className={`font-bold tabular-nums ${analytics.bidAskRatio > 1 ? 'text-green-500' : 'text-red-500'}`}>
                  {analytics.bidAskRatio.toFixed(2)}
                </span>
              </div>

              {/* Spread */}
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Spread</span>
                <span
                  className={`font-bold tabular-nums ${
                    analytics.spread < 2 ? 'text-green-500' : analytics.spread < 5 ? 'text-yellow-500' : 'text-red-500'
                  }`}
                >
                  {analytics.spread.toFixed(2)}
                </span>
              </div>

              {/* Imbalance */}
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Imbalance</span>
                <div className="flex items-center gap-1">
                  <div className="w-16 h-2 bg-muted rounded overflow-hidden">
                    <div
                      className={`h-full ${analytics.imbalanceScore > 0 ? 'bg-green-500' : 'bg-red-500'}`}
                      style={{
                        width: `${Math.abs(analytics.imbalanceScore)}%`,
                        marginLeft: analytics.imbalanceScore < 0 ? `${100 - Math.abs(analytics.imbalanceScore)}%` : '50%',
                      }}
                    />
                  </div>
                  <span className={`text-[10px] font-bold tabular-nums ${analytics.imbalanceScore > 0 ? 'text-green-500' : 'text-red-500'}`}>
                    {analytics.imbalanceScore > 0 ? '+' : ''}{analytics.imbalanceScore.toFixed(0)}%
                  </span>
                </div>
              </div>

              {/* Walls */}
              {analytics.largestBidWall && (
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Bid Wall</span>
                  <span className="text-green-500 font-mono text-[10px]">
                    {analytics.largestBidWall.qty.toLocaleString()} @ {analytics.largestBidWall.price.toFixed(2)}
                  </span>
                </div>
              )}
              {analytics.largestAskWall && (
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Ask Wall</span>
                  <span className="text-red-500 font-mono text-[10px]">
                    {analytics.largestAskWall.qty.toLocaleString()} @ {analytics.largestAskWall.price.toFixed(2)}
                  </span>
                </div>
              )}

              {/* Total volumes */}
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-green-500">Total Bid: {analytics.totalBidQty.toLocaleString()}</span>
                <span className="text-red-500">Total Ask: {analytics.totalAskQty.toLocaleString()}</span>
              </div>
            </div>
          )}

          {/* LTP / Volume */}
          <div className="border-t pt-2 flex items-center justify-between text-[10px]">
            <span>LTP: <span className="font-bold">{depth.ltp?.toFixed(2)}</span></span>
            <span>Vol: <span className="font-bold">{depth.volume?.toLocaleString()}</span></span>
            <span>OI: <span className="font-bold">{depth.oi?.toLocaleString()}</span></span>
          </div>
        </>
      ) : (
        <div className="text-muted-foreground text-center py-4">No depth data</div>
      )}
    </div>
  )
}
