import { webClient } from './client'

export interface MarketPulseRule {
  rule: string
  detail: string
  impact: 'positive' | 'negative' | 'neutral'
}

export interface CategoryScore {
  score: number
  weight: number
  direction: string
  rules: MarketPulseRule[]
}

export interface TickerItem {
  ltp?: number
  change_pct?: number
  open?: number
  high?: number
  low?: number
  prev_close?: number
}

export interface SectorData {
  key: string
  name: string
  ltp: number | null
  return_5d: number
  return_1d: number | null
  return_20d: number | null
}

export interface EquityIdea {
  symbol: string
  sector: string
  signal: 'BUY' | 'SELL' | 'HOLD' | 'AVOID'
  ltp: number
  entry: number | null
  stop_loss: number | null
  target: number | null
  conviction: 'HIGH' | 'MED' | 'LOW'
  reason: string
  rs_vs_nifty: number
}

export interface FnoIdea {
  instrument: string
  strategy: string
  strikes: string
  bias: string
  rationale: string
}

export interface AlertItem {
  type: 'major' | 'minor'
  name: string
  date: string
  hours_away: number
}

export interface MarketPulseData {
  decision: 'YES' | 'CAUTION' | 'NO'
  market_quality_score: number
  execution_window_score: number
  mode: 'swing' | 'day'
  regime: 'uptrend' | 'downtrend' | 'chop'
  scores: {
    volatility: CategoryScore
    momentum: CategoryScore
    trend: CategoryScore
    breadth: CategoryScore
    macro: CategoryScore
  }
  ticker: Record<string, TickerItem>
  sectors: SectorData[]
  alerts: AlertItem[]
  equity_ideas: EquityIdea[]
  fno_ideas: FnoIdea[]
  analysis: string | null
  execution_details: Record<string, any>
  errors: string[]
  updated_at: string
  cache_ttl: number
}

export async function fetchMarketPulse(mode: 'swing' | 'day' = 'swing'): Promise<MarketPulseData> {
  const response = await webClient.get('/market-pulse/api/data', {
    params: { mode },
  })
  if (response.data?.status === 'success') {
    return response.data.data
  }
  throw new Error(response.data?.message || 'Failed to fetch market pulse')
}
