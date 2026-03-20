import type { CategoryScore } from '@/api/market-pulse'

const CATEGORY_COLORS: Record<string, string> = {
  volatility: 'bg-purple-500',
  momentum: 'bg-blue-500',
  trend: 'bg-green-500',
  breadth: 'bg-yellow-500',
  macro: 'bg-red-500',
}

interface ScoreBreakdownProps {
  scores: Record<string, CategoryScore>
  totalScore: number
}

export function ScoreBreakdown({ scores, totalScore }: ScoreBreakdownProps) {
  const entries = Object.entries(scores)
  const total = entries.reduce((sum, [, data]) => sum + data.score * data.weight, 0)

  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <h3 className="font-mono text-sm font-bold text-white mb-3">Score Breakdown</h3>

      <div className="flex h-6 rounded overflow-hidden border border-[#30363d] mb-4">
        {entries.map(([category, data]) => {
          const contribution = (data.score * data.weight) / total
          return (
            <div
              key={category}
              className={`${CATEGORY_COLORS[category]}`}
              style={{ width: `${contribution * 100}%` }}
              title={`${category}: ${(contribution * 100).toFixed(1)}%`}
            />
          )
        })}
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        {entries.map(([category, data]) => (
          <div key={category} className="flex items-center gap-2">
            <div className={`w-3 h-3 rounded ${CATEGORY_COLORS[category]}`} />
            <span className="text-gray-400 capitalize">{category}</span>
            <span className="text-white font-bold ml-auto">{(data.score * data.weight).toFixed(0)}</span>
          </div>
        ))}
      </div>

      <div className="mt-3 pt-3 border-t border-[#30363d] flex justify-between">
        <span className="text-gray-400 text-xs">Total Score</span>
        <span className="text-white font-bold">{totalScore.toFixed(0)}</span>
      </div>
    </div>
  )
}
