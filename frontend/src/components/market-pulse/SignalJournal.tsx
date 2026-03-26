import type { JournalData } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface SignalJournalProps {
  data: JournalData | null
}

export function SignalJournal({ data }: SignalJournalProps) {
  if (!data) return null

  const signals = data.signals || []
  const stats = data.stats

  if (signals.length === 0) return null

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Trade Journal (High Conviction)
        </span>
      </div>

      {stats && stats.total_signals > 0 && (
        <div className="mt-3 flex flex-wrap gap-4 text-[10px] text-[#6b8797]">
          <span>Signals: <span className="text-[#d8eef6]">{stats.total_signals}</span></span>
          {stats.with_outcome > 0 && (
            <>
              <span>Win Rate: <span className="font-bold text-[#4ade80]">{stats.win_rate}%</span> ({stats.wins}/{stats.with_outcome})</span>
              <span>Avg PnL: <span className={stats.avg_pnl && stats.avg_pnl > 0 ? 'text-[#4ade80]' : 'text-[#f87171]'}>{stats.avg_pnl?.toFixed(2) || 0}%</span></span>
            </>
          )}
        </div>
      )}

      <div className="mt-4 space-y-2">
        {signals.slice(0, 5).map((s) => {
          const ts = new Date(s.timestamp)
          const isClosed = s.exit_price !== null
          
          return (
            <div key={s.signal_id} className="rounded-xl border border-[#1b2b37] bg-[#09111a]/80 p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={cn('text-sm font-bold', s.signal_type === 'BUY' ? 'text-[#4ade80]' : 'text-[#f87171]')}>
                    {s.signal_type}
                  </span>
                  <span className="font-semibold text-[#d8eef6]">{s.symbol}</span>
                  <span className="text-[9px] uppercase tracking-wider text-[#546b79]">{s.sector}</span>
                </div>
                <div className="text-[9px] text-[#546b79]">
                  {ts.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' })}
                </div>
              </div>
              
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px]">
                <div><span className="text-[#546b79]">Entry: </span><span className="text-[#d8eef6]">{s.entry || s.ltp}</span></div>
                {s.target && <div><span className="text-[#546b79]">Tgt: </span><span className="text-[#86efac]">{s.target}</span></div>}
                {s.stop_loss && <div><span className="text-[#546b79]">SL: </span><span className="text-[#fca5a5]">{s.stop_loss}</span></div>}
                {isClosed && s.pnl_pct !== null && (
                  <div>
                    <span className="text-[#546b79]">Result: </span>
                    <span className={cn('font-bold', s.pnl_pct >= 0 ? 'text-[#4ade80]' : 'text-[#f87171]')}>
                      {s.pnl_pct > 0 ? '+' : ''}{s.pnl_pct}%
                    </span>
                  </div>
                )}
              </div>
              <div className="mt-1 text-[10px] text-[#7fa2b1] line-clamp-1">{s.reason}</div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
