import type { EquityIdea } from '@/api/market-pulse'

const SIGNAL_STYLES = {
  BUY: { bg: 'bg-green-900/30', text: 'text-green-400', border: 'border-green-700/30' },
  SELL: { bg: 'bg-red-900/30', text: 'text-red-400', border: 'border-red-700/30' },
  HOLD: { bg: 'bg-yellow-900/30', text: 'text-yellow-400', border: 'border-yellow-700/30' },
  AVOID: { bg: 'bg-gray-900/30', text: 'text-gray-400', border: 'border-gray-700/30' },
}

interface EquityIdeasProps {
  ideas: EquityIdea[]
}

export function EquityIdeas({ ideas }: EquityIdeasProps) {
  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <h3 className="font-mono text-sm font-bold text-white mb-3">Equity Ideas</h3>

      {ideas.length === 0 ? (
        <p className="text-xs text-gray-500">No ideas available</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-[#30363d] text-gray-400">
                <th className="text-left p-2">Symbol</th>
                <th className="text-left p-2">Signal</th>
                <th className="text-right p-2">LTP</th>
                <th className="text-right p-2">Entry</th>
                <th className="text-right p-2">SL</th>
                <th className="text-right p-2">Target</th>
                <th className="text-center p-2">Conv.</th>
                <th className="text-left p-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {ideas.map((idea) => {
                const style = SIGNAL_STYLES[idea.signal]
                const convictionColor =
                  idea.conviction === 'HIGH'
                    ? 'text-green-400'
                    : idea.conviction === 'MED'
                      ? 'text-yellow-400'
                      : 'text-red-400'

                return (
                  <tr key={idea.symbol} className="border-b border-[#30363d] hover:bg-[#0d1117]">
                    <td className="p-2 text-white font-bold">{idea.symbol}</td>
                    <td className="p-2">
                      <span className={`px-2 py-1 rounded ${style.bg} ${style.text} border ${style.border}`}>
                        {idea.signal}
                      </span>
                    </td>
                    <td className="p-2 text-right text-white">{idea.ltp.toFixed(2)}</td>
                    <td className="p-2 text-right text-gray-400">{idea.entry?.toFixed(2) ?? '-'}</td>
                    <td className="p-2 text-right text-gray-400">{idea.stop_loss?.toFixed(2) ?? '-'}</td>
                    <td className="p-2 text-right text-gray-400">{idea.target?.toFixed(2) ?? '-'}</td>
                    <td className={`p-2 text-center ${convictionColor}`}>{idea.conviction[0]}</td>
                    <td className="p-2 text-gray-400 truncate">{idea.reason}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
