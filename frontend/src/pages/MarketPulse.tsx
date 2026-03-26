import { useEffect, useState } from 'react'
import type { CategoryScore } from '@/api/market-pulse'
import { AlertBanner } from '@/components/market-pulse/AlertBanner'
import { EquityIdeas } from '@/components/market-pulse/EquityIdeas'
import { FnoIdeas } from '@/components/market-pulse/FnoIdeas'
import { HeroDecision } from '@/components/market-pulse/HeroDecision'
import InstitutionalFlows from '@/components/market-pulse/InstitutionalFlows'
import { KeyLevels } from '@/components/market-pulse/KeyLevels'
import { OptionsPositioning } from '@/components/market-pulse/OptionsPositioning'
import { RulesFiring } from '@/components/market-pulse/RulesFiring'
import { ScoreBreakdown } from '@/components/market-pulse/ScoreBreakdown'
import { ScorePanel } from '@/components/market-pulse/ScorePanel'
import SectorHeatmap from '@/components/market-pulse/SectorHeatmap'
import { TerminalAnalysis } from '@/components/market-pulse/TerminalAnalysis'
import { TickerBar } from '@/components/market-pulse/TickerBar'
import { useMarketPulse } from '@/hooks/useMarketPulse'
import { useMarketPulseEnhanced } from '@/hooks/useMarketPulseEnhanced'
import { IntradayContext } from '@/components/market-pulse/IntradayContext'
import { ConfluenceBadge } from '@/components/market-pulse/ConfluenceBadge'
import { GlobalCorrelation } from '@/components/market-pulse/GlobalCorrelation'
import { OptionsGreeksDashboard } from '@/components/market-pulse/OptionsGreeksDashboard'
import { AlertHistory } from '@/components/market-pulse/AlertHistory'
import { SignalJournal } from '@/components/market-pulse/SignalJournal'

export default function MarketPulse() {
  const { data, isLoading, isFetching, error, mode, setMode, refresh, secondsAgo } =
    useMarketPulse()
  const equitySymbols = data?.equity_ideas?.map((idea) => idea.symbol) ?? []
  const enhanced = useMarketPulseEnhanced(mode, equitySymbols)
  const [showLoadingTroubleshoot, setShowLoadingTroubleshoot] = useState(false)

  useEffect(() => {
    if (!(isLoading && !data)) {
      setShowLoadingTroubleshoot(false)
      return
    }

    const timer = window.setTimeout(() => {
      setShowLoadingTroubleshoot(true)
    }, 12000)

    return () => window.clearTimeout(timer)
  }, [data, isLoading])

  if (isLoading && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-[radial-gradient(circle_at_top,rgba(8,145,178,0.18),transparent_35%),linear-gradient(180deg,#071018_0%,#09131c_40%,#0b1220_100%)] px-6 font-mono">
        <div className="max-w-xl text-center">
          <div className="text-sm uppercase tracking-[0.34em] text-[#5eead4]">
            Loading Market Pulse
          </div>
          <div className="mt-3 text-sm text-[#8db5c3]">
            Fetching quotes, breadth, event risk, and execution context.
          </div>
          {showLoadingTroubleshoot ? (
            <div className="mt-5 rounded-3xl border border-[#1f3340] bg-[#0d141d]/85 p-5 text-left shadow-[0_18px_60px_rgba(7,16,24,0.45)]">
              <div className="text-[10px] uppercase tracking-[0.28em] text-[#fbbf24]">
                Feed Delayed
              </div>
              <div className="mt-3 text-sm leading-relaxed text-[#d8eef6]">
                Market Pulse is taking longer than expected to hydrate in this tab.
                The backend may already be healthy, but the browser can still hold on to an old
                page shell after a restart.
              </div>
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => void refresh()}
                  className="rounded-full border border-[#1f8ea8] px-4 py-2 text-[10px] uppercase tracking-[0.22em] text-[#67e8f9] transition-colors hover:bg-[#1f8ea8]/20"
                >
                  Retry Feed
                </button>
                <button
                  type="button"
                  onClick={() => window.location.reload()}
                  className="rounded-full border border-[#334155] px-4 py-2 text-[10px] uppercase tracking-[0.22em] text-[#9ac0cd] transition-colors hover:bg-[#334155]/20"
                >
                  Reload Page
                </button>
              </div>
            </div>
          ) : null}
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
                qualityDecision={data.quality_decision}
                qualityScore={data.market_quality_score}
                executionScore={data.execution_window_score}
                directionalBias={data.directional_bias}
              />
              <ConfluenceBadge 
                confluence={data.confluence} 
                riskContext={data.risk_context} 
              />
              <TerminalAnalysis analysis={data.analysis} />
              <RulesFiring scores={data.scores} />
              <AlertHistory data={enhanced.alerts} />
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

              <SectorHeatmap data={enhanced.sectors} />
              <ScoreBreakdown
                scores={data.scores}
                totalScore={data.market_quality_score}
              />
              <GlobalCorrelation data={enhanced.global} />
              {mode === 'day' && <IntradayContext data={enhanced.intraday} />}
            </div>
          </div>

          <InstitutionalFlows data={enhanced.institutional} />

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="space-y-4">
              <OptionsPositioning data={data.options_context} />
              <OptionsGreeksDashboard data={enhanced.greeks} />
            </div>
            <div className="space-y-4">
              <KeyLevels data={data.market_levels} />
              <SignalJournal data={enhanced.journal} />
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <EquityIdeas ideas={data.equity_ideas} fundamentals={enhanced.fundamentals} />
            <FnoIdeas
              ideas={data.fno_ideas}
              regime={data.regime}
              executionRegime={data.execution_regime}
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
