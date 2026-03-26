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

export interface DirectionalBias {
  bias: 'LONG' | 'SHORT' | 'NEUTRAL'
  confidence: number
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
  risk_reward?: number | null
  conviction: 'HIGH' | 'MED' | 'LOW'
  reason: string
  rs_vs_nifty: number
  rs_label?: string
  volume_vs_10d_avg?: number | null
  delivery_pct?: number | null
  avg_delivery_pct_10d?: number | null
  delivery_vs_10d_avg?: number | null
  rvol?: number | null
  vwap_distance_pct?: number | null
  liquidity_note?: string | null
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
  time?: string
  hours_away: number
}

export interface InstitutionalFlows {
  source: string
  source_url: string
  freshness?: {
    is_stale: boolean
    lag_business_days: number
    latest_trading_date?: string | null
    expected_min_date?: string | null
  }
  latest: {
    date?: string
    updated_at?: string
    fii_net: number | null
    dii_net: number | null
    sentiment_score: number | null
    cash_bias: string
    derivatives_bias: string
    headline_bias: string
    fii_idx_fut_net: number | null
    fii_idx_call_net: number | null
    fii_idx_put_net: number | null
  }
  five_day: {
    fii_net: number | null
    dii_net: number | null
    divergence: number | null
    fii_buy_days: number
    dii_buy_days: number
  }
  recent: Array<{
    date: string
    fii_net: number | null
    dii_net: number | null
  }>
}

export interface OiWallLevel {
  strike: number
  oi: number
  distance_pct: number | null
}

export interface OptionsContextItem {
  underlying: string
  expiry_date: string
  spot_price: number | null
  futures_price: number | null
  atm_strike: number | null
  pcr_oi: number | null
  pcr_volume: number | null
  max_pain: number | null
  call_wall: OiWallLevel | null
  put_wall: OiWallLevel | null
  total_ce_oi: number | null
  total_pe_oi: number | null
}

export interface MarketLevelItem {
  pdh: number
  pdl: number
  pdc: number
  current: number | null
  state: 'above_pdh' | 'below_pdl' | 'inside_prior_range' | 'unknown'
  gap_pct: number | null
}

export interface MarketPulseData {
  decision: 'YES' | 'CAUTION' | 'NO'
  quality_decision?: 'YES' | 'CAUTION' | 'NO'
  market_quality_score: number
  execution_window_score: number
  mode: 'swing' | 'day'
  regime: 'uptrend' | 'downtrend' | 'chop'
  execution_regime?: 'uptrend' | 'downtrend' | 'chop'
  directional_bias: DirectionalBias
  scores: {
    volatility: CategoryScore
    momentum: CategoryScore
    trend: CategoryScore
    breadth: CategoryScore
    macro: CategoryScore
  }
  confluence?: ConfluenceData
  risk_context?: RiskContext
  ticker: Record<string, TickerItem>
  sectors: SectorData[]
  options_context: Record<string, OptionsContextItem>
  market_levels: Record<string, MarketLevelItem | null>
  institutional_flows: InstitutionalFlows | null
  alerts: AlertItem[]
  equity_ideas: EquityIdea[]
  fno_ideas: FnoIdea[]
  analysis: string | null
  execution_details: Record<string, unknown>
  errors: string[]
  updated_at: string
  cache_ttl: number
}

interface MarketPulseResponse {
  status: string
  data?: MarketPulseData
  message?: string
}

const MARKET_PULSE_TIMEOUT_MS = 75_000

export async function fetchMarketPulse(
  mode: 'swing' | 'day' = 'swing',
  refresh = false,
): Promise<MarketPulseData> {
  const params = new URLSearchParams({ mode })
  if (refresh) {
    params.set('refresh', '1')
  }

  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), MARKET_PULSE_TIMEOUT_MS)

  try {
    const response = await fetch(`/market-pulse/api/data?${params.toString()}`, {
      method: 'GET',
      credentials: 'omit',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
      },
      signal: controller.signal,
    })

    let payload: MarketPulseResponse | null = null
    try {
      payload = (await response.json()) as MarketPulseResponse
    } catch {
      payload = null
    }

    if (response.ok && payload?.status === 'success' && payload.data) {
      return payload.data
    }

    throw new Error(payload?.message || `Market Pulse request failed (${response.status})`)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(
        `Market Pulse request timed out after ${Math.round(MARKET_PULSE_TIMEOUT_MS / 1000)} seconds`,
      )
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }
}


// ══════════════════════════════════════════════════════════════════
// Progressive Loading API Functions (Phase 1)
// ══════════════════════════════════════════════════════════════════

const FAST_TIMEOUT_MS = 15_000

async function _fetchEndpoint<T>(path: string, timeoutMs = FAST_TIMEOUT_MS): Promise<T | null> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(path, {
      method: 'GET',
      credentials: 'omit',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
    const payload = await response.json()
    if (response.ok && payload?.status === 'success') {
      return payload.data as T
    }
    return null
  } catch {
    return null
  } finally {
    window.clearTimeout(timeoutId)
  }
}

// ── Phase 5: Confluence ────────────────────────────────────────
export interface ConfluenceData {
  level: 'HIGH' | 'MEDIUM' | 'LOW'
  action: string
  label: string
  color: string
  structural_regime: string
  intraday_regime: string
  bias: string
  confidence: number
}

// ── Phase 8: Risk Context ──────────────────────────────────────
export interface RiskContext {
  risk_per_trade_pct: number
  size_label: 'AGGRESSIVE' | 'NORMAL' | 'REDUCED' | 'MINIMAL'
  color: string
  multipliers: {
    quality: number
    execution: number
    vix: number
  }
  context: string
  risk_amount?: number
  suggested_position_value?: number
}

// ── Core Response (fast-path) ──────────────────────────────────
export interface MarketPulseCoreData extends Omit<
  MarketPulseData,
  'sectors' | 'equity_ideas' | 'fno_ideas' | 'analysis' | 'options_context' |
  'institutional_flows' | 'execution_details' | 'errors'
> {
  confluence?: ConfluenceData
  risk_context?: RiskContext
}

export async function fetchMarketPulseCore(
  mode: 'swing' | 'day' = 'swing',
): Promise<MarketPulseCoreData | null> {
  return _fetchEndpoint<MarketPulseCoreData>(
    `/market-pulse/api/core?mode=${mode}`,
  )
}

// ── Phase 2: Intraday Context ──────────────────────────────────
export interface SessionPhase {
  phase: string
  label: string
  start: string | null
  end: string | null
  progress_pct: number
  minutes_remaining: number
}

export interface OpeningRange {
  or_high: number
  or_low: number
  or_range: number
  or_range_pct: number
  current_vs_or: 'above' | 'below' | 'inside'
  minutes: number
  complete: boolean
}

export interface InitialBalance {
  ib_high: number
  ib_low: number
  ib_range: number
  ib_range_pct: number
  current_vs_ib: 'above' | 'below' | 'inside'
  minutes: number
  complete: boolean
}

export interface VwapBands {
  vwap: number
  upper_1: number
  lower_1: number
  upper_2: number
  lower_2: number
  std_dev: number
  current: number
  distance_pct: number
  zone: string
}

export interface AdrData {
  adr: number
  adr_median: number
  adr_pct: number | null
  lookback_days: number
  today_range: number | null
  consumed_pct: number | null
  exhaustion_warning: boolean
}

export interface DevelopingHighLow {
  dev_high: number
  dev_low: number
  dev_range: number
  high_touches: number
  low_touches: number
  current: number
  range_position_pct: number
}

export interface IntradayContext {
  symbol: string
  session_phase: SessionPhase
  opening_range: OpeningRange | null
  initial_balance: InitialBalance | null
  vwap_bands: VwapBands | null
  adr: AdrData | null
  developing_high_low: DevelopingHighLow | null
  computed_at: string
}

export async function fetchIntradayContext(
  mode: 'swing' | 'day' = 'day',
): Promise<Record<string, IntradayContext> | null> {
  return _fetchEndpoint<Record<string, IntradayContext>>(
    `/market-pulse/api/intraday?mode=${mode}`,
    20_000,
  )
}

// ── Phase 3: Options Greeks ────────────────────────────────────
export interface GexProfile {
  underlying: string
  expiry_date: string
  spot_price: number | null
  atm_strike: number | null
  pcr_oi: number | null
  total_ce_gex: number | null
  total_pe_gex: number | null
  total_net_gex: number
  gamma_positioning: 'long_gamma' | 'short_gamma' | 'neutral'
  gamma_label: string
  flip_strike: number | null
  top_call_gex_strikes: Array<{ strike: number; gex: number }>
  top_put_gex_strikes: Array<{ strike: number; gex: number }>
}

export interface IvData {
  underlying: string
  expiry_date: string
  spot_price: number | null
  atm_strike: number | null
  atm_ce_iv: number | null
  atm_pe_iv: number | null
  atm_iv: number | null
  pc_skew: number | null
  skew_interpretation: string
}

export interface OptionsGreeksData {
  gex: GexProfile | null
  iv: IvData | null
}

export async function fetchOptionsGreeks(): Promise<Record<string, OptionsGreeksData> | null> {
  return _fetchEndpoint<Record<string, OptionsGreeksData>>(
    '/market-pulse/api/greeks',
    30_000,
  )
}

// ── Phase 4: Global Correlation ────────────────────────────────
export interface NiftyBankNiftyRS {
  nifty_change_pct: number
  banknifty_change_pct: number
  spread: number
  interpretation: string
  note: string
}

export interface GapContext {
  prev_close: number
  open: number | null
  current: number
  gap_pct: number
  gap_type: string
  gap_filled: boolean
}

export interface GlobalContextData {
  nifty_banknifty_rs: NiftyBankNiftyRS | null
  gap_context: GapContext | null
  commodities: Record<string, { symbol: string; ltp: number; change_pct: number | null }>
}

export async function fetchGlobalContext(): Promise<GlobalContextData | null> {
  return _fetchEndpoint<GlobalContextData>('/market-pulse/api/global')
}

// ── Phase 6: Alerts ────────────────────────────────────────────
export interface AlertHistoryItem {
  id: string
  name: string
  message: string
  timestamp: string
  context?: Record<string, unknown>
}

export interface AlertRule {
  id: string
  name: string
  type: string
  field?: string
  threshold?: number
  message: string
  enabled: boolean
}

export interface AlertsData {
  history: AlertHistoryItem[]
  rules: AlertRule[]
}

export async function fetchAlerts(): Promise<AlertsData | null> {
  return _fetchEndpoint<AlertsData>('/market-pulse/api/alerts')
}

// ── Phase 7: Trade Journal ─────────────────────────────────────
export interface JournalSignal {
  signal_id: string
  timestamp: string
  symbol: string
  signal_type: string
  conviction: string
  entry: number | null
  stop_loss: number | null
  target: number | null
  risk_reward: number | null
  ltp: number | null
  sector: string | null
  reason: string | null
  regime: string | null
  quality_score: number | null
  mode: string | null
  exit_price: number | null
  pnl_pct: number | null
  hit_target: boolean | null
  hit_sl: boolean | null
  bars_held: number | null
}

export interface WinRateStats {
  period_days: number
  total_signals: number
  with_outcome: number
  wins: number
  losses: number
  win_rate: number
  avg_pnl: number | null
  by_regime: Array<{
    regime: string
    total: number
    wins: number
    win_rate: number
    avg_pnl: number | null
  }>
  by_sector: Array<{
    sector: string
    total: number
    wins: number
    win_rate: number
    avg_pnl: number | null
  }>
}

export interface JournalData {
  signals: JournalSignal[]
  stats: WinRateStats
}

export async function fetchJournal(days = 30): Promise<JournalData | null> {
  return _fetchEndpoint<JournalData>(`/market-pulse/api/journal?days=${days}`)
}

// ── Health Check ───────────────────────────────────────────────
export interface HealthData {
  data_cache: 'warm' | 'cold'
  cache_age_seconds: number | null
  has_nifty_history: boolean
  has_ticker: boolean
  prewarm_done: boolean
}

export async function fetchHealth(): Promise<HealthData | null> {
  return _fetchEndpoint<HealthData>('/market-pulse/api/health', 5_000)
}


// ── Phase 9: Institutional Flow Intelligence ──────────────────
export interface FiiDiiStreakData {
  direction: 'buy' | 'sell' | 'neutral'
  days: number
  cumulative: number
}

export interface FiiDiiDailyData {
  date: string
  fii_net: number
  dii_net: number
  fii_buy: number
  fii_sell: number
  dii_buy: number
  dii_sell: number
  fii_5d: number[]
  dii_5d: number[]
  fii_streak: FiiDiiStreakData
  dii_streak: FiiDiiStreakData
  flow_strength: number
  available: boolean
}

export interface FnOParticipantData {
  fii_index_futures: { long: number; short: number; ls_ratio: number }
  fii_stock_futures?: { long: number; short: number; ls_ratio: number }
  dii_index_futures?: { long: number; short: number; ls_ratio: number }
  sentiment: string
  sentiment_score: number
  available: boolean
}

export interface HeatmapDayData {
  date: string
  fii_net: number
  dii_net: number
}

export interface InstitutionalContextData {
  fii_dii: FiiDiiDailyData
  fno_participant: FnOParticipantData
  heatmap_45d: HeatmapDayData[]
  timestamp: string
}

export async function fetchInstitutionalContext(): Promise<InstitutionalContextData | null> {
  return _fetchEndpoint<InstitutionalContextData>('/market-pulse/api/institutional', 20_000)
}


// ── Phase 9: Fundamental Quality Scoring ──────────────────────
export interface PriceStrengthData {
  vs_52w_high: number | null
  vs_200dma: number | null
  vs_50dma: number | null
  rvol: number | null
  ret_1m: number | null
  ret_3m: number | null
  ret_1y: number | null
}

export interface ShareholdingData {
  promoter: number[]
  fii: number[]
  dii: number[]
  public: number[]
  quarters: string[]
}

export interface FundamentalEntry {
  quality_score: number
  pe: number | null
  pb: number | null
  div_yield: number | null
  ltp: number | null
  prev_close: number | null
  volume: number
  market_cap_tier: string
  price_strength: PriceStrengthData
  shareholding?: ShareholdingData
  error?: string
}

export type FundamentalsData = Record<string, FundamentalEntry>

export async function fetchFundamentals(symbols: string[]): Promise<FundamentalsData | null> {
  if (!symbols.length) return null
  return _fetchEndpoint<FundamentalsData>(
    `/market-pulse/api/fundamentals?symbols=${symbols.join(',')}`,
    20_000,
  )
}


// ── Phase 9: Sector Performance & Rotation ────────────────────
export interface SectorPerfEntry {
  name: string
  symbol: string
  ltp: number
  prev_close: number
  today_pct: number
  rs_vs_nifty: number
  flow_hint: string
}

export interface SectorHeatmapEntry {
  name: string
  symbol: string
  value: number
  rs: number
  intensity: number
  flow_hint: string
}

export interface RotationLeader {
  name: string
  pct: number
  rs: number
}

export interface SectorContextData {
  performance: {
    sectors: SectorPerfEntry[]
    nifty_today_pct: number
    available: boolean
  }
  heatmap: SectorHeatmapEntry[]
  rotation: {
    leaders: RotationLeader[]
    laggards: RotationLeader[]
    rotation_signal: string
  }
  timestamp: string
}

export async function fetchSectorContext(): Promise<SectorContextData | null> {
  return _fetchEndpoint<SectorContextData>('/market-pulse/api/sectors')
}
