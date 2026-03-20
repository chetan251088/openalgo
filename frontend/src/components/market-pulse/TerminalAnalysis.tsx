interface TerminalAnalysisProps {
  analysis: string | null
}

export function TerminalAnalysis({ analysis }: TerminalAnalysisProps) {
  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <h3 className="font-mono text-sm font-bold text-white mb-3">Terminal Analysis</h3>
      <p className="font-mono text-xs text-gray-400 whitespace-pre-wrap">
        {analysis || 'No analysis available'}
      </p>
    </div>
  )
}
