import type { CategoryScore } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface ScorePanelProps {
  name: string
  data: CategoryScore
}

const DIRECTION_COLOR: Record<string, string> = {
  healthy: 'text-[#4ade80]',
  improving: 'text-[#86efac]',
  neutral: 'text-[#94a3b8]',
  weakening: 'text-[#fbbf24]',
  'risk-off': 'text-[#f87171]',
}

export function ScorePanel({ name, data }: ScorePanelProps) {
  const scoreColor =
    data.score >= 70
      ? 'text-[#4ade80]'
      : data.score >= 50
        ? 'text-[#fbbf24]'
        : 'text-[#f87171]'
  const barColor =
    data.score >= 70
      ? 'bg-[#16a34a]'
      : data.score >= 50
        ? 'bg-[#d97706]'
        : 'bg-[#dc2626]'

  return (
    <div className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">{name}</span>
        <span className={cn('text-2xl font-bold', scoreColor)}>{data.score}</span>
      </div>

      <div className="mt-3 h-1.5 rounded-full bg-[#18242f]">
        <div
          className={cn('h-1.5 rounded-full transition-all', barColor)}
          style={{ width: `${Math.max(0, Math.min(100, data.score))}%` }}
        />
      </div>

      <div className="mt-3 flex items-center justify-between text-[10px] uppercase tracking-[0.22em]">
        <span className="text-[#546b79]">Status</span>
        <span className={cn(DIRECTION_COLOR[data.direction] || 'text-[#94a3b8]')}>
          {data.direction}
        </span>
      </div>

      <div className="mt-2 text-[10px] text-[#6b8797]">
        Weight {(data.weight * 100).toFixed(0)}% | Contribution {(data.score * data.weight).toFixed(1)}
      </div>
    </div>
  )
}
