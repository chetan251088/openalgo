import type { FnoIdea } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface FnoIdeasProps {
  ideas: FnoIdea[]
  regime: string
  vixLevel?: number
}

export function FnoIdeas({ ideas, regime, vixLevel }: FnoIdeasProps) {
  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          F&O Ideas
        </span>
        <div className="text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
          VIX {typeof vixLevel === 'number' ? vixLevel.toFixed(1) : 'n/a'} | Regime {regime}
        </div>
      </div>

      {ideas.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-[#223847] bg-[#09111a] p-6 text-sm text-[#6b8797]">
          No derivatives ideas available for the current tape.
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead>
              <tr className="border-b border-[#1d2e3b] text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
                <th className="pb-2 pr-4">Instrument</th>
                <th className="pb-2 pr-4">Strategy</th>
                <th className="pb-2 pr-4">Strikes</th>
                <th className="pb-2 pr-4">Bias</th>
                <th className="pb-2">Rationale</th>
              </tr>
            </thead>
            <tbody>
              {ideas.map((idea, index) => (
                <tr key={`${idea.instrument}-${idea.strategy}-${index}`} className="border-b border-[#15232d]/80 text-[#d8eef6] last:border-transparent">
                  <td className="py-3 pr-4 font-semibold">{idea.instrument}</td>
                  <td className="py-3 pr-4 text-[#67e8f9]">{idea.strategy}</td>
                  <td className="py-3 pr-4 text-[#fde68a]">{idea.strikes}</td>
                  <td className="py-3 pr-4">
                    <span
                      className={cn(
                        'rounded-full border px-2 py-1 text-[10px] uppercase tracking-[0.18em]',
                        idea.bias === 'bullish'
                          ? 'border-[#14532d] text-[#4ade80]'
                          : idea.bias === 'bearish'
                            ? 'border-[#7f1d1d] text-[#f87171]'
                            : 'border-[#334155] text-[#94a3b8]',
                      )}
                    >
                      {idea.bias}
                    </span>
                  </td>
                  <td className="py-1 text-[#7fa2b1] truncate text-ellipsis overflow-hidden max-w-[200px]">{idea.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
