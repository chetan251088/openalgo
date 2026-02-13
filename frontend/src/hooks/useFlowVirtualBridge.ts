import { useCallback, useEffect, useRef } from 'react'
import { tradingApi, type ScalpingBridgeEntry } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { buildVirtualPosition, resolveFilledOrderPrice } from '@/lib/scalpingVirtualPosition'

const BRIDGE_POLL_MS = 1000
const BRIDGE_BATCH_LIMIT = 100

function toPositiveNumber(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function normalizeAction(value: unknown): 'BUY' | 'SELL' | null {
  const normalized = String(value ?? '').trim().toUpperCase()
  if (normalized === 'BUY' || normalized === 'SELL') {
    return normalized
  }
  return null
}

function normalizeSide(value: unknown, symbol: string): 'CE' | 'PE' {
  const normalized = String(value ?? '').trim().toUpperCase()
  if (normalized === 'CE' || normalized === 'PE') {
    return normalized
  }
  return symbol.toUpperCase().endsWith('PE') ? 'PE' : 'CE'
}

export function useFlowVirtualBridge() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const defaultTpPoints = useScalpingStore((s) => s.tpPoints)
  const defaultSlPoints = useScalpingStore((s) => s.slPoints)
  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)

  const pollInFlightRef = useRef(false)

  const resolveApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKey) return apiKey
    try {
      const response = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await response.json()
      if (data.status === 'success' && data.api_key) {
        setApiKey(data.api_key)
        return data.api_key as string
      }
    } catch {
      // Ignore; next poll will retry.
    }
    return null
  }, [apiKey, setApiKey])

  const processEntry = useCallback(
    async (entry: ScalpingBridgeEntry, key: string): Promise<boolean> => {
      const eventId = Number(entry.id)
      if (!Number.isInteger(eventId) || eventId <= 0) {
        return false
      }

      const symbol = String(entry.symbol ?? '').trim().toUpperCase()
      const exchange = String(entry.exchange ?? 'NFO').trim().toUpperCase()
      const action = normalizeAction(entry.action)
      const quantity = Math.trunc(toPositiveNumber(entry.quantity) ?? 0)

      if (!symbol || !action || quantity <= 0) {
        return false
      }

      const virtualId = `flow-${eventId}`
      const existing = useVirtualOrderStore.getState().virtualTPSL[virtualId]
      if (existing) {
        return true
      }

      const side = normalizeSide(entry.side, symbol)
      const tpPoints = toPositiveNumber(entry.tp_points) ?? defaultTpPoints
      const slPoints = toPositiveNumber(entry.sl_points) ?? defaultSlPoints
      const explicitEntryPrice = toPositiveNumber(entry.entry_price)
      const orderId = String(entry.order_id ?? '').trim() || null

      const entryPrice = await resolveFilledOrderPrice({
        symbol,
        exchange,
        orderId,
        preferredPrice: explicitEntryPrice,
        fallbackPrice: explicitEntryPrice,
        apiKey: key,
      })

      if (!(entryPrice > 0)) {
        return false
      }

      const createdAt = Number(entry.created_at)
      setVirtualTPSL(
        buildVirtualPosition({
          id: virtualId,
          symbol,
          exchange,
          side,
          action,
          entryPrice,
          quantity,
          tpPoints,
          slPoints,
          managedBy: 'flow',
          createdAt: Number.isFinite(createdAt) && createdAt > 0 ? createdAt : Date.now(),
        })
      )
      return true
    },
    [defaultTpPoints, defaultSlPoints, setVirtualTPSL]
  )

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null

    async function runPoll() {
      if (!active || pollInFlightRef.current) {
        scheduleNext()
        return
      }
      pollInFlightRef.current = true

      try {
        const key = await resolveApiKey()
        if (!key) return

        const pendingResponse = await tradingApi.getScalpingBridgePending(key, BRIDGE_BATCH_LIMIT)
        const entries = Array.isArray(pendingResponse.data?.entries)
          ? pendingResponse.data.entries
          : []
        if (!entries.length) return

        const ackIds: number[] = []
        for (const entry of entries) {
          try {
            const processed = await processEntry(entry, key)
            if (processed) {
              ackIds.push(Number(entry.id))
            }
          } catch {
            // Keep unacked so it can retry on next poll.
          }
        }

        if (ackIds.length) {
          await tradingApi.ackScalpingBridgeEntries(key, ackIds)
        }
      } catch {
        // Ignore transient bridge/network errors.
      } finally {
        pollInFlightRef.current = false
        scheduleNext()
      }
    }

    function scheduleNext() {
      if (!active) return
      timer = setTimeout(runPoll, BRIDGE_POLL_MS)
    }

    void runPoll()

    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [processEntry, resolveApiKey])
}
