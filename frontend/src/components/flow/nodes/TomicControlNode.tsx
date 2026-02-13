import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { Power } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TomicControlNodeData } from '@/types/flow'

interface TomicControlNodeProps {
  data: TomicControlNodeData
  selected?: boolean
}

const actionLabel: Record<string, string> = {
  start: 'START',
  pause: 'PAUSE',
  resume: 'RESUME',
  stop: 'STOP',
}

export const TomicControlNode = memo(({ data, selected }: TomicControlNodeProps) => {
  const action = (data.action || 'start').toLowerCase()
  return (
    <div className={cn('workflow-node node-action min-w-[130px] border-l-emerald-500', selected && 'selected')}>
      <Handle type="target" position={Position.Top} />
      <div className="p-2">
        <div className="mb-1.5 flex items-center gap-1.5">
          <div className="flex h-5 w-5 items-center justify-center rounded bg-emerald-500/20 text-emerald-500">
            <Power className="h-3 w-3" />
          </div>
          <div>
            <div className="text-xs font-medium leading-tight">TOMIC Control</div>
            <div className="text-[9px] text-muted-foreground">Runtime action</div>
          </div>
        </div>
        <div className="rounded bg-muted/50 px-1.5 py-1 text-[10px]">
          <span className="text-muted-foreground">Action:</span>{' '}
          <span className="mono-data font-semibold">{actionLabel[action] || action.toUpperCase()}</span>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
})

TomicControlNode.displayName = 'TomicControlNode'

