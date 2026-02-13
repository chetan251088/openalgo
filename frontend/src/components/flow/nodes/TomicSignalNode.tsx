import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { Workflow } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TomicSignalNodeData } from '@/types/flow'

interface TomicSignalNodeProps {
  data: TomicSignalNodeData
  selected?: boolean
}

export const TomicSignalNode = memo(({ data, selected }: TomicSignalNodeProps) => {
  const direction = (data.direction || 'BUY').toUpperCase()
  const autoSelect = Boolean(data.autoSelect)
  return (
    <div className={cn('workflow-node node-action min-w-[150px] border-l-emerald-500', selected && 'selected')}>
      <Handle type="target" position={Position.Top} />
      <div className="p-2">
        <div className="mb-1.5 flex items-center gap-1.5">
          <div className="flex h-5 w-5 items-center justify-center rounded bg-emerald-500/20 text-emerald-500">
            <Workflow className="h-3 w-3" />
          </div>
          <div>
            <div className="text-xs font-medium leading-tight">TOMIC Signal</div>
            <div className="text-[9px] text-muted-foreground">Queue to risk agent</div>
          </div>
        </div>
        <div className="space-y-0.5 text-[10px]">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Instrument:</span>
            <span className="mono-data font-medium">{data.instrument || 'NIFTY'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Strategy:</span>
            <span className="mono-data">{autoSelect ? 'AUTO_SELL' : (data.strategyType || 'DITM_CALL')}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Direction:</span>
            <span className={cn('mono-data font-semibold', direction === 'BUY' ? 'text-green-500' : 'text-red-500')}>
              {direction}
            </span>
          </div>
          {autoSelect && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Fallback:</span>
              <span className="mono-data">{data.fallbackStrategy || 'IRON_CONDOR'}</span>
            </div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
})

TomicSignalNode.displayName = 'TomicSignalNode'
