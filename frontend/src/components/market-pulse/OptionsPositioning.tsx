import type { OptionsContextItem } from '@/api/market-pulse'

interface OptionsPositioningProps {
  data: Record<string, OptionsContextItem>
}

function formatNumber(value: number | null | undefined) {
  if (typeof value !== 'number') {
    return 'n/a'
  }
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function formatOi(value: number | null | undefined) {
  if (typeof value !== 'number') {
    return 'n/a'
  }
  const abs = Math.abs(value)
  if (abs >= 1e7) {
    return `${(value / 1e7).toFixed(2)} Cr`
  }
  if (abs >= 1e5) {
    return `${(value / 1e5).toFixed(1)} L`
  }
  return value.toLocaleString('en-IN')
}

function formatDistance(value: number | null | undefined) {
  if (typeof value !== 'number') {
    return 'n/a'
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

export function OptionsPositioning({ data }: OptionsPositioningProps) {
  const items = Object.values(data || {})

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Options Positioning
        </span>
        <div className="text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
          Max Pain + OI Walls
        </div>
      </div>

      {items.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-[#223847] bg-[#09111a] p-6 text-sm text-[#6b8797]">
          Option-writer positioning is unavailable right now.
        </div>
      ) : (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {items.map((item) => (
            <div
              key={item.underlying}
              className="rounded-2xl border border-[#1b2b37] bg-[#09111a]/80 p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-[10px] uppercase tracking-[0.24em] text-[#7fa2b1]">
                    {item.underlying}
                  </div>
                  <div className="mt-1 text-sm text-[#d8eef6]">
                    Spot {formatNumber(item.spot_price)}
                    {typeof item.futures_price === 'number'
                      ? ` | Fut ${formatNumber(item.futures_price)}`
                      : ''}
                  </div>
                </div>
                <div className="rounded-full border border-[#233745] px-3 py-1 text-[10px] uppercase tracking-[0.18em] text-[#94a3b8]">
                  {item.expiry_date || 'Expiry n/a'}
                </div>
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-[#6b8797]">
                    Max Pain
                  </div>
                  <div className="mt-2 text-2xl font-semibold text-[#fde68a]">
                    {formatNumber(item.max_pain)}
                  </div>
                  <div className="mt-1 text-xs text-[#7fa2b1]">
                    PCR OI {typeof item.pcr_oi === 'number' ? item.pcr_oi.toFixed(2) : 'n/a'}
                  </div>
                </div>

                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-[#6b8797]">
                    OI Balance
                  </div>
                  <div className="mt-2 text-sm text-[#d8eef6]">
                    CE {formatOi(item.total_ce_oi)} | PE {formatOi(item.total_pe_oi)}
                  </div>
                  <div className="mt-1 text-xs text-[#7fa2b1]">
                    ATM {formatNumber(item.atm_strike)}
                  </div>
                </div>
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-[#123022] bg-[#081610] p-3">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-[#6ee7b7]">
                    Put OI Support
                  </div>
                  <div className="mt-2 text-lg font-semibold text-[#bbf7d0]">
                    {formatNumber(item.put_wall?.strike)}
                  </div>
                  <div className="mt-1 text-xs text-[#7fa2b1]">
                    OI {formatOi(item.put_wall?.oi)} | {formatDistance(item.put_wall?.distance_pct)}
                  </div>
                </div>

                <div className="rounded-xl border border-[#3b1d1d] bg-[#16090a] p-3">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-[#fca5a5]">
                    Call OI Resistance
                  </div>
                  <div className="mt-2 text-lg font-semibold text-[#fecaca]">
                    {formatNumber(item.call_wall?.strike)}
                  </div>
                  <div className="mt-1 text-xs text-[#7fa2b1]">
                    OI {formatOi(item.call_wall?.oi)} | {formatDistance(item.call_wall?.distance_pct)}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
