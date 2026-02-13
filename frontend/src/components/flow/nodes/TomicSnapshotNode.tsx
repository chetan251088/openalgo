import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { Activity } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TomicSnapshotNodeData } from '@/types/flow'

interface TomicSnapshotNodeProps {
  data: TomicSnapshotNodeData
  selected?: boolean
}

export const TomicSnapshotNode = memo(({ data, selected }: TomicSnapshotNodeProps) => {
  return (
    <div className={cn('workflow-node min-w-[145px] border-l-emerald-500', selected && 'selected')}>
      <Handle type="target" position={Position.Top} />
      <div className="p-2">
        <div className="mb-1.5 flex items-center gap-1.5">
          <div className="flex h-5 w-5 items-center justify-center rounded bg-emerald-500/20 text-emerald-500">
            <Activity className="h-3 w-3" />
          </div>
          <div>
            <div className="text-xs font-medium leading-tight">TOMIC Snapshot</div>
            <div className="text-[9px] text-muted-foreground">Runtime diagnostics</div>
          </div>
        </div>
        <div className="space-y-0.5 text-[10px]">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Source:</span>
            <span className="mono-data font-medium">{data.source || 'status'}</span>
          </div>
          {data.source === 'signals' && (
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Run scan:</span>
              <span className="mono-data">{data.runScan ? 'yes' : 'no'}</span>
            </div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
})

TomicSnapshotNode.displayName = 'TomicSnapshotNode'

