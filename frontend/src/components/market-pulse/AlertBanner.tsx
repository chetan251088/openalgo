import type { AlertItem } from '@/api/market-pulse'
import { AlertTriangle, X } from 'lucide-react'
import { useState } from 'react'

interface AlertBannerProps {
  alerts: AlertItem[]
}

export function AlertBanner({ alerts }: AlertBannerProps) {
  const [dismissed, setDismissed] = useState(false)

  if (!alerts.length || dismissed) return null

  return (
    <div className="bg-amber-900/30 border-b border-amber-700/50 px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <AlertTriangle size={18} className="text-amber-400" />
        <div className="text-sm text-amber-200">
          {alerts.length} market event{alerts.length !== 1 ? 's' : ''} coming up
          {alerts[0] && ` - ${alerts[0].name} in ${alerts[0].hours_away}h`}
        </div>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="text-amber-400 hover:text-amber-300 p-1"
      >
        <X size={16} />
      </button>
    </div>
  )
}
