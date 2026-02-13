import { webClient } from './client'

export interface TomicAgentStatus {
  running: boolean
  paused: boolean
  last_heartbeat_ago_s: number
  restarts: number
}

export interface TomicRuntimeStatus {
  running: boolean
  killed: boolean
  agents: Record<string, TomicAgentStatus>
  command_queue_pending: number
  command_dead_letters: number
  signal_loop?: {
    enabled: boolean
    running: boolean
    interval_s: number
    enqueue_cooldown_s: number
    cycles: number
    last_cycle_at: string
    last_cycle_age_s: number
    last_enqueued: number
    last_dedupe_skips: number
    last_error: string
  }
  ws_data?: Record<string, unknown>
  market_bridge?: Record<string, unknown>
  signal_quality_cached_at?: string
  signal_quality_cached_age_s?: number
}

export interface TomicStatusResponse {
  status: string
  message?: string
  data?: TomicRuntimeStatus
}

export interface TomicPosition {
  instrument: string
  strategy_id: string
  direction: string
  quantity: number
  avg_price: number
  pnl: number
}

export interface TomicPositionsResponse {
  status: string
  version?: number
  positions: TomicPosition[]
  message?: string
}

export interface TomicJournalResponse {
  status: string
  trades: Array<Record<string, unknown>>
  message?: string
}

export interface TomicAnalyticsResponse {
  status: string
  metrics?: Record<string, unknown>
  strategy_breakdown?: Array<Record<string, unknown>> | Record<string, unknown>
  message?: string
}

export interface TomicMetricsResponse {
  status: string
  data?: {
    circuit_breakers?: Record<string, unknown>
    freshness?: Record<string, unknown>
    ws_data?: Record<string, unknown>
    market_bridge?: Record<string, unknown>
  }
  message?: string
}

export interface TomicSignalQualityResponse {
  status: string
  data?: {
    generated_at: string
    timestamp_epoch: number
    runtime_started: boolean
    source?: string
    cached?: boolean
    cached_age_s?: number
    message?: string
    regime?: Record<string, unknown>
    feed?: {
      ws?: Record<string, unknown>
      bridge?: Record<string, unknown>
    }
    coverage?: Record<string, unknown>
    router?: {
      route_count?: number
      position_count?: number
      max_positions?: number
      sector_limit?: number
      sector_heat?: Record<string, number>
      blocking_reasons?: Record<string, number>
      decisions?: Array<Record<string, unknown>>
    }
    risk?: {
      pending_signals?: number
      counters?: Record<string, number>
      recent_evaluations?: Array<Record<string, unknown>>
    }
    agent_inputs?: {
      sniper_readiness?: Array<Record<string, unknown>>
      volatility_readiness?: Array<Record<string, unknown>>
      volatility_snapshots?: Array<Record<string, unknown>>
    }
    diagnostics?: {
      no_action_reasons?: string[]
    }
    signals?: {
      sniper_count: number
      volatility_count: number
      routed_count: number
      sniper_avg_score?: number
      volatility_avg_strength?: number
      routed_avg_priority?: number
      routed_accept_rate_pct?: number
      decision_breakdown?: Record<string, number>
      enqueued_count?: number
      dedupe_skipped_count?: number
      enqueued_keys?: string[]
      dedupe_keys?: string[]
      top_sniper?: Array<Record<string, unknown>>
      top_volatility?: Array<Record<string, unknown>>
      top_routed?: Array<Record<string, unknown>>
      router_decisions?: Array<Record<string, unknown>>
    }
  }
  message?: string
}

export interface TomicAuditEntry {
  id: number
  timestamp: string
  user_id: string
  action: string
  details?: string
  ip_address?: string
}

export interface TomicAuditResponse {
  status: string
  entries: TomicAuditEntry[]
  message?: string
}

export interface TomicActionResponse {
  status: string
  message?: string
}

export const tomicApi = {
  getStatus: async (): Promise<TomicStatusResponse> => {
    const response = await webClient.get<TomicStatusResponse>('/tomic/status')
    return response.data
  },

  start: async (): Promise<TomicActionResponse> => {
    const response = await webClient.post<TomicActionResponse>('/tomic/start')
    return response.data
  },

  stop: async (): Promise<TomicActionResponse> => {
    const response = await webClient.post<TomicActionResponse>('/tomic/stop')
    return response.data
  },

  pause: async (reason: string): Promise<TomicActionResponse> => {
    const response = await webClient.post<TomicActionResponse>('/tomic/pause', { reason })
    return response.data
  },

  resume: async (): Promise<TomicActionResponse> => {
    const response = await webClient.post<TomicActionResponse>('/tomic/resume')
    return response.data
  },

  getPositions: async (): Promise<TomicPositionsResponse> => {
    const response = await webClient.get<TomicPositionsResponse>('/tomic/positions')
    return response.data
  },

  getJournal: async (limit = 50): Promise<TomicJournalResponse> => {
    const response = await webClient.get<TomicJournalResponse>('/tomic/journal', {
      params: { limit },
    })
    return response.data
  },

  getAnalytics: async (): Promise<TomicAnalyticsResponse> => {
    const response = await webClient.get<TomicAnalyticsResponse>('/tomic/analytics')
    return response.data
  },

  getMetrics: async (): Promise<TomicMetricsResponse> => {
    const response = await webClient.get<TomicMetricsResponse>('/tomic/metrics')
    return response.data
  },

  getSignalQuality: async (runScan = true): Promise<TomicSignalQualityResponse> => {
    const response = await webClient.get<TomicSignalQualityResponse>('/tomic/signals/quality', {
      params: { run_scan: runScan },
    })
    return response.data
  },

  getAudit: async (limit = 100): Promise<TomicAuditResponse> => {
    const response = await webClient.get<TomicAuditResponse>('/tomic/audit', {
      params: { limit },
    })
    return response.data
  },
}
