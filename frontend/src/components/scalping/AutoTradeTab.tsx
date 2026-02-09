import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { AutoTradePresets } from './AutoTradePresets'
import { AutoTradeConfig } from './AutoTradeConfig'
import { GhostSignalOverlay } from './GhostSignalOverlay'
import { OptionsContextPanel } from './OptionsContextPanel'
import { LLMAdvisorPanel } from './LLMAdvisorPanel'

export function AutoTradeTab() {
  const enabled = useAutoTradeStore((s) => s.enabled)
  const mode = useAutoTradeStore((s) => s.mode)
  const regime = useAutoTradeStore((s) => s.regime)
  const tradesCount = useAutoTradeStore((s) => s.tradesCount)
  const realizedPnl = useAutoTradeStore((s) => s.realizedPnl)
  const consecutiveLosses = useAutoTradeStore((s) => s.consecutiveLosses)

  const setEnabled = useAutoTradeStore((s) => s.setEnabled)
  const setMode = useAutoTradeStore((s) => s.setMode)
  const resetRuntime = useAutoTradeStore((s) => s.resetRuntime)

  const paperMode = useScalpingStore((s) => s.paperMode)

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

        {/* Mode toggle */}
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

        {mode === 'execute' && !paperMode && (
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
            <span className="text-muted-foreground">Trades</span>
            <span className="font-bold">{tradesCount}</span>
          </div>

          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">P&L</span>
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
