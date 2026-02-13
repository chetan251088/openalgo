import { AlertTriangle, RefreshCw, Shield } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  tomicApi,
  type TomicMetricsResponse,
  type TomicPositionsResponse,
  type TomicSignalQualityResponse,
  type TomicStatusResponse,
} from '@/api/tomic'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { showToast } from '@/utils/toast'

type ExposureRow = {
  instrument: string
  quantity: number
  notional: number
  pnl: number
  strategies: Set<string>
}

function formatNumber(value: number): string {
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function renderUnknownValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'string') {
    return String(value)
  }
  return JSON.stringify(value)
}

export default function TomicRisk() {
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [positions, setPositions] = useState<TomicPositionsResponse | null>(null)
  const [metrics, setMetrics] = useState<TomicMetricsResponse | null>(null)
  const [status, setStatus] = useState<TomicStatusResponse | null>(null)
  const [quality, setQuality] = useState<TomicSignalQualityResponse | null>(null)

  const loadData = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    try {
      const [positionsResp, metricsResp, statusResp, qualityResp] = await Promise.allSettled([
        tomicApi.getPositions(),
        tomicApi.getMetrics(),
        tomicApi.getStatus(),
        tomicApi.getSignalQuality(false),
      ])
      if (positionsResp.status === 'fulfilled') setPositions(positionsResp.value)
      if (metricsResp.status === 'fulfilled') setMetrics(metricsResp.value)
      if (statusResp.status === 'fulfilled') setStatus(statusResp.value)
      if (qualityResp.status === 'fulfilled') setQuality(qualityResp.value)
    } catch {
      if (!silent) {
        showToast.error('Failed to load TOMIC risk cockpit', 'monitoring')
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

  const openPositions = positions?.positions ?? []

  const exposureRows = useMemo(() => {
    const map = new Map<string, ExposureRow>()
    for (const pos of openPositions) {
      const key = pos.instrument || 'UNKNOWN'
      const existing = map.get(key) ?? {
        instrument: key,
        quantity: 0,
        notional: 0,
        pnl: 0,
        strategies: new Set<string>(),
      }
      existing.quantity += Number(pos.quantity) || 0
      existing.notional += Math.abs((Number(pos.quantity) || 0) * (Number(pos.avg_price) || 0))
      existing.pnl += Number(pos.pnl) || 0
      if (pos.strategy_id) existing.strategies.add(pos.strategy_id)
      map.set(key, existing)
    }
    return Array.from(map.values()).sort((a, b) => b.notional - a.notional)
  }, [openPositions])

  const grossNotional = useMemo(
    () => exposureRows.reduce((sum, row) => sum + row.notional, 0),
    [exposureRows]
  )
  const totalPnl = useMemo(
    () => exposureRows.reduce((sum, row) => sum + row.pnl, 0),
    [exposureRows]
  )

  const circuitBreakers = metrics?.data?.circuit_breakers ?? {}
  const freshness = metrics?.data?.freshness ?? {}
  const wsData = metrics?.data?.ws_data ?? {}
  const marketBridge = metrics?.data?.market_bridge ?? {}
  const loop = status?.data?.signal_loop
  const signalStats = quality?.data?.signals
  const riskCounters = quality?.data?.risk?.counters ?? {}
  const riskRecent = quality?.data?.risk?.recent_evaluations ?? []

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
            <Shield className="h-6 w-6" />
            TOMIC Risk Cockpit
          </h1>
          <p className="text-muted-foreground mt-1">
            Circuit breaker state, feed freshness, and live portfolio exposure.
          </p>
        </div>
        <Button variant="outline" onClick={() => void loadData(true)} disabled={refreshing}>
          <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Gross Notional</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{formatNumber(grossNotional)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Open Positions</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{openPositions.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Portfolio P&L</CardTitle>
          </CardHeader>
          <CardContent>
            <p className={`text-2xl font-bold ${totalPnl < 0 ? 'text-red-500' : totalPnl > 0 ? 'text-green-500' : ''}`}>
              {totalPnl >= 0 ? '+' : ''}{formatNumber(totalPnl)}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Signal Loop State</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <Badge variant={loop?.running ? 'default' : 'secondary'}>
              {loop?.running ? 'RUNNING' : 'STOPPED'}
            </Badge>
            <div className="text-xs text-muted-foreground">
              last error: {loop?.last_error || 'none'}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Enqueued / Dedupe</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{formatNumber(Number(signalStats?.enqueued_count ?? 0))}</p>
            <p className="text-xs text-muted-foreground">
              dedupe skips: {formatNumber(Number(signalStats?.dedupe_skipped_count ?? 0))}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Bridge Tick Age</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">
              {renderUnknownValue(marketBridge['last_tick_age_s'] ?? '—')}s
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Risk Pipeline Counters</CardTitle>
          <CardDescription>Latest outcome counts from Risk Agent evaluation loop.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm text-muted-foreground">
          <div>evaluated: {renderUnknownValue(riskCounters['evaluated'])}</div>
          <div>blocked_regime: {renderUnknownValue(riskCounters['blocked_regime'])}</div>
          <div>rejected_sizing: {renderUnknownValue(riskCounters['rejected_sizing'])}</div>
          <div>enqueued: {renderUnknownValue(riskCounters['enqueued'])}</div>
          <div>duplicate: {renderUnknownValue(riskCounters['duplicate'])}</div>
          <div>latest_result: {renderUnknownValue(riskRecent[0]?.['result'])}</div>
          <div>latest_reason: {renderUnknownValue(riskRecent[0]?.['reason'])}</div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5" />
              Circuit Breakers
            </CardTitle>
            <CardDescription>Supervisor-level hard stops and thresholds.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="border rounded-md">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Breaker</TableHead>
                    <TableHead>State</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(circuitBreakers).map(([key, value]) => (
                    <TableRow key={key}>
                      <TableCell>{key}</TableCell>
                      <TableCell>{renderUnknownValue(value)}</TableCell>
                    </TableRow>
                  ))}
                  {Object.keys(circuitBreakers).length === 0 && (
                    <TableRow>
                      <TableCell colSpan={2} className="text-muted-foreground">
                        No circuit breaker metrics available.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Feed & Freshness</CardTitle>
            <CardDescription>Market-data and analytics staleness diagnostics.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <h3 className="font-medium mb-2">Freshness</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(freshness).map(([key, value]) => (
                  <Badge key={key} variant="outline">
                    {key}: {renderUnknownValue(value)}
                  </Badge>
                ))}
                {Object.keys(freshness).length === 0 && (
                  <span className="text-sm text-muted-foreground">No freshness diagnostics available.</span>
                )}
              </div>
            </div>
            <div>
              <h3 className="font-medium mb-2">WS Data</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(wsData).map(([key, value]) => (
                  <Badge key={key} variant="secondary">
                    {key}: {renderUnknownValue(value)}
                  </Badge>
                ))}
                {Object.keys(wsData).length === 0 && (
                  <span className="text-sm text-muted-foreground">No WebSocket status available.</span>
                )}
              </div>
            </div>
            <div>
              <h3 className="font-medium mb-2">Market Bridge</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(marketBridge).map(([key, value]) => (
                  <Badge key={key} variant="secondary">
                    {key}: {renderUnknownValue(value)}
                  </Badge>
                ))}
                {Object.keys(marketBridge).length === 0 && (
                  <span className="text-sm text-muted-foreground">No market bridge status available.</span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Exposure by Instrument</CardTitle>
          <CardDescription>Aggregated notional and P&L across active positions.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Instrument</TableHead>
                  <TableHead>Quantity</TableHead>
                  <TableHead>Notional</TableHead>
                  <TableHead>P&L</TableHead>
                  <TableHead>Strategies</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {exposureRows.map((row) => (
                  <TableRow key={row.instrument}>
                    <TableCell className="font-medium">{row.instrument}</TableCell>
                    <TableCell>{formatNumber(row.quantity)}</TableCell>
                    <TableCell>{formatNumber(row.notional)}</TableCell>
                    <TableCell className={row.pnl < 0 ? 'text-red-500' : row.pnl > 0 ? 'text-green-500' : ''}>
                      {row.pnl >= 0 ? '+' : ''}{formatNumber(row.pnl)}
                    </TableCell>
                    <TableCell>{row.strategies.size > 0 ? Array.from(row.strategies).slice(0, 2).join(', ') : '—'}</TableCell>
                  </TableRow>
                ))}
                {exposureRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground">
                      No open positions.
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
