import type { CategoryScore } from '@/api/market-pulse'
import { AlertCircle, CheckCircle, MinusCircle } from 'lucide-react'

interface RulesFiringProps {
  scores: Record<string, CategoryScore>
}

export function RulesFiring({ scores }: RulesFiringProps) {
  const rules = Object.entries(scores)
    .flatMap(([category, data]) =>
      data.rules.map((rule) => ({
        category,
        ...rule,
      }))
    )

  const getIcon = (impact: string) => {
    switch (impact) {
      case 'positive':
        return <CheckCircle size={14} className="text-green-400" />
      case 'negative':
        return <AlertCircle size={14} className="text-red-400" />
      default:
        return <MinusCircle size={14} className="text-gray-400" />
    }
  }

  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <h3 className="font-mono text-sm font-bold text-white mb-3">Rules Firing</h3>
      <div className="max-h-64 overflow-y-auto space-y-2">
        {rules.length === 0 ? (
          <p className="text-xs text-gray-500">No rules fired</p>
        ) : (
          rules.map((rule, idx) => (
            <div key={idx} className="flex gap-2 p-2 bg-[#0d1117] rounded text-xs">
              {getIcon(rule.impact)}
              <div className="flex-1 min-w-0">
                <p className="text-gray-300">{rule.rule}</p>
                <p className="text-gray-500 text-xs">{rule.detail}</p>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
