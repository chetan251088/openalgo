import type { EquityIdea } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface EquityIdeasProps {
  ideas: EquityIdea[]
}

const SIGNAL_STYLES: Record<string, string> = {
  BUY: 'border-[#14532d] bg-[#14532d]/20 text-[#4ade80]',
  SELL: 'border-[#7f1d1d] bg-[#7f1d1d]/20 text-[#f87171]',
  HOLD: 'border-[#713f12] bg-[#713f12]/20 text-[#fbbf24]',
  AVOID: 'border-[#334155] bg-[#334155]/20 text-[#94a3b8]',
}

function formatPrice(value: number | null) {
  if (typeof value !== 'number') return 'n/a'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

export function EquityIdeas({ ideas }: EquityIdeasProps) {
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
                <th className="pb-2 pr-4">Conviction</th>
                <th className="pb-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {ideas.map((idea) => (
                <tr key={idea.symbol} className="border-b border-[#15232d]/80 text-[#d8eef6] last:border-transparent">
                  <td className="py-3 pr-4">
                    <div className="font-semibold">{idea.symbol}</div>
                    <div className="mt-1 text-[10px] uppercase tracking-[0.16em] text-[#6b8797]">
                      {idea.sector}
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
                  <td className="py-3 pr-4 text-[#9ac0cd]">{idea.conviction}</td>
                  <td className="py-1 text-[#7fa2b1] truncate text-ellipsis overflow-hidden max-w-[200px]">{idea.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
