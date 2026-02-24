import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useMarketClock } from '@/hooks/useMarketClock'
import { useTradeLogger } from '@/hooks/useTradeLogger'

interface RiskPanelProps {
  liveOpenPnl?: number
  isLivePnl?: boolean
}

export function RiskPanel({ liveOpenPnl, isLivePnl = false }: RiskPanelProps) {
  const sessionPnl = useScalpingStore((s) => s.sessionPnl)
  const tradeCount = useScalpingStore((s) => s.tradeCount)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const displayedPnl = paperMode ? sessionPnl : (liveOpenPnl ?? sessionPnl)

  const clock = useMarketClock()
  const { getStats } = useTradeLogger()
  const stats = getStats()

  // Local risk config (will be moved to a store in Phase 5)
  const [dailyLossLimit, setDailyLossLimit] = useState(5000)
  const [maxLossPerTrade, setMaxLossPerTrade] = useState(500)
  const [profitProtectPct, setProfitProtectPct] = useState(50)
  const [coolingOffTrades, setCoolingOffTrades] = useState(3)

  const isLimitHit = displayedPnl < 0 && Math.abs(displayedPnl) >= dailyLossLimit

  return (
    <div className="p-2 space-y-3 text-xs">
      {/* Market Clock */}
      <section className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="font-medium text-foreground">Market Clock</span>
          <span className="font-mono text-sm font-bold tabular-nums">{clock.currentTime} IST</span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Status</span>
          <Badge
            variant={clock.isOpen ? 'default' : 'secondary'}
            className="text-[10px] h-4"
          >
            {clock.isOpen ? 'OPEN' : 'CLOSED'}
          </Badge>
        </div>

        {clock.currentZone && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Zone</span>
            <span className="font-medium">{clock.currentZone.label}</span>
          </div>
        )}

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Sensitivity</span>
          <div className="flex items-center gap-1">
            <div className="w-12 h-1.5 bg-muted rounded overflow-hidden">
              <div
                className={`h-full rounded ${
                  clock.sensitivity >= 1.5
                    ? 'bg-red-500'
                    : clock.sensitivity >= 1.0
                      ? 'bg-yellow-500'
                      : 'bg-green-500'
                }`}
                style={{ width: `${Math.min(100, clock.sensitivity * 50)}%` }}
              />
            </div>
            <span className="tabular-nums">{clock.sensitivity.toFixed(1)}x</span>
          </div>
        </div>

        {clock.nextZone && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Next Zone</span>
            <span>
              {clock.nextZone.label}{' '}
              <span className="text-muted-foreground">in {clock.minutesToNext}m</span>
            </span>
          </div>
        )}

        {clock.isExpiryDay && (
          <Badge variant="destructive" className="text-[10px] h-4">
            EXPIRY DAY
          </Badge>
        )}
      </section>

      {/* Risk Limits */}
      <section className="border-t pt-2 space-y-2">
        <span className="font-medium text-foreground">Risk Limits</span>

        {isLimitHit && (
          <div className="p-1.5 bg-red-500/10 border border-red-500/30 rounded text-red-500 font-medium">
            Daily loss limit reached! Trading disabled.
          </div>
        )}

        <div className="space-y-1">
          <Label className="text-[10px]">Daily Loss Limit</Label>
          <Input
            type="number"
            value={dailyLossLimit}
            onChange={(e) => setDailyLossLimit(Number(e.target.value) || 0)}
            className="h-6 text-xs"
          />
        </div>

        <div className="space-y-1">
          <Label className="text-[10px]">Max Loss Per Trade (pts)</Label>
          <Input
            type="number"
            value={maxLossPerTrade}
            onChange={(e) => setMaxLossPerTrade(Number(e.target.value) || 0)}
            className="h-6 text-xs"
          />
        </div>

        <div className="space-y-1">
          <Label className="text-[10px]">Profit Protection (%)</Label>
          <Input
            type="number"
            value={profitProtectPct}
            onChange={(e) => setProfitProtectPct(Number(e.target.value) || 0)}
            className="h-6 text-xs"
          />
        </div>

        <div className="space-y-1">
          <Label className="text-[10px]">Cooling Off After N Losses</Label>
          <Input
            type="number"
            value={coolingOffTrades}
            onChange={(e) => setCoolingOffTrades(Number(e.target.value) || 0)}
            className="h-6 text-xs"
          />
        </div>
      </section>

      {/* Session Stats */}
      <section className="border-t pt-2 space-y-1.5">
        <span className="font-medium text-foreground">Session Stats</span>

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Trades Today</span>
          <span className="font-bold">{tradeCount}</span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Session P&L</span>
          <div className="flex items-center gap-1.5">
            <span
              className={`font-bold tabular-nums ${
                displayedPnl > 0 ? 'text-green-500' : displayedPnl < 0 ? 'text-red-500' : ''
              }`}
            >
              {displayedPnl >= 0 ? '+' : ''}{displayedPnl.toFixed(0)}
            </span>
            {!paperMode && (
              <span className={`text-[10px] ${isLivePnl ? 'text-green-500' : 'text-muted-foreground'}`}>
                {isLivePnl ? 'LIVE' : 'REST'}
              </span>
            )}
          </div>
        </div>

        {stats.count > 0 && (
          <>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Win Rate</span>
              <span className="font-bold">{stats.winRate.toFixed(0)}%</span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Avg P&L</span>
              <span
                className={`font-bold tabular-nums ${
                  stats.avgPnl > 0 ? 'text-green-500' : stats.avgPnl < 0 ? 'text-red-500' : ''
                }`}
              >
                {stats.avgPnl >= 0 ? '+' : ''}{stats.avgPnl.toFixed(0)}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Best / Worst</span>
              <span>
                <span className="text-green-500">+{stats.bestTrade.toFixed(0)}</span>
                {' / '}
                <span className="text-red-500">{stats.worstTrade.toFixed(0)}</span>
              </span>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
