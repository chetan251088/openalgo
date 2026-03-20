import type { CategoryScore } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface RulesFiringProps {
  scores: Record<string, CategoryScore>
}

export function RulesFiring({ scores }: RulesFiringProps) {
  const allRules = Object.entries(scores).flatMap(([category, data]) =>
    data.rules.map((rule) => ({ ...rule, category })),
  )

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
        Rules Firing
      </div>
      <div className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
        {allRules.length === 0 && (
          <div className="text-xs text-[#6b8797]">No active rules yet.</div>
        )}
        {allRules.map((rule, index) => (
          <div key={`${rule.category}-${rule.rule}-${index}`} className="flex gap-3 text-xs">
            <span
              className={cn(
                'mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full border text-[10px] uppercase',
                rule.impact === 'positive'
                  ? 'border-[#14532d] text-[#4ade80]'
                  : rule.impact === 'negative'
                    ? 'border-[#7f1d1d] text-[#f87171]'
                    : 'border-[#713f12] text-[#fbbf24]',
              )}
            >
              {rule.impact === 'positive' ? '+' : rule.impact === 'negative' ? '-' : '0'}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[#d7eef6]">{rule.rule}</span>
                <span className="rounded-full border border-[#223847] px-2 py-0.5 text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
                  {rule.category}
                </span>
              </div>
              <div className="mt-1 text-[#6b8797]">{rule.detail}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
