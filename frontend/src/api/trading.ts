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

export interface SplitOrderLeg {
  orderId: string
  quantity: number
}

export type PlaceOrderResponse = ApiResponse<{ orderid: string }> & {
  orderid?: string
  orderids?: string[]
  split?: boolean
  split_size?: number
  split_legs?: SplitOrderLeg[]
  total_quantity?: number
}

interface SymbolInfoData {
  symbol: string
  exchange: string
  name?: string
  lotsize?: number
  freeze_qty?: number
}

type SymbolInfoResponse = ApiResponse<SymbolInfoData>

type TradeProduct = NonNullable<PlaceOrderRequest['product']>
const MIN_AUTO_SLICE_FREEZE_QTY = 2
const FALLBACK_AUTO_SLICE_LOTS = 10
const freezeQtyCache = new Map<string, number>()
const INDEX_PREFIXES = [
  'NIFTY',
  'BANKNIFTY',
  'FINNIFTY',
  'MIDCPNIFTY',
  'NIFTYNXT50',
  'SENSEX',
  'BANKEX',
  'SENSEX50',
]

function normalizeProduct(product?: string): TradeProduct {
  const normalized = String(product ?? '').toUpperCase()
  if (normalized === 'MIS' || normalized === 'NRML' || normalized === 'CNC') {
    return normalized
  }
  return 'MIS'
}

function normalizeText(value: unknown): string {
  return String(value ?? '').trim().toUpperCase()
}

function parseSignedQuantity(value: unknown): number {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : 0
  }
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, '').trim())
    return Number.isFinite(parsed) ? parsed : 0
  }
  return 0
}

function parsePositiveInteger(value: unknown): number | null {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    const parsed = Math.floor(value)
    return parsed > 0 ? parsed : null
  }
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, '').trim())
    if (!Number.isFinite(parsed)) return null
    const intValue = Math.floor(parsed)
    return intValue > 0 ? intValue : null
  }
  return null
}

function getFreezeCacheKey(symbol: string, exchange: string): string {
  return `${normalizeText(exchange)}:${normalizeText(symbol)}`
}

function isKnownIndexSymbol(symbol: unknown): boolean {
  const normalized = normalizeText(symbol)
  if (!normalized) return false
  return INDEX_PREFIXES.some((prefix) => normalized.startsWith(prefix))
}

function deriveFallbackSliceSize(
  order: PlaceOrderRequest,
  symbolInfo: SymbolInfoData | undefined
): number | null {
  const lotSize = parsePositiveInteger(symbolInfo?.lotsize)
  if (lotSize == null || lotSize < MIN_AUTO_SLICE_FREEZE_QTY) return null

  const isIndexLike =
    isKnownIndexSymbol(order.symbol) ||
    isKnownIndexSymbol(symbolInfo?.symbol) ||
    isKnownIndexSymbol(symbolInfo?.name)
  if (!isIndexLike) return null

  const fallbackSliceSize = lotSize * FALLBACK_AUTO_SLICE_LOTS
  return fallbackSliceSize >= MIN_AUTO_SLICE_FREEZE_QTY ? fallbackSliceSize : null
}

function deriveStoreFallbackSliceSize(order: PlaceOrderRequest): number | null {
  const state = useScalpingStore.getState()
  const lotSize = parsePositiveInteger(state.lotSize)
  if (lotSize == null || lotSize < MIN_AUTO_SLICE_FREEZE_QTY) return null

  const isIndexLike =
    isKnownIndexSymbol(order.symbol) ||
    isKnownIndexSymbol(state.underlying)
  if (!isIndexLike) return null

  const fallbackSliceSize = lotSize * FALLBACK_AUTO_SLICE_LOTS
  return fallbackSliceSize >= MIN_AUTO_SLICE_FREEZE_QTY ? fallbackSliceSize : null
}

function resolveLotSizeForSlice(
  order: PlaceOrderRequest,
  symbolInfo?: SymbolInfoData
): number | null {
  const symbolLot = parsePositiveInteger(symbolInfo?.lotsize)
  if (symbolLot != null && symbolLot > 0) return symbolLot

  const stateLot = parsePositiveInteger(useScalpingStore.getState().lotSize)
  if (stateLot != null && stateLot > 0 && isKnownIndexSymbol(order.symbol)) {
    return stateLot
  }

  return null
}

function normalizeSliceSizeToLot(
  order: PlaceOrderRequest,
  sliceSize: number,
  symbolInfo?: SymbolInfoData
): number {
  const lotSize = resolveLotSizeForSlice(order, symbolInfo)
  if (lotSize == null || lotSize <= 1) return sliceSize

  const normalized = Math.floor(sliceSize / lotSize) * lotSize
  if (normalized >= lotSize) return normalized
  return lotSize
}

function parseSplitLeg(raw: unknown): SplitOrderLeg | null {
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  const status = normalizeText(record.status)
  if (status && status !== 'SUCCESS') return null

  const orderId = String(record.orderid ?? '').trim()
  if (!orderId) return null

  const quantity = parsePositiveInteger(record.quantity) ?? 0
  return { orderId, quantity }
}

function parseSplitOrderLegs(response: unknown): SplitOrderLeg[] {
  if (!response || typeof response !== 'object') return []
  const record = response as Record<string, unknown>
  const results = record.results
  if (!Array.isArray(results)) return []

  const legs: SplitOrderLeg[] = []
  for (const item of results) {
    const leg = parseSplitLeg(item)
    if (leg) legs.push(leg)
  }
  return legs
}

function parseSplitOrderErrors(response: unknown): string[] {
  if (!response || typeof response !== 'object') return []
  const record = response as Record<string, unknown>
  const results = record.results
  if (!Array.isArray(results)) return []

  const errors: string[] = []
  for (const item of results) {
    if (!item || typeof item !== 'object') continue
    const leg = item as Record<string, unknown>
    const status = normalizeText(leg.status)
    if (status === 'SUCCESS') continue
    const message = String(leg.message ?? '').trim()
    if (message) errors.push(message)
  }
  return errors
}

function normalizeSplitOrderResponse(
  response: unknown,
  splitSize: number,
  totalQuantity: number
): PlaceOrderResponse {
  if (!response || typeof response !== 'object') {
    return { status: 'error', message: 'Invalid splitorder response' }
  }

  const root = response as Record<string, unknown>
  const rawStatus = String(root.status ?? '').trim().toLowerCase()
  if (rawStatus === 'error') {
    return {
      status: 'error',
      message: String(root.message ?? 'Split order failed'),
    }
  }

  const legs = parseSplitOrderLegs(response)
  if (legs.length === 0) {
    const errors = parseSplitOrderErrors(response)
    const detail = errors.length > 0 ? `: ${errors[0]}` : ''
    return {
      status: 'error',
      message: String(root.message ?? `Split order returned no successful child orders${detail}`),
    }
  }

  const orderIds = legs.map((leg) => leg.orderId)
  const primaryOrderId = orderIds[0]

  return {
    status: 'success',
    message: String(
      root.message ?? `Split order placed (${legs.length} slices of ${splitSize})`
    ),
    data: { orderid: primaryOrderId },
    orderid: primaryOrderId,
    orderids: orderIds,
    split: true,
    split_size: splitSize,
    split_legs: legs,
    total_quantity: totalQuantity,
  }
}

function isScalpingStrategy(strategy: string): boolean {
  return normalizeText(strategy).startsWith('SCALPING')
}

function shouldAttemptAutoSlice(order: PlaceOrderRequest): boolean {
  if (!isScalpingStrategy(order.strategy)) return false
  const qty = Number(order.quantity)
  if (!Number.isFinite(qty) || qty <= 1) return false
  const cachedThreshold = freezeQtyCache.get(getFreezeCacheKey(order.symbol, order.exchange))
  if (cachedThreshold != null && qty <= cachedThreshold) {
    return false
  }

  const storeFallbackSliceSize = deriveStoreFallbackSliceSize(order)
  if (storeFallbackSliceSize != null) {
    const normalizedStoreFallbackSliceSize = normalizeSliceSizeToLot(order, storeFallbackSliceSize)
    if (normalizedStoreFallbackSliceSize >= MIN_AUTO_SLICE_FREEZE_QTY && qty <= normalizedStoreFallbackSliceSize) {
      return false
    }
  }

  return true
}

async function getFreezeQtyForOrder(order: PlaceOrderRequest): Promise<number | null> {
  const cacheKey = getFreezeCacheKey(order.symbol, order.exchange)
  if (freezeQtyCache.has(cacheKey)) {
    return freezeQtyCache.get(cacheKey) ?? null
  }
  const storeFallbackSliceSize = deriveStoreFallbackSliceSize(order)

  try {
    const payload = {
      apikey: order.apikey,
      symbol: order.symbol,
      exchange: order.exchange,
    }
    let response: SymbolInfoResponse
    if (isMultiBrokerUnifiedMode()) {
      response = await proxyV1ByRole<SymbolInfoResponse>('execution', 'symbol', payload)
    } else {
      const result = await apiClient.post<SymbolInfoResponse>('/symbol', payload)
      response = result.data
    }
    const freezeQty = parsePositiveInteger(response.data?.freeze_qty)
    if (freezeQty != null && freezeQty >= MIN_AUTO_SLICE_FREEZE_QTY) {
      const normalizedFreezeQty = normalizeSliceSizeToLot(order, freezeQty, response.data)
      if (normalizedFreezeQty >= MIN_AUTO_SLICE_FREEZE_QTY) {
        freezeQtyCache.set(cacheKey, normalizedFreezeQty)
        return normalizedFreezeQty
      }
    }

    const fallbackSliceSize = deriveFallbackSliceSize(order, response.data)
    if (fallbackSliceSize != null) {
      const normalizedFallbackSliceSize = normalizeSliceSizeToLot(
        order,
        fallbackSliceSize,
        response.data
      )
      if (normalizedFallbackSliceSize >= MIN_AUTO_SLICE_FREEZE_QTY) {
        freezeQtyCache.set(cacheKey, normalizedFallbackSliceSize)
        return normalizedFallbackSliceSize
      }
    }
  } catch (error) {
    console.warn('[Scalping] Auto-slice freeze lookup failed; using local fallback if available.', {
      symbol: order.symbol,
      exchange: order.exchange,
      error,
    })
  }

  if (storeFallbackSliceSize != null) {
    const normalizedStoreFallbackSliceSize = normalizeSliceSizeToLot(order, storeFallbackSliceSize)
    if (normalizedStoreFallbackSliceSize >= MIN_AUTO_SLICE_FREEZE_QTY) {
      freezeQtyCache.set(cacheKey, normalizedStoreFallbackSliceSize)
      return normalizedStoreFallbackSliceSize
    }
  }

  return null
}

function isResponseSuccess(status: ApiResponse<unknown>['status'] | undefined): boolean {
  return status === 'success' || status === 'info'
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

function extractOrderIdFromResponse(response: PlaceOrderResponse): string {
  const rootOrderId = (response as { orderid?: unknown }).orderid
  if (typeof rootOrderId === 'string' && rootOrderId.trim().length > 0) {
    return rootOrderId.trim()
  }

  const rootOrderIds = (response as { orderids?: unknown }).orderids
  if (Array.isArray(rootOrderIds)) {
    const first = rootOrderIds.find((value) => typeof value === 'string' && value.trim().length > 0)
    if (typeof first === 'string') return first.trim()
  }

  const rootSplitLegs = (response as { split_legs?: unknown }).split_legs
  if (Array.isArray(rootSplitLegs)) {
    const first = rootSplitLegs.find((value) => {
      if (!value || typeof value !== 'object') return false
      const orderId = (value as { orderId?: unknown }).orderId
      return typeof orderId === 'string' && orderId.trim().length > 0
    }) as { orderId?: string } | undefined
    if (first?.orderId) return first.orderId.trim()
  }

  const nestedOrderId = (response.data as { orderid?: unknown } | undefined)?.orderid
  if (typeof nestedOrderId === 'string' && nestedOrderId.trim().length > 0) {
    return nestedOrderId.trim()
  }

  return ''
}

function recordScalpingOrderAck(order: PlaceOrderRequest, response: PlaceOrderResponse): void {
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
  placeOrder: async (order: PlaceOrderRequest): Promise<PlaceOrderResponse> => {
    let result: PlaceOrderResponse | null = null

    if (shouldAttemptAutoSlice(order)) {
      const freezeQty = await getFreezeQtyForOrder(order)
      if (
        freezeQty != null &&
        freezeQty >= MIN_AUTO_SLICE_FREEZE_QTY &&
        Number(order.quantity) > freezeQty
      ) {
        const splitPayload = {
          ...order,
          splitsize: freezeQty,
        }
        try {
          console.info('[Scalping] Auto-slice splitorder route', {
            symbol: order.symbol,
            exchange: order.exchange,
            quantity: Number(order.quantity),
            splitSize: freezeQty,
          })
          if (isMultiBrokerUnifiedMode()) {
            const splitResponse = await proxyV1ByRole<unknown>(
              'execution',
              'splitorder',
              splitPayload
            )
            result = normalizeSplitOrderResponse(splitResponse, freezeQty, Number(order.quantity))
          } else {
            const splitResponse = await apiClient.post<unknown>('/splitorder', splitPayload)
            result = normalizeSplitOrderResponse(
              splitResponse.data,
              freezeQty,
              Number(order.quantity)
            )
          }
        } catch {
          console.warn('[Scalping] Auto-slice splitorder failed; falling back to placeorder.', {
            symbol: order.symbol,
            exchange: order.exchange,
            quantity: Number(order.quantity),
            splitSize: freezeQty,
          })
          result = null
        }
      }
    }

    if (result == null) {
      if (isMultiBrokerUnifiedMode()) {
        result = await proxyV1ByRole<PlaceOrderResponse>('execution', 'placeorder', order)
      } else {
        const response = await apiClient.post<PlaceOrderResponse>('/placeorder', order)
        result = response.data
      }
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
    product: string,
    options?: {
      knownQuantity?: number | null
      knownAction?: 'BUY' | 'SELL' | string | null
    }
  ): Promise<ApiResponse<void>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }

      const knownQty = parsePositiveInteger(options?.knownQuantity)
      const knownAction = normalizeText(options?.knownAction)
      if (knownQty != null && (knownAction === 'BUY' || knownAction === 'SELL')) {
        const closeAction = knownAction === 'BUY' ? 'SELL' : 'BUY'
        const directClose = await tradingApi.placeOrder({
          apikey: apiKey,
          strategy: 'Scalping',
          exchange,
          symbol,
          action: closeAction,
          quantity: knownQty,
          pricetype: 'MARKET',
          product: normalizeProduct(product),
          price: 0,
          trigger_price: 0,
          disclosed_quantity: 0,
        })
        if (isResponseSuccess(directClose.status)) {
          return { status: 'success', message: 'Position closed' }
        }
      }

      const positionsResponse = await tradingApi.getPositions(apiKey)
      if (!isResponseSuccess(positionsResponse.status)) {
        return {
          status: 'error',
          message: positionsResponse.message ?? 'Failed to fetch positions before close',
        }
      }

      const list = Array.isArray(positionsResponse.data) ? positionsResponse.data : []
      const openPositions = list.filter((p) => parseSignedQuantity(p.quantity) !== 0)
      const wantedSymbol = normalizeText(symbol)
      const wantedExchange = normalizeText(exchange)
      const wantedProduct = normalizeProduct(product)

      const symbolMatches = openPositions.filter((p) => normalizeText(p.symbol) === wantedSymbol)
      const exchangeMatches = symbolMatches.filter((p) => normalizeText(p.exchange) === wantedExchange)
      const exactMatches = exchangeMatches.filter(
        (p) => normalizeProduct((p as { product?: string }).product) === wantedProduct
      )

      const targets =
        exactMatches.length > 0
          ? exactMatches
          : exchangeMatches.length > 0
            ? exchangeMatches
            : symbolMatches

      if (!targets.length) {
        return { status: 'success', message: 'No open position to close' }
      }

      const closeResults = await Promise.allSettled(
        targets.map((target) => {
          const signedQty = parseSignedQuantity(target.quantity)
          const closeQty = Math.abs(signedQty)
          const closeAction = signedQty > 0 ? 'SELL' : 'BUY'
          return tradingApi.placeOrder({
            apikey: apiKey,
            strategy: 'Scalping',
            exchange: target.exchange || exchange,
            symbol: target.symbol || symbol,
            action: closeAction,
            quantity: closeQty,
            pricetype: 'MARKET',
            product: normalizeProduct((target as { product?: string }).product ?? product),
            price: 0,
            trigger_price: 0,
            disclosed_quantity: 0,
          })
        })
      )

      let failed = 0
      for (const result of closeResults) {
        if (result.status !== 'fulfilled' || !isResponseSuccess(result.value.status)) {
          failed += 1
        }
      }

      if (failed > 0) {
        return {
          status: 'error',
          message: `Failed to close ${failed} position leg(s)`,
        }
      }
      return { status: 'success', message: 'Position closed' }
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
  closeAllPositions: async (
    options?: { verify?: boolean; verifyDelayMs?: number }
  ): Promise<ApiResponse<void>> => {
    if (isMultiBrokerUnifiedMode()) {
      const apiKey = await resolveRuntimeApiKey()
      if (!apiKey) {
        return { status: 'error', message: 'Missing API key' }
      }
      const shouldVerify = options?.verify !== false
      const verifyDelayMs =
        typeof options?.verifyDelayMs === 'number' && Number.isFinite(options.verifyDelayMs)
          ? Math.max(0, Math.floor(options.verifyDelayMs))
          : 120

      const closeRemainingByMarket = async (): Promise<ApiResponse<void>> => {
        const positionsResponse = await tradingApi.getPositions(apiKey)
        if (!isResponseSuccess(positionsResponse.status)) {
          return {
            status: 'error',
            message: positionsResponse.message ?? 'Failed to fetch positions before close-all',
          }
        }

        const list = Array.isArray(positionsResponse.data) ? positionsResponse.data : []
        const openPositions = list.filter((p) => parseSignedQuantity(p.quantity) !== 0)
        if (!openPositions.length) {
          return { status: 'success', message: 'No open positions to close' }
        }

        const closeResults = await Promise.allSettled(
          openPositions.map((position) => {
            const signedQty = parseSignedQuantity(position.quantity)
            const closeQty = Math.abs(signedQty)
            const action = signedQty > 0 ? 'SELL' : 'BUY'
            return tradingApi.placeOrder({
              apikey: apiKey,
              strategy: 'Scalping',
              exchange: position.exchange,
              symbol: position.symbol,
              action,
              quantity: closeQty,
              pricetype: 'MARKET',
              product: normalizeProduct((position as { product?: string }).product),
              price: 0,
              trigger_price: 0,
              disclosed_quantity: 0,
            })
          })
        )

        let failed = 0
        for (const result of closeResults) {
          if (result.status !== 'fulfilled' || !isResponseSuccess(result.value.status)) {
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

      let brokerCloseMessage = ''
      try {
        const closeResponse = await proxyV1ByRole<ApiResponse<void>>('execution', 'closeposition', {
          apikey: apiKey,
          strategy: 'Scalping',
        })

        if (isResponseSuccess(closeResponse.status)) {
          if (!shouldVerify) {
            return {
              status: 'success',
              message: closeResponse.message ?? 'Close-all request submitted',
            }
          }

          await new Promise((resolve) => setTimeout(resolve, verifyDelayMs))
          const verifyResponse = await closeRemainingByMarket()
          if (isResponseSuccess(verifyResponse.status)) {
            return {
              status: 'success',
              message: closeResponse.message ?? verifyResponse.message ?? 'All positions closed',
            }
          }
          return verifyResponse
        }

        brokerCloseMessage = String(closeResponse.message ?? '').trim()
      } catch (error) {
        if (error instanceof Error) {
          brokerCloseMessage = error.message
        }
      }

      const fallbackResponse = await closeRemainingByMarket()
      if (isResponseSuccess(fallbackResponse.status)) {
        return fallbackResponse
      }

      if (brokerCloseMessage && fallbackResponse.message) {
        return {
          status: 'error',
          message: `${brokerCloseMessage}. ${fallbackResponse.message}`,
        }
      }

      return fallbackResponse
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
