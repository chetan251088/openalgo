import { useMemo } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { AutoTradePresets } from './AutoTradePresets'
import { AutoTradeConfig } from './AutoTradeConfig'
import { GhostSignalOverlay } from './GhostSignalOverlay'
import { OptionsContextPanel } from './OptionsContextPanel'
import { LLMAdvisorPanel } from './LLMAdvisorPanel'

function getPlaybook(regime: string) {
  if (regime === 'TRENDING') return 'Trend Breakout'
  if (regime === 'RANGING') return 'Mean Reversion'
  if (regime === 'VOLATILE') return 'Vol Expansion'
  return 'Standby / No-Trade'
}

export function AutoTradeTab() {
  const enabled = useAutoTradeStore((s) => s.enabled)
  const mode = useAutoTradeStore((s) => s.mode)
  const replayMode = useAutoTradeStore((s) => s.replayMode)
  const regime = useAutoTradeStore((s) => s.regime)
  const tradesCount = useAutoTradeStore((s) => s.tradesCount)
  const realizedPnl = useAutoTradeStore((s) => s.realizedPnl)
  const consecutiveLosses = useAutoTradeStore((s) => s.consecutiveLosses)
  const killSwitch = useAutoTradeStore((s) => s.killSwitch)
  const lockProfitEnabled = useAutoTradeStore((s) => s.lockProfitEnabled)
  const lockProfitTriggered = useAutoTradeStore((s) => s.lockProfitTriggered)
  const accountPeakPnl = useAutoTradeStore((s) => s.accountPeakPnl)
  const accountDrawdown = useAutoTradeStore((s) => s.accountDrawdown)
  const autoPeakPnl = useAutoTradeStore((s) => s.autoPeakPnl)
  const autoDrawdown = useAutoTradeStore((s) => s.autoDrawdown)
  const sideEntryCount = useAutoTradeStore((s) => s.sideEntryCount)
  const sideLastExitAt = useAutoTradeStore((s) => s.sideLastExitAt)
  const sideLossPnl = useAutoTradeStore((s) => s.sideLossPnl)
  const lastDecisionBySide = useAutoTradeStore((s) => s.lastDecisionBySide)
  const decisionHistory = useAutoTradeStore((s) => s.decisionHistory)
  const executionSamples = useAutoTradeStore((s) => s.executionSamples)
  const config = useAutoTradeStore((s) => s.config)

  const setEnabled = useAutoTradeStore((s) => s.setEnabled)
  const setMode = useAutoTradeStore((s) => s.setMode)
  const setReplayMode = useAutoTradeStore((s) => s.setReplayMode)
  const setKillSwitch = useAutoTradeStore((s) => s.setKillSwitch)
  const setLockProfitEnabled = useAutoTradeStore((s) => s.setLockProfitEnabled)
  const resetRuntime = useAutoTradeStore((s) => s.resetRuntime)

  const activeSide = useScalpingStore((s) => s.activeSide)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)

  const latestDecision = useMemo(
    () => lastDecisionBySide[activeSide] ?? decisionHistory[decisionHistory.length - 1] ?? null,
    [activeSide, decisionHistory, lastDecisionBySide]
  )

  const regimeConfidence = useMemo(() => {
    if (!latestDecision) return regime === 'UNKNOWN' ? 0 : 55
    return Math.max(0, Math.min(99, Math.round((latestDecision.score / Math.max(1, latestDecision.minScore)) * 100)))
  }, [latestDecision, regime])

  const exposure = useMemo(() => {
    return Object.values(virtualTPSL).reduce(
      (acc, order) => {
        acc[order.side] += order.quantity
        return acc
      },
      { CE: 0, PE: 0 }
    )
  }, [virtualTPSL])

  const autoExposure = useMemo(() => {
    return Object.values(virtualTPSL).reduce(
      (acc, order) => {
        if (order.managedBy !== 'auto') return acc
        acc[order.side] += order.quantity
        return acc
      },
      { CE: 0, PE: 0 }
    )
  }, [virtualTPSL])

  const fillStats = useMemo(() => {
    const recent = executionSamples.slice(-10)
    const fills = recent.filter((s) => s.status === 'filled')
    const rejects = recent.filter((s) => s.status === 'rejected')
    const avgSpread = fills.length > 0
      ? fills.reduce((sum, sample) => sum + sample.spread, 0) / fills.length
      : 0
    const avgSlippage = fills.length > 0
      ? fills.reduce((sum, sample) => sum + sample.expectedSlippage, 0) / fills.length
      : 0
    const rejectRate = recent.length > 0 ? (rejects.length / recent.length) * 100 : 0
    return {
      recent: recent.slice().reverse(),
      avgSpread,
      avgSlippage,
      rejectRate,
    }
  }, [executionSamples])

  const spreadState = latestDecision
    ? latestDecision.spread <= config.entryMaxSpread * 0.4
      ? 'TIGHT'
      : latestDecision.spread <= config.entryMaxSpread
        ? 'NORMAL'
        : 'WIDE'
    : 'NA'

  const activeLastExitAgo = sideLastExitAt[activeSide] > 0
    ? Math.max(0, Math.round((Date.now() - sideLastExitAt[activeSide]) / 1000))
    : null

  const formatLoss = (value: number) => {
    if (value <= 0) return '0'
    return `-${value.toFixed(0)}`
  }

  return (
    <div className="p-2 space-y-3 text-xs overflow-y-auto">
      {/* Enable/Disable + Mode */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Label className="text-xs font-medium">Auto-Trade</Label>
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              className="h-5 w-9"
            />
          </div>
          <Badge
            variant={enabled ? (mode === 'execute' ? 'default' : 'secondary') : 'outline'}
            className="text-[10px] h-4"
          >
            {!enabled ? 'OFF' : mode === 'execute' ? 'EXECUTING' : 'GHOST'}
          </Badge>
        </div>

        <div className="flex gap-1">
          <Button
            variant={mode === 'ghost' ? 'secondary' : 'ghost'}
            size="sm"
            className="h-6 text-[10px] flex-1"
            onClick={() => setMode('ghost')}
          >
            Ghost (Signals Only)
          </Button>
          <Button
            variant={mode === 'execute' ? 'secondary' : 'ghost'}
            size="sm"
            className="h-6 text-[10px] flex-1"
            onClick={() => setMode('execute')}
          >
            Execute (Live)
          </Button>
        </div>

        <div className="rounded border border-border/60 p-1.5 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground uppercase">Replay / Simulator</span>
            <Switch checked={replayMode} onCheckedChange={setReplayMode} className="h-4 w-8" />
          </div>
          <p className="text-[9px] text-muted-foreground">
            When enabled, execute mode is blocked and engine runs as simulation-only diagnostics.
          </p>
        </div>

        {mode === 'execute' && !paperMode && !replayMode && (
          <div className="p-1 bg-red-500/10 border border-red-500/30 rounded text-red-500 text-[10px] text-center font-medium">
            LIVE EXECUTION MODE - Real orders will be placed
          </div>
        )}
      </div>

      {/* Runtime Status */}
      <section className="border-t pt-2 space-y-1">
        <span className="text-[10px] font-medium text-muted-foreground uppercase">Status</span>

        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Regime</span>
            <Badge variant="outline" className="text-[9px] h-3.5 px-1">{regime}</Badge>
          </div>

          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Auto Trades</span>
            <span className="font-bold">{tradesCount}</span>
          </div>

          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Auto P&L</span>
            <span
              className={`font-bold tabular-nums ${
                realizedPnl > 0 ? 'text-green-500' : realizedPnl < 0 ? 'text-red-500' : ''
              }`}
            >
              {realizedPnl >= 0 ? '+' : ''}{realizedPnl.toFixed(0)}
            </span>
          </div>

          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Consec. Losses</span>
            <span className={`font-bold ${consecutiveLosses >= 3 ? 'text-red-500' : ''}`}>
              {consecutiveLosses}
            </span>
          </div>
        </div>

        <Button
          variant="ghost"
          size="sm"
          className="h-5 text-[9px] w-full"
          onClick={resetRuntime}
        >
          Reset Runtime
        </Button>
      </section>

      {/* Why Trade */}
      <section className="border-t pt-2 space-y-1.5">
        <span className="text-[10px] font-medium text-muted-foreground uppercase">Why Trade?</span>
        {!latestDecision ? (
          <div className="text-[10px] text-muted-foreground">Waiting for first decision snapshot...</div>
        ) : (
          <div className="rounded border border-border/60 p-1.5 space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">{latestDecision.side} {latestDecision.symbol.slice(-12)}</span>
              <span className={`font-bold ${latestDecision.enter ? 'text-green-500' : 'text-red-500'}`}>
                {latestDecision.score.toFixed(1)} / {latestDecision.minScore.toFixed(1)}
              </span>
            </div>
            <p className="text-[10px] text-muted-foreground leading-tight">{latestDecision.reason}</p>
            <div className="space-y-0.5">
              {latestDecision.checks.slice(0, 8).map((check) => (
                <div key={check.id} className="flex items-center justify-between">
                  <span className="text-[10px] text-muted-foreground">{check.label}</span>
                  <span className={`text-[10px] font-medium ${check.pass ? 'text-green-500' : 'text-red-500'}`}>
                    {check.pass ? 'PASS' : 'FAIL'}{check.value ? ` (${check.value})` : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Regime Router */}
      <section className="border-t pt-2 space-y-1.5">
        <span className="text-[10px] font-medium text-muted-foreground uppercase">Regime Router</span>
        <div className="rounded border border-border/60 p-1.5 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Playbook</span>
            <span className="font-medium">{getPlaybook(regime)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Confidence</span>
            <span className="font-bold">{regimeConfidence}%</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Block Reason</span>
            <span className="text-[10px] text-right max-w-[70%] truncate">
              {latestDecision && !latestDecision.enter ? latestDecision.reason : 'None'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Last Exit ({activeSide})</span>
            <span className="font-mono text-[10px]">
              {activeLastExitAgo == null ? '—' : `${activeLastExitAgo}s ago`}
            </span>
          </div>
        </div>
      </section>

      {/* Execution Panel */}
      <section className="border-t pt-2 space-y-1.5">
        <span className="text-[10px] font-medium text-muted-foreground uppercase">Execution</span>
        <div className="rounded border border-border/60 p-1.5 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Spread State</span>
            <span className={`font-medium ${spreadState === 'WIDE' ? 'text-red-500' : spreadState === 'TIGHT' ? 'text-green-500' : ''}`}>
              {spreadState}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Expected Slippage</span>
            <span className="font-mono">{(latestDecision?.expectedSlippage ?? 0).toFixed(2)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Depth Ratio</span>
            <span className="font-mono">{latestDecision?.depthRatio != null ? latestDecision.depthRatio.toFixed(2) : 'NA'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Last 10 Avg Spread</span>
            <span className="font-mono">{fillStats.avgSpread.toFixed(2)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Last 10 Avg Slip</span>
            <span className="font-mono">{fillStats.avgSlippage.toFixed(2)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Reject Rate</span>
            <span className={`font-mono ${fillStats.rejectRate > 30 ? 'text-red-500' : ''}`}>
              {fillStats.rejectRate.toFixed(0)}%
            </span>
          </div>
          <div className="space-y-0.5 pt-1 border-t">
            {fillStats.recent.slice(0, 5).map((sample) => (
              <div key={`${sample.timestamp}-${sample.symbol}`} className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground truncate max-w-[65%]">{sample.side} {sample.symbol.slice(-10)}</span>
                <span className={sample.status === 'rejected' ? 'text-red-500' : sample.status === 'filled' ? 'text-green-500' : 'text-muted-foreground'}>
                  {sample.status}
                </span>
              </div>
            ))}
            {fillStats.recent.length === 0 && (
              <div className="text-[10px] text-muted-foreground">No execution samples yet.</div>
            )}
          </div>
        </div>
      </section>

      {/* Risk Cockpit */}
      <section className="border-t pt-2 space-y-1.5">
        <span className="text-[10px] font-medium text-muted-foreground uppercase">Risk Cockpit</span>
        <div className="rounded border border-border/60 p-1.5 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Kill Switch</span>
            <div className="flex items-center gap-1.5">
              {killSwitch && <Badge variant="destructive" className="text-[9px] h-3.5 px-1">ON</Badge>}
              <Switch checked={killSwitch} onCheckedChange={setKillSwitch} className="h-4 w-8" />
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Lock-Profit</span>
            <div className="flex items-center gap-1.5">
              {lockProfitTriggered && <Badge variant="secondary" className="text-[9px] h-3.5 px-1">TRIGGERED</Badge>}
              <Switch checked={lockProfitEnabled} onCheckedChange={setLockProfitEnabled} className="h-4 w-8" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Peak P&L (All)</span>
              <span className="font-mono">{accountPeakPnl.toFixed(0)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Drawdown (All)</span>
              <span className={`font-mono ${accountDrawdown > 0 ? 'text-red-500' : ''}`}>{accountDrawdown.toFixed(0)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Peak P&L (Auto)</span>
              <span className="font-mono">{autoPeakPnl.toFixed(0)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Drawdown (Auto)</span>
              <span className={`font-mono ${autoDrawdown > 0 ? 'text-red-500' : ''}`}>{autoDrawdown.toFixed(0)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Exposure CE (Virtual)</span>
              <span className="font-mono">{exposure.CE}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Exposure PE (Virtual)</span>
              <span className="font-mono">{exposure.PE}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Exposure CE (Auto)</span>
              <span className="font-mono">{autoExposure.CE}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Exposure PE (Auto)</span>
              <span className="font-mono">{autoExposure.PE}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">CE Entries</span>
              <span className="font-mono">{sideEntryCount.CE}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">PE Entries</span>
              <span className="font-mono">{sideEntryCount.PE}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">CE Loss</span>
              <span className={`font-mono ${sideLossPnl.CE > 0 ? 'text-red-500' : ''}`}>
                {formatLoss(sideLossPnl.CE)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">PE Loss</span>
              <span className={`font-mono ${sideLossPnl.PE > 0 ? 'text-red-500' : ''}`}>
                {formatLoss(sideLossPnl.PE)}
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* Options Context */}
      <section className="border-t pt-2">
        <OptionsContextPanel />
      </section>

      {/* Ghost Signals */}
      <section className="border-t pt-2">
        <GhostSignalOverlay />
      </section>

      {/* LLM Advisor */}
      <section className="border-t pt-2">
        <LLMAdvisorPanel />
      </section>

      {/* Presets */}
      <section className="border-t pt-2">
        <span className="text-[10px] font-medium text-muted-foreground uppercase block mb-1">Presets</span>
        <AutoTradePresets />
      </section>

      {/* Config */}
      <section className="border-t pt-2">
        <span className="text-[10px] font-medium text-muted-foreground uppercase block mb-1">Configuration</span>
        <AutoTradeConfig />
      </section>
    </div>
  )
}
