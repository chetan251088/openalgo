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
  if (state.dataFeed === 'auto') return ['zerodha', 'dhan']
  return [resolveFeedBroker(state.dataFeed)]
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
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'POST'
): Promise<T> {
  const brokers = getRoleBrokerCandidates(role)
  let lastError: unknown = null

  for (const broker of brokers) {
    try {
      return await proxyV1<T>({
        broker,
        path,
        method,
        payload,
      })
    } catch (error) {
      lastError = error
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
