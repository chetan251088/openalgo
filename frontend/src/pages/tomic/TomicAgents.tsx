import { BrainCircuit, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  tomicApi,
  type TomicAuditEntry,
  type TomicSignalQualityResponse,
  type TomicStatusResponse,
} from '@/api/tomic'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { showToast } from '@/utils/toast'

function asString(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return '—'
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function formatTs(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function asFixed(value: unknown, digits = 2): string {
  const num = asNumber(value)
  if (num == null) return '—'
  return num.toFixed(digits)
}

export default function TomicAgents() {
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [status, setStatus] = useState<TomicStatusResponse | null>(null)
  const [trades, setTrades] = useState<Array<Record<string, unknown>>>([])
  const [auditEntries, setAuditEntries] = useState<TomicAuditEntry[]>([])
  const [quality, setQuality] = useState<TomicSignalQualityResponse | null>(null)

  const loadData = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)
    try {
      const [statusResp, journalResp, auditResp, qualityResp] = await Promise.allSettled([
        tomicApi.getStatus(),
        tomicApi.getJournal(60),
        tomicApi.getAudit(80),
        tomicApi.getSignalQuality(false),
      ])
      if (statusResp.status === 'fulfilled') setStatus(statusResp.value)
      if (journalResp.status === 'fulfilled') setTrades(journalResp.value.trades ?? [])
      if (auditResp.status === 'fulfilled') setAuditEntries(auditResp.value.entries ?? [])
      if (qualityResp.status === 'fulfilled') setQuality(qualityResp.value)
    } catch {
      if (!silent) showToast.error('Failed to load TOMIC agent view', 'monitoring')
    } finally {
      if (silent) setRefreshing(false)
      else setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData(false)
    const timer = setInterval(() => {
      void loadData(true)
    }, 6000)
    return () => clearInterval(timer)
  }, [loadData])

  const agentRows = useMemo(
    () => Object.entries(status?.data?.agents ?? {}),
    [status?.data?.agents]
  )
  const decisionBreakdown = quality?.data?.signals?.decision_breakdown ?? {}
  const topRouted = quality?.data?.signals?.top_routed ?? []
  const enqueuedKeys = quality?.data?.signals?.enqueued_keys ?? []
  const routerDecisions = quality?.data?.signals?.router_decisions ?? []
  const blockingReasons = quality?.data?.router?.blocking_reasons ?? {}
  const noActionReasons = quality?.data?.diagnostics?.no_action_reasons ?? []
  const ws = quality?.data?.feed?.ws ?? {}
  const riskRecent = quality?.data?.risk?.recent_evaluations ?? []
  const riskCounters = quality?.data?.risk?.counters ?? {}
  const sniperReadiness = quality?.data?.agent_inputs?.sniper_readiness ?? []
  const volReadiness = quality?.data?.agent_inputs?.volatility_readiness ?? []
  const volSnapshots = quality?.data?.agent_inputs?.volatility_snapshots ?? []

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
            <BrainCircuit className="h-6 w-6" />
            TOMIC Agents
          </h1>
          <p className="text-muted-foreground mt-1">
            Live health, decision traces, and control audit entries.
          </p>
        </div>
        <Button variant="outline" onClick={() => void loadData(true)} disabled={refreshing}>
          <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Agent Health Matrix</CardTitle>
          <CardDescription>Supervisor heartbeat monitor across all agents.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Paused</TableHead>
                  <TableHead>Heartbeat (s)</TableHead>
                  <TableHead>Restarts</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {agentRows.map(([name, agent]) => (
                  <TableRow key={name}>
                    <TableCell className="font-medium">{name}</TableCell>
                    <TableCell>
                      <Badge variant={agent.running ? 'default' : 'secondary'}>
                        {agent.running ? 'RUNNING' : 'STOPPED'}
                      </Badge>
                    </TableCell>
                    <TableCell>{agent.paused ? 'Yes' : 'No'}</TableCell>
                    <TableCell>{agent.last_heartbeat_ago_s.toFixed(1)}</TableCell>
                    <TableCell>{agent.restarts}</TableCell>
                  </TableRow>
                ))}
                {agentRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground">
                      No agent telemetry available.
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
          <CardTitle>Signal Router Snapshot</CardTitle>
          <CardDescription>
            Last generated: {quality?.data?.generated_at || '—'}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Decision Breakdown</p>
            {Object.entries(decisionBreakdown).map(([key, value]) => (
              <div key={key} className="text-xs text-muted-foreground">
                {key}: {String(value)}
              </div>
            ))}
            {Object.keys(decisionBreakdown).length === 0 && (
              <div className="text-xs text-muted-foreground">No router decisions yet.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Top Routed</p>
            {topRouted.slice(0, 6).map((row, idx) => (
              <div key={`route-${idx}`} className="text-xs text-muted-foreground">
                {String(row.instrument ?? '—')} | {String(row.strategy_type ?? '—')} | {String(row.direction ?? '—')}
              </div>
            ))}
            {topRouted.length === 0 && (
              <div className="text-xs text-muted-foreground">No routed entries yet.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Recent Enqueued Keys</p>
            {enqueuedKeys.slice(0, 6).map((key, idx) => (
              <div key={`${key}-${idx}`} className="text-xs text-muted-foreground">
                {String(key)}
              </div>
            ))}
            {enqueuedKeys.length === 0 && (
              <div className="text-xs text-muted-foreground">No auto-enqueue this cycle.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Live Pipeline Debug</CardTitle>
          <CardDescription>Feed auth state, block reasons, and why no action happened.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 xl:grid-cols-3">
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Feed WS Status</p>
            <div className="text-xs text-muted-foreground">connected: {asString(ws.connected)}</div>
            <div className="text-xs text-muted-foreground">authenticated: {asString(ws.authenticated)}</div>
            <div className="text-xs text-muted-foreground">last_auth_status: {asString(ws.last_auth_status)}</div>
            <div className="text-xs text-muted-foreground">last_auth_message: {asString(ws.last_auth_message)}</div>
            <div className="text-xs text-muted-foreground">last_error: {asString(ws.last_error)}</div>
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Router Blocking Reasons</p>
            {Object.entries(blockingReasons).map(([reason, count]) => (
              <div key={reason} className="text-xs text-muted-foreground">
                {count}x - {reason}
              </div>
            ))}
            {Object.keys(blockingReasons).length === 0 && (
              <div className="text-xs text-muted-foreground">No router blocks this cycle.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">No-Action Reasons</p>
            {noActionReasons.map((reason, idx) => (
              <div key={`${reason}-${idx}`} className="text-xs text-muted-foreground">
                {idx + 1}. {reason}
              </div>
            ))}
            {noActionReasons.length === 0 && (
              <div className="text-xs text-muted-foreground">No blocking reason recorded.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Router Decisions (Latest Cycle)</CardTitle>
          <CardDescription>Every routed/deferred/rejected decision with reason.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Instrument</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {routerDecisions.slice(0, 40).map((row, idx) => (
                  <TableRow key={`route-decision-${idx}`}>
                    <TableCell>{asString(row.source)}</TableCell>
                    <TableCell>{asString(row.instrument)}</TableCell>
                    <TableCell>{asString(row.strategy_type)}</TableCell>
                    <TableCell>{asString(row.action)}</TableCell>
                    <TableCell>{asFixed(row.priority_score)}</TableCell>
                    <TableCell className="max-w-[420px] truncate">{asString(row.reason)}</TableCell>
                  </TableRow>
                ))}
                {routerDecisions.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">
                      No router decision trace available yet.
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
          <CardTitle>Risk Evaluation Trace</CardTitle>
          <CardDescription>What Risk Agent did with routed signals, including lots and reject reason.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Risk Counters</p>
            <div className="text-xs text-muted-foreground">
              evaluated={asString(riskCounters.evaluated)} | blocked_regime={asString(riskCounters.blocked_regime)} | rejected_sizing={asString(riskCounters.rejected_sizing)} | enqueued={asString(riskCounters.enqueued)} | duplicate={asString(riskCounters.duplicate)}
            </div>
          </div>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Instrument</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead>Lots</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {riskRecent.slice(0, 40).map((row, idx) => (
                  <TableRow key={`risk-eval-${idx}`}>
                    <TableCell>{formatTs(row.at)}</TableCell>
                    <TableCell>{asString(row.instrument)}</TableCell>
                    <TableCell>{asString(row.strategy_type)}</TableCell>
                    <TableCell>{asString(row.result)}</TableCell>
                    <TableCell>{asString(row.final_lots)}</TableCell>
                    <TableCell className="max-w-[420px] truncate">{asString(row.reason)}</TableCell>
                  </TableRow>
                ))}
                {riskRecent.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">
                      No risk evaluation trace yet.
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
          <CardTitle>Options Calc Readiness</CardTitle>
          <CardDescription>Real-time readiness of sniper bars and volatility IV/HV calculations.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 xl:grid-cols-3">
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Sniper Readiness</p>
            {sniperReadiness.map((row, idx) => (
              <div key={`sniper-ready-${idx}`} className="text-xs text-muted-foreground">
                {asString(row.instrument)} | bars={asString(row.bars)} | ready30={asString(row.ready_30)} | zones={asString(row.zones_cached)}
              </div>
            ))}
            {sniperReadiness.length === 0 && (
              <div className="text-xs text-muted-foreground">No sniper readiness rows.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Volatility Readiness</p>
            {volReadiness.map((row, idx) => (
              <div key={`vol-ready-${idx}`} className="text-xs text-muted-foreground">
                {asString(row.underlying)} | price_bars={asString(row.price_bars)} | has_iv={asString(row.has_iv)} | hv_ready31={asString(row.hv_ready_31)}
              </div>
            ))}
            {volReadiness.length === 0 && (
              <div className="text-xs text-muted-foreground">No volatility readiness rows.</div>
            )}
          </div>
          <div className="rounded-md border p-3">
            <p className="text-sm font-medium mb-2">Volatility Snapshots</p>
            {volSnapshots.map((row, idx) => (
              <div key={`vol-snap-${idx}`} className="text-xs text-muted-foreground">
                {asString(row.underlying)} | IV={asFixed(row.iv, 3)} | HV={asFixed(row.hv, 3)} | IVR={asFixed(row.iv_rank, 1)} | {asString(row.vol_regime)}
              </div>
            ))}
            {volSnapshots.length === 0 && (
              <div className="text-xs text-muted-foreground">No volatility snapshot yet.</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent Strategy Decisions</CardTitle>
          <CardDescription>Recent journal rows from TOMIC trade lifecycle.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Instrument</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead>Direction</TableHead>
                  <TableHead>P&L</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.slice(0, 30).map((trade, idx) => {
                  const pnl = asNumber(trade.pnl ?? trade.realized_pnl ?? trade.net_pnl)
                  return (
                    <TableRow key={`${asString(trade.event_id)}-${idx}`}>
                      <TableCell>{formatTs(trade.timestamp ?? trade.entry_time ?? trade.created_at)}</TableCell>
                      <TableCell>{asString(trade.instrument ?? trade.underlying)}</TableCell>
                      <TableCell>{asString(trade.strategy_type ?? trade.strategy_id ?? trade.strategy)}</TableCell>
                      <TableCell>{asString(trade.direction)}</TableCell>
                      <TableCell className={pnl != null && pnl < 0 ? 'text-red-500' : pnl != null && pnl > 0 ? 'text-green-500' : ''}>
                        {pnl == null ? '—' : pnl.toFixed(2)}
                      </TableCell>
                      <TableCell className="max-w-[280px] truncate">
                        {asString(trade.reason ?? trade.entry_reason ?? trade.exit_reason)}
                      </TableCell>
                    </TableRow>
                  )
                })}
                {trades.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">
                      No journal activity yet.
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
          <CardTitle>Control Audit Trail</CardTitle>
          <CardDescription>Operator actions and control-plane history.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border rounded-md">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>User</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Details</TableHead>
                  <TableHead>IP</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {auditEntries.slice(0, 40).map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell>{formatTs(entry.timestamp)}</TableCell>
                    <TableCell>{entry.user_id || '—'}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{entry.action}</Badge>
                    </TableCell>
                    <TableCell className="max-w-[360px] truncate">{entry.details || '—'}</TableCell>
                    <TableCell>{entry.ip_address || '—'}</TableCell>
                  </TableRow>
                ))}
                {auditEntries.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground">
                      No audit entries found.
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
