import { useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { tradingApi } from '@/api/trading'
import { toast } from 'sonner'
import {
  buildVirtualPosition,
  extractOrderId,
  extractOrderIds,
  extractOrderLegs,
  resolveFilledOrderPrice,
  resolveEntryPrice,
} from '@/lib/scalpingVirtualPosition'
import type { PlaceOrderRequest } from '@/types/trading'

function extractErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const message = (error as { message?: unknown }).message
    if (typeof message === 'string' && message.trim().length > 0) {
      return message.trim()
    }
  }
  if (typeof error === 'string' && error.trim().length > 0) {
    return error.trim()
  }
  return fallback
}

export function ManualTradeTab() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)

  const activeSide = useScalpingStore((s) => s.activeSide)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const product = useScalpingStore((s) => s.product)
  const orderType = useScalpingStore((s) => s.orderType)
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const trailDistancePoints = useScalpingStore((s) => s.trailDistancePoints)
  const trailSlEnabled = useScalpingStore((s) => s.trailSlEnabled)
  const limitPrice = useScalpingStore((s) => s.limitPrice)
  const pendingLimitPlacement = useScalpingStore((s) => s.pendingLimitPlacement)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)
  const setPendingLimitPlacement = useScalpingStore((s) => s.setPendingLimitPlacement)
  const clearPendingLimitPlacement = useScalpingStore((s) => s.clearPendingLimitPlacement)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
  const clearVirtualForSymbol = useVirtualOrderStore((s) => s.clearForSymbol)
  const clearVirtualOrders = useVirtualOrderStore((s) => s.clearAll)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)
  const getTPSLForSymbol = useVirtualOrderStore((s) => s.getTPSLForSymbol)

  const setQuantity = useScalpingStore((s) => s.setQuantity)
  const incrementQuantity = useScalpingStore((s) => s.incrementQuantity)
  const decrementQuantity = useScalpingStore((s) => s.decrementQuantity)
  const setOrderType = useScalpingStore((s) => s.setOrderType)
  const setProduct = useScalpingStore((s) => s.setProduct)
  const setTpPoints = useScalpingStore((s) => s.setTpPoints)
  const setSlPoints = useScalpingStore((s) => s.setSlPoints)
  const setTrailDistancePoints = useScalpingStore((s) => s.setTrailDistancePoints)
  const setTrailSlEnabled = useScalpingStore((s) => s.setTrailSlEnabled)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)

  const activeSymbol = activeSide === 'CE' ? selectedCESymbol : selectedPESymbol
  const effectiveTrailDistancePoints = trailSlEnabled ? trailDistancePoints : 0

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
      toast.error('Failed to fetch API key')
    }
    console.warn('[Scalping] No API key available — generate one at /apikey')
    toast.error('API key missing. Generate one on /apikey')
    return null
  }, [apiKey, setApiKey])

  const placeOrder = useCallback(
    async (action: 'BUY' | 'SELL') => {
      if (!activeSymbol) {
        console.warn('[Scalping] No symbol selected — click a strike first')
        toast.error('Select a strike first')
        return
      }

      // TRIGGER mode: arm side/action and place trigger only from chart click.
      if (orderType === 'TRIGGER') {
        const existingTrigger = Object.values(triggerOrders).find(
          (t) =>
            t.symbol === activeSymbol &&
            t.exchange === optionExchange &&
            t.side === activeSide
        )
        if (existingTrigger) {
          removeTriggerOrder(existingTrigger.id)
        }
        clearPendingLimitPlacement()
        setLimitPrice(null)
        setPendingEntryAction(action)
        console.log(`[Scalping] TRIGGER armed (${action}) — click chart to place trigger line`)
        return
      }

      // LIMIT mode: require chart click to set a limit line first.
      if (orderType === 'LIMIT' && !limitPrice) {
        setPendingEntryAction(action)
        console.log(`[Scalping] LIMIT armed (${action}) — click chart to set limit line`)
        return
      }
      if (
        orderType === 'LIMIT' &&
        pendingLimitPlacement &&
        pendingLimitPlacement.symbol === activeSymbol &&
        pendingLimitPlacement.side === activeSide
      ) {
        console.warn('[Scalping] LIMIT already pending for this symbol. Wait for fill/cancel before placing another.')
        toast.error('A LIMIT order is already pending for this symbol')
        return
      }

      if (paperMode) {
        console.log(
          `[Paper] ${action} ${activeSide} ${activeSymbol} qty=${quantity * lotSize} @ ${orderType}`
        )
        if (orderType === 'MARKET' || orderType === 'LIMIT') {
          const entryPrice = await resolveEntryPrice({
            symbol: activeSymbol,
            exchange: optionExchange,
            preferredPrice: orderType === 'LIMIT' ? (limitPrice ?? undefined) : undefined,
          })
          if (entryPrice > 0) {
            setVirtualTPSL(
              buildVirtualPosition({
                symbol: activeSymbol,
                exchange: optionExchange,
                side: activeSide,
                action,
                entryPrice,
                quantity: quantity * lotSize,
                tpPoints,
                slPoints,
                trailDistancePoints: effectiveTrailDistancePoints,
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
        return
      }

      const key = await ensureApiKey()
      if (!key) return

      // MARKET or LIMIT — real broker order
      const pricetype = orderType === 'LIMIT' ? 'LIMIT' : 'MARKET'

      const order: PlaceOrderRequest = {
        apikey: key,
        strategy: 'Scalping',
        exchange: optionExchange,
        symbol: activeSymbol,
        action,
        quantity: quantity * lotSize,
        pricetype,
        product,
        price: orderType === 'LIMIT' && limitPrice ? limitPrice : 0,
        trigger_price: 0,
        disclosed_quantity: 0,
      }

      try {
        console.log(`[Scalping] Placing ${action} ${activeSymbol} qty=${quantity * lotSize} ${pricetype}`)
        const res = await tradingApi.placeOrder(order)
        if (res.status === 'success') {
          const brokerOrderId = extractOrderId(res)
          console.log(
            `[Scalping] Order placed: ${action} ${activeSymbol} id=${brokerOrderId ?? 'n/a'}`
          )
          setPendingEntryAction(null)

          if (pricetype === 'LIMIT') {
            if (limitPrice == null) {
              console.warn('[Scalping] LIMIT order acknowledged without a tracked limitPrice; keeping existing line state.')
            }
            const brokerOrderIds = extractOrderIds(res)
            const splitLegs = extractOrderLegs(res)
            setPendingLimitPlacement({
              symbol: activeSymbol,
              side: activeSide,
              action,
              orderId: brokerOrderId,
              orderIds: brokerOrderIds.length > 0 ? brokerOrderIds : undefined,
              splitLegs: splitLegs.length > 0 ? splitLegs : undefined,
              quantity: quantity * lotSize,
              entryPrice: limitPrice ?? 0,
              tpPoints,
              slPoints,
              trailDistancePoints: effectiveTrailDistancePoints,
            })
            if (limitPrice != null) setLimitPrice(limitPrice)
            return
          }

          clearPendingLimitPlacement()
          incrementTradeCount()

          if (pricetype === 'MARKET') {
            // Resolve virtual position asynchronously — don't block order confirmation feedback
            const snapSymbol = activeSymbol
            const snapExchange = optionExchange
            const snapSide = activeSide
            const snapQty = quantity * lotSize
            void (async () => {
              const marketPriceHint = await resolveEntryPrice({
                symbol: snapSymbol,
                exchange: snapExchange,
                apiKey: null,
              })
              const entryPrice = await resolveFilledOrderPrice({
                symbol: snapSymbol,
                exchange: snapExchange,
                orderId: brokerOrderId,
                preferredPrice: marketPriceHint > 0 ? marketPriceHint : undefined,
                apiKey: key,
              })
              if (entryPrice > 0) {
                setVirtualTPSL(
                  buildVirtualPosition({
                    symbol: snapSymbol,
                    exchange: snapExchange,
                    side: snapSide,
                    action,
                    entryPrice,
                    quantity: snapQty,
                    tpPoints,
                    slPoints,
                    trailDistancePoints: effectiveTrailDistancePoints,
                    managedBy: 'manual',
                  })
                )
              }
            })()
          }
        } else {
          console.error(`[Scalping] Order rejected:`, res)
          toast.error(extractErrorMessage(res, 'Order was rejected'))
        }
      } catch (err) {
        console.error('[Scalping] Order failed:', err)
        toast.error(extractErrorMessage(err, 'Order placement failed'))
      }
    },
    [
      activeSymbol,
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
      effectiveTrailDistancePoints,
      paperMode,
      clearVirtualForSymbol,
      triggerOrders,
      ensureApiKey,
      setPendingLimitPlacement,
      clearPendingLimitPlacement,
      incrementTradeCount,
      setVirtualTPSL,
      removeTriggerOrder,
      setLimitPrice,
      setPendingEntryAction,
    ]
  )

  const closePosition = useCallback(async () => {
    if (!activeSymbol) return

    if (paperMode) {
      console.log(`[Paper] Close ${activeSide} ${activeSymbol}`)
      clearVirtualForSymbol(activeSymbol)
      setLimitPrice(null)
      setPendingEntryAction(null)
      clearPendingLimitPlacement()
      return
    }

    const key = await ensureApiKey()
    if (!key) return

    // Fast path: use virtual position data to place direct exit order (skips broker position fetch)
    const virtualPos = getTPSLForSymbol(activeSymbol)
    if (virtualPos && virtualPos.quantity > 0) {
      const closeAction = virtualPos.action === 'BUY' ? 'SELL' : 'BUY'
      try {
        const res = await tradingApi.placeOrder({
          apikey: key,
          strategy: 'Scalping',
          exchange: optionExchange,
          symbol: activeSymbol,
          action: closeAction,
          quantity: virtualPos.quantity,
          pricetype: 'MARKET',
          product,
          price: 0,
          trigger_price: 0,
          disclosed_quantity: 0,
        })
        if (res.status === 'success') {
          console.log(`[Scalping] Closed ${activeSide} ${activeSymbol} qty=${virtualPos.quantity} (fast path)`)
          clearVirtualForSymbol(activeSymbol)
          setLimitPrice(null)
          setPendingEntryAction(null)
          clearPendingLimitPlacement()
          return
        }
        console.warn('[Scalping] Fast-close rejected, falling back to position-fetch close:', res)
      } catch (err) {
        console.warn('[Scalping] Fast-close failed, falling back:', err)
      }
    }

    // Fallback: server-side close (fetches positions from broker)
    try {
      const res = await tradingApi.closePosition(activeSymbol, optionExchange, product)
      if (res.status === 'success') {
        console.log(`[Scalping] Closed ${activeSide} ${activeSymbol}`)
        clearVirtualForSymbol(activeSymbol)
        setLimitPrice(null)
        setPendingEntryAction(null)
        clearPendingLimitPlacement()
      } else {
        console.error('[Scalping] Close rejected:', res)
        toast.error(extractErrorMessage(res, 'Close position rejected'))
      }
    } catch (err) {
      console.error('[Scalping] Close failed:', err)
      toast.error(extractErrorMessage(err, 'Close position failed'))
    }
  }, [
    activeSymbol,
    activeSide,
    optionExchange,
    product,
    paperMode,
    getTPSLForSymbol,
    ensureApiKey,
    clearVirtualForSymbol,
    setLimitPrice,
    setPendingEntryAction,
    clearPendingLimitPlacement,
  ])

  const closeAll = useCallback(async () => {
    if (paperMode) {
      console.log('[Paper] Close all positions')
      clearVirtualOrders()
      setLimitPrice(null)
      setPendingEntryAction(null)
      clearPendingLimitPlacement()
      return
    }

    const cancelAllPromise = tradingApi.cancelAllOrders().catch((error) => {
      console.warn('[Scalping] Cancel all orders failed:', error)
      return { status: 'error', message: extractErrorMessage(error, 'Cancel all orders failed') }
    })

    try {
      const res = await tradingApi.closeAllPositions({ verify: false })
      const cancelAllRes = await cancelAllPromise

      if (res.status === 'success' || res.status === 'info') {
        if (cancelAllRes.status === 'error') {
          console.warn('[Scalping] Close-all completed but cancel-all-orders failed:', cancelAllRes)
        }
        console.log('[Scalping] Closed all positions')
        clearVirtualOrders()
        setLimitPrice(null)
        setPendingEntryAction(null)
        clearPendingLimitPlacement()
      } else {
        console.error('[Scalping] Close all rejected:', res)
        toast.error(extractErrorMessage(res, 'Close all rejected'))
      }
    } catch (err) {
      console.error('[Scalping] Close all failed:', err)
      toast.error(extractErrorMessage(err, 'Close all failed'))
    }
  }, [paperMode, clearVirtualOrders, setLimitPrice, setPendingEntryAction, clearPendingLimitPlacement])

  return (
    <div className="p-3 space-y-3">
      {/* Active symbol display */}
      <div className="space-y-1 text-center">
        <span className="text-[11px] text-muted-foreground">Active Side</span>
        <div className="text-sm font-bold leading-none">
          <span
            className={
              activeSide === 'CE' ? 'text-green-500' : 'text-red-500'
            }
          >
            {activeSide}
          </span>
          {' '}
          <span className="text-foreground font-mono text-xs">
            {activeSymbol || 'No strike selected'}
          </span>
        </div>
      </div>

      {/* Buy / Sell buttons */}
      <div className="flex items-center justify-center gap-2">
        <Button
          size="sm"
          className="h-8 min-w-[88px] px-4 bg-green-600 hover:bg-green-700 text-white font-bold"
          onClick={() => placeOrder('BUY')}
          disabled={!activeSymbol}
        >
          BUY
        </Button>
        <Button
          size="sm"
          className="h-8 min-w-[88px] px-4 bg-red-600 hover:bg-red-700 text-white font-bold"
          onClick={() => placeOrder('SELL')}
          disabled={!activeSymbol}
        >
          SELL
        </Button>
      </div>

      {/* Quantity stepper */}
      <div className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-card/20 px-2.5 py-2">
        <Label className="text-xs">Lots</Label>
        <div className="flex items-center gap-1">
          <Button variant="outline" size="sm" className="h-7 w-7 p-0" onClick={decrementQuantity}>
            -
          </Button>
          <Input
            type="number"
            min={1}
            value={quantity}
            onChange={(e) => setQuantity(Number.parseInt(e.target.value) || 1)}
            className="h-7 w-14 text-center text-sm"
          />
          <Button variant="outline" size="sm" className="h-7 w-7 p-0" onClick={incrementQuantity}>
            +
          </Button>
          <span className="ml-1 text-[11px] text-muted-foreground">
            = {quantity * lotSize} qty
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {/* Order type toggle */}
        <div className="space-y-1">
          <Label className="text-[11px]">Type</Label>
          <div className="grid grid-cols-3 gap-1">
            {(['MARKET', 'LIMIT', 'TRIGGER'] as const).map((t) => (
              <Button
                key={t}
                variant={orderType === t ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-0 text-[10px]"
                onClick={() => setOrderType(t)}
              >
                {t}
              </Button>
            ))}
          </div>
        </div>

        {/* Product toggle */}
        <div className="space-y-1">
          <Label className="text-[11px]">Product</Label>
          <div className="grid grid-cols-2 gap-1">
            {(['MIS', 'NRML'] as const).map((p) => (
              <Button
                key={p}
                variant={product === p ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-0 text-[10px]"
                onClick={() => setProduct(p)}
              >
                {p}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* TP / SL / Trail points */}
      <div className="grid grid-cols-3 gap-1.5 rounded-lg border border-border/60 bg-card/20 p-2">
        <div className="space-y-1">
          <Label className="text-[10px] text-green-500">TP</Label>
          <Input
            type="number"
            min={0}
            step={5}
            value={tpPoints}
            onChange={(e) => setTpPoints(Number.parseFloat(e.target.value) || 0)}
            className="h-7 px-2 text-center text-sm"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-[10px] text-red-500">SL</Label>
          <Input
            type="number"
            min={0}
            step={5}
            value={slPoints}
            onChange={(e) => setSlPoints(Number.parseFloat(e.target.value) || 0)}
            className="h-7 px-2 text-center text-sm"
          />
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between gap-1">
            <Label className="text-[10px] text-amber-400">Trail</Label>
            <label className="flex items-center gap-1 text-[9px] text-muted-foreground">
              <Checkbox
                checked={trailSlEnabled}
                onCheckedChange={(checked) => setTrailSlEnabled(checked === true)}
                className="h-3 w-3"
              />
              On
            </label>
          </div>
          <Input
            type="number"
            min={0}
            step={0.5}
            value={trailDistancePoints}
            onChange={(e) => setTrailDistancePoints(Number.parseFloat(e.target.value) || 0)}
            className="h-7 px-2 text-center text-sm"
            disabled={!trailSlEnabled}
          />
        </div>
      </div>

      <div className="border-t pt-2">
        {/* Close active side */}
        <div className="flex justify-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8 min-w-[128px] px-3 text-xs"
            onClick={closePosition}
            disabled={!activeSymbol}
          >
            Close {activeSide} Position
          </Button>
          <Button
            variant="destructive"
            size="sm"
            className="h-8 min-w-[128px] px-3 text-xs"
            onClick={closeAll}
          >
            Close All Positions
          </Button>
        </div>
      </div>
    </div>
  )
}
