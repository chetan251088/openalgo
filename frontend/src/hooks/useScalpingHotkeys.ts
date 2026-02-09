import { useEffect, useCallback } from 'react'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { tradingApi } from '@/api/trading'
import type { PlaceOrderRequest } from '@/types/trading'

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
 * ArrowUp/Down = Move strike selection
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
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const paperMode = useScalpingStore((s) => s.paperMode)

  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)

  const toggleActiveSide = useScalpingStore((s) => s.toggleActiveSide)
  const toggleFloatingWidget = useScalpingStore((s) => s.toggleFloatingWidget)
  const setQuantity = useScalpingStore((s) => s.setQuantity)

  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)

  const getActiveSymbol = useCallback(() => {
    return activeSide === 'CE' ? selectedCESymbol : selectedPESymbol
  }, [activeSide, selectedCESymbol, selectedPESymbol])

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
      const symbol = getActiveSymbol()
      if (!symbol) {
        console.warn('[Scalping] No symbol selected — click a strike first')
        return
      }

      if (paperMode) {
        console.log(`[Paper] ${action} ${activeSide} ${symbol} qty=${quantity * lotSize} @ ${orderType}`)
        return
      }

      // TRIGGER mode = virtual client-side trigger
      if (orderType === 'TRIGGER' && limitPrice) {
        setVirtualTPSL({
          id: `trigger-${Date.now()}`,
          symbol,
          exchange: optionExchange,
          side: activeSide,
          action,
          entryPrice: limitPrice,
          quantity: quantity * lotSize,
          tpPrice: tpPoints > 0 ? limitPrice + (action === 'BUY' ? tpPoints : -tpPoints) : null,
          slPrice: slPoints > 0 ? limitPrice + (action === 'BUY' ? -slPoints : slPoints) : null,
          tpPoints,
          slPoints,
          createdAt: Date.now(),
        })
        console.log(`[Scalping] Virtual trigger set: ${action} ${symbol} @ ${limitPrice}`)
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
          console.log(`[Scalping] Order placed: ${action} ${symbol} id=${res.data?.orderid}`)

          // Create virtual TP/SL for the filled order
          const entryPrice = (orderType === 'LIMIT' && limitPrice) ? limitPrice : 0
          if (entryPrice > 0 && (tpPoints > 0 || slPoints > 0)) {
            const isBuy = action === 'BUY'
            setVirtualTPSL({
              id: `tpsl-${Date.now()}`,
              symbol,
              exchange: optionExchange,
              side: activeSide,
              action,
              entryPrice,
              quantity: quantity * lotSize,
              tpPrice: tpPoints > 0 ? entryPrice + (isBuy ? tpPoints : -tpPoints) : null,
              slPrice: slPoints > 0 ? entryPrice + (isBuy ? -slPoints : slPoints) : null,
              tpPoints,
              slPoints,
              createdAt: Date.now(),
            })
          }
        } else {
          console.error(`[Scalping] Order rejected:`, res)
        }
      } catch (err) {
        console.error('[Scalping] Order failed:', err)
      }
    },
    [getActiveSymbol, activeSide, quantity, lotSize, optionExchange, product, orderType, limitPrice, tpPoints, slPoints, paperMode, ensureApiKey, setVirtualTPSL]
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
    toggleFloatingWidget,
    setQuantity,
    opts,
  ])
}
