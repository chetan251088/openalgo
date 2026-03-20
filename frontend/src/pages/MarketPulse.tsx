import { useEffect, useState } from 'react'
import { TickerBar } from '@/components/market-pulse/TickerBar'
import { AlertBanner } from '@/components/market-pulse/AlertBanner'
import { HeroDecision } from '@/components/market-pulse/HeroDecision'
import { ScorePanel } from '@/components/market-pulse/ScorePanel'
import { RulesFiring } from '@/components/market-pulse/RulesFiring'
import { TerminalAnalysis } from '@/components/market-pulse/TerminalAnalysis'
import { SectorHeatmap } from '@/components/market-pulse/SectorHeatmap'
import { ScoreBreakdown } from '@/components/market-pulse/ScoreBreakdown'
import { EquityIdeas } from '@/components/market-pulse/EquityIdeas'
import { FnoIdeas } from '@/components/market-pulse/FnoIdeas'
import { useMarketPulse } from '@/hooks/useMarketPulse'
import { AlertCircle } from 'lucide-react'

export default function MarketPulse() {
  const { data, isLoading, error: queryError, mode, setMode, refresh, secondsAgo } = useMarketPulse()
  const [internalSecondsAgo, setInternalSecondsAgo] = useState(0)

  // Update seconds ago counter
  useEffect(() => {
    const interval = setInterval(() => {
      setInternalSecondsAgo((prev) => prev + 1)
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  // Reset counter when data updates
  useEffect(() => {
    setInternalSecondsAgo(0)
  }, [data?.updated_at])

  const totalScore = data
    ? (data.market_quality_score + data.execution_window_score) / 2
    : 0

  const vixLevel = data?.ticker['NSE:INDIAVIX-INDEX']?.ltp ?? null

  return (
    <div className="h-screen bg-[#0a0e1a] flex flex-col overflow-hidden">
      {/* Header Ticker Bar */}
      <TickerBar
        data={data ?? null}
        mode={mode}
        onModeChange={setMode}
        secondsAgo={secondsAgo ?? internalSecondsAgo}
        onRefresh={refresh}
        isLoading={isLoading}
      />

      {/* Alert Banner */}
      {data?.alerts && data.alerts.length > 0 && (
        <AlertBanner alerts={data.alerts} />
      )}

      {/* Error Banner */}
      {queryError && (
        <div className="bg-red-900/30 border-b border-red-700/50 px-6 py-3 flex items-center gap-3 text-red-200">
          <AlertCircle size={18} />
          <span className="text-sm">{queryError instanceof Error ? queryError.message : 'Failed to load market pulse data'}</span>
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-12 gap-4 p-6">
          {/* Left Column - Hero & Rules */}
          <div className="col-span-4 space-y-4">
            {/* Hero Decision */}
            <HeroDecision
              decision={data?.decision ?? 'CAUTION'}
              qualityScore={data?.market_quality_score ?? 0}
              executionScore={data?.execution_window_score ?? 0}
            />

            {/* Terminal Analysis */}
            <TerminalAnalysis analysis={data?.analysis ?? null} />

            {/* Rules Firing */}
            {data?.scores && <RulesFiring scores={data.scores} />}
          </div>

          {/* Right Column - Scores & Heatmap */}
          <div className="col-span-8 space-y-4">
            {/* Score Panels Grid */}
            {data?.scores && (
              <div className="grid grid-cols-3 gap-4">
                {Object.entries(data.scores).map(([name, scoreData]) => (
                  <ScorePanel
                    key={name}
                    name={name.charAt(0).toUpperCase() + name.slice(1)}
                    data={scoreData}
                  />
                ))}
              </div>
            )}

            {/* Sector Heatmap */}
            {data?.sectors && (
              <SectorHeatmap sectors={data.sectors} />
            )}

            {/* Score Breakdown */}
            {data?.scores && (
              <ScoreBreakdown scores={data.scores} totalScore={totalScore} />
            )}
          </div>
        </div>

        {/* Bottom Row - Ideas */}
        {data && (
          <div className="grid grid-cols-2 gap-4 p-6 pt-0">
            <EquityIdeas ideas={data.equity_ideas} />
            <FnoIdeas
              ideas={data.fno_ideas}
              regime={data.regime}
              vixLevel={vixLevel}
            />
          </div>
        )}
      </div>
    </div>
  )
}
