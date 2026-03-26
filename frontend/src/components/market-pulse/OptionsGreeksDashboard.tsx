import type { OptionsGreeksData } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface OptionsGreeksProps {
  data: Record<string, OptionsGreeksData> | null
}

function fmt(v: number | null | undefined, decimals = 2) {
  if (typeof v !== 'number') return '—'
  return v.toLocaleString('en-IN', { maximumFractionDigits: decimals })
}

export function OptionsGreeksDashboard({ data }: OptionsGreeksProps) {
  if (!data) return null

  const symbols = Object.entries(data)
  if (symbols.length === 0) return null

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Options Context & Greeks
        </span>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {symbols.map(([symbol, greekData]) => {
          const gex = greekData?.gex
          const iv = greekData?.iv

          if (!gex && !iv) return null

          return (
            <div key={symbol} className="rounded-2xl border border-[#1b2b37] bg-[#09111a]/80 p-4">
              <div className="flex items-center justify-between">
                <div className="text-[10px] uppercase tracking-[0.24em] text-[#7fa2b1]">
                  {symbol}
                  {gex?.expiry_date && (
                    <span className="ml-2 text-[#546b79]">{gex.expiry_date}</span>
                  )}
                </div>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {/* Gamma Positioning */}
                {gex && (
                  <div className="col-span-2 rounded-xl border border-[#1a3140] bg-[#0b1620] p-3 text-center sm:col-span-2">
                    <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                      Dealer Gamma
                    </div>
                    <div
                      className={cn(
                        'mt-1 text-sm font-bold',
                        gex.gamma_positioning === 'long_gamma'
                          ? 'text-[#4ade80]'
                          : gex.gamma_positioning === 'short_gamma'
                            ? 'text-[#f87171]'
                            : 'text-[#fbbf24]',
                      )}
                    >
                      {gex.gamma_label}
                    </div>
                    <div className="mt-1 text-[9px] text-[#546b79]">
                      Net GEX: {fmt(gex.total_net_gex, 0)}
                    </div>
                  </div>
                )}

                {/* IV & Skew */}
                {iv && (
                  <>
                    <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3 text-center">
                      <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                        ATM IV
                      </div>
                      <div className="mt-1 text-sm font-bold text-[#d8eef6]">{fmt(iv.atm_iv)}</div>
                    </div>
                    <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3 text-center">
                      <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                        Skew
                      </div>
                      <div
                        className={cn(
                          'mt-1 text-sm font-bold',
                          (iv.pc_skew ?? 0) > 1
                            ? 'text-[#f87171]'
                            : (iv.pc_skew ?? 0) < -1
                              ? 'text-[#4ade80]'
                              : 'text-[#d8eef6]',
                        )}
                      >
                        {fmt(iv.pc_skew)}
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* GEX Strikes */}
              {gex && (gex.top_call_gex_strikes.length > 0 || gex.top_put_gex_strikes.length > 0) && (
                <div className="mt-3 grid grid-cols-2 gap-2 text-[10px]">
                  <div className="rounded-lg border border-[#1a3140] bg-[#0b1620] p-2">
                    <div className="mb-1 text-[#6b8797]">Call Walls (Resistance)</div>
                    {gex.top_call_gex_strikes.slice(0, 2).map((s, i) => (
                      <div key={i} className="flex justify-between text-[#86efac]">
                        <span>{s.strike}</span>
                      </div>
                    ))}
                  </div>
                  <div className="rounded-lg border border-[#1a3140] bg-[#0b1620] p-2">
                    <div className="mb-1 text-[#6b8797]">Put Walls (Support)</div>
                    {gex.top_put_gex_strikes.slice(0, 2).map((s, i) => (
                      <div key={i} className="flex justify-between text-[#fca5a5]">
                        <span>{s.strike}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
