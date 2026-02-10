import { useCallback, useEffect, useMemo, useState } from 'react'
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
import { useMarketData } from '@/hooks/useMarketData'
import { useVirtualTPSL } from '@/hooks/useVirtualTPSL'
import { useTrailingMonitor } from '@/hooks/useTrailingMonitor'
import { useScalpingPositions } from '@/hooks/useScalpingPositions'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { useAuthStore } from '@/stores/authStore'
import { tradingApi } from '@/api/trading'

export default function ScalpingDashboard() {
  const [showHelp, setShowHelp] = useState(false)

  const toggleHelp = useCallback(() => setShowHelp((v) => !v), [])
  const closeHelp = useCallback(() => setShowHelp(false), [])

  const paperMode = useScalpingStore((s) => s.paperMode)
  const underlying = useScalpingStore((s) => s.underlying)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const product = useScalpingStore((s) => s.product)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const clearVirtualOrders = useVirtualOrderStore((s) => s.clearAll)
  const clearVirtualForSymbol = useVirtualOrderStore((s) => s.clearForSymbol)
  const clearTriggerOrders = useVirtualOrderStore((s) => s.clearTriggerOrders)
  const imbalanceFilterEnabled = useAutoTradeStore((s) => s.config.imbalanceFilterEnabled)
  const updateRiskState = useAutoTradeStore((s) => s.updateRiskState)
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)

  // Eagerly fetch apiKey on mount so expiry/chain loading don't wait for AuthSync
  useEffect(() => {
    if (apiKey) return
    fetch('/api/websocket/apikey', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        if (data.status === 'success' && data.api_key) {
          setApiKey(data.api_key)
        }
      })
      .catch(() => {})
  }, [apiKey, setApiKey])

  // Safety: trigger orders are session-only and should not auto-fire after reload.
  useEffect(() => {
    clearTriggerOrders()
  }, [clearTriggerOrders])

  // Close active side position
  const handleClose = useCallback(
    async (side: 'CE' | 'PE', symbol: string) => {
      if (paperMode) {
        console.log(`[Paper] Close ${side} ${symbol}`)
        clearVirtualForSymbol(symbol)
        setLimitPrice(null)
        setPendingEntryAction(null)
        return
      }
      try {
        await tradingApi.closePosition(symbol, optionExchange, product)
        console.log(`[Scalping] Closed ${side} ${symbol}`)
        clearVirtualForSymbol(symbol)
        setLimitPrice(null)
        setPendingEntryAction(null)
      } catch (err) {
        console.error('[Scalping] Close failed:', err)
      }
    },
    [paperMode, optionExchange, product, clearVirtualForSymbol, setLimitPrice, setPendingEntryAction]
  )

  // Close all positions
  const handleCloseAll = useCallback(async () => {
    if (paperMode) {
      console.log('[Paper] Close all positions')
      clearVirtualOrders()
      setLimitPrice(null)
      setPendingEntryAction(null)
      return
    }
    try {
      await tradingApi.closeAllPositions()
      console.log('[Scalping] Closed all positions')
      clearVirtualOrders()
      setLimitPrice(null)
      setPendingEntryAction(null)
    } catch (err) {
      console.error('[Scalping] Close all failed:', err)
    }
  }, [paperMode, clearVirtualOrders, setLimitPrice, setPendingEntryAction])

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
          price: 0,
          trigger_price: 0,
          disclosed_quantity: 0,
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

  // Build symbol list for shared tick stream (positions, triggers, selected strikes)
  const tickSymbols = useMemo(() => {
    const symbols: Array<{ symbol: string; exchange: string }> = []

    if (underlying && indexExchange) {
      symbols.push({ symbol: underlying, exchange: indexExchange })
    }

    if (selectedCESymbol) symbols.push({ symbol: selectedCESymbol, exchange: optionExchange })
    if (selectedPESymbol) symbols.push({ symbol: selectedPESymbol, exchange: optionExchange })

    for (const order of Object.values(virtualTPSL)) {
      symbols.push({ symbol: order.symbol, exchange: order.exchange })
    }

    for (const order of Object.values(triggerOrders)) {
      symbols.push({ symbol: order.symbol, exchange: order.exchange })
    }

    const unique = new Map<string, { symbol: string; exchange: string }>()
    for (const item of symbols) {
      unique.set(`${item.exchange}:${item.symbol}`, item)
    }

    return Array.from(unique.values())
  }, [
    underlying,
    indexExchange,
    selectedCESymbol,
    selectedPESymbol,
    optionExchange,
    virtualTPSL,
    triggerOrders,
  ])

  const { data: tickData } = useMarketData({
    symbols: tickSymbols,
    mode: imbalanceFilterEnabled ? 'Quote' : 'LTP',
    enabled: tickSymbols.length > 0,
  })

  const {
    positions: livePositions,
    totalPnl: liveOpenPnl,
    isLive: isLivePnl,
  } = useScalpingPositions()

  useEffect(() => {
    updateRiskState(liveOpenPnl)
  }, [liveOpenPnl, updateRiskState])

  // Auto-trade engine (execute or ghost mode)
  useAutoTradeEngine(tickData)

  // Trailing stop monitor for active positions
  useTrailingMonitor()

  // Virtual TP/SL + trigger monitoring
  useVirtualTPSL(tickData)

  return (
    <div className="flex flex-col h-full">
      <TopBar liveOpenPnl={liveOpenPnl} isLivePnl={isLivePnl} />

      <div className="flex-1 min-h-0 min-w-0">
        <ResizablePanelGroup orientation="horizontal" className="h-full w-full min-w-0">
          {/* Left: Option Chain */}
          <ResizablePanel defaultSize="20%" minSize="12%" maxSize="36%">
            <OptionChainPanel />
          </ResizablePanel>

          <ResizableHandle withHandle className="bg-border/70" />

          {/* Center: Charts */}
          <ResizablePanel defaultSize="50%" minSize="30%">
            <ChartPanel />
          </ResizablePanel>

          <ResizableHandle withHandle className="bg-border/70" />

          {/* Right: Control Panel */}
          <ResizablePanel defaultSize="30%" minSize="18%" maxSize="42%">
            <ControlPanel liveOpenPnl={liveOpenPnl} isLivePnl={isLivePnl} />
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      <BottomBar positions={livePositions} totalPnl={liveOpenPnl} isLivePnl={isLivePnl} />
      <HotkeyHelp open={showHelp} onClose={closeHelp} />
    </div>
  )
}
