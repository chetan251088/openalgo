import { useState, useCallback, useEffect, useRef } from 'react'
import type { GroupImperativeHandle } from 'react-resizable-panels'
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable'
import { IndexChartView } from './IndexChartView'
import { OptionChartView } from './OptionChartView'
import { ChartToolbar } from './ChartToolbar'
import { useScalpingStore } from '@/stores/scalpingStore'
import { cn } from '@/lib/utils'

type FootprintDensity = 'sparse' | 'balanced' | 'all'

export function ChartPanel() {
  const activeSide = useScalpingStore((s) => s.activeSide)
  const optionChartsRef = useRef<GroupImperativeHandle | null>(null)
  const didInitOptionLayoutRef = useRef(false)
  const [showEma9, setShowEma9] = useState(true)
  const [showEma21, setShowEma21] = useState(true)
  const [showSupertrend, setShowSupertrend] = useState(true)
  const [showVwap, setShowVwap] = useState(true)
  const [showOrderFlow, setShowOrderFlow] = useState(false)
  const [showFootprints, setShowFootprints] = useState(false)
  const [footprintDensity, setFootprintDensity] = useState<FootprintDensity>('sparse')

  const toggleEma9 = useCallback(() => setShowEma9((v) => !v), [])
  const toggleEma21 = useCallback(() => setShowEma21((v) => !v), [])
  const toggleSupertrend = useCallback(() => setShowSupertrend((v) => !v), [])
  const toggleVwap = useCallback(() => setShowVwap((v) => !v), [])
  const toggleOrderFlow = useCallback(() => setShowOrderFlow((v) => !v), [])
  const toggleFootprints = useCallback(() => setShowFootprints((v) => !v), [])
  const cycleFootprintDensity = useCallback(() => {
    setFootprintDensity((mode) => {
      if (mode === 'sparse') return 'balanced'
      if (mode === 'balanced') return 'all'
      return 'sparse'
    })
  }, [])
  useEffect(() => {
    if (!didInitOptionLayoutRef.current) {
      didInitOptionLayoutRef.current = true
      return
    }

    const group = optionChartsRef.current
    if (!group) return

    const rafId = window.requestAnimationFrame(() => {
      try {
        group.setLayout(
          activeSide === 'CE'
            ? { 'option-ce-panel': 68, 'option-pe-panel': 32 }
            : { 'option-ce-panel': 32, 'option-pe-panel': 68 }
        )
      } catch (error) {
        console.warn('[Scalping] Option layout sync skipped:', error)
      }
    })

    return () => window.cancelAnimationFrame(rafId)
  }, [activeSide])

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">
      <ChartToolbar
        showEma9={showEma9}
        showEma21={showEma21}
        showSupertrend={showSupertrend}
        showVwap={showVwap}
        showOrderFlow={showOrderFlow}
        showFootprints={showFootprints}
        footprintDensity={footprintDensity}
        onToggleEma9={toggleEma9}
        onToggleEma21={toggleEma21}
        onToggleSupertrend={toggleSupertrend}
        onToggleVwap={toggleVwap}
        onToggleOrderFlow={toggleOrderFlow}
        onToggleFootprints={toggleFootprints}
        onCycleFootprintDensity={cycleFootprintDensity}
      />

      <div className="flex-1 min-h-0 min-w-0 overflow-hidden">
        <ResizablePanelGroup orientation="vertical">
          {/* Index chart */}
          <ResizablePanel defaultSize="34%" minSize="18%">
            <div className="h-full px-2 pb-2 pt-1">
              <div className="h-full overflow-hidden rounded-lg border border-border/70 bg-card/20 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
                <IndexChartView
                  showEma9={showEma9}
                  showEma21={showEma21}
                  showSupertrend={showSupertrend}
                  showVwap={showVwap}
                />
              </div>
            </div>
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* Fixed CE/PE order with draggable split; active side gets more space */}
          <ResizablePanel defaultSize="66%" minSize="24%">
            <div className="h-full px-2 pb-2 pt-1">
              <div className="h-full overflow-hidden rounded-lg border border-border/70 bg-card/20 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
                <ResizablePanelGroup
                  orientation="horizontal"
                  groupRef={optionChartsRef}
                  defaultLayout={{ 'option-ce-panel': 68, 'option-pe-panel': 32 }}
                >
                  <ResizablePanel id="option-ce-panel" defaultSize="68%" minSize="22%">
                    <div className="flex h-full min-h-0 flex-col overflow-hidden">
                      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                        <span>{activeSide === 'CE' ? 'Primary' : 'Preview'}</span>
                        <span className={cn(activeSide === 'CE' ? 'text-emerald-400' : 'text-muted-foreground')}>
                          CE
                        </span>
                      </div>
                      <div className="min-h-0 flex-1">
                        <OptionChartView
                          side="CE"
                          prominence={activeSide === 'CE' ? 'primary' : 'secondary'}
                          showEma9={showEma9}
                          showEma21={showEma21}
                          showSupertrend={showSupertrend}
                          showVwap={showVwap}
                          showOrderFlow={showOrderFlow}
                          showFootprints={showFootprints}
                          footprintDensity={footprintDensity}
                        />
                      </div>
                    </div>
                  </ResizablePanel>

                  <ResizableHandle withHandle className="bg-border/70" />

                  <ResizablePanel id="option-pe-panel" defaultSize="32%" minSize="22%">
                    <div className="flex h-full min-h-0 flex-col overflow-hidden">
                      <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                        <span>{activeSide === 'PE' ? 'Primary' : 'Preview'}</span>
                        <span className={cn(activeSide === 'PE' ? 'text-rose-400' : 'text-muted-foreground')}>
                          PE
                        </span>
                      </div>
                      <div className="min-h-0 flex-1">
                        <OptionChartView
                          side="PE"
                          prominence={activeSide === 'PE' ? 'primary' : 'secondary'}
                          showEma9={showEma9}
                          showEma21={showEma21}
                          showSupertrend={showSupertrend}
                          showVwap={showVwap}
                          showOrderFlow={showOrderFlow}
                          showFootprints={showFootprints}
                          footprintDensity={footprintDensity}
                        />
                      </div>
                    </div>
                  </ResizablePanel>
                </ResizablePanelGroup>
              </div>
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  )
}
