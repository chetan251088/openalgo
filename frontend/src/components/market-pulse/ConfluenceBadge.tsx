import type { ConfluenceData, RiskContext } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface ConfluenceBadgeProps {
  confluence?: ConfluenceData
  riskContext?: RiskContext
}

const COLOR_MAP: Record<string, string> = {
  green: 'border-[#14532d] bg-[#14532d]/15 text-[#4ade80]',
  red: 'border-[#7f1d1d] bg-[#7f1d1d]/15 text-[#f87171]',
  amber: 'border-[#92400e] bg-[#92400e]/15 text-[#fbbf24]',
  cyan: 'border-[#1e3a5f] bg-[#1e3a5f]/15 text-[#67e8f9]',
}

const RISK_COLORS: Record<string, string> = {
  AGGRESSIVE: 'text-[#4ade80]',
  NORMAL: 'text-[#67e8f9]',
  REDUCED: 'text-[#fbbf24]',
  MINIMAL: 'text-[#f87171]',
}

export function ConfluenceBadge({ confluence, riskContext }: ConfluenceBadgeProps) {
  if (!confluence && !riskContext) return null

  return (
    <div className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      {confluence && (
        <div>
          <div className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
            Multi-Timeframe Confluence
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <span
              className={cn(
                'inline-flex rounded-full border px-3 py-1.5 text-sm font-bold uppercase tracking-[0.18em]',
                COLOR_MAP[confluence.color] || COLOR_MAP.cyan,
              )}
            >
              {confluence.level}
            </span>
            <div>
              <div className="text-sm font-semibold text-[#d8eef6]">{confluence.label}</div>
              <div className="text-[10px] text-[#6b8797]">Action: {confluence.action}</div>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg border border-[#1a3140] bg-[#0b1620] p-2">
              <div className="text-[8px] uppercase tracking-wider text-[#546b79]">Structural</div>
              <div className="mt-0.5 text-xs font-semibold text-[#d8eef6]">
                {confluence.structural_regime}
              </div>
            </div>
            <div className="rounded-lg border border-[#1a3140] bg-[#0b1620] p-2">
              <div className="text-[8px] uppercase tracking-wider text-[#546b79]">Intraday</div>
              <div className="mt-0.5 text-xs font-semibold text-[#d8eef6]">
                {confluence.intraday_regime}
              </div>
            </div>
            <div className="rounded-lg border border-[#1a3140] bg-[#0b1620] p-2">
              <div className="text-[8px] uppercase tracking-wider text-[#546b79]">Confidence</div>
              <div className="mt-0.5 text-xs font-bold text-[#67e8f9]">{confluence.confidence}%</div>
            </div>
          </div>
        </div>
      )}

      {riskContext && (
        <div className={confluence ? 'mt-4 border-t border-[#1b2b37] pt-4' : ''}>
          <div className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
            Position Sizing
          </div>
          <div className="mt-2 flex items-center gap-3">
            <span
              className={cn(
                'text-2xl font-bold',
                RISK_COLORS[riskContext.size_label] || 'text-[#94a3b8]',
              )}
            >
              {riskContext.risk_per_trade_pct}%
            </span>
            <div>
              <div
                className={cn(
                  'text-xs font-semibold uppercase',
                  RISK_COLORS[riskContext.size_label] || 'text-[#94a3b8]',
                )}
              >
                {riskContext.size_label}
              </div>
              <div className="text-[10px] text-[#6b8797]">risk per trade</div>
            </div>
          </div>
          <div className="mt-2 text-[10px] leading-relaxed text-[#7fa2b1]">
            {riskContext.context}
          </div>
        </div>
      )}
    </div>
  )
}
