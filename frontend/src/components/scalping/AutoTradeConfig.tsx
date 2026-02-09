import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import type { AutoTradeConfigFields } from '@/lib/scalpingPresets'

type NumericField = {
  [K in keyof AutoTradeConfigFields]: AutoTradeConfigFields[K] extends number ? K : never
}[keyof AutoTradeConfigFields]

type BooleanField = {
  [K in keyof AutoTradeConfigFields]: AutoTradeConfigFields[K] extends boolean ? K : never
}[keyof AutoTradeConfigFields]

function NumField({
  label,
  field,
  step,
}: {
  label: string
  field: NumericField
  step?: number
}) {
  const value = useAutoTradeStore((s) => s.config[field]) as number
  const updateConfig = useAutoTradeStore((s) => s.updateConfig)

  return (
    <div className="flex items-center justify-between gap-2">
      <Label className="text-[10px] text-muted-foreground shrink-0">{label}</Label>
      <Input
        type="number"
        value={value}
        step={step ?? 1}
        onChange={(e) => updateConfig({ [field]: Number(e.target.value) || 0 })}
        className="h-5 w-16 text-[10px] text-right"
      />
    </div>
  )
}

function BoolField({ label, field }: { label: string; field: BooleanField }) {
  const value = useAutoTradeStore((s) => s.config[field]) as boolean
  const updateConfig = useAutoTradeStore((s) => s.updateConfig)

  return (
    <div className="flex items-center justify-between gap-2">
      <Label className="text-[10px] text-muted-foreground">{label}</Label>
      <Switch
        checked={value}
        onCheckedChange={(v) => updateConfig({ [field]: v })}
        className="h-4 w-7"
      />
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="group">
      <summary className="cursor-pointer text-[10px] font-medium uppercase text-muted-foreground hover:text-foreground py-1 border-b">
        {title}
      </summary>
      <div className="py-1.5 space-y-1">{children}</div>
    </details>
  )
}

export function AutoTradeConfig() {
  return (
    <div className="space-y-0.5">
      <Section title="Entry Conditions">
        <NumField label="Momentum Count" field="entryMomentumCount" />
        <NumField label="Momentum Velocity" field="entryMomentumVelocity" />
        <NumField label="Min Score" field="entryMinScore" />
        <NumField label="Max Spread" field="entryMaxSpread" />
      </Section>

      <Section title="Trailing SL (5-Stage)">
        <NumField label="Initial SL (pts)" field="trailInitialSL" />
        <NumField label="Breakeven Trigger" field="trailBreakevenTrigger" />
        <NumField label="Lock Trigger" field="trailLockTrigger" />
        <NumField label="Lock Amount" field="trailLockAmount" />
        <NumField label="Trail Start" field="trailStartTrigger" />
        <NumField label="Trail Step" field="trailStepSize" />
        <NumField label="Tight Trigger" field="trailTightTrigger" />
        <NumField label="Tight Step" field="trailTightStep" />
      </Section>

      <Section title="Breakeven">
        <NumField label="Trigger (pts)" field="breakevenTriggerPts" />
        <NumField label="Buffer" field="breakevenBuffer" step={0.5} />
      </Section>

      <Section title="Risk">
        <NumField label="Max Daily Loss" field="maxDailyLoss" />
        <NumField label="Per-Trade Max Loss" field="perTradeMaxLoss" />
        <NumField label="Max Trades/Day" field="maxTradesPerDay" />
        <NumField label="Max Trades/Min" field="maxTradesPerMinute" />
        <NumField label="Min Gap (ms)" field="minGapMs" step={500} />
        <NumField label="Cooldown After Loss (s)" field="cooldownAfterLossSec" />
        <NumField label="Cool Off After Losses" field="coolingOffAfterLosses" />
        <NumField label="Max Position Size" field="maxPositionSize" />
      </Section>

      <Section title="Imbalance Filter">
        <BoolField label="Enabled" field="imbalanceFilterEnabled" />
        <NumField label="Threshold Ratio" field="imbalanceThreshold" step={0.1} />
      </Section>

      <Section title="Regime Detection">
        <NumField label="Detection Period" field="regimeDetectionPeriod" />
        <NumField label="Ranging Threshold (pts)" field="rangingThresholdPts" />
      </Section>

      <Section title="Index Bias">
        <BoolField label="Enabled" field="indexBiasEnabled" />
        <NumField label="Weight" field="indexBiasWeight" step={0.1} />
      </Section>

      <Section title="Options Context">
        <BoolField label="Enabled" field="optionsContextEnabled" />
        <NumField label="PCR Bullish ≤" field="pcrBullishThreshold" step={0.1} />
        <NumField label="PCR Bearish ≥" field="pcrBearishThreshold" step={0.1} />
        <NumField label="Max Pain Proximity" field="maxPainProximityFilter" />
        <BoolField label="GEX Wall Filter" field="gexWallFilterEnabled" />
        <BoolField label="IV Spike Exit" field="ivSpikeExitEnabled" />
        <NumField label="IV Spike Threshold %" field="ivSpikeThreshold" />
      </Section>

      <Section title="Time-of-Day">
        <BoolField label="Respect Hot Zones" field="respectHotZones" />
        <NumField label="Sensitivity Multiplier" field="sensitivityMultiplier" step={0.1} />
      </Section>

      <Section title="No-Trade Zone">
        <BoolField label="Enabled" field="noTradeZoneEnabled" />
        <NumField label="Range (pts)" field="noTradeZoneRangePts" />
        <NumField label="Period" field="noTradeZonePeriod" />
      </Section>

      <Section title="Re-Entry">
        <BoolField label="Enabled" field="reEntryEnabled" />
        <NumField label="Delay (sec)" field="reEntryDelaySec" />
        <NumField label="Max Per Side" field="reEntryMaxPerSide" />
      </Section>

      <Section title="Telegram Alerts">
        <BoolField label="Entry Alerts" field="telegramAlertsEntry" />
        <BoolField label="Exit Alerts" field="telegramAlertsExit" />
        <BoolField label="Tune Alerts" field="telegramAlertsTune" />
      </Section>
    </div>
  )
}
