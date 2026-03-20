interface TerminalAnalysisProps {
  analysis: string | null
}

export function TerminalAnalysis({ analysis }: TerminalAnalysisProps) {
  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#081018]/90 p-4 font-mono shadow-[inset_0_1px_0_rgba(148,163,184,0.05)]">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Terminal Analysis
        </span>
        <span className="rounded-full border border-[#223847] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
          LLM
        </span>
      </div>
      <div className="mt-3 rounded-2xl border border-[#152733] bg-[#050b12] p-4">
        <p className="whitespace-pre-wrap text-sm leading-7 text-[#d8eef6]">
          {analysis || 'Awaiting fresh market commentary.'}
        </p>
      </div>
    </section>
  )
}
