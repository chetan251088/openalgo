import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { buildVirtualPosition, resolveFilledOrderPrice } from '@/lib/scalpingVirtualPosition'

const VIRTUAL_POSITION_SYNC_GRACE_MS = 15000

export default function ScalpingDashboard() {
  const [showHelp, setShowHelp] = useState(false)
  const pendingLimitAttachRef = useRef(false)

  const toggleHelp = useCallback(() => setShowHelp((v) => !v), [])
  const closeHelp = useCallback(() => setShowHelp(false), [])

  const paperMode = useScalpingStore((s) => s.paperMode)
  const underlying = useScalpingStore((s) => s.underlying)
  const indexExchange = useScalpingStore((s) => s.indexExchange)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const product = useScalpingStore((s) => s.product)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)
  const pendingLimitPlacement = useScalpingStore((s) => s.pendingLimitPlacement)
  const clearPendingLimitPlacement = useScalpingStore((s) => s.clearPendingLimitPlacement)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const clearVirtualOrders = useVirtualOrderStore((s) => s.clearAll)
  const clearVirtualForSymbol = useVirtualOrderStore((s) => s.clearForSymbol)
  const clearTriggerOrders = useVirtualOrderStore((s) => s.clearTriggerOrders)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)
  const updateVirtualTPSL = useVirtualOrderStore((s) => s.updateVirtualTPSL)
  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
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
        clearPendingLimitPlacement()
        return
      }
      try {
        await tradingApi.closePosition(symbol, optionExchange, product)
        console.log(`[Scalping] Closed ${side} ${symbol}`)
        clearVirtualForSymbol(symbol)
        setLimitPrice(null)
        setPendingEntryAction(null)
        clearPendingLimitPlacement()
      } catch (err) {
        console.error('[Scalping] Close failed:', err)
      }
    },
    [
      paperMode,
      optionExchange,
      product,
      clearVirtualForSymbol,
      setLimitPrice,
      setPendingEntryAction,
      clearPendingLimitPlacement,
    ]
  )

  // Close all positions
  const handleCloseAll = useCallback(async () => {
    if (paperMode) {
      console.log('[Paper] Close all positions')
      clearVirtualOrders()
      setLimitPrice(null)
      setPendingEntryAction(null)
      clearPendingLimitPlacement()
      return
    }
    try {
      await tradingApi.closeAllPositions()
      console.log('[Scalping] Closed all positions')
      clearVirtualOrders()
      setLimitPrice(null)
      setPendingEntryAction(null)
      clearPendingLimitPlacement()
    } catch (err) {
      console.error('[Scalping] Close all failed:', err)
    }
  }, [paperMode, clearVirtualOrders, setLimitPrice, setPendingEntryAction, clearPendingLimitPlacement])

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

  // Reconcile virtual lines with live broker positions in live mode:
  // 1) clear stale lines after grace when broker no longer reports a position
  // 2) preserve per-fill entry anchors (never snap to broker average)
  // 3) cap tracked qty to live qty to avoid over-closing on TP/SL
  useEffect(() => {
    if (paperMode) return

    const liveBySymbol = new Map(livePositions.map((p) => [p.symbol, p]))
    const orders = Object.values(virtualTPSL)

    for (const order of orders) {
      const live = liveBySymbol.get(order.symbol)
      if (!live) {
        const createdAt = typeof order.createdAt === 'number' ? order.createdAt : 0
        const ageMs = createdAt > 0 ? Date.now() - createdAt : Number.POSITIVE_INFINITY
        if (ageMs < VIRTUAL_POSITION_SYNC_GRACE_MS) continue
        removeVirtualTPSL(order.id)
        continue
      }

      if (live.side !== order.side || live.action !== order.action || order.quantity <= 0) {
        removeVirtualTPSL(order.id)
      }
    }

    const syncedOrders = Object.values(useVirtualOrderStore.getState().virtualTPSL)
    const trackedBySymbol = new Map<string, (typeof syncedOrders)>()
    for (const order of syncedOrders) {
      const list = trackedBySymbol.get(order.symbol) ?? []
      list.push(order)
      trackedBySymbol.set(order.symbol, list)
    }

    for (const [symbol, tracked] of trackedBySymbol.entries()) {
      const live = liveBySymbol.get(symbol)
      if (!live) continue

      const liveQty = Math.max(0, live.quantity)
      const trackedQty = tracked.reduce((sum, order) => sum + Math.max(0, order.quantity), 0)
      let overflow = trackedQty - liveQty
      if (overflow <= 0) continue

      // LIFO trim: reduce/remove newest tracked fills first.
      const newestFirst = [...tracked].sort((a, b) => b.createdAt - a.createdAt)
      for (const order of newestFirst) {
        if (overflow <= 0) break
        const qty = Math.max(0, order.quantity)
        if (qty <= overflow + 1e-6) {
          removeVirtualTPSL(order.id)
          overflow -= qty
          continue
        }
        const nextQty = Math.max(0, Number((qty - overflow).toFixed(2)))
        updateVirtualTPSL(order.id, { quantity: nextQty })
        overflow = 0
      }
    }

    // Attach virtual TP/SL for live LIMIT only after fill confirmation.
    if (!pendingLimitPlacement || pendingLimitAttachRef.current) return
    const live = liveBySymbol.get(pendingLimitPlacement.symbol)
    if (!live) return
    if (live.side !== pendingLimitPlacement.side || live.action !== pendingLimitPlacement.action) return

    pendingLimitAttachRef.current = true
    void (async () => {
      try {
        const entryPrice = await resolveFilledOrderPrice({
          symbol: pendingLimitPlacement.symbol,
          exchange: live.exchange,
          orderId: pendingLimitPlacement.orderId,
          preferredPrice: pendingLimitPlacement.entryPrice,
          fallbackPrice: live.avgPrice,
          apiKey,
        })
        if (entryPrice <= 0) return

        setVirtualTPSL(
          buildVirtualPosition({
            symbol: pendingLimitPlacement.symbol,
            exchange: live.exchange,
            side: pendingLimitPlacement.side,
            action: pendingLimitPlacement.action,
            entryPrice,
            quantity: pendingLimitPlacement.quantity,
            tpPoints: pendingLimitPlacement.tpPoints,
            slPoints: pendingLimitPlacement.slPoints,
            managedBy: 'manual',
          })
        )
        incrementTradeCount()
        clearPendingLimitPlacement()
        setLimitPrice(null)
        setPendingEntryAction(null)
      } finally {
        pendingLimitAttachRef.current = false
      }
    })()
  }, [
    paperMode,
    livePositions,
    virtualTPSL,
    pendingLimitPlacement,
    apiKey,
    removeVirtualTPSL,
    updateVirtualTPSL,
    setVirtualTPSL,
    incrementTradeCount,
    clearPendingLimitPlacement,
    setLimitPrice,
    setPendingEntryAction,
  ])

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
