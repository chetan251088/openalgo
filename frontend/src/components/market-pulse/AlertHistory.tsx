import type { AlertsData } from '@/api/market-pulse'

interface AlertHistoryProps {
  data: AlertsData | null
}

export function AlertHistory({ data }: AlertHistoryProps) {
  if (!data) return null

  const history = data.history || []
  if (history.length === 0) return null

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Recent Alerts
        </span>
        <span className="text-[10px] text-[#546b79]">{history.length} total</span>
      </div>

      <div className="mt-3 space-y-2">
        {history
          .slice(-8)
          .reverse()
          .map((alert, i) => {
            const ts = new Date(alert.timestamp)
            const timeStr = ts.toLocaleTimeString('en-IN', {
              hour: '2-digit',
              minute: '2-digit',
            })

            return (
              <div
                key={`${alert.id}-${i}`}
                className="flex items-start gap-3 rounded-xl border border-[#1b2b37] bg-[#09111a]/80 p-3"
              >
                <span className="mt-0.5 text-[10px] tabular-nums text-[#546b79]">{timeStr}</span>
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-[#d8eef6]">{alert.message}</div>
                  <div className="mt-0.5 text-[9px] uppercase tracking-wider text-[#546b79]">
                    {alert.name}
                  </div>
                </div>
              </div>
            )
          })}
      </div>
    </section>
  )
}
