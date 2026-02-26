import { webClient } from './client'
import { resolveFeedBroker, useMultiBrokerStore, type UnifiedBroker } from '@/stores/multiBrokerStore'

type BrokerRole = 'feed' | 'execution'

interface ProxyV1Request {
  broker: UnifiedBroker
  path: string
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  payload?: unknown
  params?: Record<string, unknown>
  timeout_ms?: number
}

interface ProxyV1ByRoleOptions {
  timeoutMs?: number
}

const FEED_BROKER_FAILURE_COOLDOWN_MS = 12000
const feedBrokerFailureUntil = new Map<UnifiedBroker, number>()

interface MultiBrokerWsTarget {
  broker: UnifiedBroker
  websocket_url: string
  api_key?: string
}

interface MultiBrokerWsConfigResponse {
  status: 'success' | 'error'
  feed?: string
  api_key?: string
  targets?: MultiBrokerWsTarget[]
  message?: string
}

function getRoleBrokerCandidates(role: BrokerRole): UnifiedBroker[] {
  const state = useMultiBrokerStore.getState()
  if (role === 'execution') return [state.executionBroker]
  const preferred =
    state.dataFeed === 'auto' ? (['zerodha', 'dhan'] as UnifiedBroker[]) : [resolveFeedBroker(state.dataFeed)]

  if (preferred.length <= 1) return preferred

  const now = Date.now()
  const available = preferred.filter((broker) => (feedBrokerFailureUntil.get(broker) ?? 0) <= now)
  return available.length > 0 ? available : preferred
}

function shouldCooldownFeedBroker(error: unknown): boolean {
  if (!error || typeof error !== 'object') return true

  const status = (error as { response?: { status?: unknown } }).response?.status
  if (typeof status === 'number') {
    return status >= 500 || status === 408 || status === 429
  }

  const code = String((error as { code?: unknown }).code ?? '').trim().toUpperCase()
  if (code === 'ECONNABORTED' || code === 'ETIMEDOUT' || code === 'ERR_NETWORK') {
    return true
  }

  return true
}

function markFeedBrokerFailure(broker: UnifiedBroker): void {
  feedBrokerFailureUntil.set(broker, Date.now() + FEED_BROKER_FAILURE_COOLDOWN_MS)
}

function clearFeedBrokerFailure(broker: UnifiedBroker): void {
  feedBrokerFailureUntil.delete(broker)
}

function normalizeTimeoutMs(timeoutMs: number | undefined): number | undefined {
  if (typeof timeoutMs !== 'number' || !Number.isFinite(timeoutMs)) return undefined
  const normalized = Math.floor(timeoutMs)
  if (normalized <= 0) return undefined
  return Math.max(200, Math.min(normalized, 30000))
}

function getErrorSummary(error: unknown): { status: number | null; message: string } {
  if (error && typeof error === 'object') {
    const response = (error as { response?: { status?: unknown; data?: unknown } }).response
    const status = typeof response?.status === 'number' ? response.status : null

    const data = response?.data
    if (data && typeof data === 'object') {
      const message = String((data as { message?: unknown }).message ?? '').trim()
      if (message) return { status, message }
      const raw = JSON.stringify(data)
      if (raw) return { status, message: raw }
    } else if (typeof data === 'string' && data.trim()) {
      return { status, message: data.trim() }
    }

    const message = String((error as { message?: unknown }).message ?? '').trim()
    if (message) return { status, message }
    return { status, message: 'Request failed' }
  }

  if (typeof error === 'string' && error.trim()) {
    return { status: null, message: error.trim() }
  }

  return { status: null, message: 'Request failed' }
}

export function isMultiBrokerUnifiedMode(): boolean {
  return useMultiBrokerStore.getState().unifiedMode
}

export async function proxyV1<T>(request: ProxyV1Request): Promise<T> {
  const response = await webClient.post<T>('/api/multibroker/v1', request)
  return response.data
}

export async function proxyV1ByRole<T>(
  role: BrokerRole,
  path: string,
  payload: unknown,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'POST',
  options?: ProxyV1ByRoleOptions
): Promise<T> {
  const brokers = getRoleBrokerCandidates(role)
  let lastError: unknown = null
  const timeoutMs = normalizeTimeoutMs(options?.timeoutMs)

  for (const broker of brokers) {
    try {
      const response = await proxyV1<T>({
        broker,
        path,
        method,
        payload,
        timeout_ms: timeoutMs,
      })
      if (role === 'feed') {
        clearFeedBrokerFailure(broker)
      }
      return response
    } catch (error) {
      lastError = error
      const summary = getErrorSummary(error)
      console.warn('[MultiBroker] Proxy request failed', {
        role,
        broker,
        path,
        status: summary.status,
        message: summary.message,
      })
      if (role === 'feed' && shouldCooldownFeedBroker(error)) {
        markFeedBrokerFailure(broker)
      }
    }
  }

  throw lastError ?? new Error(`Broker proxy failed for ${path}`)
}

export async function getUnifiedWsConfig(feed: 'auto' | 'dhan' | 'zerodha'): Promise<MultiBrokerWsConfigResponse> {
  const response = await webClient.post<MultiBrokerWsConfigResponse>('/api/multibroker/ws-config', {
    feed,
  })
  return response.data
}
