import { useScalpingStore } from '@/stores/scalpingStore'
import { ManualTradeTab } from './ManualTradeTab'
import { AutoTradeTab } from './AutoTradeTab'
import { OrdersTab } from './OrdersTab'
import { DepthScoutTab } from './DepthScoutTab'
import { RiskPanel } from './RiskPanel'
import type { ControlTab } from '@/types/scalping'

const TABS: { id: ControlTab; label: string }[] = [
  { id: 'manual', label: 'Manual' },
  { id: 'auto', label: 'Auto' },
  { id: 'risk', label: 'Risk' },
  { id: 'depth', label: 'Depth' },
  { id: 'orders', label: 'Orders' },
]

interface ControlPanelProps {
  liveOpenPnl?: number
  isLivePnl?: boolean
}

export function ControlPanel({ liveOpenPnl, isLivePnl = false }: ControlPanelProps) {
  const controlTab = useScalpingStore((s) => s.controlTab)
  const setControlTab = useScalpingStore((s) => s.setControlTab)

  return (
    <div className="flex flex-col h-full bg-card">
      {/* Tab bar */}
      <div className="flex items-center gap-0.5 px-1.5 py-1 border-b shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setControlTab(tab.id)}
            className={`px-2 py-1 text-xs rounded-sm transition-colors ${
              controlTab === tab.id
                ? 'text-foreground bg-accent font-medium'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {controlTab === 'manual' && <ManualTradeTab />}
        {controlTab === 'auto' && <AutoTradeTab />}
        {controlTab === 'risk' && <RiskPanel liveOpenPnl={liveOpenPnl} isLivePnl={isLivePnl} />}
        {controlTab === 'depth' && <DepthScoutTab />}
        {controlTab === 'orders' && <OrdersTab />}
      </div>
    </div>
  )
}
