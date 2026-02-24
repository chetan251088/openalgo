import { tradingApi } from '@/api/trading'
import { MarketDataManager } from '@/lib/MarketDataManager'
import type { ActiveSide, OrderAction, VirtualTPSL } from '@/types/scalping'

const TICK_SIZE = 0.05
const DEFAULT_TRAIL_DISTANCE_POINTS = 2
const SUPER_FAST_SCALPING_MODE = true

function roundToTick(price: number): number {
  return Math.round(price / TICK_SIZE) * TICK_SIZE
}

function normalizePrice(price: number | null | undefined): number | null {
  if (typeof price !== 'number' || !Number.isFinite(price) || price <= 0) return null
  return roundToTick(price)
}

interface ResolveEntryPriceParams {
  symbol: string
  exchange: string
  preferredPrice?: number | null
  fallbackPrice?: number | null
  apiKey?: string | null
}

/**
 * Resolve the best available entry price for virtual position tracking.
 *
 * Priority:
 * 1) Explicit preferred price (e.g. LIMIT price, local LTP)
 * 2) Shared market-data cache
 * 3) Explicit fallback price
 * 4) Fresh quote API call (if apiKey available)
 */
export async function resolveEntryPrice({
  symbol,
  exchange,
  preferredPrice = null,
  fallbackPrice = null,
  apiKey = null,
}: ResolveEntryPriceParams): Promise<number> {
  const preferred = normalizePrice(preferredPrice)
  if (preferred != null) return preferred

  const mdm = MarketDataManager.getInstance()
  const cachedLtp = normalizePrice(mdm.getCachedData(symbol, exchange)?.data?.ltp)
  if (cachedLtp != null) return cachedLtp

  const fallback = normalizePrice(fallbackPrice)
  if (fallback != null) return fallback

  if (apiKey) {
    try {
      const quote = await tradingApi.getQuotes(apiKey, symbol, exchange)
      const quoteLtp = normalizePrice(quote.data?.ltp)
      if (quoteLtp != null) return quoteLtp
    } catch {
      // Ignore quote fetch failure and fallback to 0 below.
    }
  }

  return 0
}

function parseNumeric(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, '').trim())
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function getOrderIdFromRecord(record: Record<string, unknown>): string | null {
  return normalizeOrderId(record.orderid ?? record.order_id)
}

function getOrderStatus(record: Record<string, unknown>): string {
  const raw = record.order_status ?? record.status ?? ''
  return String(raw).trim().toUpperCase()
}

function extractFilledPrice(record: Record<string, unknown>): number | null {
  const candidates = [
    record.average_price,
    record.averageprice,
    record.fill_price,
    record.fillprice,
    record.traded_price,
    record.tradedprice,
    record.price,
  ]
  for (const value of candidates) {
    const parsed = normalizePrice(parseNumeric(value))
    if (parsed != null) return parsed
  }
  return null
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

function resolveFastPriceWithoutBroker({
  symbol,
  exchange,
  preferredPrice,
  fallbackPrice,
}: {
  symbol: string
  exchange: string
  preferredPrice: number | null
  fallbackPrice: number | null
}): number {
  const preferred = normalizePrice(preferredPrice)
  if (preferred != null) return preferred

  const mdm = MarketDataManager.getInstance()
  const cachedLtp = normalizePrice(mdm.getCachedData(symbol, exchange)?.data?.ltp)
  if (cachedLtp != null) return cachedLtp

  const fallback = normalizePrice(fallbackPrice)
  if (fallback != null) return fallback

  return 0
}

interface ResolveFilledOrderPriceParams {
  symbol: string
  exchange: string
  orderId?: string | null
  preferredPrice?: number | null
  fallbackPrice?: number | null
  apiKey?: string | null
  maxAttempts?: number
  retryDelayMs?: number
}

/**
 * Resolve fill price with orderbook-first logic.
 *
 * Priority:
 * 1) Filled order price from orderbook (when orderId + apiKey are available)
 * 2) Generic entry resolver fallback (preferred/cache/fallback/quote)
 */
export async function resolveFilledOrderPrice({
  symbol,
  exchange,
  orderId = null,
  preferredPrice = null,
  fallbackPrice = null,
  apiKey = null,
  maxAttempts = 5,
  retryDelayMs = 250,
}: ResolveFilledOrderPriceParams): Promise<number> {
  if (SUPER_FAST_SCALPING_MODE) {
    return resolveFastPriceWithoutBroker({
      symbol,
      exchange,
      preferredPrice,
      fallbackPrice,
    })
  }

  const normalizedOrderId = normalizeOrderId(orderId)
  if (apiKey && normalizedOrderId) {
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      try {
        const orderbook = await tradingApi.getOrders(apiKey)
        const orders = orderbook.data?.orders
        if (Array.isArray(orders)) {
          const matched = orders.find((raw) => {
            if (!raw || typeof raw !== 'object') return false
            const record = raw as unknown as Record<string, unknown>
            return getOrderIdFromRecord(record) === normalizedOrderId
          })

          if (matched && typeof matched === 'object') {
            const record = matched as unknown as Record<string, unknown>
            const status = getOrderStatus(record)
            const filledPrice = extractFilledPrice(record)
            const isFilled =
              status.includes('COMPLETE') ||
              status.includes('FILLED') ||
              status.includes('EXECUTED') ||
              status.includes('TRADED')

            if (filledPrice != null && (isFilled || attempt === maxAttempts - 1)) {
              return filledPrice
            }
          }
        }
      } catch {
        // Ignore transient orderbook failures and fall back below.
      }

      if (attempt < maxAttempts - 1) {
        await wait(retryDelayMs)
      }
    }
  }

  return resolveEntryPrice({
    symbol,
    exchange,
    preferredPrice,
    fallbackPrice,
    apiKey,
  })
}

interface BuildVirtualPositionParams {
  id?: string
  symbol: string
  exchange: string
  side: ActiveSide
  action: OrderAction
  entryPrice: number
  quantity: number
  tpPoints: number
  slPoints: number
  trailDistancePoints?: number
  createdAt?: number
  managedBy?: 'manual' | 'auto' | 'trigger' | 'hotkey' | 'ghost' | 'flow'
  autoEntryScore?: number
  autoEntryReason?: string
}

export function buildVirtualPosition({
  id = `tpsl-${Date.now()}`,
  symbol,
  exchange,
  side,
  action,
  entryPrice,
  quantity,
  tpPoints,
  slPoints,
  trailDistancePoints,
  createdAt = Date.now(),
  managedBy = 'manual',
  autoEntryScore,
  autoEntryReason,
}: BuildVirtualPositionParams): VirtualTPSL {
  const entry = roundToTick(entryPrice)
  const normalizedTpPoints = Number(Math.max(0, tpPoints || 0).toFixed(2))
  const normalizedSlPoints = Number(Math.max(0, slPoints || 0).toFixed(2))
  const normalizedTrailDistancePoints = Number(
    Math.max(0, trailDistancePoints ?? DEFAULT_TRAIL_DISTANCE_POINTS).toFixed(2)
  )
  const isBuy = action === 'BUY'

  return {
    id,
    symbol,
    exchange,
    side,
    action,
    entryPrice: entry,
    quantity,
    tpPrice:
      normalizedTpPoints > 0
        ? roundToTick(entry + (isBuy ? normalizedTpPoints : -normalizedTpPoints))
        : null,
    slPrice:
      normalizedSlPoints > 0
        ? roundToTick(entry + (isBuy ? -normalizedSlPoints : normalizedSlPoints))
        : null,
    tpPoints: normalizedTpPoints,
    slPoints: normalizedSlPoints,
    trailDistancePoints: normalizedTrailDistancePoints,
    trailStage: managedBy === 'auto' ? 'INITIAL' : undefined,
    createdAt,
    managedBy,
    autoEntryScore,
    autoEntryReason,
  }
}

function normalizeOrderId(value: unknown): string | null {
  if (value == null) return null
  const text = String(value).trim()
  return text.length > 0 ? text : null
}

export interface ParsedSplitLeg {
  orderId: string
  quantity: number
}

function parsePositiveNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) && value > 0 ? value : null
  }
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, '').trim())
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null
  }
  return null
}

function readOrderIdFromObject(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  return normalizeOrderId(record.orderid ?? record.order_id ?? record.orderId)
}

function extractSplitLegsFromResults(value: unknown): ParsedSplitLeg[] {
  if (!Array.isArray(value)) return []
  const legs: ParsedSplitLeg[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const status = String(record.status ?? '').trim().toUpperCase()
    if (status && status !== 'SUCCESS') continue

    const orderId = readOrderIdFromObject(record)
    if (!orderId) continue
    const quantity = parsePositiveNumber(record.quantity) ?? 0
    legs.push({ orderId, quantity })
  }
  return legs
}

/**
 * Extract all broker order ids from regular and split-order responses.
 */
export function extractOrderIds(response: unknown): string[] {
  if (!response || typeof response !== 'object') return []
  const root = response as Record<string, unknown>
  const ids: string[] = []
  const seen = new Set<string>()

  const pushId = (value: unknown) => {
    const normalized = normalizeOrderId(value)
    if (!normalized || seen.has(normalized)) return
    seen.add(normalized)
    ids.push(normalized)
  }

  pushId(root.orderid ?? root.order_id ?? root.orderId)
  pushId((root.data as Record<string, unknown> | undefined)?.orderid)

  const rootOrderIds = root.orderids
  if (Array.isArray(rootOrderIds)) {
    for (const value of rootOrderIds) pushId(value)
  }

  const dataOrderIds = (root.data as Record<string, unknown> | undefined)?.orderids
  if (Array.isArray(dataOrderIds)) {
    for (const value of dataOrderIds) pushId(value)
  }

  const splitLegs = extractSplitLegsFromResults(root.split_legs)
  for (const leg of splitLegs) pushId(leg.orderId)

  const splitResults = extractSplitLegsFromResults(root.results)
  for (const leg of splitResults) pushId(leg.orderId)

  return ids
}

/**
 * Extract split child legs (`orderId`, `quantity`) from order response.
 */
export function extractOrderLegs(response: unknown): ParsedSplitLeg[] {
  if (!response || typeof response !== 'object') return []
  const root = response as Record<string, unknown>

  const fromSplitLegs = extractSplitLegsFromResults(root.split_legs)
  if (fromSplitLegs.length > 0) return fromSplitLegs

  const fromResults = extractSplitLegsFromResults(root.results)
  if (fromResults.length > 0) return fromResults

  const primaryOrderId = readOrderIdFromObject(root)
  const quantity = parsePositiveNumber(root.quantity)
  if (primaryOrderId && quantity != null) {
    return [{ orderId: primaryOrderId, quantity }]
  }
  return []
}

/**
 * Broker responses can expose order ids in different shapes:
 * - { status, data: { orderid } }
 * - { status, orderid }
 * - snake_case variants with order_id
 */
export function extractOrderId(response: unknown): string | null {
  const ids = extractOrderIds(response)
  return ids.length > 0 ? ids[0] : null
}

/**
 * Merge a fresh entry into an existing virtual position for the same symbol.
 * This keeps line/PnL continuity when users stack entries on the same strike.
 */
export function mergeVirtualPosition(
  existing: VirtualTPSL | undefined,
  incoming: VirtualTPSL
): VirtualTPSL {
  if (!existing) return incoming
  if (existing.symbol !== incoming.symbol || existing.exchange !== incoming.exchange) return incoming

  const sameDirection = existing.side === incoming.side && existing.action === incoming.action
  if (!sameDirection) {
    // Replace direction but retain id so overlay/line handles stay stable.
    return {
      ...incoming,
      id: existing.id,
      trailDistancePoints: incoming.trailDistancePoints ?? existing.trailDistancePoints,
    }
  }

  const existingQty = Math.max(0, existing.quantity)
  const incomingQty = Math.max(0, incoming.quantity)
  const mergedQty = existingQty + incomingQty
  if (mergedQty <= 0) {
    return {
      ...incoming,
      id: existing.id,
    }
  }

  const weightedEntry = roundToTick(
    (existing.entryPrice * existingQty + incoming.entryPrice * incomingQty) / mergedQty
  )
  const tpPoints = existing.tpPrice == null ? 0 : existing.tpPoints
  const slPoints = existing.slPrice == null ? 0 : existing.slPoints
  const isBuy = incoming.action === 'BUY'

  return {
    ...incoming,
    id: existing.id,
    createdAt: incoming.createdAt,
    quantity: mergedQty,
    entryPrice: weightedEntry,
    tpPoints,
    slPoints,
    tpPrice:
      tpPoints > 0
        ? roundToTick(weightedEntry + (isBuy ? tpPoints : -tpPoints))
        : null,
    slPrice:
      slPoints > 0
        ? roundToTick(weightedEntry + (isBuy ? -slPoints : slPoints))
        : null,
    trailDistancePoints: incoming.trailDistancePoints ?? existing.trailDistancePoints,
    managedBy: incoming.managedBy ?? existing.managedBy,
    autoEntryScore: incoming.autoEntryScore ?? existing.autoEntryScore,
    autoEntryReason: incoming.autoEntryReason ?? existing.autoEntryReason,
  }
}
