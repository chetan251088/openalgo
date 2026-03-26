import type { GlobalContextData } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface GlobalCorrelationProps {
  data: GlobalContextData | null
}

export function GlobalCorrelation({ data }: GlobalCorrelationProps) {
  if (!data) return null

  const rs = data.nifty_banknifty_rs
  const gap = data.gap_context
  const commodities = data.commodities || {}
  const hasCommodities = Object.keys(commodities).length > 0

  if (!rs && !gap && !hasCommodities) return null

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
        Cross-Market Context
      </span>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {/* Nifty vs BankNifty RS */}
        {rs && (
          <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
            <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
              Nifty / BankNifty RS
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-lg font-bold text-[#d8eef6]">
                {rs.spread >= 0 ? '+' : ''}
                {rs.spread.toFixed(2)}
              </span>
              <span className="text-[10px] text-[#6b8797]">spread</span>
            </div>
            <div className="mt-1 grid grid-cols-2 gap-2 text-[10px]">
              <div>
                <span className="text-[#6b8797]">Nifty </span>
                <span
                  className={cn(
                    rs.nifty_change_pct >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]',
                  )}
                >
                  {rs.nifty_change_pct >= 0 ? '+' : ''}
                  {rs.nifty_change_pct.toFixed(2)}%
                </span>
              </div>
              <div>
                <span className="text-[#6b8797]">BN </span>
                <span
                  className={cn(
                    rs.banknifty_change_pct >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]',
                  )}
                >
                  {rs.banknifty_change_pct >= 0 ? '+' : ''}
                  {rs.banknifty_change_pct.toFixed(2)}%
                </span>
              </div>
            </div>
            <div className="mt-2 text-[10px] italic text-[#7fa2b1]">{rs.note}</div>
          </div>
        )}

        {/* Gap Context */}
        {gap && (
          <div className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
            <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
              Gap Analysis
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span
                className={cn(
                  'text-lg font-bold',
                  gap.gap_pct >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]',
                )}
              >
                {gap.gap_pct >= 0 ? '+' : ''}
                {gap.gap_pct.toFixed(2)}%
              </span>
              <span className="text-[10px] uppercase text-[#6b8797]">{gap.gap_type.replace(/_/g, ' ')}</span>
            </div>
            <div className="mt-1 text-[10px] text-[#6b8797]">
              PDC: {gap.prev_close.toLocaleString('en-IN')}
              {gap.open && ` · Open: ${gap.open.toLocaleString('en-IN')}`}
            </div>
            {gap.gap_filled && (
              <div className="mt-1 text-[10px] font-semibold text-[#fbbf24]">
                Gap filled ✓
              </div>
            )}
          </div>
        )}

        {/* Commodities */}
        {Object.entries(commodities).map(([key, commodity]) => (
          <div key={key} className="rounded-xl border border-[#1a3140] bg-[#0b1620] p-3">
            <div className="text-[9px] uppercase tracking-[0.2em] text-[#6b8797]">
              {key === 'gold' ? '🥇 Gold' : '🛢️ Crude'}
            </div>
            <div className="mt-2 text-lg font-bold text-[#d8eef6]">
              {commodity.ltp?.toLocaleString('en-IN')}
            </div>
            {commodity.change_pct !== null && (
              <div
                className={cn(
                  'mt-1 text-xs font-semibold',
                  (commodity.change_pct ?? 0) >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]',
                )}
              >
                {(commodity.change_pct ?? 0) >= 0 ? '+' : ''}
                {commodity.change_pct?.toFixed(2)}%
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
