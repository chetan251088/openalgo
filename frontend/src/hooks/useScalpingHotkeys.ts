import { useEffect, useCallback } from 'react'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { tradingApi } from '@/api/trading'
import { toast } from 'sonner'
import {
  buildVirtualPosition,
  extractOrderId,
  resolveFilledOrderPrice,
  resolveEntryPrice,
} from '@/lib/scalpingVirtualPosition'
import type { PlaceOrderRequest } from '@/types/trading'

function extractErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const responseData = (error as { response?: { data?: unknown } }).response?.data
    if (responseData && typeof responseData === 'object') {
      const responseMessage = (responseData as { message?: unknown }).message
      if (typeof responseMessage === 'string' && responseMessage.trim().length > 0) {
        return responseMessage.trim()
      }
      const responseError = (responseData as { error?: unknown }).error
      if (typeof responseError === 'string' && responseError.trim().length > 0) {
        return responseError.trim()
      }
    } else if (typeof responseData === 'string' && responseData.trim().length > 0) {
      return responseData.trim()
    }

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

interface UseScalpingHotkeysOptions {
  onBuy?: (side: 'CE' | 'PE', symbol: string) => void
  onSell?: (side: 'CE' | 'PE', symbol: string) => void
  onClose?: (side: 'CE' | 'PE', symbol: string) => void
  onCloseAll?: () => void
  onReversal?: (side: 'CE' | 'PE', symbol: string) => void
  onToggleHelp?: () => void
}

/**
 * Keyboard hotkey handler for scalping - active-side-aware.
 * B = Buy active side, S = Sell active side, C = Close active side,
 * X = Close all, R = Reversal, Tab = Toggle side, W = Toggle widget,
 * ArrowUp = CE market buy, ArrowDown = PE market buy
 */
export function useScalpingHotkeys(opts: UseScalpingHotkeysOptions = {}) {
  const hotkeysEnabled = useScalpingStore((s) => s.hotkeysEnabled)
  const activeSide = useScalpingStore((s) => s.activeSide)
  const selectedCESymbol = useScalpingStore((s) => s.selectedCESymbol)
  const selectedPESymbol = useScalpingStore((s) => s.selectedPESymbol)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const product = useScalpingStore((s) => s.product)
  const orderType = useScalpingStore((s) => s.orderType)
  const limitPrice = useScalpingStore((s) => s.limitPrice)
  const pendingLimitPlacement = useScalpingStore((s) => s.pendingLimitPlacement)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setPendingLimitPlacement = useScalpingStore((s) => s.setPendingLimitPlacement)
  const clearPendingLimitPlacement = useScalpingStore((s) => s.clearPendingLimitPlacement)
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)

  const setActiveSide = useScalpingStore((s) => s.setActiveSide)
  const toggleActiveSide = useScalpingStore((s) => s.toggleActiveSide)
  const toggleFloatingWidget = useScalpingStore((s) => s.toggleFloatingWidget)
  const setQuantity = useScalpingStore((s) => s.setQuantity)

  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)

  const getActiveSymbol = useCallback(() => {
    return activeSide === 'CE' ? selectedCESymbol : selectedPESymbol
  }, [activeSide, selectedCESymbol, selectedPESymbol])

  const getSymbolForSide = useCallback(
    (side: 'CE' | 'PE') => (side === 'CE' ? selectedCESymbol : selectedPESymbol),
    [selectedCESymbol, selectedPESymbol]
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
      toast.error('Failed to fetch API key')
    }
    console.warn('[Scalping] No API key available — generate one at /apikey')
    toast.error('API key missing. Generate one on /apikey')
    return null
  }, [apiKey, setApiKey])

  const placeOrder = useCallback(
    async (
      action: 'BUY' | 'SELL',
      sideOverride?: 'CE' | 'PE',
      forceMarket: boolean = false
    ) => {
      const side = sideOverride ?? activeSide
      const symbol = getSymbolForSide(side)
      const effectiveOrderType = forceMarket ? 'MARKET' : orderType
      if (!symbol) {
        console.warn('[Scalping] No symbol selected — click a strike first')
        toast.error('Select a strike first')
        return
      }

      // TRIGGER mode: arm side/action and place trigger only from chart click.
      if (effectiveOrderType === 'TRIGGER') {
        const existingTrigger = Object.values(triggerOrders).find(
          (t) => t.symbol === symbol && t.exchange === optionExchange && t.side === side
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
      if (effectiveOrderType === 'LIMIT' && !limitPrice) {
        setPendingEntryAction(action)
        console.log(`[Scalping] LIMIT armed (${action}) — click chart to set limit line`)
        return
      }
      if (
        effectiveOrderType === 'LIMIT' &&
        pendingLimitPlacement &&
        pendingLimitPlacement.symbol === symbol &&
        pendingLimitPlacement.side === side
      ) {
        console.warn('[Scalping] LIMIT already pending for this symbol. Wait for fill/cancel before placing another.')
        toast.error('A LIMIT order is already pending for this symbol')
        return
      }

      if (paperMode) {
        console.log(`[Paper] ${action} ${side} ${symbol} qty=${quantity * lotSize} @ ${effectiveOrderType}`)
        if (effectiveOrderType === 'MARKET' || effectiveOrderType === 'LIMIT') {
          const entryPrice = await resolveEntryPrice({
            symbol,
            exchange: optionExchange,
            preferredPrice: effectiveOrderType === 'LIMIT' ? (limitPrice ?? undefined) : undefined,
          })
          if (entryPrice > 0) {
            setVirtualTPSL(
              buildVirtualPosition({
                symbol,
                exchange: optionExchange,
                side,
                action,
                entryPrice,
                quantity: quantity * lotSize,
                tpPoints,
                slPoints,
                managedBy: 'hotkey',
              })
              )
            if (effectiveOrderType === 'LIMIT') {
              setLimitPrice(null)
            }
          }
        }
        clearPendingLimitPlacement()
        return
      }

      const key = await ensureApiKey()
      if (!key) return

      // MARKET or LIMIT — real broker order
      const pricetype = effectiveOrderType === 'LIMIT' ? 'LIMIT' : 'MARKET'

      const order: PlaceOrderRequest = {
        apikey: key,
        strategy: 'Scalping',
        exchange: optionExchange,
        symbol,
        action,
        quantity: quantity * lotSize,
        pricetype,
        product,
        price: effectiveOrderType === 'LIMIT' && limitPrice ? limitPrice : 0,
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
            if (limitPrice == null) {
              console.warn('[Scalping] LIMIT order acknowledged without a tracked limitPrice; keeping existing line state.')
            }
            setPendingLimitPlacement({
              symbol,
              side,
              action,
              orderId: brokerOrderId,
              quantity: quantity * lotSize,
              entryPrice: limitPrice ?? 0,
              tpPoints,
              slPoints,
            })
            if (limitPrice != null) setLimitPrice(limitPrice)
            return
          }

          clearPendingLimitPlacement()

          if (pricetype === 'MARKET') {
            const entryPrice = await resolveFilledOrderPrice({
              symbol,
              exchange: optionExchange,
              orderId: brokerOrderId,
              preferredPrice: undefined,
              apiKey: key,
            })
            if (entryPrice > 0) {
              setVirtualTPSL(
                buildVirtualPosition({
                  symbol,
                  exchange: optionExchange,
                  side,
                  action,
                  entryPrice,
                  quantity: quantity * lotSize,
                  tpPoints,
                  slPoints,
                  managedBy: 'hotkey',
                })
              )
            }
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
      getSymbolForSide,
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
      triggerOrders,
      ensureApiKey,
      setPendingLimitPlacement,
      clearPendingLimitPlacement,
      setVirtualTPSL,
      removeTriggerOrder,
      setLimitPrice,
      setPendingEntryAction,
    ]
  )

  useEffect(() => {
    if (!hotkeysEnabled) return

    const handler = (e: KeyboardEvent) => {
      // Skip when typing in inputs
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      const symbol = getActiveSymbol()

      switch (e.key.toLowerCase()) {
        case 'b': {
          e.preventDefault()
          if (symbol) {
            placeOrder('BUY')
            opts.onBuy?.(activeSide, symbol)
          }
          break
        }
        case 's': {
          e.preventDefault()
          if (symbol) {
            placeOrder('SELL')
            opts.onSell?.(activeSide, symbol)
          }
          break
        }
        case 'c': {
          e.preventDefault()
          if (symbol) {
            opts.onClose?.(activeSide, symbol)
          }
          break
        }
        case 'x': {
          e.preventDefault()
          opts.onCloseAll?.()
          break
        }
        case 'r': {
          e.preventDefault()
          if (symbol) {
            opts.onReversal?.(activeSide, symbol)
          }
          break
        }
        case 'w': {
          e.preventDefault()
          toggleFloatingWidget()
          break
        }
        case 'tab': {
          e.preventDefault()
          toggleActiveSide()
          break
        }
        case '1': {
          e.preventDefault()
          setQuantity(1)
          break
        }
        case '2': {
          e.preventDefault()
          setQuantity(2)
          break
        }
        case '3': {
          e.preventDefault()
          setQuantity(3)
          break
        }
        case '?': {
          e.preventDefault()
          opts.onToggleHelp?.()
          break
        }
        case 'arrowup': {
          e.preventDefault()
          if (selectedCESymbol) {
            setActiveSide('CE')
            placeOrder('BUY', 'CE', true)
            opts.onBuy?.('CE', selectedCESymbol)
          }
          break
        }
        case 'arrowdown': {
          e.preventDefault()
          if (selectedPESymbol) {
            setActiveSide('PE')
            placeOrder('BUY', 'PE', true)
            opts.onBuy?.('PE', selectedPESymbol)
          }
          break
        }
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [
    hotkeysEnabled,
    activeSide,
    getActiveSymbol,
    placeOrder,
    toggleActiveSide,
    setActiveSide,
    toggleFloatingWidget,
    setQuantity,
    selectedCESymbol,
    selectedPESymbol,
    opts,
  ])
}
