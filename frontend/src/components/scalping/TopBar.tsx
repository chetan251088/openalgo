import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { optionChainApi } from '@/api/option-chain'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import type { Underlying } from '@/types/scalping'

const UNDERLYINGS: Underlying[] = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY']

export function TopBar() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const broker = useAuthStore((s) => s.user?.broker)

  const underlying = useScalpingStore((s) => s.underlying)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const expiryWeek = useScalpingStore((s) => s.expiryWeek)
  const expiry = useScalpingStore((s) => s.expiry)
  const expiries = useScalpingStore((s) => s.expiries)
  const paperMode = useScalpingStore((s) => s.paperMode)
  const sessionPnl = useScalpingStore((s) => s.sessionPnl)
  const tradeCount = useScalpingStore((s) => s.tradeCount)

  const setUnderlying = useScalpingStore((s) => s.setUnderlying)
  const setExpiryWeek = useScalpingStore((s) => s.setExpiryWeek)
  const setExpiry = useScalpingStore((s) => s.setExpiry)
  const setExpiries = useScalpingStore((s) => s.setExpiries)
  const setPaperMode = useScalpingStore((s) => s.setPaperMode)

  // Fetch expiries when underlying changes
  const { data: expiryData } = useQuery({
    queryKey: ['scalping-expiries', underlying, optionExchange],
    queryFn: () =>
      optionChainApi.getExpiries(apiKey!, underlying, optionExchange),
    enabled: !!apiKey,
    staleTime: 5 * 60 * 1000,
  })

  // Update expiries and auto-select
  useEffect(() => {
    if (expiryData?.status === 'success' && expiryData.data.length > 0) {
      setExpiries(expiryData.data)
      const idx = expiryWeek === 'current' ? 0 : Math.min(1, expiryData.data.length - 1)
      setExpiry(expiryData.data[idx])
    }
  }, [expiryData, expiryWeek, setExpiries, setExpiry])

  const handleExpiryWeekToggle = (week: 'current' | 'next') => {
    setExpiryWeek(week)
    if (expiries.length > 0) {
      const idx = week === 'current' ? 0 : Math.min(1, expiries.length - 1)
      setExpiry(expiries[idx])
    }
  }

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b bg-card shrink-0">
      {/* Underlying selector */}
      <div className="flex items-center gap-1">
        {UNDERLYINGS.map((u) => (
          <Button
            key={u}
            variant={underlying === u ? 'default' : 'ghost'}
            size="sm"
            className="h-7 px-2.5 text-xs font-semibold"
            onClick={() => setUnderlying(u)}
          >
            {u}
          </Button>
        ))}
      </div>

      <div className="w-px h-5 bg-border" />

      {/* Expiry week toggle */}
      <div className="flex items-center gap-1">
        <Button
          variant={expiryWeek === 'current' ? 'secondary' : 'ghost'}
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => handleExpiryWeekToggle('current')}
        >
          CurWk
        </Button>
        <Button
          variant={expiryWeek === 'next' ? 'secondary' : 'ghost'}
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => handleExpiryWeekToggle('next')}
        >
          NxtWk
        </Button>
      </div>

      {expiry && (
        <span className="text-xs text-muted-foreground font-mono">{expiry}</span>
      )}

      <div className="flex-1" />

      {/* Paper/Live toggle */}
      <div className="flex items-center gap-1.5">
        <span className={`text-xs font-medium ${paperMode ? 'text-blue-400' : 'text-muted-foreground'}`}>
          Paper
        </span>
        <Switch
          checked={!paperMode}
          onCheckedChange={(checked) => setPaperMode(!checked)}
          className="h-5 w-9"
        />
        <span className={`text-xs font-medium ${!paperMode ? 'text-green-400' : 'text-muted-foreground'}`}>
          Live
        </span>
      </div>

      <div className="w-px h-5 bg-border" />

      {/* Broker badge */}
      {broker && (
        <Badge variant="outline" className="text-xs h-6">
          {broker}
        </Badge>
      )}

      <div className="w-px h-5 bg-border" />

      {/* Session P&L */}
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground">P&L:</span>
        <span
          className={`text-sm font-bold tabular-nums ${
            sessionPnl > 0
              ? 'text-green-500'
              : sessionPnl < 0
                ? 'text-red-500'
                : 'text-foreground'
          }`}
        >
          {sessionPnl >= 0 ? '+' : ''}
          {sessionPnl.toFixed(0)}
        </span>
        {tradeCount > 0 && (
          <span className="text-xs text-muted-foreground">({tradeCount})</span>
        )}
      </div>
    </div>
  )
}
