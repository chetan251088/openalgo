import { useState, useCallback } from 'react'
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable'
import { IndexChartView } from './IndexChartView'
import { OptionChartView } from './OptionChartView'
import { FloatingTradeWidget } from './FloatingTradeWidget'
import { ChartToolbar } from './ChartToolbar'

export function ChartPanel() {
  const [showEma9, setShowEma9] = useState(true)
  const [showEma21, setShowEma21] = useState(true)
  const [showSupertrend, setShowSupertrend] = useState(true)
  const [showVwap, setShowVwap] = useState(true)

  const toggleEma9 = useCallback(() => setShowEma9((v) => !v), [])
  const toggleEma21 = useCallback(() => setShowEma21((v) => !v), [])
  const toggleSupertrend = useCallback(() => setShowSupertrend((v) => !v), [])
  const toggleVwap = useCallback(() => setShowVwap((v) => !v), [])

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">
      <ChartToolbar
        showEma9={showEma9}
        showEma21={showEma21}
        showSupertrend={showSupertrend}
        showVwap={showVwap}
        onToggleEma9={toggleEma9}
        onToggleEma21={toggleEma21}
        onToggleSupertrend={toggleSupertrend}
        onToggleVwap={toggleVwap}
      />

      <div className="flex-1 min-h-0 min-w-0 overflow-hidden">
        <ResizablePanelGroup orientation="vertical">
          {/* Index chart */}
          <ResizablePanel defaultSize="40%" minSize="15%">
            <IndexChartView />
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* CE + PE charts with single floating widget */}
          <ResizablePanel defaultSize="60%" minSize="20%">
            <div className="relative h-full w-full">
              <ResizablePanelGroup orientation="horizontal">
                <ResizablePanel defaultSize="50%" minSize="20%">
                  <OptionChartView side="CE" />
                </ResizablePanel>
                <ResizableHandle withHandle />
                <ResizablePanel defaultSize="50%" minSize="20%">
                  <OptionChartView side="PE" />
                </ResizablePanel>
              </ResizablePanelGroup>
              <FloatingTradeWidget />
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  )
}
