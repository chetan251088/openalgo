import { webClient } from './client'
import type { AutoTradeAnalyticsParams, AutoTradeAnalyticsResponse } from '@/types/ai-scalper'

export async function fetchManualTradeAnalytics(params: AutoTradeAnalyticsParams) {
  const response = await webClient.get<AutoTradeAnalyticsResponse>(
    '/manual_trades/analytics',
    { params },
  )
  return response.data
}

export async function fetchManualTradeLogs(params: {
  limit?: number
  mode?: string
  source?: string
  symbol?: string
  side?: string
  underlying?: string
  since?: string
  until?: string
}) {
  const response = await webClient.get<{ status: string; logs: unknown[] }>(
    '/manual_trades/logs',
    { params },
  )
  return response.data
}
