import type { MarketLevelItem } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface KeyLevelsProps {
  data: Record<string, MarketLevelItem | null>
}

function formatNumber(value: number | null | undefined) {
  if (typeof value !== 'number') {
    return 'n/a'
  }
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function stateLabel(state: MarketLevelItem['state'] | undefined) {
  switch (state) {
    case 'above_pdh':
      return 'Above PDH'
    case 'below_pdl':
      return 'Below PDL'
    case 'inside_prior_range':
      return 'Inside Range'
    default:
      return 'Unknown'
  }
}

export function KeyLevels({ data }: KeyLevelsProps) {
  const items = Object.entries(data || {}).filter(([, levels]) => levels)

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Key Levels
        </span>
        <div className="text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
          PDH / PDL / PDC
        </div>
      </div>

      {items.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-[#223847] bg-[#09111a] p-6 text-sm text-[#6b8797]">
          Prior-session market levels are unavailable right now.
        </div>
      ) : (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {items.map(([instrument, levels]) => {
            if (!levels) {
              return null
            }

            return (
              <div
                key={instrument}
                className="rounded-2xl border border-[#1b2b37] bg-[#09111a]/80 p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-[10px] uppercase tracking-[0.24em] text-[#7fa2b1]">
                    {instrument}
                  </div>
                  <span
                    className={cn(
                      'rounded-full border px-3 py-1 text-[10px] uppercase tracking-[0.18em]',
                      levels.state === 'above_pdh'
                        ? 'border-[#14532d] text-[#4ade80]'
                        : levels.state === 'below_pdl'
                          ? 'border-[#7f1d1d] text-[#f87171]'
                          : 'border-[#334155] text-[#94a3b8]',
                    )}
                  >
                    {stateLabel(levels.state)}
                  </span>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                    <div className="text-[10px] uppercase tracking-[0.2em] text-[#6b8797]">
                      Current
                    </div>
                    <div className="mt-2 text-sm font-semibold text-[#d8eef6]">
                      {formatNumber(levels.current)}
                    </div>
                  </div>
                  <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                    <div className="text-[10px] uppercase tracking-[0.2em] text-[#6b8797]">
                      PDH
                    </div>
                    <div className="mt-2 text-sm font-semibold text-[#bbf7d0]">
                      {formatNumber(levels.pdh)}
                    </div>
                  </div>
                  <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                    <div className="text-[10px] uppercase tracking-[0.2em] text-[#6b8797]">
                      PDL
                    </div>
                    <div className="mt-2 text-sm font-semibold text-[#fecaca]">
                      {formatNumber(levels.pdl)}
                    </div>
                  </div>
                  <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                    <div className="text-[10px] uppercase tracking-[0.2em] text-[#6b8797]">
                      PDC
                    </div>
                    <div className="mt-2 text-sm font-semibold text-[#fde68a]">
                      {formatNumber(levels.pdc)}
                    </div>
                  </div>
                </div>

                <div className="mt-3 text-xs text-[#7fa2b1]">
                  Gap vs PDC:{' '}
                  {typeof levels.gap_pct === 'number'
                    ? `${levels.gap_pct >= 0 ? '+' : ''}${levels.gap_pct.toFixed(2)}%`
                    : 'n/a'}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
