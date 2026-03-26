import type { MarketPulseData, TickerItem } from '@/api/market-pulse'
import { cn } from '@/lib/utils'
import { ModeSwitcher } from './ModeSwitcher'

interface TickerBarProps {
  data?: MarketPulseData
  mode: 'swing' | 'day'
  onModeChange: (mode: 'swing' | 'day') => void
  secondsAgo: number | null
  onRefresh: () => void | Promise<void>
  isLoading: boolean
}

const TICKERS: Array<{ key: keyof MarketPulseData['ticker']; label: string }> = [
  { key: 'NIFTY', label: 'NIFTY' },
  { key: 'SENSEX', label: 'SENSEX' },
  { key: 'BANKNIFTY', label: 'BANK' },
  { key: 'INDIAVIX', label: 'VIX' },
  { key: 'USDINR', label: 'USDINR' },
]

function formatValue(value?: number) {
  if (typeof value !== 'number') return 'n/a'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function formatChange(value?: number) {
  if (typeof value !== 'number') return 'n/a'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function biasBadgeClasses(bias?: string) {
  if (bias === 'LONG') return 'border-[#14532d] bg-[#14532d]/15 text-[#86efac]'
  if (bias === 'SHORT') return 'border-[#7f1d1d] bg-[#7f1d1d]/15 text-[#fca5a5]'
  return 'border-[#334155] bg-[#334155]/15 text-[#cbd5e1]'
}

function TickerChip({ label, item }: { label: string; item?: TickerItem }) {
  const change = item?.change_pct
  const changeClass =
    typeof change === 'number'
      ? change > 0
        ? 'text-[#4ade80]'
        : change < 0
          ? 'text-[#f87171]'
          : 'text-[#94a3b8]'
      : 'text-[#94a3b8]'

  return (
    <div className="rounded-full border border-[#233846] bg-[#0d1720]/90 px-3 py-2">
      <div className="flex items-baseline gap-2 font-mono">
        <span className="text-[10px] uppercase tracking-[0.24em] text-[#6b8797]">{label}</span>
        <span className="text-sm font-semibold text-[#e7f7fb]">{formatValue(item?.ltp)}</span>
        <span className={cn('text-[11px]', changeClass)}>{formatChange(change)}</span>
      </div>
    </div>
  )
}

export function TickerBar({
  data,
  mode,
  onModeChange,
  secondsAgo,
  onRefresh,
  isLoading,
}: TickerBarProps) {
  const showExecutionRegime =
    mode === 'day' &&
    data?.execution_regime &&
    data.execution_regime !== data.regime

  return (
    <div className="sticky top-0 z-10 border-b border-[#1f3340] bg-[linear-gradient(135deg,#071018_0%,#0b1823_50%,#0f1c26_100%)] px-3 py-3 shadow-[0_18px_48px_rgba(2,8,23,0.45)]">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <div className="mr-2">
            <div className="font-mono text-[10px] uppercase tracking-[0.34em] text-[#5eead4]">
              Market Pulse
            </div>
            <div className="font-mono text-xs text-[#8db5c3]">
              India risk radar and execution monitor
            </div>
          </div>
          {TICKERS.map((ticker) => (
            <TickerChip
              key={ticker.key}
              label={ticker.label}
              item={data?.ticker?.[ticker.key]}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2 font-mono">
          <ModeSwitcher mode={mode} onChange={onModeChange} />
          {mode === 'day' ? (
            <div className="rounded-full border border-[#14532d] bg-[#14532d]/15 px-3 py-2 text-[10px] uppercase tracking-[0.22em] text-[#86efac]">
              Live Scores
            </div>
          ) : null}
          <div className="rounded-full border border-[#233846] bg-[#0d1720]/90 px-3 py-2 text-[10px] uppercase tracking-[0.22em] text-[#8db5c3]">
            Decision {data?.decision ?? '...'}
          </div>
          <div
            className={cn(
              'rounded-full border px-3 py-2 text-[10px] uppercase tracking-[0.22em]',
              biasBadgeClasses(data?.directional_bias?.bias),
            )}
          >
            Bias {data?.directional_bias?.bias ?? '...'}
            {typeof data?.directional_bias?.confidence === 'number'
              ? ` ${data.directional_bias.confidence}`
              : ''}
          </div>
          <div className="rounded-full border border-[#233846] bg-[#0d1720]/90 px-3 py-2 text-[10px] uppercase tracking-[0.22em] text-[#8db5c3]">
            {mode === 'day' ? 'Backdrop' : 'Regime'} {data?.regime ?? '...'}
          </div>
          {showExecutionRegime ? (
            <div className="rounded-full border border-[#14532d] bg-[#14532d]/10 px-3 py-2 text-[10px] uppercase tracking-[0.22em] text-[#86efac]">
              Session {data?.execution_regime}
            </div>
          ) : null}
          <div className="flex items-center gap-2 rounded-full border border-[#233846] bg-[#0d1720]/90 px-3 py-2 text-[10px] uppercase tracking-[0.22em] text-[#8db5c3]">
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                isLoading ? 'animate-pulse bg-[#f59e0b]' : 'bg-[#22c55e]',
              )}
            />
            <span>{isLoading ? 'Updating' : 'Live'}</span>
            {secondsAgo != null && <span>{secondsAgo}s</span>}
          </div>
          <button
            type="button"
            onClick={() => void onRefresh()}
            disabled={isLoading}
            className={cn(
              'rounded-full border px-3 py-2 text-[10px] uppercase tracking-[0.22em] transition-colors',
              isLoading
                ? 'cursor-wait border-[#365766] bg-[#12313b]/70 text-[#8db5c3]'
                : 'border-[#2b5260] bg-[#0f766e]/20 text-[#5eead4] hover:bg-[#0f766e]/30',
            )}
          >
            {isLoading ? 'Refreshing' : 'Refresh'}
          </button>
        </div>
      </div>
    </div>
  )
}
