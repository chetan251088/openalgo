import type {
  ApiResponse,
  Holding,
  MarginData,
  Order,
  OrderStats,
  PlaceOrderRequest,
  PortfolioStats,
  Position,
  Trade,
} from '@/types/trading'
import { apiClient, webClient } from './client'
import { proxyV1ByRole, isMultiBrokerUnifiedMode } from './multi-broker'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useMultiBrokerStore } from '@/stores/multiBrokerStore'

export interface QuotesData {
  ask: number
  bid: number
  high: number
  low: number
  ltp: number
  oi: number
  open: number
  prev_close: number
  volume: number
}

export interface DepthLevel {
  price: number
  quantity: number
}

export interface DepthData {
  asks: DepthLevel[]
  bids: DepthLevel[]
  high: number
  low: number
  ltp: number
  ltq: number
  oi: number
  open: number
  prev_close: number
  totalbuyqty: number
  totalsellqty: number
  volume: number
}

export interface MultiQuotesSymbol {
  symbol: string
  exchange: string
}

export interface MultiQuotesResult {
  symbol: string
  exchange: string
  data: QuotesData
}

export interface HistoryCandleData {
  timestamp?: number | string
  time?: number | string
  datetime?: string
  date?: string
  open?: number | string
  high?: number | string
  low?: number | string
  close?: number | string
  volume?: number | string
  oi?: number | string
}

// MultiQuotes API has a different response structure (results at root, not in data)
export interface MultiQuotesApiResponse {
  status: 'success' | 'error'
  results?: MultiQuotesResult[]
  message?: string
}

export interface ScalpingBridgeEntry {
  id: number
  status: 'pending' | 'acked'
  created_at: number
  updated_at: number
  symbol: string
  exchange: string
  side: 'CE' | 'PE'
  action: 'BUY' | 'SELL'
  quantity: number
  tp_points: number
  sl_points: number
  entry_price?: number
  order_id?: string
  source?: string
  managed_by?: string
}

export interface ScalpingBridgePendingData {
  entries: ScalpingBridgeEntry[]
  count: number
}

type TradeProduct = NonNullable<PlaceOrderRequest['product']>

function normalizeProduct(product?: string): TradeProduct {
  const normalized = String(product ?? '').toUpperCase()
  if (normalized === 'MIS' || normalized === 'NRML' || normalized === 'CNC') {
    return normalized
  }
  return 'MIS'
}

async function resolveRuntimeApiKey(): Promise<string | null> {
  const existing = useAuthStore.getState().apiKey
  if (existing) return existing

  try {
    const response = await fetch('/api/websocket/apikey', { credentials: 'include' })
    const data = await response.json()
    if (data.status === 'success' && data.api_key) {
      useAuthStore.getState().setApiKey(data.api_key)
      return data.api_key as string
    }
  } catch {
    // ignore
  }
  return null
}

function extractOrderIdFromResponse(response: ApiResponse<{ orderid: string }>): string {
  const rootOrderId = (response as { orderid?: unknown }).orderid
  if (typeof rootOrderId === 'string' && rootOrderId.trim().length > 0) {
    return rootOrderId.trim()
  }

  const nestedOrderId = (response.data as { orderid?: unknown } | undefined)?.orderid
  if (typeof nestedOrderId === 'string' && nestedOrderId.trim().length > 0) {
    return nestedOrderId.trim()
  }

  return ''
}

function recordScalpingOrderAck(order: PlaceOrderRequest, response: ApiResponse<{ orderid: string }>): void {
  const orderId = extractOrderIdFromResponse(response)
  if (!orderId) return

  const unified = isMultiBrokerUnifiedMode()
  const brokerName = unified
    ? useMultiBrokerStore.getState().executionBroker
    : (useAuthStore.getState().user?.broker ?? 'unknown')

  useScalpingStore.getState().setLastOrderAck({
    orderId,
    broker: String(brokerName).toUpperCase(),
    symbol: order.symbol,
    action: order.action,
    timestamp: Date.now(),
  })
}

export const tradingApi = {
  /**
   * Get real-time quotes for a symbol
   */
  getQuotes: async (
    apiKey: string,
    symbol: string,
    exchange: string
  ): Promise<ApiResponse<QuotesData>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<QuotesData>>('feed', 'quotes', {
        apikey: apiKey,
        symbol,
        exchange,
      })
    }
    const response = await apiClient.post<ApiResponse<QuotesData>>('/quotes', {
      apikey: apiKey,
      symbol,
      exchange,
    })
    return response.data
  },

  /**
   * Get real-time quotes for multiple symbols
   */
  getMultiQuotes: async (
    apiKey: string,
    symbols: MultiQuotesSymbol[]
  ): Promise<MultiQuotesApiResponse> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<MultiQuotesApiResponse>('feed', 'multiquotes', {
        apikey: apiKey,
        symbols,
      })
    }
    const response = await apiClient.post<MultiQuotesApiResponse>('/multiquotes', {
      apikey: apiKey,
      symbols,
    })
    return response.data
  },

  /**
   * Get historical OHLC data for a symbol
   */
  getHistory: async (
    apiKey: string,
    symbol: string,
    exchange: string,
    interval: string,
    startDate: string,
    endDate: string,
    source: 'api' | 'db' = 'api'
  ): Promise<ApiResponse<HistoryCandleData[]>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<HistoryCandleData[]>>('feed', 'history', {
        apikey: apiKey,
        symbol,
        exchange,
        interval,
        start_date: startDate,
        end_date: endDate,
        source,
      })
    }
    const response = await apiClient.post<ApiResponse<HistoryCandleData[]>>('/history', {
      apikey: apiKey,
      symbol,
      exchange,
      interval,
      start_date: startDate,
      end_date: endDate,
      source,
    })
    return response.data
  },

  /**
   * Get market depth for a symbol (5-level order book)
   */
  getDepth: async (
    apiKey: string,
    symbol: string,
    exchange: string
  ): Promise<ApiResponse<DepthData>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<DepthData>>('feed', 'depth', {
        apikey: apiKey,
        symbol,
        exchange,
      })
    }
    const response = await apiClient.post<ApiResponse<DepthData>>('/depth', {
      apikey: apiKey,
      symbol,
      exchange,
    })
    return response.data
  },

  /**
   * Get margin/funds data
   */
  getFunds: async (apiKey: string): Promise<ApiResponse<MarginData>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<MarginData>>('execution', 'funds', {
        apikey: apiKey,
      })
    }
    const response = await apiClient.post<ApiResponse<MarginData>>('/funds', {
      apikey: apiKey,
    })
    return response.data
  },

  /**
   * Get positions
   */
  getPositions: async (apiKey: string): Promise<ApiResponse<Position[]>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<Position[]>>('execution', 'positionbook', {
        apikey: apiKey,
      })
    }
    const response = await apiClient.post<ApiResponse<Position[]>>('/positionbook', {
      apikey: apiKey,
    })
    return response.data
  },

  /**
   * Get order book
   */
  getOrders: async (
    apiKey: string
  ): Promise<ApiResponse<{ orders: Order[]; statistics: OrderStats }>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<{ orders: Order[]; statistics: OrderStats }>>(
        'execution',
        'orderbook',
        {
          apikey: apiKey,
        }
      )
    }
    const response = await apiClient.post<ApiResponse<{ orders: Order[]; statistics: OrderStats }>>(
      '/orderbook',
      {
        apikey: apiKey,
      }
    )
    return response.data
  },

  /**
   * Get trade book
   */
  getTrades: async (apiKey: string): Promise<ApiResponse<Trade[]>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<Trade[]>>('execution', 'tradebook', {
        apikey: apiKey,
      })
    }
    const response = await apiClient.post<ApiResponse<Trade[]>>('/tradebook', {
      apikey: apiKey,
    })
    return response.data
  },

  /**
   * Get holdings
   */
  getHoldings: async (
    apiKey: string
  ): Promise<ApiResponse<{ holdings: Holding[]; statistics: PortfolioStats }>> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ApiResponse<{ holdings: Holding[]; statistics: PortfolioStats }>>(
        'execution',
        'holdings',
        {
          apikey: apiKey,
        }
      )
    }
    const response = await apiClient.post<
      ApiResponse<{ holdings: Holding[]; statistics: PortfolioStats }>
    >('/holdings', {
      apikey: apiKey,
    })
    return response.data
  },

  /**
   * Place order
   */
  placeOrder: async (order: PlaceOrderRequest): Promise<ApiResponse<{ orderid: string }>> => {
    let result: ApiResponse<{ orderid: string }>
    if (isMultiBrokerUnifiedMode()) {
      result = await proxyV1ByRole<ApiResponse<{ orderid: string }>>('execution', 'placeorder', order)
    } else {
      const response = await apiClient.post<ApiResponse<{ orderid: string }>>('/placeorder', order)
      result = response.data
    }

    if (result.status === 'success') {
      recordScalpingOrderAck(order, result)
    }
    return result
  },

  /**
   * Modify order (uses session auth with CSRF)
   */
  modifyOrder: async (
    orderid: string,
    orderData: {
      symbol: string
      exchange: string
      action: string
      product: string
      pricetype: string
      price: number
      quantity: number
      trigger_price?: number
      disclosed_quantity?: number
    }
  ): Promise<ApiResponse<{ orderid: string }>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }
      return proxyV1ByRole<ApiResponse<{ orderid: string }>>('execution', 'modifyorder', {
        apikey: apiKey,
        strategy: 'Scalping',
        orderid,
        symbol: orderData.symbol,
        exchange: orderData.exchange,
        action: orderData.action,
        product: orderData.product,
        pricetype: orderData.pricetype,
        price: orderData.price,
        quantity: orderData.quantity,
        disclosed_quantity: orderData.disclosed_quantity ?? 0,
        trigger_price: orderData.trigger_price ?? 0,
      })
    }

    const response = await webClient.post<ApiResponse<{ orderid: string }>>('/modify_order', {
      orderid,
      ...orderData,
    })
    return response.data
  },

  /**
   * Cancel order (uses session auth with CSRF)
   */
  cancelOrder: async (orderid: string): Promise<ApiResponse<{ orderid: string }>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }
      return proxyV1ByRole<ApiResponse<{ orderid: string }>>('execution', 'cancelorder', {
        apikey: apiKey,
        strategy: 'Scalping',
        orderid,
      })
    }

    const response = await webClient.post<ApiResponse<{ orderid: string }>>('/cancel_order', {
      orderid,
    })
    return response.data
  },

  /**
   * Close a specific position (uses session auth with CSRF)
   */
  closePosition: async (
    symbol: string,
    exchange: string,
    product: string
  ): Promise<ApiResponse<void>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }

      const positionsResponse = await tradingApi.getPositions(apiKey)
      const list = Array.isArray(positionsResponse.data) ? positionsResponse.data : []
      const target = list.find((p) => {
        const qty = Number(p.quantity) || 0
        return (
          p.symbol === symbol &&
          p.exchange === exchange &&
          p.product === product &&
          qty !== 0
        )
      })

      if (!target) {
        return { status: 'success', message: 'No open position to close' }
      }

      const signedQty = Number(target.quantity) || 0
      const closeQty = Math.abs(signedQty)
      if (closeQty <= 0) {
        return { status: 'success', message: 'No open position to close' }
      }

      const closeAction = signedQty > 0 ? 'SELL' : 'BUY'
      const closeRes = await tradingApi.placeOrder({
        apikey: apiKey,
        strategy: 'Scalping',
        exchange,
        symbol,
        action: closeAction,
        quantity: closeQty,
        pricetype: 'MARKET',
        product: normalizeProduct(product),
        price: 0,
        trigger_price: 0,
        disclosed_quantity: 0,
      })
      return {
        status: closeRes.status,
        message: closeRes.message,
      }
    }

    // Uses the web route which handles session-based auth with CSRF
    const response = await webClient.post<ApiResponse<void>>('/close_position', {
      symbol,
      exchange,
      product,
    })
    return response.data
  },

  /**
   * Close all positions (uses session auth with CSRF)
   */
  closeAllPositions: async (): Promise<ApiResponse<void>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }

      const positionsResponse = await tradingApi.getPositions(apiKey)
      const list = Array.isArray(positionsResponse.data) ? positionsResponse.data : []
      const openPositions = list.filter((p) => (Number(p.quantity) || 0) !== 0)
      if (!openPositions.length) {
        return { status: 'success', message: 'No open positions to close' }
      }

      let failed = 0
      for (const p of openPositions) {
        const signedQty = Number(p.quantity) || 0
        const qty = Math.abs(signedQty)
        if (qty <= 0) continue

        const action = signedQty > 0 ? 'SELL' : 'BUY'
        const result = await tradingApi.placeOrder({
          apikey: apiKey,
          strategy: 'Scalping',
          exchange: p.exchange,
          symbol: p.symbol,
          action,
          quantity: qty,
          pricetype: 'MARKET',
          product: normalizeProduct(p.product),
          price: 0,
          trigger_price: 0,
          disclosed_quantity: 0,
        })
        if (result.status !== 'success') {
          failed += 1
        }
      }

      if (failed > 0) {
        return {
          status: 'error',
          message: `Failed to close ${failed} position(s)`,
        }
      }
      return { status: 'success', message: 'All positions closed' }
    }

    const response = await webClient.post<ApiResponse<void>>('/close_all_positions', {})
    return response.data
  },

  /**
   * Cancel all orders (uses session auth with CSRF)
   */
  cancelAllOrders: async (): Promise<ApiResponse<void>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }
      return proxyV1ByRole<ApiResponse<void>>('execution', 'cancelallorder', {
        apikey: apiKey,
        strategy: 'Scalping',
      })
    }

    const response = await webClient.post<ApiResponse<void>>('/cancel_all_orders', {})
    return response.data
  },

  /**
   * Read pending flow-triggered entries that should attach virtual TP/SL lines.
   */
  getScalpingBridgePending: async (
    apiKey: string,
    limit = 50
  ): Promise<ApiResponse<ScalpingBridgePendingData>> => {
    const response = await apiClient.post<ApiResponse<ScalpingBridgePendingData>>(
      '/scalpingbridge/pending',
      {
        apikey: apiKey,
        limit,
      }
    )
    return response.data
  },

  /**
   * Acknowledge consumed flow bridge entries so they are not replayed.
   */
  ackScalpingBridgeEntries: async (
    apiKey: string,
    ids: number[]
  ): Promise<ApiResponse<{ acked: number }>> => {
    const normalizedIds = ids
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0)
    const response = await apiClient.post<ApiResponse<{ acked: number }>>('/scalpingbridge/ack', {
      apikey: apiKey,
      ids: normalizedIds,
    })
    return response.data
  },
}
