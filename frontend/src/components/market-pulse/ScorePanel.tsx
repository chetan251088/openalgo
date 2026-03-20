import type { CategoryScore } from '@/api/market-pulse'
import { TrendingUp, TrendingDown } from 'lucide-react'

interface ScorePanelProps {
  name: string
  data: CategoryScore
}

export function ScorePanel({ name, data }: ScorePanelProps) {
  const getColor = (score: number) => {
    if (score >= 70) return 'bg-green-400'
    if (score >= 40) return 'bg-yellow-400'
    return 'bg-red-400'
  }

  const isPositive = data.direction === 'positive' || data.direction === 'bullish'

  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-mono text-sm font-bold text-white">{name}</h3>
        {isPositive ? (
          <TrendingUp size={16} className="text-green-400" />
        ) : (
          <TrendingDown size={16} className="text-red-400" />
        )}
      </div>

      <div className="mb-3">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-lg font-bold text-white">{data.score.toFixed(0)}</span>
          <span className="text-xs text-gray-400">{(data.weight * 100).toFixed(0)}% weight</span>
        </div>
        <div className="w-full h-2 bg-[#0d1117] rounded overflow-hidden">
          <div
            className={`h-full ${getColor(data.score)} transition-all`}
            style={{ width: `${Math.min(data.score, 100)}%` }}
          />
        </div>
      </div>

      <p className="text-xs text-gray-400 capitalize">{data.direction}</p>
    </div>
  )
}
