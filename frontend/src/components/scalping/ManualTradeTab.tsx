import { useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { tradingApi } from '@/api/trading'
import { buildVirtualPosition, resolveEntryPrice } from '@/lib/scalpingVirtualPosition'
import type { PlaceOrderRequest } from '@/types/trading'

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
  const limitPrice = useScalpingStore((s) => s.limitPrice)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
  const getTPSLForSymbol = useVirtualOrderStore((s) => s.getTPSLForSymbol)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)

  const setQuantity = useScalpingStore((s) => s.setQuantity)
  const incrementQuantity = useScalpingStore((s) => s.incrementQuantity)
  const decrementQuantity = useScalpingStore((s) => s.decrementQuantity)
  const setOrderType = useScalpingStore((s) => s.setOrderType)
  const setProduct = useScalpingStore((s) => s.setProduct)
  const setTpPoints = useScalpingStore((s) => s.setTpPoints)
  const setSlPoints = useScalpingStore((s) => s.setSlPoints)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)

  const activeSymbol = activeSide === 'CE' ? selectedCESymbol : selectedPESymbol

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
      if (!activeSymbol) {
        console.warn('[Scalping] No symbol selected — click a strike first')
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
            const existingVirtual = getTPSLForSymbol(activeSymbol)
            if (existingVirtual) removeVirtualTPSL(existingVirtual.id)
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
                })
              )
              if (orderType === 'LIMIT') {
                setLimitPrice(null)
              }
          }
        }
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
          console.log(
            `[Scalping] Order placed: ${action} ${activeSymbol} id=${res.data?.orderid}`
          )
          setPendingEntryAction(null)
          incrementTradeCount()

          // Market and limit entries should immediately render virtual position/TP/SL lines.
          if (pricetype === 'MARKET' || pricetype === 'LIMIT') {
            const entryPrice = await resolveEntryPrice({
              symbol: activeSymbol,
              exchange: optionExchange,
              preferredPrice: pricetype === 'LIMIT' ? (limitPrice ?? undefined) : undefined,
              apiKey: key,
            })
            if (entryPrice > 0) {
              const existingVirtual = getTPSLForSymbol(activeSymbol)
              if (existingVirtual) removeVirtualTPSL(existingVirtual.id)
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
                })
              )
              if (pricetype === 'LIMIT') {
                setLimitPrice(null)
              }
            }
          }
        } else {
          console.error(`[Scalping] Order rejected:`, res)
        }
      } catch (err) {
        console.error('[Scalping] Order failed:', err)
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
      tpPoints,
      slPoints,
      paperMode,
      getTPSLForSymbol,
      removeVirtualTPSL,
      triggerOrders,
      ensureApiKey,
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
      return
    }

    const key = await ensureApiKey()
    if (!key) return

    try {
      // Close by placing opposite MARKET order (matches old chart_window.html approach)
      const order: PlaceOrderRequest = {
        apikey: key,
        strategy: 'Scalping',
        exchange: optionExchange,
        symbol: activeSymbol,
        action: 'SELL',
        quantity: quantity * lotSize,
        pricetype: 'MARKET',
        product,
        price: 0,
        trigger_price: 0,
        disclosed_quantity: 0,
      }
      const res = await tradingApi.placeOrder(order)
      if (res.status === 'success') {
        console.log(`[Scalping] Closed ${activeSide} ${activeSymbol} id=${res.data?.orderid}`)
      } else {
        console.error('[Scalping] Close rejected:', res)
      }
    } catch (err) {
      console.error('[Scalping] Close failed:', err)
    }
  }, [activeSymbol, activeSide, quantity, lotSize, optionExchange, product, paperMode, ensureApiKey])

  const closeAll = useCallback(async () => {
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

  return (
    <div className="p-3 space-y-4">
      {/* Active symbol display */}
      <div className="text-center">
        <span className="text-xs text-muted-foreground">Active Side</span>
        <div className="text-sm font-bold">
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
      <div className="grid grid-cols-2 gap-2">
        <Button
          size="sm"
          className="bg-green-600 hover:bg-green-700 text-white font-bold"
          onClick={() => placeOrder('BUY')}
          disabled={!activeSymbol}
        >
          BUY
        </Button>
        <Button
          size="sm"
          className="bg-red-600 hover:bg-red-700 text-white font-bold"
          onClick={() => placeOrder('SELL')}
          disabled={!activeSymbol}
        >
          SELL
        </Button>
      </div>

      {/* Quantity stepper */}
      <div className="space-y-1">
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
            className="h-7 text-center text-sm w-16"
          />
          <Button variant="outline" size="sm" className="h-7 w-7 p-0" onClick={incrementQuantity}>
            +
          </Button>
          <span className="text-xs text-muted-foreground ml-1">
            = {quantity * lotSize} qty
          </span>
        </div>
      </div>

      {/* Order type toggle */}
      <div className="space-y-1">
        <Label className="text-xs">Order Type</Label>
        <div className="flex gap-1">
          {(['MARKET', 'LIMIT', 'TRIGGER'] as const).map((t) => (
            <Button
              key={t}
              variant={orderType === t ? 'secondary' : 'ghost'}
              size="sm"
              className="h-7 text-xs flex-1"
              onClick={() => setOrderType(t)}
            >
              {t}
            </Button>
          ))}
        </div>
      </div>

      {/* Product toggle */}
      <div className="space-y-1">
        <Label className="text-xs">Product</Label>
        <div className="flex gap-1">
          {(['MIS', 'NRML'] as const).map((p) => (
            <Button
              key={p}
              variant={product === p ? 'secondary' : 'ghost'}
              size="sm"
              className="h-7 text-xs flex-1"
              onClick={() => setProduct(p)}
            >
              {p}
            </Button>
          ))}
        </div>
      </div>

      {/* TP / SL points */}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs text-green-500">TP Points</Label>
          <Input
            type="number"
            min={0}
            step={5}
            value={tpPoints}
            onChange={(e) => setTpPoints(Number.parseFloat(e.target.value) || 0)}
            className="h-7 text-sm"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-red-500">SL Points</Label>
          <Input
            type="number"
            min={0}
            step={5}
            value={slPoints}
            onChange={(e) => setSlPoints(Number.parseFloat(e.target.value) || 0)}
            className="h-7 text-sm"
          />
        </div>
      </div>

      <div className="border-t pt-3 space-y-2">
        {/* Close active side */}
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs"
          onClick={closePosition}
          disabled={!activeSymbol}
        >
          Close {activeSide} Position
        </Button>

        {/* Close all */}
        <Button
          variant="destructive"
          size="sm"
          className="w-full text-xs"
          onClick={closeAll}
        >
          Close All Positions
        </Button>
      </div>
    </div>
  )
}
