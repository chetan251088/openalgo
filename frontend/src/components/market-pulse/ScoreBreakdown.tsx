import type { CategoryScore } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface ScoreBreakdownProps {
  scores: Record<string, CategoryScore>
  totalScore: number
}

const CATEGORY_COLORS: Record<string, string> = {
  volatility: 'bg-[#7c3aed]',
  momentum: 'bg-[#0284c7]',
  trend: 'bg-[#16a34a]',
  breadth: 'bg-[#d97706]',
  macro: 'bg-[#0891b2]',
}

export function ScoreBreakdown({ scores, totalScore }: ScoreBreakdownProps) {
  const contributions = Object.entries(scores).map(([key, data]) => ({
    key,
    contribution: data.score * data.weight,
    score: data.score,
    weight: data.weight,
  }))

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
        Score Contribution
      </div>

      <div className="mt-4 flex h-4 overflow-hidden rounded-full bg-[#15232d]">
        {contributions.map(({ key, contribution }) => (
          <div
            key={key}
            className={cn(CATEGORY_COLORS[key], 'opacity-85')}
            style={{ width: `${(contribution / Math.max(totalScore, 1)) * 100}%` }}
            title={`${key}: ${contribution.toFixed(1)}`}
          />
        ))}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {contributions.map(({ key, contribution, score, weight }) => (
          <div key={key} className="rounded-2xl border border-[#1d2e3b] bg-[#0a1118] p-3 text-center">
            <div className="flex items-center justify-center gap-2">
              <div className={cn('h-2.5 w-2.5 rounded-full', CATEGORY_COLORS[key])} />
              <span className="text-[10px] uppercase tracking-[0.18em] text-[#7fa2b1]">
                {key}
              </span>
            </div>
            <div className="mt-2 text-lg font-semibold text-[#e7f7fb]">
              {contribution.toFixed(1)}
            </div>
            <div className="mt-1 text-[10px] text-[#6b8797]">
              {score} x {(weight * 100).toFixed(0)}%
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
