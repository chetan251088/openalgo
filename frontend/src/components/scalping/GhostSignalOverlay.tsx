import { useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'
import type { GhostSignal } from '@/types/scalping'
import type { PlaceOrderRequest } from '@/types/trading'

export function GhostSignalOverlay() {
  const ghostSignals = useAutoTradeStore((s) => s.ghostSignals)
  const clearGhostSignals = useAutoTradeStore((s) => s.clearGhostSignals)

  if (ghostSignals.length === 0) {
    return (
      <div className="text-center text-[10px] text-muted-foreground py-2">
        No ghost signals yet. Engine is analyzing...
      </div>
    )
  }

  const recent = ghostSignals.slice(-10).reverse()

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium text-muted-foreground">
          Ghost Signals ({ghostSignals.length})
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-4 text-[9px] px-1"
          onClick={clearGhostSignals}
        >
          Clear
        </Button>
      </div>

      {recent.map((signal) => (
        <GhostSignalCard key={signal.id} signal={signal} />
      ))}
    </div>
  )
}

function GhostSignalCard({ signal }: { signal: GhostSignal }) {
  const apiKey = useAuthStore((s) => s.apiKey)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const product = useScalpingStore((s) => s.product)
  const paperMode = useScalpingStore((s) => s.paperMode)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)

  const takeSignal = useCallback(async () => {
    if (paperMode) {
      console.log(`[Ghost Take] ${signal.action} ${signal.side} ${signal.symbol}`)
      incrementTradeCount()
      return
    }

    if (!apiKey) return

    const order: PlaceOrderRequest = {
      apikey: apiKey,
      strategy: 'Scalping-Ghost',
      exchange: optionExchange,
      symbol: signal.symbol,
      action: signal.action,
      quantity: quantity * lotSize,
      pricetype: 'MARKET',
      product,
    }

    try {
      const res = await tradingApi.placeOrder(order)
      if (res.status === 'success') {
        console.log(`[Ghost Take] ${signal.action} ${signal.symbol} id=${res.data?.orderid}`)
        incrementTradeCount()
      }
    } catch (err) {
      console.error('[Ghost Take] Failed:', err)
    }
  }, [signal, apiKey, optionExchange, quantity, lotSize, product, paperMode, incrementTradeCount])

  const age = Math.round((Date.now() - signal.timestamp) / 1000)
  const ageStr = age < 60 ? `${age}s ago` : `${Math.round(age / 60)}m ago`

  return (
    <div className="p-1.5 rounded border border-border/50 bg-muted/30 space-y-0.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <Badge
            variant={signal.side === 'CE' ? 'default' : 'destructive'}
            className="text-[9px] h-3.5 px-1"
          >
            {signal.side}
          </Badge>
          <span className="text-[10px] font-mono">{signal.symbol.slice(-10)}</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="text-[9px] text-muted-foreground">{ageStr}</span>
          <Badge variant="outline" className="text-[9px] h-3.5 px-1">
            {signal.score}/10
          </Badge>
        </div>
      </div>

      <p className="text-[10px] text-muted-foreground">{signal.reason}</p>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 text-[9px] text-muted-foreground">
          {signal.regime && <span>Regime: {signal.regime}</span>}
          {signal.pcr !== undefined && <span>PCR: {signal.pcr.toFixed(2)}</span>}
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-5 text-[9px] px-1.5"
          onClick={takeSignal}
        >
          Take Trade
        </Button>
      </div>
    </div>
  )
}
