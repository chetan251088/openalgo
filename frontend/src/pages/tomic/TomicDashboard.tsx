import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Square,
  Trash2,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  tomicApi,
  type TomicAnalyticsResponse,
  type TomicCircuitBreakerDetail,
  type TomicCircuitBreakersStructured,
  type TomicDailyPlansResponse,
  type TomicDeadLetter,
  type TomicMarketContextResponse,
  type TomicMetricsResponse,
  type TomicPositionsResponse,
  type TomicSignalQualityResponse,
  type TomicStatusResponse,
} from '@/api/tomic'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { showToast } from '@/utils/toast'

type ActionKind = 'start' | 'pause' | 'resume' | 'stop'

const PERMANENT_ERROR_CLASSES = new Set(['broker_reject', 'validation'])

// ── formatting helpers ──────────────────────────────────────────────────────

function formatNumber(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function formatAge(dateStr: string | null | undefined): string {
  if (!dateStr) return '—'
  const diffSecs = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000)
  if (Number.isNaN(diffSecs)) return '—'
  if (diffSecs < 60) return `${diffSecs}s ago`
  if (diffSecs < 3600) return `${Math.floor(diffSecs / 60)}m ago`
  if (diffSecs < 86400) return `${Math.floor(diffSecs / 3600)}h ago`
  return `${Math.floor(diffSecs / 86400)}d ago`
}

function resolveMetric(metrics: Record<string, unknown> | undefined, candidates: string[]): number | null {
  if (!metrics) return null
  const normalized = new Map(Object.entries(metrics).map(([k, v]) => [k.toLowerCase(), v]))
  for (const key of candidates) {
    const val = normalized.get(key.toLowerCase())
    if (typeof val === 'number' && Number.isFinite(val)) return val
  }
  return null
}

// ── colour helpers ─────────────────────────────────────────────────────────

function vixRegimeClass(regime: string | undefined): string {
  switch (regime?.toUpperCase()) {
    case 'TOO_LOW':  return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200'
    case 'NORMAL':   return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
    case 'ELEVATED': return 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200'
    case 'HIGH':     return 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200'
    case 'EXTREME':  return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
    default:         return 'bg-muted text-muted-foreground'
  }
}

function pcrBiasClass(bias: string | undefined): string {
  switch (bias?.toUpperCase()) {
    case 'BULLISH': return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
    case 'BEARISH': return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
    default:        return 'bg-muted text-muted-foreground'
  }
}

function trendClass(trend: string | undefined): string {
  if (trend === 'ABOVE_20MA') return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
  if (trend === 'BELOW_20MA') return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
  return 'bg-muted text-muted-foreground'
}

function strategyBadgeVariant(st: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (st === 'SKIP') return 'secondary'
  if (st.includes('BEAR') || st === 'SHORT_STRANGLE' || st === 'SHORT_STRADDLE') return 'destructive'
  if (st.includes('BULL') || st === 'IRON_CONDOR' || st === 'JADE_LIZARD') return 'default'
  return 'outline'
}

// ── component ──────────────────────────────────────────────────────────────

export default function TomicDashboard() {
  // data
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefreshed, setLastRefreshed] = useState(0)
  const [status, setStatus] = useState<TomicStatusResponse | null>(null)
  const [positions, setPositions] = useState<TomicPositionsResponse | null>(null)
  const [analytics, setAnalytics] = useState<TomicAnalyticsResponse | null>(null)
  const [quality, setQuality] = useState<TomicSignalQualityResponse | null>(null)
  const [marketContext, setMarketContext] = useState<TomicMarketContextResponse | null>(null)
  const [dailyPlans, setDailyPlans] = useState<TomicDailyPlansResponse | null>(null)
  const [metrics, setMetrics] = useState<TomicMetricsResponse | null>(null)

  // actions
  const [actionBusy, setActionBusy] = useState<ActionKind | null>(null)
  const [pauseOpen, setPauseOpen] = useState(false)
  const [pauseReason, setPauseReason] = useState('')
  const [scanBusy, setScanBusy] = useState(false)

  // dead letters
  const [deadLetters, setDeadLetters] = useState<TomicDeadLetter[]>([])
  const [deadLettersLoading, setDeadLettersLoading] = useState(false)
  const [retryAllBusy, setRetryAllBusy] = useState(false)
  const [deleteAllBusy, setDeleteAllBusy] = useState(false)

  // ── data loading ─────────────────────────────────────────────────────────

  const loadData = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    try {
      const [s, p, a, q, mc, dp, m] = await Promise.allSettled([
        tomicApi.getStatus(),
        tomicApi.getPositions(),
        tomicApi.getAnalytics(),
        tomicApi.getSignalQuality(false),
        tomicApi.getMarketContext(),
        tomicApi.getDailyPlans(),
        tomicApi.getMetrics(),
      ])
      if (s.status  === 'fulfilled') setStatus(s.value)
      if (p.status  === 'fulfilled') setPositions(p.value)
      if (a.status  === 'fulfilled') setAnalytics(a.value)
      if (q.status  === 'fulfilled') setQuality(q.value)
      if (mc.status === 'fulfilled') setMarketContext(mc.value)
      if (dp.status === 'fulfilled') setDailyPlans(dp.value)
      if (m.status  === 'fulfilled') setMetrics(m.value)
      setLastRefreshed(Date.now())
    } catch {
      if (!silent) showToast.error('Failed to load TOMIC dashboard', 'monitoring')
    } finally {
      if (silent) setRefreshing(false)
      else setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData(false)
    const timer = setInterval(() => void loadData(true), 5000)
    return () => clearInterval(timer)
  }, [loadData])

  const fetchDeadLetters = useCallback(async () => {
    setDeadLettersLoading(true)
    try {
      const resp = await tomicApi.getDeadLetters(50, 0)
      setDeadLetters(resp.items ?? [])
    } catch {
      showToast.error('Failed to load dead letters', 'monitoring')
    } finally {
      setDeadLettersLoading(false)
    }
  }, [])

  const deadLetterCount = status?.data?.command_dead_letters ?? 0
  useEffect(() => { void fetchDeadLetters() }, [fetchDeadLetters, deadLetterCount])

  // ── actions ──────────────────────────────────────────────────────────────

  const executeAction = useCallback(async (action: ActionKind, reason = '') => {
    setActionBusy(action)
    try {
      if (action === 'start')  await tomicApi.start()
      if (action === 'pause')  await tomicApi.pause(reason || 'Paused from dashboard')
      if (action === 'resume') await tomicApi.resume()
      if (action === 'stop')   await tomicApi.stop()
      showToast.success(`TOMIC ${action} requested`, 'monitoring')
      await loadData(true)
    } catch {
      showToast.error(`Failed to ${action} TOMIC`, 'monitoring')
    } finally {
      setActionBusy(null)
    }
  }, [loadData])

  const handlePauseConfirm = useCallback(async () => {
    setPauseOpen(false)
    await executeAction('pause', pauseReason)
    setPauseReason('')
  }, [executeAction, pauseReason])

  const handleRunScan = useCallback(async () => {
    setScanBusy(true)
    try {
      const result = await tomicApi.getSignalQuality(true)
      setQuality(result)
      showToast.success('Signal scan complete', 'monitoring')
    } catch {
      showToast.error('Scan failed', 'monitoring')
    } finally {
      setScanBusy(false)
    }
  }, [])

  const handleRetryOne = useCallback(async (id: number) => {
    try {
      await tomicApi.retryDeadLetter(id)
      setDeadLetters(prev => prev.filter(x => x.id !== id))
      showToast.success('Dead letter requeued', 'monitoring')
    } catch { showToast.error('Failed to retry', 'monitoring') }
  }, [])

  const handleRetryAll = useCallback(async () => {
    setRetryAllBusy(true)
    try {
      const r = await tomicApi.retryAllDeadLetters()
      showToast.success(`Requeued ${r.requeued} dead letter(s)`, 'monitoring')
      await fetchDeadLetters()
    } catch { showToast.error('Failed to retry all', 'monitoring') }
    finally { setRetryAllBusy(false) }
  }, [fetchDeadLetters])

  const handleDeleteOne = useCallback(async (id: number) => {
    try {
      await tomicApi.deleteDeadLetter(id)
      setDeadLetters(prev => prev.filter(x => x.id !== id))
      showToast.success('Deleted', 'monitoring')
    } catch { showToast.error('Failed to delete', 'monitoring') }
  }, [])

  const handleDeleteAll = useCallback(async () => {
    setDeleteAllBusy(true)
    try {
      const r = await tomicApi.deleteAllDeadLetters()
      showToast.success(`Deleted ${r.deleted} dead letter(s)`, 'monitoring')
      setDeadLetters([])
    } catch { showToast.error('Failed to delete all', 'monitoring') }
    finally { setDeleteAllBusy(false) }
  }, [])

  // ── derived values ────────────────────────────────────────────────────────

  const runtime    = status?.data
  const loop       = runtime?.signal_loop
  const agentRows  = useMemo(() => Object.entries(runtime?.agents ?? {}), [runtime?.agents])
  const openPositions = positions?.positions ?? []
  const totalPnl = useMemo(
    () => openPositions.reduce((s, p) => s + (Number(p.pnl) || 0), 0),
    [openPositions]
  )

  const expectancy   = resolveMetric(analytics?.metrics, ['expectancy', 'expectancy_30', 'rolling_expectancy'])
  const winRate      = resolveMetric(analytics?.metrics, ['win_rate', 'winrate'])
  const maxDrawdown  = resolveMetric(analytics?.metrics, ['max_drawdown', 'max_dd'])
  const sharpe       = resolveMetric(analytics?.metrics, ['sharpe', 'sharpe_ratio'])

  const signalStats     = quality?.data?.signals
  const signalFeedWs    = quality?.data?.feed?.ws ?? {}
  const signalFeedBridge = quality?.data?.feed?.bridge ?? {}
  const noActionReasons = quality?.data?.diagnostics?.no_action_reasons ?? []
  const wsConnected     = Boolean(signalFeedWs['connected'])
  const wsAuth          = signalFeedWs['authenticated'] == null ? true : Boolean(signalFeedWs['authenticated'])
  const bridgeTickAge   = Number(signalFeedBridge['last_tick_age_s'] ?? -1)

  const circuitBreakers = metrics?.data?.circuit_breakers as TomicCircuitBreakersStructured | undefined
  const cbEntries       = Object.entries(circuitBreakers?.breakers ?? {})

  const ageSecs = lastRefreshed > 0 ? Math.floor((Date.now() - lastRefreshed) / 1000) : null

  // ── runtime status badge ──────────────────────────────────────────────────

  const isRunning = Boolean(runtime?.running)
  const isKilled  = Boolean(runtime?.killed)
  const runtimeLabel = isKilled ? 'KILLED' : isRunning ? 'RUNNING' : 'OFFLINE'
  const runtimeDotClass = isKilled
    ? 'bg-destructive'
    : isRunning
    ? 'bg-emerald-500 animate-pulse'
    : 'bg-muted-foreground'
  const runtimeTextClass = isKilled
    ? 'text-destructive'
    : isRunning
    ? 'text-emerald-600 dark:text-emerald-400'
    : 'text-muted-foreground'

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="pb-8">

        {/* ════════════════════════════════════════════════════════════════
            Sticky Header
            ════════════════════════════════════════════════════════════════ */}
        <div className="sticky top-0 z-20 bg-background/95 backdrop-blur-sm border-b space-y-2 pt-4 pb-3 -mx-4 md:-mx-6 px-4 md:px-6">

          {/* Row 1 — title / status / quick stats / action buttons */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              {/* Logo */}
              <div className="flex items-center gap-1.5 font-bold text-base">
                <Activity className="h-4 w-4 text-primary shrink-0" />
                TOMIC
              </div>

              {/* Status pill */}
              <div className="flex items-center gap-1.5">
                <span className={`h-2 w-2 rounded-full shrink-0 ${runtimeDotClass}`} />
                <span className={`text-xs font-semibold ${runtimeTextClass}`}>{runtimeLabel}</span>
                {isKilled && <ShieldAlert className="h-3.5 w-3.5 text-destructive" />}
              </div>

              <div className="h-3.5 w-px bg-border" />

              {/* Quick stats */}
              <span className="text-xs text-muted-foreground">
                Queue <span className="text-foreground font-medium">{runtime?.command_queue_pending ?? 0}</span>
              </span>
              <span className="text-xs text-muted-foreground">
                Pos <span className="text-foreground font-medium">{openPositions.length}</span>
              </span>
              <span className={`text-xs font-semibold ${totalPnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                {totalPnl >= 0 ? '+' : ''}{formatNumber(totalPnl)}
              </span>
              {deadLetterCount > 0 && (
                <Badge variant="destructive" className="text-[10px] h-4 px-1.5 py-0">
                  {deadLetterCount} dead
                </Badge>
              )}
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-1.5">
              <Button
                size="sm" variant="ghost"
                className="h-8 w-8 p-0"
                onClick={() => void loadData(true)}
                disabled={refreshing}
                title="Refresh"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              </Button>

              <Button size="sm" className="h-8"
                onClick={() => void executeAction('start')}
                disabled={actionBusy !== null}
              >
                <PlayCircle className="h-3.5 w-3.5 mr-1" />
                {actionBusy === 'start' ? '…' : 'Start'}
              </Button>

              {/* Pause with reason popover */}
              <Popover open={pauseOpen} onOpenChange={setPauseOpen}>
                <PopoverTrigger asChild>
                  <Button size="sm" variant="secondary" className="h-8" disabled={actionBusy !== null}>
                    <PauseCircle className="h-3.5 w-3.5 mr-1" />
                    {actionBusy === 'pause' ? '…' : 'Pause'}
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-72" align="end" sideOffset={6}>
                  <div className="space-y-3">
                    <div>
                      <p className="text-sm font-semibold">Pause reason</p>
                      <p className="text-xs text-muted-foreground mt-0.5">Recorded in audit trail</p>
                    </div>
                    <Input
                      autoFocus
                      placeholder="e.g. Pre-event pause, review…"
                      value={pauseReason}
                      onChange={e => setPauseReason(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') void handlePauseConfirm() }}
                      className="h-8 text-sm"
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm" variant="ghost" className="h-7"
                        onClick={() => { setPauseOpen(false); setPauseReason('') }}
                      >
                        Cancel
                      </Button>
                      <Button size="sm" className="h-7" onClick={() => void handlePauseConfirm()}>
                        Confirm Pause
                      </Button>
                    </div>
                  </div>
                </PopoverContent>
              </Popover>

              <Button size="sm" variant="secondary" className="h-8"
                onClick={() => void executeAction('resume')}
                disabled={actionBusy !== null}
              >
                <PlayCircle className="h-3.5 w-3.5 mr-1" />
                {actionBusy === 'resume' ? '…' : 'Resume'}
              </Button>

              <Button size="sm" variant="destructive" className="h-8"
                onClick={() => void executeAction('stop')}
                disabled={actionBusy !== null}
              >
                <Square className="h-3.5 w-3.5 mr-1" />
                {actionBusy === 'stop' ? '…' : 'Stop'}
              </Button>
            </div>
          </div>

          {/* Row 2 — Market context ribbon */}
          <div className="flex flex-wrap items-center gap-1.5 min-h-6">
            <span className="text-[11px] text-muted-foreground font-medium">Market</span>

            {/* VIX */}
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${vixRegimeClass(marketContext?.data?.vix_regime)}`}>
              VIX {formatNumber(marketContext?.data?.vix)}
              <span className="opacity-70 text-[10px]">[{marketContext?.data?.vix_regime ?? '—'}]</span>
            </span>

            {/* PCR */}
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${pcrBiasClass(marketContext?.data?.pcr_bias)}`}>
              PCR {formatNumber(marketContext?.data?.pcr)}
              <span className="opacity-70 text-[10px]">[{marketContext?.data?.pcr_bias ?? '—'}]</span>
            </span>

            {/* NIFTY */}
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${trendClass(marketContext?.data?.nifty_trend)}`}>
              {marketContext?.data?.nifty_trend === 'ABOVE_20MA'
                ? <TrendingUp className="h-3 w-3" />
                : marketContext?.data?.nifty_trend === 'BELOW_20MA'
                ? <TrendingDown className="h-3 w-3" />
                : null}
              NIFTY {formatNumber(marketContext?.data?.nifty_ltp)}
            </span>

            {/* BANKNIFTY */}
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${trendClass(marketContext?.data?.banknifty_trend)}`}>
              {marketContext?.data?.banknifty_trend === 'ABOVE_20MA'
                ? <TrendingUp className="h-3 w-3" />
                : marketContext?.data?.banknifty_trend === 'BELOW_20MA'
                ? <TrendingDown className="h-3 w-3" />
                : null}
              BANKNIFTY {formatNumber(marketContext?.data?.banknifty_ltp)}
            </span>

            {ageSecs !== null && (
              <span className="text-[11px] text-muted-foreground ml-1">· updated {ageSecs}s ago</span>
            )}
          </div>
        </div>

        {/* ════════════════════════════════════════════════════════════════
            Tabs
            ════════════════════════════════════════════════════════════════ */}
        <Tabs defaultValue="overview" className="mt-5">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="plans">Plans &amp; Signals</TabsTrigger>
            <TabsTrigger value="operations" className="gap-1.5">
              Operations
              {deadLetterCount > 0 && (
                <Badge variant="destructive" className="h-4 min-w-4 px-1 text-[10px] py-0">
                  {deadLetterCount}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* ── Overview ────────────────────────────────────────────────── */}
          <TabsContent value="overview" className="space-y-4 mt-4">

            {/* KPI cards */}
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Runtime</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4 space-y-1">
                  <Badge variant={isRunning ? 'default' : 'secondary'} className="text-xs">
                    {runtimeLabel}
                  </Badge>
                  <p className="text-xs text-muted-foreground">
                    Loop: {loop?.enabled ? (loop?.running ? 'running' : 'stopped') : 'disabled'}
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Signals Routed</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4">
                  <p className="text-2xl font-bold">{formatNumber(signalStats?.routed_count ?? 0)}</p>
                  <p className="text-xs text-muted-foreground">
                    enqueued {formatNumber(signalStats?.enqueued_count ?? 0)}
                    {' · '}accept {formatNumber(signalStats?.routed_accept_rate_pct)}%
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Dead Letters</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4">
                  <p className={`text-2xl font-bold ${deadLetterCount > 0 ? 'text-destructive' : ''}`}>
                    {deadLetterCount}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    queue: {runtime?.command_queue_pending ?? 0} pending
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Open Positions</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4">
                  <p className="text-2xl font-bold">{openPositions.length}</p>
                  <p className={`text-xs font-semibold ${totalPnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>
                    {totalPnl >= 0 ? '+' : ''}{formatNumber(totalPnl)} P&L
                  </p>
                </CardContent>
              </Card>
            </div>

            {/* Circuit breaker strip */}
            {cbEntries.length > 0 && (
              <Card>
                <CardHeader className="pb-2 pt-4 px-4">
                  <CardTitle className="text-sm flex items-center gap-1.5">
                    <Zap className="h-4 w-4" />
                    Circuit Breakers
                    {circuitBreakers?.capital !== undefined && (
                      <span className="text-xs font-normal text-muted-foreground ml-1">
                        Capital ₹{formatNumber(circuitBreakers.capital)}
                      </span>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4">
                  <div className="flex flex-wrap gap-1.5">
                    {cbEntries.map(([key, cbRaw]) => {
                      const cb = cbRaw as TomicCircuitBreakerDetail
                      return (
                        <Tooltip key={key}>
                          <TooltipTrigger asChild>
                            <span className={`inline-flex cursor-default select-none items-center gap-1 rounded px-2 py-0.5 text-xs font-medium
                              ${cb.tripped
                                ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
                                : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200'
                              }`}
                            >
                              {cb.tripped
                                ? <AlertTriangle className="h-3 w-3" />
                                : <CheckCircle2 className="h-3 w-3" />
                              }
                              {key}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="bottom" className="max-w-60 space-y-0.5">
                            <p className="font-semibold text-xs">{key}</p>
                            {cb.description && <p className="text-xs opacity-80">{cb.description}</p>}
                            {cb.threshold !== undefined && (
                              <p className="text-xs">Threshold: {formatNumber(cb.threshold)}</p>
                            )}
                            {cb.current !== undefined && (
                              <p className="text-xs">Current: {formatNumber(cb.current)}</p>
                            )}
                            {cb.message && <p className="text-xs text-muted-foreground">{cb.message}</p>}
                          </TooltipContent>
                        </Tooltip>
                      )
                    })}
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Analytics KPIs */}
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {([
                { label: 'Expectancy',   value: expectancy  },
                { label: 'Win Rate %',   value: winRate     },
                { label: 'Max Drawdown', value: maxDrawdown },
                { label: 'Sharpe',       value: sharpe      },
              ] as const).map(({ label, value }) => (
                <Card key={label}>
                  <CardHeader className="pb-1 pt-4 px-4">
                    <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</CardTitle>
                  </CardHeader>
                  <CardContent className="pb-4 px-4">
                    <p className="text-xl font-semibold">{formatNumber(value)}</p>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* Agent Health */}
            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <CardTitle className="text-sm">Agent Health</CardTitle>
                <CardDescription className="text-xs">Heartbeat and restart counters from Supervisor.</CardDescription>
              </CardHeader>
              <CardContent className="pb-4 px-4">
                <div className="border rounded-md">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Agent</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Heartbeat (s)</TableHead>
                        <TableHead>Restarts</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {agentRows.map(([name, agent]) => (
                        <TableRow key={name}>
                          <TableCell className="font-medium text-sm">{name}</TableCell>
                          <TableCell>
                            <Badge variant={agent.running ? 'default' : 'secondary'} className="text-xs">
                              {agent.running ? (agent.paused ? 'PAUSED' : 'RUNNING') : 'STOPPED'}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-sm tabular-nums">{formatNumber(agent.last_heartbeat_ago_s)}</TableCell>
                          <TableCell className="text-sm tabular-nums">{formatNumber(agent.restarts)}</TableCell>
                        </TableRow>
                      ))}
                      {agentRows.length === 0 && (
                        <TableRow>
                          <TableCell colSpan={4} className="text-center text-sm text-muted-foreground py-4">
                            No agent data.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── Plans & Signals ──────────────────────────────────────────── */}
          <TabsContent value="plans" className="space-y-4 mt-4">

            {/* Daily Plans */}
            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <CardTitle className="text-sm">Daily Trade Plans</CardTitle>
                <CardDescription className="text-xs">Generated at 9:45 AM — active strategies for today.</CardDescription>
              </CardHeader>
              <CardContent className="pb-4 px-4">
                {(dailyPlans?.plans ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-6">No plans generated yet.</p>
                ) : (
                  <div className="border rounded-md overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Instrument</TableHead>
                          <TableHead>Strategy</TableHead>
                          <TableHead className="tabular-nums">VIX</TableHead>
                          <TableHead>Regime</TableHead>
                          <TableHead className="tabular-nums">Short Δ</TableHead>
                          <TableHead className="tabular-nums">Wing Δ</TableHead>
                          <TableHead className="tabular-nums">Lots</TableHead>
                          <TableHead>Expiry</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Rationale</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(dailyPlans?.plans ?? []).map((plan, idx) => (
                          <TableRow key={`plan-${idx}`}>
                            <TableCell className="font-semibold text-sm">{plan.instrument}</TableCell>
                            <TableCell>
                              <Badge variant={strategyBadgeVariant(plan.strategy_type)} className="text-xs whitespace-nowrap">
                                {plan.strategy_type}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-sm tabular-nums">{formatNumber(plan.vix_at_plan)}</TableCell>
                            <TableCell className="text-sm">{plan.regime_at_plan}</TableCell>
                            <TableCell className="text-sm tabular-nums">{formatNumber(plan.short_delta_target)}</TableCell>
                            <TableCell className="text-sm tabular-nums">{formatNumber(plan.wing_delta_target)}</TableCell>
                            <TableCell className="text-sm font-semibold tabular-nums">{formatNumber(plan.lots)}</TableCell>
                            <TableCell className="text-xs font-mono">{plan.expiry_date || '—'}</TableCell>
                            <TableCell>
                              <Badge variant={plan.is_active ? 'default' : 'secondary'} className="text-xs">
                                {plan.is_active ? 'ACTIVE' : 'DONE'}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-xs text-muted-foreground max-w-[180px] truncate" title={plan.rationale}>
                              {plan.rationale}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Signal Quality */}
            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle className="text-sm">Signal Quality</CardTitle>
                    <CardDescription className="text-xs mt-0.5">
                      {quality?.data?.generated_at
                        ? `Scanned ${formatAge(quality.data.generated_at)}`
                        : 'Not yet scanned'}
                      {' · '}Accept rate: {formatNumber(signalStats?.routed_accept_rate_pct)}%
                    </CardDescription>
                  </div>
                  <Button
                    size="sm" variant="outline" className="h-7 text-xs shrink-0"
                    onClick={() => void handleRunScan()}
                    disabled={scanBusy}
                  >
                    {scanBusy
                      ? <><RefreshCw className="h-3 w-3 mr-1 animate-spin" />Scanning…</>
                      : <><Zap className="h-3 w-3 mr-1" />Run Scan</>
                    }
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="pb-4 px-4 grid gap-3 md:grid-cols-3">
                {([
                  {
                    label: 'Top Routed',
                    items: signalStats?.top_routed ?? [],
                    render: (r: Record<string, unknown>) =>
                      `${r.instrument ?? '—'} · ${r.strategy_type ?? '—'} · ${r.direction ?? '—'}`,
                  },
                  {
                    label: 'Top Sniper',
                    items: signalStats?.top_sniper ?? [],
                    render: (r: Record<string, unknown>) =>
                      `${r.instrument ?? '—'} · ${r.pattern ?? '—'} · ${r.direction ?? '—'}`,
                  },
                  {
                    label: 'Top Volatility',
                    items: signalStats?.top_volatility ?? [],
                    render: (r: Record<string, unknown>) =>
                      `${r.underlying ?? '—'} · ${r.strategy_type ?? '—'} · ${r.direction ?? '—'}`,
                  },
                ] as const).map(({ label, items, render }) => (
                  <div key={label} className="rounded-md border p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">{label}</p>
                    {(items as Record<string, unknown>[]).slice(0, 5).map((row, i) => (
                      <div key={i} className="text-xs py-0.5 border-b last:border-0 text-muted-foreground">
                        {render(row)}
                      </div>
                    ))}
                    {items.length === 0 && (
                      <p className="text-xs text-muted-foreground">No signals yet.</p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* Why No Action */}
            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <CardTitle className="text-sm">Why No Action</CardTitle>
                <CardDescription className="text-xs">
                  Real-time blockers from feed, router, and risk pipeline.
                </CardDescription>
              </CardHeader>
              <CardContent className="pb-4 px-4 space-y-1.5">
                <div className="text-xs text-muted-foreground">
                  WS auth: {String(signalFeedWs['last_auth_status'] ?? '—')}
                  {signalFeedWs['last_auth_message'] ? ` · ${String(signalFeedWs['last_auth_message'])}` : ''}
                </div>
                {noActionReasons.map((reason, i) => (
                  <div key={`reason-${i}`} className="text-sm flex gap-2">
                    <span className="text-muted-foreground shrink-0 tabular-nums">{i + 1}.</span>
                    <span>{reason}</span>
                  </div>
                ))}
                {noActionReasons.length === 0 && (
                  <p className="text-sm text-muted-foreground">No blocking reason in latest cycle.</p>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── Operations ────────────────────────────────────────────────── */}
          <TabsContent value="operations" className="space-y-4 mt-4">

            {/* Signal Loop detail */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Loop Status</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4 space-y-1">
                  <Badge variant={loop?.running ? 'default' : 'secondary'} className="text-xs">
                    {loop?.running ? 'RUNNING' : 'STOPPED'}
                  </Badge>
                  <p className="text-xs text-muted-foreground">{loop?.enabled ? 'Enabled' : 'Disabled'}</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Interval / Cooldown</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4">
                  <p className="text-lg font-bold tabular-nums">{formatNumber(loop?.interval_s)}s</p>
                  <p className="text-xs text-muted-foreground">cooldown {formatNumber(loop?.enqueue_cooldown_s)}s</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Last Enqueued</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4">
                  <p className="text-lg font-bold tabular-nums">
                    {formatNumber(loop?.last_enqueued ?? signalStats?.enqueued_count ?? 0)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    dedupe skips {formatNumber(loop?.last_dedupe_skips ?? signalStats?.dedupe_skipped_count ?? 0)}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-1 pt-4 px-4">
                  <CardTitle className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Feed Tick Age</CardTitle>
                </CardHeader>
                <CardContent className="pb-4 px-4 space-y-1">
                  <p className="text-lg font-bold tabular-nums">
                    {bridgeTickAge >= 0 ? `${formatNumber(bridgeTickAge)}s` : '—'}
                  </p>
                  <Badge variant={wsConnected && wsAuth ? 'default' : 'destructive'} className="text-xs">
                    {wsConnected && wsAuth ? 'LIVE' : 'STALE/OFFLINE'}
                  </Badge>
                </CardContent>
              </Card>
            </div>

            {/* Dead Letters */}
            <Card>
              <CardHeader className="pb-2 pt-4 px-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <CardTitle className="text-sm flex items-center gap-2">
                      Dead Letters
                      {deadLetterCount > 0 && (
                        <Badge variant="destructive" className="text-xs">{deadLetterCount}</Badge>
                      )}
                    </CardTitle>
                    <CardDescription className="text-xs mt-0.5">
                      Failed commands. Transient errors can be requeued.
                    </CardDescription>
                  </div>
                  {deadLetters.length > 0 && (
                    <div className="flex gap-1.5 shrink-0">
                      <Button
                        size="sm" variant="outline" className="h-7 text-xs"
                        onClick={() => void handleRetryAll()}
                        disabled={retryAllBusy}
                      >
                        <RotateCcw className={`h-3 w-3 mr-1 ${retryAllBusy ? 'animate-spin' : ''}`} />
                        {retryAllBusy ? '…' : 'Retry All'}
                      </Button>
                      <Button
                        size="sm" variant="outline" className="h-7 text-xs text-destructive hover:text-destructive hover:bg-destructive/10"
                        onClick={() => void handleDeleteAll()}
                        disabled={deleteAllBusy}
                      >
                        <Trash2 className="h-3 w-3 mr-1" />
                        {deleteAllBusy ? '…' : 'Delete All'}
                      </Button>
                    </div>
                  )}
                </div>
              </CardHeader>
              <CardContent className="pb-4 px-4">
                {deadLettersLoading ? (
                  <div className="flex justify-center py-8">
                    <div className="h-6 w-6 animate-spin rounded-full border-b-2 border-primary" />
                  </div>
                ) : deadLetters.length === 0 ? (
                  <p className="text-sm text-muted-foreground text-center py-6">No dead letters.</p>
                ) : (
                  <div className="border rounded-md overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-12">ID</TableHead>
                          <TableHead>Event Type</TableHead>
                          <TableHead>Source</TableHead>
                          <TableHead>Error Class</TableHead>
                          <TableHead>Message</TableHead>
                          <TableHead className="w-20 tabular-nums">Attempts</TableHead>
                          <TableHead className="w-20">Age</TableHead>
                          <TableHead className="w-16">Actions</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {deadLetters.map(item => {
                          const ec = item.error_class ?? 'unknown'
                          const isPerm = PERMANENT_ERROR_CLASSES.has(ec)
                          const isAmber = ec === 'network_timeout' || ec === 'broker_rate_limit'
                          return (
                            <TableRow key={item.id}>
                              <TableCell className="font-mono text-xs tabular-nums">{item.id}</TableCell>
                              <TableCell className="text-xs">{item.event_type}</TableCell>
                              <TableCell className="text-xs text-muted-foreground">{item.source_agent}</TableCell>
                              <TableCell>
                                {isPerm
                                  ? <Badge variant="destructive" className="text-xs">{ec}</Badge>
                                  : isAmber
                                  ? <Badge className="text-xs bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200">{ec}</Badge>
                                  : <Badge variant="secondary" className="text-xs">{ec}</Badge>
                                }
                              </TableCell>
                              <TableCell
                                className="text-xs text-muted-foreground max-w-[200px] truncate"
                                title={item.error_message ?? ''}
                              >
                                {item.error_message || '—'}
                              </TableCell>
                              <TableCell className="text-xs tabular-nums">{item.attempt_count}/{item.max_attempts}</TableCell>
                              <TableCell className="text-xs text-muted-foreground">
                                {formatAge(item.processed_at ?? item.created_at)}
                              </TableCell>
                              <TableCell>
                                <div className="flex gap-0.5">
                                  {!isPerm && (
                                    <Button
                                      size="sm" variant="ghost" className="h-6 w-6 p-0"
                                      title="Retry"
                                      onClick={() => void handleRetryOne(item.id)}
                                    >
                                      <RotateCcw className="h-3 w-3" />
                                    </Button>
                                  )}
                                  <Button
                                    size="sm" variant="ghost" className="h-6 w-6 p-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                                    title="Delete"
                                    onClick={() => void handleDeleteOne(item.id)}
                                  >
                                    <Trash2 className="h-3 w-3" />
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </TooltipProvider>
  )
}
