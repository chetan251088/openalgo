import type { DirectionalBias } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface HeroDecisionProps {
  decision: 'YES' | 'CAUTION' | 'NO'
  qualityDecision?: 'YES' | 'CAUTION' | 'NO'
  qualityScore: number
  executionScore: number
  directionalBias: DirectionalBias
}

const DECISION_STYLES = {
  YES: {
    border: 'border-[#166534]',
    glow: 'shadow-[0_18px_50px_rgba(22,163,74,0.18)]',
    text: 'text-[#4ade80]',
    ring: '#4ade80',
  },
  CAUTION: {
    border: 'border-[#92400e]',
    glow: 'shadow-[0_18px_50px_rgba(245,158,11,0.18)]',
    text: 'text-[#fbbf24]',
    ring: '#fbbf24',
  },
  NO: {
    border: 'border-[#991b1b]',
    glow: 'shadow-[0_18px_50px_rgba(239,68,68,0.18)]',
    text: 'text-[#f87171]',
    ring: '#f87171',
  },
}

export function HeroDecision({
  decision,
  qualityDecision,
  qualityScore,
  executionScore,
  directionalBias,
}: HeroDecisionProps) {
  const style = DECISION_STYLES[decision]
  const dash = Math.max(0, Math.min(100, qualityScore)) * 2.64
  const biasStyle =
    directionalBias.bias === 'LONG'
      ? 'border-[#14532d] bg-[#14532d]/15 text-[#4ade80]'
      : directionalBias.bias === 'SHORT'
        ? 'border-[#7f1d1d] bg-[#7f1d1d]/15 text-[#f87171]'
        : 'border-[#334155] bg-[#334155]/15 text-[#cbd5e1]'

  return (
    <section
      className={cn(
        'overflow-hidden rounded-3xl border bg-[radial-gradient(circle_at_top,rgba(20,83,45,0.16),transparent_45%),linear-gradient(145deg,#09111a_0%,#08131d_45%,#101a24_100%)] p-6 font-mono',
        style.border,
        style.glow,
      )}
    >
      <div className="text-[10px] uppercase tracking-[0.34em] text-[#7fa2b1]">
        Should I Trade India
      </div>

      <div className={cn('mt-4 text-5xl font-bold tracking-[0.2em]', style.text)}>
        {decision}
      </div>

      <div className="mt-6 flex items-center gap-6">
        <div className="relative h-28 w-28 shrink-0">
          <svg className="h-28 w-28 -rotate-90" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="42" fill="none" stroke="#1d2a35" strokeWidth="6" />
            <circle
              cx="50"
              cy="50"
              r="42"
              fill="none"
              stroke={style.ring}
              strokeWidth="6"
              strokeDasharray={`${dash} 264`}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className={cn('text-3xl font-bold', style.text)}>{qualityScore}</span>
            <span className="text-[10px] uppercase tracking-[0.22em] text-[#6b8797]">
              Score
            </span>
          </div>
        </div>

        <div className="space-y-3 text-sm">
          <div>
            <div className="text-[10px] uppercase tracking-[0.24em] text-[#6b8797]">
              Market Quality
            </div>
            <div className="text-[#d9f2f9]">
              Weighted read of volatility, momentum, trend, breadth, and macro.
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl border border-[#213240] bg-[#0e1720]/80 p-3">
              <div className="text-[10px] uppercase tracking-[0.24em] text-[#6b8797]">
                Directional Bias
              </div>
              <div className="mt-2 flex items-center gap-2">
                <span
                  className={cn(
                    'inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]',
                    biasStyle,
                  )}
                >
                  {directionalBias.bias}
                </span>
                <span className="text-lg font-bold text-[#d9f2f9]">
                  {directionalBias.confidence}
                </span>
              </div>
              {qualityDecision ? (
                <div className="mt-2 text-[10px] uppercase tracking-[0.18em] text-[#6b8797]">
                  Base Quality {qualityDecision}
                </div>
              ) : null}
            </div>
            <div className="rounded-2xl border border-[#213240] bg-[#0e1720]/80 p-3">
              <div className="text-[10px] uppercase tracking-[0.24em] text-[#6b8797]">
                Execution Window
              </div>
              <div
                className={cn(
                  'mt-1 text-2xl font-bold',
                  executionScore >= 70
                    ? 'text-[#4ade80]'
                    : executionScore >= 50
                      ? 'text-[#fbbf24]'
                      : 'text-[#f87171]',
                )}
              >
                {executionScore}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
