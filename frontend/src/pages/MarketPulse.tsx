import type { CategoryScore } from '@/api/market-pulse'
import { AlertBanner } from '@/components/market-pulse/AlertBanner'
import { EquityIdeas } from '@/components/market-pulse/EquityIdeas'
import { FnoIdeas } from '@/components/market-pulse/FnoIdeas'
import { HeroDecision } from '@/components/market-pulse/HeroDecision'
import { RulesFiring } from '@/components/market-pulse/RulesFiring'
import { ScoreBreakdown } from '@/components/market-pulse/ScoreBreakdown'
import { ScorePanel } from '@/components/market-pulse/ScorePanel'
import { SectorHeatmap } from '@/components/market-pulse/SectorHeatmap'
import { TerminalAnalysis } from '@/components/market-pulse/TerminalAnalysis'
import { TickerBar } from '@/components/market-pulse/TickerBar'
import { useMarketPulse } from '@/hooks/useMarketPulse'

export default function MarketPulse() {
  const { data, isLoading, isFetching, error, mode, setMode, refresh, secondsAgo } =
    useMarketPulse()

  if (isLoading && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-[radial-gradient(circle_at_top,rgba(8,145,178,0.18),transparent_35%),linear-gradient(180deg,#071018_0%,#09131c_40%,#0b1220_100%)] px-6 font-mono">
        <div className="text-center">
          <div className="text-sm uppercase tracking-[0.34em] text-[#5eead4]">
            Loading Market Pulse
          </div>
          <div className="mt-3 text-sm text-[#8db5c3]">
            Fetching quotes, breadth, event risk, and execution context.
          </div>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex h-full items-center justify-center bg-[linear-gradient(180deg,#071018_0%,#09131c_40%,#0b1220_100%)] px-6 font-mono">
        <div className="max-w-lg rounded-3xl border border-[#7f1d1d] bg-[#1a0f12]/90 p-8 text-center shadow-[0_24px_70px_rgba(127,29,29,0.18)]">
          <div className="text-[10px] uppercase tracking-[0.34em] text-[#fca5a5]">
            Feed Unavailable
          </div>
          <div className="mt-3 text-sm text-[#fecaca]">
            {error instanceof Error
              ? error.message
              : 'Market Pulse data is unavailable right now.'}
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-5 rounded-full border border-[#7f1d1d] px-4 py-2 text-[10px] uppercase tracking-[0.22em] text-[#fca5a5] transition-colors hover:bg-[#7f1d1d]/20"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  const executionPanel: CategoryScore = {
    score: data.execution_window_score,
    weight: 0,
    direction:
      data.execution_window_score >= 70
        ? 'healthy'
        : data.execution_window_score >= 50
          ? 'neutral'
          : 'weakening',
    rules: [],
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[radial-gradient(circle_at_top,rgba(8,145,178,0.16),transparent_28%),radial-gradient(circle_at_right,rgba(251,191,36,0.08),transparent_24%),linear-gradient(180deg,#071018_0%,#0a131c_42%,#0c1420_100%)] text-white">
      <TickerBar
        data={data}
        mode={mode}
        onModeChange={setMode}
        secondsAgo={secondsAgo}
        onRefresh={refresh}
        isLoading={isFetching}
      />

      {data.alerts.length > 0 && <AlertBanner alerts={data.alerts} />}

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-3 py-4 lg:px-4">
          <div className="grid gap-4 lg:grid-cols-12">
            <div className="space-y-4 lg:col-span-4">
              <HeroDecision
                decision={data.decision}
                qualityScore={data.market_quality_score}
                executionScore={data.execution_window_score}
              />
              <TerminalAnalysis analysis={data.analysis} />
              <RulesFiring scores={data.scores} />
            </div>

            <div className="space-y-4 lg:col-span-8">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                <ScorePanel name="Volatility" data={data.scores.volatility} />
                <ScorePanel name="Momentum" data={data.scores.momentum} />
                <ScorePanel name="Trend" data={data.scores.trend} />
                <ScorePanel name="Breadth" data={data.scores.breadth} />
                <ScorePanel name="Macro" data={data.scores.macro} />
                <ScorePanel name="Execution" data={executionPanel} />
              </div>

              <SectorHeatmap sectors={data.sectors} />
              <ScoreBreakdown
                scores={data.scores}
                totalScore={data.market_quality_score}
              />
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <EquityIdeas ideas={data.equity_ideas} />
            <FnoIdeas
              ideas={data.fno_ideas}
              regime={data.regime}
              vixLevel={data.ticker?.INDIAVIX?.ltp}
            />
          </div>

          {(data.errors.length > 0 || error) && (
            <div className="rounded-2xl border border-[#7f1d1d] bg-[#1a0f12]/80 p-4 font-mono text-xs text-[#fecaca]">
              <div className="text-[10px] uppercase tracking-[0.24em] text-[#fca5a5]">
                Data Issues
              </div>
              <div className="mt-2 space-y-1">
                {data.errors.map((item) => (
                  <div key={item}>{item}</div>
                ))}
                {error instanceof Error && <div>{error.message}</div>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
