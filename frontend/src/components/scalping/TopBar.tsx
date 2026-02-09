import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { optionChainApi } from '@/api/option-chain'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import type { Underlying } from '@/types/scalping'

const UNDERLYINGS: Underlying[] = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY']
const STRIKE_COUNTS = [10, 15, 20, 25, 30]
const EXPIRY_PLACEHOLDER_VALUE = '__none__'

function areStringListsEqual(a: string[], b: string[]) {
  return a.length === b.length && a.every((value, idx) => value === b[idx])
}

interface TopBarProps {
  liveOpenPnl?: number
  isLivePnl?: boolean
}

export function TopBar({ liveOpenPnl, isLivePnl = false }: TopBarProps) {
  const apiKey = useAuthStore((s) => s.apiKey)
  const broker = useAuthStore((s) => s.user?.broker)

  const underlying = useScalpingStore((s) => s.underlying)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const expiryWeek = useScalpingStore((s) => s.expiryWeek)
  const expiry = useScalpingStore((s) => s.expiry)
  const expiries = useScalpingStore((s) => s.expiries)
  const chainStrikeCount = useScalpingStore((s) => s.chainStrikeCount)
  const paperMode = useScalpingStore((s) => s.paperMode)
  const sessionPnl = useScalpingStore((s) => s.sessionPnl)
  const tradeCount = useScalpingStore((s) => s.tradeCount)

  const setUnderlying = useScalpingStore((s) => s.setUnderlying)
  const setExpiryWeek = useScalpingStore((s) => s.setExpiryWeek)
  const setExpiry = useScalpingStore((s) => s.setExpiry)
  const setExpiries = useScalpingStore((s) => s.setExpiries)
  const setChainStrikeCount = useScalpingStore((s) => s.setChainStrikeCount)
  const setPaperMode = useScalpingStore((s) => s.setPaperMode)

  const displayedPnl = paperMode ? sessionPnl : (liveOpenPnl ?? sessionPnl)
  const expiryValue = expiry && expiries.includes(expiry) ? expiry : EXPIRY_PLACEHOLDER_VALUE

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
    if (expiryData?.status !== 'success') return

    const nextExpiries = expiryData.data ?? []
    if (!areStringListsEqual(expiries, nextExpiries)) {
      setExpiries(nextExpiries)
    }

    if (nextExpiries.length === 0) {
      if (expiry) setExpiry('')
      return
    }

    if (!expiry || !nextExpiries.includes(expiry)) {
      const idx = expiryWeek === 'next' ? Math.min(1, nextExpiries.length - 1) : 0
      const nextExpiry = nextExpiries[idx]
      const nextWeek = idx <= 0 ? 'current' : 'next'
      if (expiry !== nextExpiry) setExpiry(nextExpiry)
      if (expiryWeek !== nextWeek) setExpiryWeek(nextWeek)
    }
  }, [expiryData, expiryWeek, expiries, expiry, setExpiries, setExpiry, setExpiryWeek])

  const handleExpiryChange = (nextExpiry: string) => {
    if (nextExpiry === EXPIRY_PLACEHOLDER_VALUE) return
    if (expiry !== nextExpiry) setExpiry(nextExpiry)
    const idx = expiries.indexOf(nextExpiry)
    const nextWeek = idx <= 0 ? 'current' : 'next'
    if (expiryWeek !== nextWeek) setExpiryWeek(nextWeek)
  }

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b bg-card shrink-0">
      {/* Index selector */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Index</span>
        <Select value={underlying} onValueChange={(value) => setUnderlying(value as Underlying)}>
          <SelectTrigger className="h-7 w-[128px] text-xs font-semibold">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {UNDERLYINGS.map((u) => (
              <SelectItem key={u} value={u}>
                {u}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="w-px h-5 bg-border" />

      {/* Expiry selector */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Expiry</span>
        <Select
          value={expiryValue}
          onValueChange={handleExpiryChange}
          disabled={expiries.length === 0}
        >
          <SelectTrigger className="h-7 w-[122px] text-xs font-mono">
            <SelectValue placeholder="Select expiry" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={EXPIRY_PLACEHOLDER_VALUE} disabled>
              Select expiry
            </SelectItem>
            {expiries.map((value) => (
              <SelectItem key={value} value={value}>
                {value}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="w-px h-5 bg-border" />

      {/* Strike-count selector */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Strikes</span>
        <Select
          value={String(chainStrikeCount)}
          onValueChange={(value) => setChainStrikeCount(Number(value))}
        >
          <SelectTrigger className="h-7 w-[94px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STRIKE_COUNTS.map((count) => (
              <SelectItem key={count} value={String(count)}>
                {count}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex-1" />

      {/* Paper/Live toggle */}
      <div className="flex items-center gap-1.5">
        <div className="inline-flex rounded-md border border-border/60 bg-muted/30 p-0.5">
          <button
            type="button"
            className={`h-6 px-2 text-xs rounded-sm ${
              paperMode ? 'bg-blue-500/20 text-blue-400' : 'text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => setPaperMode(true)}
          >
            Paper
          </button>
          <button
            type="button"
            className={`h-6 px-2 text-xs rounded-sm ${
              !paperMode ? 'bg-green-500/20 text-green-400' : 'text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => setPaperMode(false)}
          >
            Live
          </button>
        </div>
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
            displayedPnl > 0
              ? 'text-green-500'
              : displayedPnl < 0
                ? 'text-red-500'
                : 'text-foreground'
          }`}
        >
          {displayedPnl >= 0 ? '+' : ''}
          {displayedPnl.toFixed(0)}
        </span>
        {!paperMode && (
          <span className={`text-[10px] ${isLivePnl ? 'text-green-500' : 'text-muted-foreground'}`}>
            {isLivePnl ? 'LIVE' : 'REST'}
          </span>
        )}
        {tradeCount > 0 && (
          <span className="text-xs text-muted-foreground">({tradeCount})</span>
        )}
      </div>
    </div>
  )
}
