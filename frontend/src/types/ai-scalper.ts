export interface AutoTradeSummary {
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
  profit_factor: number | null
  avg_hold_s: number | null
  max_win: number
  max_loss: number
  max_drawdown: number
}

export interface AutoTradeEquityPoint {
  time: number | null
  value: number
  drawdown: number
}

export interface AutoTradeDistribution {
  bucket: number
  count: number
}

export interface AutoTradeTimeBucket {
  bucket: string
  count: number
  pnl: number
  win_rate: number
  avg_pnl: number
}

export interface AutoTradeReasonBreakdown {
  reason: string
  count: number
  pnl: number
}

export interface AutoTradeSideBreakdown {
  side: string
  count: number
  pnl: number
  win_rate: number
}

export interface AutoTradeAnalyticsResponse {
  status: string
  summary: AutoTradeSummary
  equity: AutoTradeEquityPoint[]
  distribution: AutoTradeDistribution[]
  time_buckets: AutoTradeTimeBucket[]
  reason_breakdown: AutoTradeReasonBreakdown[]
  side_breakdown: AutoTradeSideBreakdown[]
  limit: number
}

export interface AutoTradeAnalyticsParams {
  limit?: number
  mode?: string
  source?: string
  symbol?: string
  side?: string
  underlying?: string
  since?: string
  until?: string
  bucket?: number
  interval_min?: number
}

export interface ModelTuningRun {
  run_id: string
  created_ts: number
  created_iso: string
  status: string
  provider: string
  model: string | null
  underlying?: string | null
  objective: string | null
  score: number | null
  recommendations: Record<string, number | boolean | string | null>
  applied_changes: Record<string, number | boolean | string | null>
  notes: string
  applied: boolean
  applied_iso?: string | null
  applied_by?: string | null
  requested_by?: string | null
  error?: string | null
}

export interface ModelTuningSchedule {
  enabled: boolean
  type?: string
  interval_s?: number
  time_of_day?: string
  next_run_time?: string
}

export interface ModelTuningStatus {
  status: string
  enabled: boolean
  provider: string
  model: string | null
  base_url?: string | null
  auto_apply_paper: boolean
  min_trades: number
  apply_clamps: boolean
  notify_email: boolean
  notify_telegram: boolean
  underlying: string
  paper_mode: boolean | null
  agent_running: boolean
  last_run: ModelTuningRun | null
  schedule: ModelTuningSchedule
  current: Record<string, number | boolean | string | null>
}

export interface ModelTuningRunResponse {
  status: string
  message: string
  run_id?: string
}

export interface ModelTuningRecommendationsResponse {
  status: string
  runs: ModelTuningRun[]
}
