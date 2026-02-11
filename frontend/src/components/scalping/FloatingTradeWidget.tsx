import { useRef, useState, useCallback, useEffect, type MouseEvent } from 'react'
import { Button } from '@/components/ui/button'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { tradingApi } from '@/api/trading'
import { MarketDataManager } from '@/lib/MarketDataManager'
import {
  buildVirtualPosition,
  extractOrderId,
  resolveFilledOrderPrice,
  resolveEntryPrice,
} from '@/lib/scalpingVirtualPosition'
import type { PlaceOrderRequest } from '@/types/trading'

/**
 * Single floating trade widget that follows the active side (CE or PE).
 * Renders once in ChartPanel, not per-chart.
 */
export function FloatingTradeWidget() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)

  const showFloatingWidget = useScalpingStore((s) => s.showFloatingWidget)
  const floatingWidgetMinimized = useScalpingStore((s) => s.floatingWidgetMinimized)
  const setFloatingWidgetMinimized = useScalpingStore((s) => s.setFloatingWidgetMinimized)
  const activeSide = useScalpingStore((s) => s.activeSide)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const product = useScalpingStore((s) => s.product)
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const orderType = useScalpingStore((s) => s.orderType)
  const limitPrice = useScalpingStore((s) => s.limitPrice)
  const pendingLimitPlacement = useScalpingStore((s) => s.pendingLimitPlacement)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)
  const setPendingLimitPlacement = useScalpingStore((s) => s.setPendingLimitPlacement)
  const clearPendingLimitPlacement = useScalpingStore((s) => s.clearPendingLimitPlacement)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
  const clearVirtualForSymbol = useVirtualOrderStore((s) => s.clearForSymbol)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)

  const widgetPos = useScalpingStore((s) => s.ceWidgetPos) // reuse CE position for the single widget
  const setWidgetPos = useScalpingStore((s) => s.setCeWidgetPos)

  const incrementQuantity = useScalpingStore((s) => s.incrementQuantity)
  const decrementQuantity = useScalpingStore((s) => s.decrementQuantity)
  const setTpPoints = useScalpingStore((s) => s.setTpPoints)
  const setSlPoints = useScalpingStore((s) => s.setSlPoints)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)

  const symbol = activeSide === 'CE' ? selectedCESymbol : selectedPESymbol

  const [executing, setExecuting] = useState(false)
  const [ltp, setLtp] = useState<number | null>(null)
  const [prevLtp, setPrevLtp] = useState<number | null>(null)
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null)
  const widgetRef = useRef<HTMLDivElement>(null)

  // Subscribe to LTP for the active symbol
  useEffect(() => {
    if (!symbol) {
      setLtp(null)
      setPrevLtp(null)
      return
    }

    const mdm = MarketDataManager.getInstance()
    const unsubscribe = mdm.subscribe(symbol, optionExchange, 'LTP', (data) => {
      const newLtp = data.data.ltp
      if (newLtp != null && newLtp > 0) {
        setPrevLtp(ltp)
        setLtp(newLtp)
      }
    })

    return () => {
      unsubscribe()
    }
    // Intentionally exclude ltp from deps to avoid re-subscribing on every tick
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, optionExchange])

  // Drag handling
  const onMouseDown = useCallback(
    (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest('button, input')) return
      e.preventDefault()
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        origX: widgetPos.x,
        origY: widgetPos.y,
      }

      const onMouseMove = (ev: globalThis.MouseEvent) => {
        if (!dragRef.current) return
        const dx = ev.clientX - dragRef.current.startX
        const dy = ev.clientY - dragRef.current.startY
        setWidgetPos({
          x: Math.max(0, dragRef.current.origX + dx),
          y: Math.max(0, dragRef.current.origY + dy),
        })
      }

      const onMouseUp = () => {
        dragRef.current = null
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
      }

      document.addEventListener('mousemove', onMouseMove)
      document.addEventListener('mouseup', onMouseUp)
    },
    [widgetPos, setWidgetPos]
  )

  // Ensure apiKey is available — fetch if missing
  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKey) return apiKey
    try {
      const resp = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await resp.json()
      if (data.status === 'success' && data.api_key) {
        setApiKey(data.api_key)
        return data.api_key
      }
    } catch (err) {
      console.error('[Scalping] Failed to fetch API key:', err)
    }
    console.warn('[Scalping] No API key available — generate one at /apikey')
    return null
  }, [apiKey, setApiKey])

  const placeOrder = useCallback(
    async (action: 'BUY' | 'SELL') => {
      if (!symbol || executing) {
        if (!symbol) console.warn('[Scalping] No symbol selected — click a strike first')
        return
      }
      setExecuting(true)

      // TRIGGER mode: arm side/action and place trigger only from chart click.
      if (orderType === 'TRIGGER') {
        const existingTrigger = Object.values(triggerOrders).find(
          (t) => t.symbol === symbol && t.exchange === optionExchange && t.side === activeSide
        )
        if (existingTrigger) {
          removeTriggerOrder(existingTrigger.id)
        }
        clearPendingLimitPlacement()
        setLimitPrice(null)
        setPendingEntryAction(action)
        console.log(`[Scalping] TRIGGER armed (${action}) — click chart to place trigger line`)
        setExecuting(false)
        return
      }

      // LIMIT mode: require chart click to set a limit line first.
      if (orderType === 'LIMIT' && !limitPrice) {
        setPendingEntryAction(action)
        console.log(`[Scalping] LIMIT armed (${action}) — click chart to set limit line`)
        setExecuting(false)
        return
      }
      if (
        orderType === 'LIMIT' &&
        pendingLimitPlacement &&
        pendingLimitPlacement.symbol === symbol &&
        pendingLimitPlacement.side === activeSide
      ) {
        console.warn('[Scalping] LIMIT already pending for this symbol. Wait for fill/cancel before placing another.')
        setExecuting(false)
        return
      }

      if (paperMode) {
        console.log(`[Paper] ${action} ${activeSide} ${symbol} qty=${quantity * lotSize} @ ${orderType}`)
        if (orderType === 'MARKET' || orderType === 'LIMIT') {
          const entryPrice = await resolveEntryPrice({
            symbol,
            exchange: optionExchange,
            preferredPrice: orderType === 'LIMIT' ? (limitPrice ?? ltp ?? undefined) : (ltp ?? undefined),
          })
          if (entryPrice > 0) {
            setVirtualTPSL(
              buildVirtualPosition({
                symbol,
                exchange: optionExchange,
                side: activeSide,
                action,
                entryPrice,
                quantity: quantity * lotSize,
                tpPoints,
                slPoints,
                managedBy: 'manual',
              })
            )
            if (orderType === 'LIMIT') {
              setLimitPrice(null)
            }
          }
        }
        clearPendingLimitPlacement()
        incrementTradeCount()
        setExecuting(false)
        return
      }

      const key = await ensureApiKey()
      if (!key) { setExecuting(false); return }

      // MARKET or LIMIT — real broker order
      const pricetype = orderType === 'LIMIT' ? 'LIMIT' : 'MARKET'

      const order: PlaceOrderRequest = {
        apikey: key,
        strategy: 'Scalping',
        exchange: optionExchange,
        symbol,
        action,
        quantity: quantity * lotSize,
        pricetype,
        product,
        price: orderType === 'LIMIT' && limitPrice ? limitPrice : 0,
        trigger_price: 0,
        disclosed_quantity: 0,
      }

      try {
        console.log(`[Scalping] Placing ${action} ${symbol} qty=${quantity * lotSize} ${pricetype}`)
        const res = await tradingApi.placeOrder(order)
        if (res.status === 'success') {
          const brokerOrderId = extractOrderId(res)
          console.log(`[Scalping] Order placed: ${action} ${symbol} id=${brokerOrderId ?? 'n/a'}`)
          setPendingEntryAction(null)

          if (pricetype === 'LIMIT') {
            const pendingEntryPrice = limitPrice ?? ltp ?? 0
            if (pendingEntryPrice <= 0) {
              console.warn('[Scalping] LIMIT order acknowledged without a usable entry price; keeping existing line state.')
            }
            setPendingLimitPlacement({
              symbol,
              side: activeSide,
              action,
              orderId: brokerOrderId,
              quantity: quantity * lotSize,
              entryPrice: pendingEntryPrice,
              tpPoints,
              slPoints,
            })
            if (pendingEntryPrice > 0) setLimitPrice(pendingEntryPrice)
            setExecuting(false)
            return
          }

          clearPendingLimitPlacement()
          incrementTradeCount()

          if (pricetype === 'MARKET') {
            const entryPrice = await resolveFilledOrderPrice({
              symbol,
              exchange: optionExchange,
              orderId: brokerOrderId,
              preferredPrice: ltp ?? undefined,
              apiKey: key,
            })
            if (entryPrice > 0) {
              setVirtualTPSL(
                buildVirtualPosition({
                  symbol,
                  exchange: optionExchange,
                  side: activeSide,
                  action,
                  entryPrice,
                  quantity: quantity * lotSize,
                  tpPoints,
                  slPoints,
                  managedBy: 'manual',
                })
              )
            }
          }
        } else {
          console.error(`[Scalping] Order rejected:`, res)
        }
      } catch (err) {
        console.error('[Scalping] Order failed:', err)
      }
      setExecuting(false)
    },
    [
      symbol,
      activeSide,
      quantity,
      lotSize,
      optionExchange,
      product,
      orderType,
      limitPrice,
      pendingLimitPlacement,
      tpPoints,
      slPoints,
      paperMode,
      clearVirtualForSymbol,
      triggerOrders,
      executing,
      ltp,
      ensureApiKey,
      incrementTradeCount,
      setPendingLimitPlacement,
      setVirtualTPSL,
      removeTriggerOrder,
      setLimitPrice,
      setPendingEntryAction,
      clearPendingLimitPlacement,
    ]
  )

  const reversal = useCallback(async () => {
    if (!symbol || executing) return
    setExecuting(true)

    if (paperMode) {
      console.log(`[Paper] Reversal ${activeSide} ${symbol}`)
      incrementTradeCount()
      setExecuting(false)
      return
    }

    const key = await ensureApiKey()
    if (!key) { setExecuting(false); return }

    try {
      // Close existing position with opposite MARKET order, then open new
      const closeOrder: PlaceOrderRequest = {
        apikey: key,
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
      }
      await tradingApi.placeOrder(closeOrder)

      const openOrder: PlaceOrderRequest = {
        apikey: key,
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
      }
      await tradingApi.placeOrder(openOrder)
      console.log(`[Scalping] Reversed ${activeSide} ${symbol}`)
      incrementTradeCount()
    } catch (err) {
      console.error('[Scalping] Reversal failed:', err)
    }
    setExecuting(false)
  }, [symbol, activeSide, quantity, lotSize, optionExchange, product, paperMode, executing, ensureApiKey, incrementTradeCount])

  const closePosition = useCallback(async () => {
    if (!symbol) return
    if (paperMode) {
      console.log(`[Paper] Close ${activeSide} ${symbol}`)
      clearVirtualForSymbol(symbol)
      setLimitPrice(null)
      setPendingEntryAction(null)
      clearPendingLimitPlacement()
      return
    }

    try {
      const res = await tradingApi.closePosition(symbol, optionExchange, product)
      if (res.status === 'success') {
        console.log(`[Scalping] Closed ${activeSide} ${symbol}`)
        clearVirtualForSymbol(symbol)
        setLimitPrice(null)
        setPendingEntryAction(null)
        clearPendingLimitPlacement()
      } else {
        console.error('[Scalping] Close rejected:', res)
      }
    } catch (err) {
      console.error('[Scalping] Close failed:', err)
    }
  }, [
    symbol,
    activeSide,
    optionExchange,
    product,
    paperMode,
    clearVirtualForSymbol,
    setLimitPrice,
    setPendingEntryAction,
    clearPendingLimitPlacement,
  ])

  if (!showFloatingWidget || !symbol) return null

  const ltpDir = ltp && prevLtp ? (ltp > prevLtp ? 'up' : ltp < prevLtp ? 'down' : 'flat') : 'flat'

  if (floatingWidgetMinimized) {
    return (
      <div
        ref={widgetRef}
        onMouseDown={onMouseDown}
        className="absolute z-20 select-none cursor-move rounded-md border shadow-md backdrop-blur-sm bg-card/90 border-primary/40"
        style={{
          left: widgetPos.x,
          top: widgetPos.y,
        }}
      >
        <div className="flex items-center gap-2 px-2 py-1">
          <span className={`text-[10px] font-bold ${activeSide === 'CE' ? 'text-green-500' : 'text-red-500'}`}>
            {activeSide}
          </span>
          <span className="text-[10px] font-mono text-muted-foreground max-w-[72px] truncate">
            {symbol.slice(-10)}
          </span>
          <span
            className={`text-xs font-bold tabular-nums ${
              ltpDir === 'up' ? 'text-green-500' : ltpDir === 'down' ? 'text-red-500' : 'text-foreground'
            }`}
          >
            {ltp?.toFixed(2) ?? '--'}
          </span>
          <Button
            size="sm"
            variant="outline"
            className="h-5 px-1.5 text-[10px]"
            onClick={() => setFloatingWidgetMinimized(false)}
            title="Open trade widget"
          >
            Open
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={widgetRef}
      onMouseDown={onMouseDown}
      className={`absolute z-20 select-none cursor-move rounded-lg border shadow-lg backdrop-blur-sm bg-card/90 border-primary/40`}
      style={{
        left: widgetPos.x,
        top: widgetPos.y,
        minWidth: 220,
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1 border-b border-border/30">
        <span className={`text-xs font-bold ${activeSide === 'CE' ? 'text-green-500' : 'text-red-500'}`}>
          {activeSide}
        </span>
        <span className="text-xs font-mono text-muted-foreground truncate mx-1 max-w-[100px]">
          {symbol.slice(-10)}
        </span>
        <span
          className={`text-sm font-bold tabular-nums ${
            ltpDir === 'up' ? 'text-green-500' : ltpDir === 'down' ? 'text-red-500' : 'text-foreground'
          }`}
        >
          {ltp?.toFixed(2) ?? '--'}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-5 px-1 text-[10px]"
          onClick={() => setFloatingWidgetMinimized(true)}
          title="Minimize widget"
        >
          _
        </Button>
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-1 px-2 py-1.5">
        <Button
          size="sm"
          className="h-6 px-2 text-[10px] font-bold bg-green-600 hover:bg-green-700 text-white flex-1"
          onClick={() => placeOrder('BUY')}
          disabled={executing}
        >
          BUY
        </Button>
        <Button
          size="sm"
          className="h-6 px-2 text-[10px] font-bold bg-red-600 hover:bg-red-700 text-white flex-1"
          onClick={() => placeOrder('SELL')}
          disabled={executing}
        >
          SELL
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[10px] font-bold flex-1"
          onClick={reversal}
          disabled={executing}
        >
          REV
        </Button>
      </div>

      {/* Lot stepper + TP/SL */}
      <div className="flex items-center gap-1 px-2 py-1 border-t border-border/30">
        <Button variant="ghost" size="sm" className="h-5 w-5 p-0 text-xs" onClick={decrementQuantity}>
          -
        </Button>
        <span className="text-xs font-bold tabular-nums w-4 text-center">{quantity}</span>
        <Button variant="ghost" size="sm" className="h-5 w-5 p-0 text-xs" onClick={incrementQuantity}>
          +
        </Button>

        <div className="flex-1" />

        <span className="text-[10px] text-green-500">TP:</span>
        <input
          type="number"
          value={tpPoints}
          onChange={(e) => setTpPoints(Number.parseFloat(e.target.value) || 0)}
          className="w-10 h-5 text-[10px] text-center bg-transparent border rounded px-0.5"
        />
        <span className="text-[10px] text-red-500">SL:</span>
        <input
          type="number"
          value={slPoints}
          onChange={(e) => setSlPoints(Number.parseFloat(e.target.value) || 0)}
          className="w-10 h-5 text-[10px] text-center bg-transparent border rounded px-0.5"
        />
      </div>

      {/* Close button */}
      <div className="px-2 py-1 border-t border-border/30">
        <Button
          variant="ghost"
          size="sm"
          className="h-5 w-full text-[10px] text-muted-foreground hover:text-destructive"
          onClick={closePosition}
        >
          x Close
        </Button>
      </div>
    </div>
  )
}
