import { useCallback, useState } from 'react'
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable'
import { TopBar, OptionChainPanel, BottomBar } from '@/components/scalping'
import { ChartPanel } from '@/components/scalping/ChartPanel'
import { ControlPanel } from '@/components/scalping/ControlPanel'
import { HotkeyHelp } from '@/components/scalping/HotkeyHelp'
import { useScalpingHotkeys } from '@/hooks/useScalpingHotkeys'
import { useOptionsContext } from '@/hooks/useOptionsContext'
import { useAutoTradeEngine } from '@/hooks/useAutoTradeEngine'
import { useTrailingMonitor } from '@/hooks/useTrailingMonitor'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'

export default function ScalpingDashboard() {
  const [showHelp, setShowHelp] = useState(false)

  const toggleHelp = useCallback(() => setShowHelp((v) => !v), [])
  const closeHelp = useCallback(() => setShowHelp(false), [])

  const paperMode = useScalpingStore((s) => s.paperMode)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const product = useScalpingStore((s) => s.product)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const apiKey = useAuthStore((s) => s.apiKey)

  // Close active side position
  const handleClose = useCallback(
    async (side: 'CE' | 'PE', symbol: string) => {
      if (paperMode) {
        console.log(`[Paper] Close ${side} ${symbol}`)
        return
      }
      try {
        await tradingApi.closePosition(symbol, optionExchange, product)
        console.log(`[Scalping] Closed ${side} ${symbol}`)
      } catch (err) {
        console.error('[Scalping] Close failed:', err)
      }
    },
    [paperMode, optionExchange, product]
  )

  // Close all positions
  const handleCloseAll = useCallback(async () => {
    if (paperMode) {
      console.log('[Paper] Close all positions')
      return
    }
    try {
      await tradingApi.closeAllPositions()
      console.log('[Scalping] Closed all positions')
    } catch (err) {
      console.error('[Scalping] Close all failed:', err)
    }
  }, [paperMode])

  // Reversal: close current position, enter opposite direction
  const handleReversal = useCallback(
    async (side: 'CE' | 'PE', symbol: string) => {
      if (paperMode) {
        console.log(`[Paper] Reverse ${side} ${symbol}`)
        return
      }
      if (!apiKey) return

      try {
        // Close existing position first
        await tradingApi.closePosition(symbol, optionExchange, product)

        // Enter opposite direction (if you were long, sell; if short, buy)
        // For scalping reversal, we sell to reverse a long
        await tradingApi.placeOrder({
          apikey: apiKey,
          strategy: 'Scalping',
          exchange: optionExchange,
          symbol,
          action: 'SELL',
          quantity: quantity * lotSize,
          pricetype: 'MARKET',
          product,
        })
        console.log(`[Scalping] Reversed ${side} ${symbol}`)
      } catch (err) {
        console.error('[Scalping] Reversal failed:', err)
      }
    },
    [paperMode, apiKey, optionExchange, product, quantity, lotSize]
  )

  // Wire up keyboard hotkeys
  useScalpingHotkeys({
    onToggleHelp: toggleHelp,
    onClose: handleClose,
    onCloseAll: handleCloseAll,
    onReversal: handleReversal,
  })

  // Options context polling (PCR, MaxPain, GEX, IV, Straddle)
  useOptionsContext()

  // Auto-trade engine (execute or ghost mode)
  useAutoTradeEngine(null)

  // Trailing stop monitor for active positions
  useTrailingMonitor()

  return (
    <div className="flex flex-col h-full">
      <TopBar />

      <div className="flex-1 min-h-0">
        <ResizablePanelGroup orientation="horizontal">
          {/* Left: Option Chain */}
          <ResizablePanel defaultSize="18%" minSize="10%" maxSize="35%">
            <OptionChainPanel />
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* Center: Charts */}
          <ResizablePanel defaultSize="52%" minSize="25%">
            <ChartPanel />
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* Right: Control Panel */}
          <ResizablePanel defaultSize="30%" minSize="18%" maxSize="45%">
            <ControlPanel />
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      <BottomBar />
      <HotkeyHelp open={showHelp} onClose={closeHelp} />
    </div>
  )
}
