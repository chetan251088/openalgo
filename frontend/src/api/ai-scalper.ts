import { webClient } from './client'
import type {
  AutoTradeAnalyticsParams,
  AutoTradeAnalyticsResponse,
  ModelTuningRecommendationsResponse,
  ModelTuningRunResponse,
  ModelTuningStatus,
} from '@/types/ai-scalper'

export async function fetchAutoTradeAnalytics(params: AutoTradeAnalyticsParams) {
  const response = await webClient.get<AutoTradeAnalyticsResponse>('/ai_scalper/analytics', {
    params,
  })
  return response.data
}

export async function fetchModelTuningStatus() {
  const response = await webClient.get<ModelTuningStatus>('/ai_scalper/model/status')
  return response.data
}

export async function fetchModelTuningRecommendations(limit = 20) {
  const response = await webClient.get<ModelTuningRecommendationsResponse>(
    '/ai_scalper/model/recommendations',
    {
      params: { limit },
    },
  )
  return response.data
}

export async function runModelTuning(payload: Record<string, unknown>) {
  const response = await webClient.post<ModelTuningRunResponse>('/ai_scalper/model/run', payload)
  return response.data
}

export async function applyModelTuningRecommendation(run_id: string) {
  const response = await webClient.post<ModelTuningRunResponse>('/ai_scalper/model/apply', {
    run_id,
  })
  return response.data
}
