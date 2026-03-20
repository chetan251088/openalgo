import type { FnoIdea } from '@/api/market-pulse'

interface FnoIdeasProps {
  ideas: FnoIdea[]
  regime: string
  vixLevel: number | null
}

export function FnoIdeas({ ideas, regime, vixLevel }: FnoIdeasProps) {
  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-mono text-sm font-bold text-white">F&O Ideas</h3>
        <div className="text-xs text-gray-400 space-x-4">
          <span>Regime: <span className="text-white capitalize">{regime}</span></span>
          {vixLevel !== null && <span>VIX: <span className="text-white">{vixLevel.toFixed(2)}</span></span>}
        </div>
      </div>

      {ideas.length === 0 ? (
        <p className="text-xs text-gray-500">No ideas available</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-[#30363d] text-gray-400">
                <th className="text-left p-2">Instrument</th>
                <th className="text-left p-2">Strategy</th>
                <th className="text-left p-2">Strikes</th>
                <th className="text-left p-2">Bias</th>
                <th className="text-left p-2">Rationale</th>
              </tr>
            </thead>
            <tbody>
              {ideas.map((idea, idx) => (
                <tr key={idx} className="border-b border-[#30363d] hover:bg-[#0d1117]">
                  <td className="p-2 text-white font-bold">{idea.instrument}</td>
                  <td className="p-2 text-gray-300">{idea.strategy}</td>
                  <td className="p-2 text-gray-400">{idea.strikes}</td>
                  <td className="p-2">
                    <span
                      className={`${
                        idea.bias === 'bullish' || idea.bias === 'call'
                          ? 'text-green-400'
                          : 'text-red-400'
                      }`}
                    >
                      {idea.bias}
                    </span>
                  </td>
                  <td className="p-2 text-gray-400 truncate">{idea.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
