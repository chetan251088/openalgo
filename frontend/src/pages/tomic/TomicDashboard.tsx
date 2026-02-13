import { Activity, PauseCircle, PlayCircle, RefreshCw, ShieldAlert, Square } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  tomicApi,
  type TomicAnalyticsResponse,
  type TomicPositionsResponse,
  type TomicSignalQualityResponse,
  type TomicStatusResponse,
} from '@/api/tomic'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { showToast } from '@/utils/toast'

type ActionKind = 'start' | 'pause' | 'resume' | 'stop'

function formatNumber(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function resolveMetric(metrics: Record<string, unknown> | undefined, candidates: string[]): number | null {
  if (!metrics) return null
  const normalized = new Map<string, unknown>()
  for (const [key, value] of Object.entries(metrics)) {
    normalized.set(key.toLowerCase(), value)
  }
  for (const key of candidates) {
    const value = normalized.get(key.toLowerCase())
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

const TOMIC_PAGE_LINKS: Array<{ label: string; href: string }> = [
  { label: 'Dashboard', href: '/tomic/dashboard' },
  { label: 'Agents', href: '/tomic/agents' },
  { label: 'Risk', href: '/tomic/risk' },
]

const TOMIC_DATA_LINKS: Array<{ label: string; href: string }> = [
  { label: 'Status JSON', href: '/tomic/status' },
  { label: 'Positions JSON', href: '/tomic/positions' },
  { label: 'Journal JSON', href: '/tomic/journal' },
  { label: 'Analytics JSON', href: '/tomic/analytics' },
  { label: 'Metrics JSON', href: '/tomic/metrics' },
  { label: 'Signal Quality JSON', href: '/tomic/signals/quality' },
  { label: 'Audit JSON', href: '/tomic/audit' },
]

export default function TomicDashboard() {
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [actionBusy, setActionBusy] = useState<ActionKind | null>(null)

  const [status, setStatus] = useState<TomicStatusResponse | null>(null)
  const [positions, setPositions] = useState<TomicPositionsResponse | null>(null)
  const [analytics, setAnalytics] = useState<TomicAnalyticsResponse | null>(null)
  const [quality, setQuality] = useState<TomicSignalQualityResponse | null>(null)

  const loadData = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)

    try {
      const [statusResp, positionsResp, analyticsResp, qualityResp] = await Promise.allSettled([
        tomicApi.getStatus(),
        tomicApi.getPositions(),
        tomicApi.getAnalytics(),
        tomicApi.getSignalQuality(false),
      ])

      if (statusResp.status === 'fulfilled') setStatus(statusResp.value)
      if (positionsResp.status === 'fulfilled') setPositions(positionsResp.value)
      if (analyticsResp.status === 'fulfilled') setAnalytics(analyticsResp.value)
      if (qualityResp.status === 'fulfilled') setQuality(qualityResp.value)
    } catch {
      if (!silent) {
        showToast.error('Failed to load TOMIC dashboard', 'monitoring')
      }
    } finally {
      if (silent) setRefreshing(false)
      else setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData(false)
    const timer = setInterval(() => {
      void loadData(true)
    }, 5000)
    return () => clearInterval(timer)
  }, [loadData])

  const executeAction = useCallback(
    async (action: ActionKind) => {
      setActionBusy(action)
      try {
        if (action === 'start') await tomicApi.start()
        if (action === 'pause') await tomicApi.pause('Paused from dashboard')
        if (action === 'resume') await tomicApi.resume()
        if (action === 'stop') await tomicApi.stop()
        showToast.success(`TOMIC ${action} requested`, 'monitoring')
        await loadData(true)
      } catch {
        showToast.error(`Failed to ${action} TOMIC`, 'monitoring')
      } finally {
        setActionBusy(null)
      }
    },
    [loadData]
  )

  const runtime = status?.data
  const loop = runtime?.signal_loop
  const agentRows = useMemo(
    () => Object.entries(runtime?.agents ?? {}),
    [runtime?.agents]
  )

  const openPositions = positions?.positions ?? []
  const totalPnl = useMemo(
    () => openPositions.reduce((sum, pos) => sum + (Number(pos.pnl) || 0), 0),
    [openPositions]
  )

  const expectancy = resolveMetric(analytics?.metrics, ['expectancy', 'expectancy_30', 'rolling_expectancy'])
  const winRate = resolveMetric(analytics?.metrics, ['win_rate', 'winrate'])
  const maxDrawdown = resolveMetric(analytics?.metrics, ['max_drawdown', 'max_dd'])
  const sharpe = resolveMetric(analytics?.metrics, ['sharpe', 'sharpe_ratio'])
  const signalStats = quality?.data?.signals
  const signalFeedWs = quality?.data?.feed?.ws ?? {}
  const signalFeedBridge = quality?.data?.feed?.bridge ?? {}
  const noActionReasons = quality?.data?.diagnostics?.no_action_reasons ?? []
  const wsConnected = Boolean(signalFeedWs['connected'])
  const wsAuth = signalFeedWs['authenticated'] == null ? true : Boolean(signalFeedWs['authenticated'])
  const feedHealthy = wsConnected && wsAuth
  const bridgeTickAge = Number(signalFeedBridge['last_tick_age_s'] ?? -1)

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary" />
      </div>
    )
  }

  return (
    <div className="space-y-6 py-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Activity className="h-6 w-6" />
            TOMIC Dashboard
          </h1>
          <p className="text-muted-foreground mt-1">
            Runtime health, queue state, and high-level performance.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => void loadData(true)} disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button onClick={() => void executeAction('start')} disabled={actionBusy !== null}>
            <PlayCircle className="h-4 w-4 mr-2" />
            {actionBusy === 'start' ? 'Starting...' : 'Start'}
          </Button>
          <Button variant="secondary" onClick={() => void executeAction('pause')} disabled={actionBusy !== null}>
            <PauseCircle className="h-4 w-4 mr-2" />
            {actionBusy === 'pause' ? 'Pausing...' : 'Pause'}
          </Button>
          <Button variant="secondary" onClick={() => void executeAction('resume')} disabled={actionBusy !== null}>
            <PlayCircle className="h-4 w-4 mr-2" />
            {actionBusy === 'resume' ? 'Resuming...' : 'Resume'}
          </Button>
          <Button variant="destructive" onClick={() => void executeAction('stop')} disabled={actionBusy !== null}>
            <Square className="h-4 w-4 mr-2" />
            {actionBusy === 'stop' ? 'Stopping...' : 'Stop'}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>TOMIC Routes</CardTitle>
          <CardDescription>
            Quick navigation for all dashboard pages and read-only JSON endpoints.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {TOMIC_PAGE_LINKS.map((item) => (
              <Button key={item.href} asChild variant="outline" size="sm">
                <Link to={item.href}>{item.label}</Link>
              </Button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {TOMIC_DATA_LINKS.map((item) => (
              <Button key={item.href} asChild variant="secondary" size="sm">
                <a href={item.href} target="_blank" rel="noreferrer">
                  {item.label}
                </a>
              </Button>
            ))}
          </div>
          <div className="text-xs text-muted-foreground">
            Control routes are POST-only and mapped to the action buttons above:
            <code className="ml-1">/tomic/start</code>,
            <code className="ml-1">/tomic/pause</code>,
            <code className="ml-1">/tomic/resume</code>,
            <code className="ml-1">/tomic/stop</code>.
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Runtime</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <Badge variant={runtime?.running ? 'default' : 'secondary'}>
              {runtime?.running ? 'RUNNING' : 'OFFLINE'}
            </Badge>
            {runtime?.killed && (
              <div className="text-xs text-red-500 flex items-center gap-1">
                <ShieldAlert className="h-3.5 w-3.5" />
                Kill switch active
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Queue Pending</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{formatNumber(runtime?.command_queue_pending ?? 0)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Dead Letters</CardTitle>
          </CardHeader>
          <CardContent>
            <p className={`text-2xl font-bold ${(runtime?.command_dead_letters ?? 0) > 0 ? 'text-red-500' : ''}`}>
              {formatNumber(runtime?.command_dead_letters ?? 0)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Open Positions</CardTitle>
            <CardDescription>Net P&L: {totalPnl >= 0 ? '+' : ''}{formatNumber(totalPnl)}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{formatNumber(openPositions.length)}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Signal Loop</CardTitle>
            <CardDescription>{loop?.enabled ? 'Enabled' : 'Disabled'}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1">
            <Badge variant={loop?.running ? 'default' : 'secondary'}>
              {loop?.running ? 'RUNNING' : 'STOPPED'}
            </Badge>
            <div className="text-xs text-muted-foreground">
              interval: {formatNumber(loop?.interval_s)}s
            </div>
            <div className="text-xs text-muted-foreground">
              cooldown: {formatNumber(loop?.enqueue_cooldown_s)}s
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Signals Routed</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{formatNumber(signalStats?.routed_count ?? 0)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Enqueued This Cycle</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{formatNumber(signalStats?.enqueued_count ?? loop?.last_enqueued ?? 0)}</p>
            <p className="text-xs text-muted-foreground">dedupe skips: {formatNumber(signalStats?.dedupe_skipped_count ?? loop?.last_dedupe_skips ?? 0)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Feed Health</CardTitle>
            <CardDescription>
              tick age: {formatNumber(bridgeTickAge)}s
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Badge variant={feedHealthy ? 'default' : 'destructive'}>
              {feedHealthy ? 'LIVE' : 'STALE/OFFLINE'}
            </Badge>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Expectancy</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{formatNumber(expectancy)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Win Rate %</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{formatNumber(winRate)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Max Drawdown</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{formatNumber(maxDrawdown)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Sharpe</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xl font-semibold">{formatNumber(sharpe)}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Signal Quality</CardTitle>
          <CardDescription>
            Generated: {quality?.data?.generated_at || '—'} | Accept rate: {formatNumber(signalStats?.routed_accept_rate_pct)}%
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Top Routed</p>
            {(signalStats?.top_routed ?? []).slice(0, 5).map((row, idx) => (
              <div key={`routed-${idx}`} className="text-xs text-muted-foreground">
                {String(row.instrument ?? '—')} | {String(row.strategy_type ?? '—')} | {String(row.direction ?? '—')}
              </div>
            ))}
            {(signalStats?.top_routed ?? []).length === 0 && (
              <div className="text-xs text-muted-foreground">No routed signals yet.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Top Sniper</p>
            {(signalStats?.top_sniper ?? []).slice(0, 5).map((row, idx) => (
              <div key={`sniper-${idx}`} className="text-xs text-muted-foreground">
                {String(row.instrument ?? '—')} | {String(row.pattern ?? '—')} | {String(row.direction ?? '—')}
              </div>
            ))}
            {(signalStats?.top_sniper ?? []).length === 0 && (
              <div className="text-xs text-muted-foreground">No sniper signals yet.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Top Volatility</p>
            {(signalStats?.top_volatility ?? []).slice(0, 5).map((row, idx) => (
              <div key={`vol-${idx}`} className="text-xs text-muted-foreground">
                {String(row.underlying ?? '—')} | {String(row.strategy_type ?? '—')} | {String(row.direction ?? '—')}
              </div>
            ))}
            {(signalStats?.top_volatility ?? []).length === 0 && (
              <div className="text-xs text-muted-foreground">No volatility signals yet.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Why No Action</CardTitle>
          <CardDescription>
            Real-time blockers from feed, router, and risk pipeline.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="text-xs text-muted-foreground">
            ws_auth_status: {String(signalFeedWs['last_auth_status'] ?? '—')} | ws_auth_message: {String(signalFeedWs['last_auth_message'] ?? '—')}
          </div>
          {noActionReasons.map((reason, idx) => (
            <div key={`${reason}-${idx}`} className="text-sm text-muted-foreground">
              {idx + 1}. {reason}
            </div>
          ))}
          {noActionReasons.length === 0 && (
            <div className="text-sm text-muted-foreground">No blocking reason recorded in latest cycle.</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Agent Health</CardTitle>
          <CardDescription>Heartbeat and restart counters from Supervisor.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last Heartbeat (s)</TableHead>
                  <TableHead>Restarts</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {agentRows.map(([name, agent]) => (
                  <TableRow key={name}>
                    <TableCell className="font-medium">{name}</TableCell>
                    <TableCell>
                      <Badge variant={agent.running ? 'default' : 'secondary'}>
                        {agent.running ? (agent.paused ? 'PAUSED' : 'RUNNING') : 'STOPPED'}
                      </Badge>
                    </TableCell>
                    <TableCell>{formatNumber(agent.last_heartbeat_ago_s)}</TableCell>
                    <TableCell>{formatNumber(agent.restarts)}</TableCell>
                  </TableRow>
                ))}
                {agentRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="text-muted-foreground">
                      No agent status data available.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
