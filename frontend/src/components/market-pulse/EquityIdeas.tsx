import type { EquityIdea, FundamentalsData } from '@/api/market-pulse'
import { cn } from '@/lib/utils'
import FundamentalBadge from '@/components/market-pulse/FundamentalBadge'

interface EquityIdeasProps {
  ideas: EquityIdea[]
  fundamentals?: FundamentalsData | null
}

const SIGNAL_STYLES: Record<string, string> = {
  BUY: 'border-[#14532d] bg-[#14532d]/20 text-[#4ade80]',
  SELL: 'border-[#7f1d1d] bg-[#7f1d1d]/20 text-[#f87171]',
  HOLD: 'border-[#713f12] bg-[#713f12]/20 text-[#fbbf24]',
  AVOID: 'border-[#334155] bg-[#334155]/20 text-[#94a3b8]',
}

function formatPrice(value: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function formatRiskReward(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return `${value.toFixed(2)}R`
}

function formatPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function isNonActionableSignal(signal: EquityIdea['signal']) {
  return signal === 'HOLD' || signal === 'AVOID'
}

export function EquityIdeas({ ideas, fundamentals }: EquityIdeasProps) {
  const hasWatchlistRows = ideas.some((idea) => isNonActionableSignal(idea.signal))

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Equity Ideas
        </span>
        <span className="text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
          {ideas.length} setups
        </span>
      </div>

      <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-[#6b8797]">
        {hasWatchlistRows ? (
          <p>
            HOLD and AVOID rows are watchlist signals, so entry and target stay blank until a trade setup appears.
          </p>
        ) : null}
        <p>
          Day ideas require VWAP and RVOL confirmation. Swing rows now show delivery percentage versus the 10-day delivery baseline when NSE archive data is available.
        </p>
      </div>

      {ideas.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-[#223847] bg-[#09111a] p-6 text-sm text-[#6b8797]">
          No ideas match the current regime filters.
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead>
              <tr className="border-b border-[#1d2e3b] text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
                <th className="pb-2 pr-4">Symbol</th>
                <th className="pb-2 pr-4">Signal</th>
                <th className="pb-2 pr-4 text-right">LTP</th>
                <th className="pb-2 pr-4 text-right">Entry</th>
                <th className="pb-2 pr-4 text-right">SL</th>
                <th className="pb-2 pr-4 text-right">Target</th>
                <th className="pb-2 pr-4 text-right">R:R</th>
                <th className="pb-2 pr-4">RS vs NIFTY</th>
                <th className="pb-2 pr-4">Tape / Liquidity</th>
                <th className="pb-2 pr-4">Conviction</th>
                <th className="pb-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {ideas.map((idea) => (
                <tr
                  key={idea.symbol}
                  className={cn(
                    'border-b border-[#15232d]/80 text-[#d8eef6] last:border-transparent',
                    isNonActionableSignal(idea.signal) && 'text-[#b3c9d3]',
                  )}
                >
                  <td className="py-3 pr-4">
                    <div className="font-semibold">{idea.symbol}</div>
                    <div className="mt-1 flex items-center gap-2">
                      <FundamentalBadge symbol={idea.symbol} data={fundamentals?.[idea.symbol]} compact={true} />
                      <span className="text-[10px] uppercase tracking-[0.16em] text-[#6b8797]">
                        {idea.sector}
                      </span>
                    </div>
                  </td>
                  <td className="py-3 pr-4">
                    <span
                      className={cn(
                        'inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]',
                        SIGNAL_STYLES[idea.signal],
                      )}
                    >
                      {idea.signal}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-right">{formatPrice(idea.ltp)}</td>
                  <td className="py-3 pr-4 text-right text-[#9ac0cd]">{formatPrice(idea.entry)}</td>
                  <td className="py-3 pr-4 text-right text-[#fca5a5]">{formatPrice(idea.stop_loss)}</td>
                  <td className="py-3 pr-4 text-right text-[#86efac]">{formatPrice(idea.target)}</td>
                  <td className="py-3 pr-4 text-right text-[#9ac0cd]">{formatRiskReward(idea.risk_reward)}</td>
                  <td className="py-3 pr-4">
                    <div
                      className={cn(
                        'font-semibold',
                        idea.rs_vs_nifty > 0
                          ? 'text-[#86efac]'
                          : idea.rs_vs_nifty < 0
                            ? 'text-[#fca5a5]'
                            : 'text-[#9ac0cd]',
                      )}
                    >
                      {idea.rs_label ?? formatPercent(idea.rs_vs_nifty)}
                    </div>
                    {typeof idea.vwap_distance_pct === 'number' ? (
                      <div className="mt-1 text-[10px] uppercase tracking-[0.16em] text-[#6b8797]">
                        VWAP {formatPercent(idea.vwap_distance_pct)}
                      </div>
                    ) : null}
                  </td>
                  <td className="py-3 pr-4 text-[#9ac0cd]">
                    <div className="max-w-[200px] whitespace-normal leading-relaxed">
                      {idea.liquidity_note ?? '--'}
                    </div>
                  </td>
                  <td className="py-3 pr-4 text-[#9ac0cd]">{idea.conviction}</td>
                  <td className="py-3 text-[#7fa2b1]">
                    <div className="max-w-[240px] whitespace-normal leading-relaxed">
                      {idea.reason}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
