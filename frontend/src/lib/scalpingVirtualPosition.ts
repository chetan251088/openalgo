import { tradingApi } from '@/api/trading'
import { MarketDataManager } from '@/lib/MarketDataManager'
import type { ActiveSide, OrderAction, VirtualTPSL } from '@/types/scalping'

const TICK_SIZE = 0.05

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
  createdAt?: number
  managedBy?: 'manual' | 'auto' | 'trigger' | 'hotkey' | 'ghost'
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
  createdAt = Date.now(),
  managedBy = 'manual',
  autoEntryScore,
  autoEntryReason,
}: BuildVirtualPositionParams): VirtualTPSL {
  const entry = roundToTick(entryPrice)
  const normalizedTpPoints = Number(Math.max(0, tpPoints || 0).toFixed(2))
  const normalizedSlPoints = Number(Math.max(0, slPoints || 0).toFixed(2))
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
    createdAt,
    managedBy,
    autoEntryScore,
    autoEntryReason,
  }
}
