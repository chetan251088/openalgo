import { useState } from 'react'
import type { AlertItem } from '@/api/market-pulse'

interface AlertBannerProps {
  alerts: AlertItem[]
}

export function AlertBanner({ alerts }: AlertBannerProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  const visibleAlerts = alerts.filter((alert) => {
    const key = `${alert.name}-${alert.date}-${alert.time ?? ''}`
    return !dismissed.has(key)
  })

  if (visibleAlerts.length === 0) return null

  return (
    <div className="border-b border-[#5a3d18] bg-[linear-gradient(90deg,rgba(120,53,15,0.28),rgba(69,26,3,0.22))] px-3 py-2 font-mono">
      <div className="flex flex-col gap-2">
        {visibleAlerts.map((alert) => {
          const key = `${alert.name}-${alert.date}-${alert.time ?? ''}`
          return (
            <div key={key} className="flex items-center justify-between gap-3 text-xs">
              <div className="flex flex-wrap items-center gap-2 text-[#fcd34d]">
                <span className="rounded-full border border-[#7c5a18] px-2 py-0.5 text-[10px] uppercase tracking-[0.2em]">
                  Alert
                </span>
                <span className="text-[#fde68a]">{alert.name}</span>
                <span className="text-[#d1a45d]">
                  {alert.hours_away < 24
                    ? `in ${Math.round(alert.hours_away)}h`
                    : `${alert.date}${alert.time ? ` ${alert.time}` : ''}`}
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  setDismissed((prev) => {
                    const next = new Set(prev)
                    next.add(key)
                    return next
                  })
                }}
                className="text-[10px] uppercase tracking-[0.22em] text-[#fbbf24] hover:text-[#fde68a]"
              >
                Dismiss
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
