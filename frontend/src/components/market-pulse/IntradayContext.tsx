import type { IntradayContext as IntradayCtxType } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface IntradayContextProps {
  data: Record<string, IntradayCtxType> | null
}

function fmt(v: number | null | undefined, decimals = 2) {
  if (typeof v !== 'number') return '—'
  return v.toLocaleString('en-IN', { maximumFractionDigits: decimals })
}

const PHASE_COLORS: Record<string, string> = {
  pre_market: 'border-[#334155] text-[#94a3b8]',
  pre_open: 'border-[#334155] text-[#94a3b8]',
  opening_drive: 'border-[#14532d] text-[#4ade80]',
  morning_range: 'border-[#1e3a5f] text-[#67e8f9]',
  lunch_chop: 'border-[#92400e] text-[#fbbf24]',
  afternoon_trend: 'border-[#1e3a5f] text-[#67e8f9]',
  closing_session: 'border-[#7f1d1d] text-[#fca5a5]',
  post_market: 'border-[#334155] text-[#94a3b8]',
}

const OR_STATE_COLORS: Record<string, string> = {
  above: 'text-[#4ade80]',
  below: 'text-[#f87171]',
  inside: 'text-[#fbbf24]',
}

export function IntradayContext({ data }: IntradayContextProps) {
  if (!data) return null

  const symbols = Object.entries(data)
  if (symbols.length === 0) return null

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Intraday Context
        </span>
        {symbols[0]?.[1]?.session_phase && (
          <span
            className={cn(
              'inline-flex rounded-full border px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]',
              PHASE_COLORS[symbols[0][1].session_phase.phase] || 'border-[#334155] text-[#94a3b8]',
            )}
          >
            {symbols[0][1].session_phase.label}
            {symbols[0][1].session_phase.minutes_remaining > 0 &&
              ` · ${symbols[0][1].session_phase.minutes_remaining}m left`}
          </span>
        )}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {symbols.map(([symbol, ctx]) => (
          <div key={symbol} className="rounded-2xl border border-[#1b2b37] bg-[#09111a]/80 p-4">
            <div className="text-[10px] uppercase tracking-[0.24em] text-[#7fa2b1]">{symbol}</div>

            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {/* Opening Range */}
              {ctx.opening_range && (
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                    Opening Range
                  </div>
                  <div className="mt-1.5 flex items-baseline gap-1">
                    <span className="text-sm font-semibold text-[#d8eef6]">
                      {fmt(ctx.opening_range.or_high)} – {fmt(ctx.opening_range.or_low)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-1.5">
                    <span
                      className={cn(
                        'text-[10px] font-semibold uppercase',
                        OR_STATE_COLORS[ctx.opening_range.current_vs_or] || 'text-[#94a3b8]',
                      )}
                    >
                      {ctx.opening_range.current_vs_or}
                    </span>
                    {!ctx.opening_range.complete && (
                      <span className="text-[9px] text-[#6b8797]">forming</span>
                    )}
                  </div>
                </div>
              )}

              {/* Initial Balance */}
              {ctx.initial_balance && (
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                    Initial Balance
                  </div>
                  <div className="mt-1.5 text-sm font-semibold text-[#d8eef6]">
                    {fmt(ctx.initial_balance.ib_high)} – {fmt(ctx.initial_balance.ib_low)}
                  </div>
                  <div className="mt-1">
                    <span
                      className={cn(
                        'text-[10px] font-semibold uppercase',
                        OR_STATE_COLORS[ctx.initial_balance.current_vs_ib] || 'text-[#94a3b8]',
                      )}
                    >
                      {ctx.initial_balance.current_vs_ib}
                    </span>
                  </div>
                </div>
              )}

              {/* VWAP */}
              {ctx.vwap_bands && (
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">VWAP</div>
                  <div className="mt-1.5 text-sm font-bold text-[#67e8f9]">
                    {fmt(ctx.vwap_bands.vwap)}
                  </div>
                  <div className="mt-1 text-[10px] text-[#6b8797]">
                    Dist: {ctx.vwap_bands.distance_pct >= 0 ? '+' : ''}
                    {ctx.vwap_bands.distance_pct.toFixed(2)}%
                  </div>
                  <div className="mt-0.5 text-[9px] text-[#546b79]">
                    ±1σ: {fmt(ctx.vwap_bands.lower_1)}–{fmt(ctx.vwap_bands.upper_1)}
                  </div>
                </div>
              )}

              {/* ADR */}
              {ctx.adr && (
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">ADR</div>
                  <div className="mt-1.5 text-sm font-semibold text-[#d8eef6]">
                    {fmt(ctx.adr.adr)}
                  </div>
                  {ctx.adr.consumed_pct !== null && (
                    <>
                      <div className="mt-1.5 h-1 rounded-full bg-[#18242f]">
                        <div
                          className={cn(
                            'h-1 rounded-full transition-all',
                            ctx.adr.consumed_pct >= 80
                              ? 'bg-[#f87171]'
                              : ctx.adr.consumed_pct >= 50
                                ? 'bg-[#fbbf24]'
                                : 'bg-[#4ade80]',
                          )}
                          style={{ width: `${Math.min(100, ctx.adr.consumed_pct)}%` }}
                        />
                      </div>
                      <div className="mt-1 text-[10px] text-[#6b8797]">
                        {ctx.adr.consumed_pct.toFixed(0)}% consumed
                        {ctx.adr.exhaustion_warning && (
                          <span className="ml-1 text-[#f87171]">⚠ Exhaustion</span>
                        )}
                      </div>
                    </>
                  )}
                </div>
              )}

              {/* Developing High/Low */}
              {ctx.developing_high_low && (
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                    Dev H/L
                  </div>
                  <div className="mt-1.5 flex gap-2 text-xs">
                    <span className="text-[#bbf7d0]">H: {fmt(ctx.developing_high_low.dev_high)}</span>
                    <span className="text-[#fecaca]">L: {fmt(ctx.developing_high_low.dev_low)}</span>
                  </div>
                  <div className="mt-1.5 h-1 rounded-full bg-[#18242f]">
                    <div
                      className="h-1 rounded-full bg-[#67e8f9] transition-all"
                      style={{
                        width: `${Math.max(2, Math.min(100, ctx.developing_high_low.range_position_pct))}%`,
                      }}
                    />
                  </div>
                  <div className="mt-1 text-[9px] text-[#546b79]">
                    H×{ctx.developing_high_low.high_touches} L×{ctx.developing_high_low.low_touches}
                  </div>
                </div>
              )}

              {/* Session phase progress */}
              {ctx.session_phase && ctx.session_phase.progress_pct > 0 && ctx.session_phase.progress_pct < 100 && (
                <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
                  <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
                    Session
                  </div>
                  <div className="mt-1.5 text-sm font-semibold text-[#d8eef6]">
                    {ctx.session_phase.label}
                  </div>
                  <div className="mt-1.5 h-1 rounded-full bg-[#18242f]">
                    <div
                      className="h-1 rounded-full bg-[#a78bfa] transition-all"
                      style={{ width: `${ctx.session_phase.progress_pct}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
