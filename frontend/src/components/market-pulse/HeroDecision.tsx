const DECISION_STYLES = {
  YES: { bg: 'bg-green-900/30', text: 'text-green-400', border: 'border-green-700/50' },
  CAUTION: { bg: 'bg-yellow-900/30', text: 'text-yellow-400', border: 'border-yellow-700/50' },
  NO: { bg: 'bg-red-900/30', text: 'text-red-400', border: 'border-red-700/50' },
}

interface HeroDecisionProps {
  decision: 'YES' | 'CAUTION' | 'NO'
  qualityScore: number
  executionScore: number
}

export function HeroDecision({ decision, qualityScore, executionScore }: HeroDecisionProps) {
  const style = DECISION_STYLES[decision]
  const avgScore = (qualityScore + executionScore) / 2

  return (
    <div className={`rounded border ${style.border} ${style.bg} p-6 text-center`}>
      <div className="relative w-48 h-48 mx-auto mb-4">
        <svg className="w-full h-full" viewBox="0 0 200 200">
          <circle cx="100" cy="100" r="95" fill="none" stroke="#30363d" strokeWidth="2" />
          <circle
            cx="100"
            cy="100"
            r="95"
            fill="none"
            stroke="currentColor"
            strokeWidth="4"
            strokeDasharray={`${2 * Math.PI * 95 * (avgScore / 100)} ${2 * Math.PI * 95}`}
            strokeLinecap="round"
            className={style.text}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={`text-4xl font-bold ${style.text}`}>{avgScore.toFixed(0)}%</span>
        </div>
      </div>

      <h2 className={`text-5xl font-bold mb-2 ${style.text}`}>{decision}</h2>
      <p className="text-gray-400 text-sm mb-4">Quality: {qualityScore.toFixed(0)}% | Execution: {executionScore.toFixed(0)}%</p>
    </div>
  )
}
