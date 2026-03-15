import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { webClient } from '@/api/client'
import { useIntelligence } from '@/hooks/useIntelligence'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import {
  Activity,
  AlertTriangle,
  ArrowRightLeft,
  BarChart3,
  Brain,
  CheckCircle2,
  Globe,
  Layers,
  Monitor,
  Power,
  RefreshCw,
  Shield,
  TrendingDown,
  TrendingUp,
  Minus,
  Wifi,
  WifiOff,
  Zap,
  XCircle,
} from 'lucide-react'

interface ServiceHealth {
  name: string
  url: string
  status: 'online' | 'offline' | 'degraded' | 'checking'
  latencyMs: number | null
  lastCheck: number
}

interface TomicStatus {
  running: boolean
  mode: string
  agentCount: number
  activePositions: number
  dailyPnl: number
  circuitBreakersTripped: number
  deadLetters: number
  signalQuality: string
}

interface ScalpingStatus {
  autoEnabled: boolean
  mode: string
  regime: string
  tradesCount: number
  realizedPnl: number
  killSwitch: boolean
  ghostSignals: number
}

interface PositionSummary {
  broker: string
  totalPositions: number
  optionPositions: number
  totalPnl: number
  marginUsed: number
}

const SERVICES = [
  { name: 'OpenAlgo (Kotak)', url: 'http://localhost:5000', key: 'kotak' },
  { name: 'OpenAlgo (Dhan)', url: 'http://localhost:5001', key: 'dhan' },
  { name: 'OpenAlgo (Zerodha)', url: 'http://localhost:5002', key: 'zerodha' },
  { name: 'MiroFish API', url: 'http://localhost:5003', key: 'mirofish' },
  { name: 'Sector Rotation', url: 'http://localhost:8000', key: 'rotation' },
  { name: 'WS Proxy (Kotak)', url: 'ws://localhost:8765', key: 'ws_kotak' },
  { name: 'WS Proxy (Dhan)', url: 'ws://localhost:8766', key: 'ws_dhan' },
  { name: 'WS Proxy (Zerodha)', url: 'ws://localhost:8767', key: 'ws_zerodha' },
]

export default function CommandCenter() {
  const [services, setServices] = useState<Record<string, ServiceHealth>>({})
  const [tomicStatus, setTomicStatus] = useState<TomicStatus | null>(null)
  const [positions, setPositions] = useState<PositionSummary[]>([])
  const [alerts, setAlerts] = useState<Array<{ time: string; level: string; message: string }>>([])
  const [refreshing, setRefreshing] = useState(false)
  const [lastFullRefresh, setLastFullRefresh] = useState(0)
  const [killSwitchActive, setKillSwitchActive] = useState(false)

  const { mirofish, rotation, fundamentals, refreshIntelligence } = useIntelligence(true)
  const autoEnabled = useAutoTradeStore((state) => state.enabled)
  const autoMode = useAutoTradeStore((state) => state.mode)
  const regime = useAutoTradeStore((state) => state.regime)
  const tradesCount = useAutoTradeStore((state) => state.tradesCount)
  const realizedPnl = useAutoTradeStore((state) => state.realizedPnl)
  const scalpingKillSwitch = useAutoTradeStore((state) => state.killSwitch)
  const ghostSignalCount = useAutoTradeStore((state) => state.ghostSignals.length)

  const scalpingStatus: ScalpingStatus = {
    autoEnabled,
    mode: autoMode,
    regime,
    tradesCount,
    realizedPnl,
    killSwitch: scalpingKillSwitch,
    ghostSignals: ghostSignalCount,
  }

  const checkServiceHealth = useCallback(async () => {
    const results: Record<string, ServiceHealth> = {}

    for (const svc of SERVICES) {
      if (svc.url.startsWith('ws://')) {
        results[svc.key] = {
          name: svc.name, url: svc.url,
          status: 'online', latencyMs: null, lastCheck: Date.now(),
        }
        continue
      }

      const start = performance.now()
      try {
        const healthUrl = svc.key === 'mirofish'
          ? `${svc.url}/health`
          : svc.key === 'rotation'
            ? `${svc.url}/api/health`
            : `${svc.url}/health`

        const resp = await fetch(healthUrl, { signal: AbortSignal.timeout(3000) })
        const latency = Math.round(performance.now() - start)
        results[svc.key] = {
          name: svc.name, url: svc.url,
          status: resp.ok ? 'online' : 'degraded',
          latencyMs: latency, lastCheck: Date.now(),
        }
      } catch {
        results[svc.key] = {
          name: svc.name, url: svc.url,
          status: 'offline', latencyMs: null, lastCheck: Date.now(),
        }
      }
    }

    setServices(results)
  }, [])

  const fetchTomicStatus = useCallback(async () => {
    try {
      const [statusResp, metricsResp] = await Promise.allSettled([
        webClient.get('/tomic/status'),
        webClient.get('/tomic/metrics'),
      ])

      const status = statusResp.status === 'fulfilled' ? statusResp.value.data : null
      const metrics = metricsResp.status === 'fulfilled' ? metricsResp.value.data : null

      if (status?.data) {
        setTomicStatus({
          running: status.data.status === 'running',
          mode: status.data.mode || 'unknown',
          agentCount: status.data.agents?.length || 0,
          activePositions: status.data.positions?.active || 0,
          dailyPnl: status.data.pnl?.daily || 0,
          circuitBreakersTripped: metrics?.data?.circuit_breakers?.breakers
            ? Object.values(metrics.data.circuit_breakers.breakers as Record<string, any>).filter((b: any) => b.tripped).length
            : 0,
          deadLetters: status.data.queue?.dead_letters || 0,
          signalQuality: status.data.signal_quality?.verdict || 'unknown',
        })
      }
    } catch {
      // TOMIC endpoints may not exist yet
    }
  }, [])

  const fetchPositions = useCallback(async () => {
    // For now, fetch from the local instance
    try {
      const resp = await webClient.get('/tomic/positions')
      if (resp.data?.status === 'success') {
        setPositions([{
          broker: 'Primary',
          totalPositions: resp.data.positions?.length || 0,
          optionPositions: resp.data.positions?.filter((p: any) =>
            p.symbol?.includes('CE') || p.symbol?.includes('PE')
          ).length || 0,
          totalPnl: resp.data.positions?.reduce((sum: number, p: any) => sum + (p.pnl || 0), 0) || 0,
          marginUsed: 0,
        }])
      }
    } catch {
      // positions endpoint may not be available
    }
  }, [])

  const fullRefresh = useCallback(async () => {
    setRefreshing(true)
    await Promise.all([
      checkServiceHealth(),
      fetchTomicStatus(),
      fetchPositions(),
      refreshIntelligence(),
    ])
    setLastFullRefresh(Date.now())
    setRefreshing(false)
  }, [checkServiceHealth, fetchTomicStatus, fetchPositions, refreshIntelligence])

  useEffect(() => {
    fullRefresh()
    const interval = setInterval(() => {
      checkServiceHealth()
      fetchTomicStatus()
    }, 15_000)
    return () => clearInterval(interval)
  }, [fullRefresh, checkServiceHealth, fetchTomicStatus])

  // Derive alerts from intelligence and TOMIC status
  useEffect(() => {
    const newAlerts: typeof alerts = []
    const now = new Date().toLocaleTimeString('en-IN', { hour12: false })

    if (rotation?.transitions && rotation.transitions.length > 0) {
      for (const t of rotation.transitions) {
        newAlerts.push({
          time: now, level: 'warning',
          message: `Sector ${t.symbol} rotated: ${t.from_quadrant} → ${t.to_quadrant}`,
        })
      }
    }

    if (mirofish?.confidence !== undefined && mirofish.confidence < 0.3) {
      newAlerts.push({
        time: now, level: 'warning',
        message: `MiroFish confidence low: ${(mirofish.confidence * 100).toFixed(0)}% — consider tightening stops`,
      })
    }

    if (tomicStatus?.deadLetters && tomicStatus.deadLetters > 0) {
      newAlerts.push({
        time: now, level: 'error',
        message: `${tomicStatus.deadLetters} dead-lettered commands in TOMIC queue`,
      })
    }

    if (tomicStatus?.circuitBreakersTripped && tomicStatus.circuitBreakersTripped > 0) {
      newAlerts.push({
        time: now, level: 'error',
        message: `${tomicStatus.circuitBreakersTripped} circuit breaker(s) tripped`,
      })
    }

    if (newAlerts.length > 0) {
      setAlerts(prev => [...newAlerts, ...prev].slice(0, 50))
    }
  }, [rotation, mirofish, tomicStatus])

  const onlineCount = Object.values(services).filter(s => s.status === 'online').length
  const totalServices = SERVICES.length
  const positionSummary = positions[0] ?? null

  const biasColor = mirofish?.bias === 'BULLISH' ? 'text-green-500' : mirofish?.bias === 'BEARISH' ? 'text-red-500' : 'text-gray-400'
  const biasIcon = mirofish?.bias === 'BULLISH' ? <TrendingUp className="h-5 w-5" /> : mirofish?.bias === 'BEARISH' ? <TrendingDown className="h-5 w-5" /> : <Minus className="h-5 w-5" />

  return (
    <div className="min-h-screen bg-background p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Globe className="h-7 w-7 text-primary" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Command Center</h1>
            <p className="text-sm text-muted-foreground">
              Unified monitoring across MiroFish + OpenAlgo + Sector Rotation + OpenScreener
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            Last refresh: {lastFullRefresh ? new Date(lastFullRefresh).toLocaleTimeString() : 'never'}
          </span>
          <Button size="sm" onClick={fullRefresh} disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh All
          </Button>
        </div>
      </div>

      {/* Row 1: Service Health + Intelligence Summary */}
      <div className="grid grid-cols-12 gap-4">
        {/* Service Health Grid */}
        <Card className="col-span-12 xl:col-span-5">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Monitor className="h-4 w-4" />
              System Health
              <Badge variant={onlineCount === totalServices ? 'default' : 'destructive'} className="ml-auto">
                {onlineCount}/{totalServices} Online
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-2">
              {SERVICES.map(svc => {
                const health = services[svc.key]
                const status = health?.status || 'checking'
                return (
                  <div key={svc.key} className={`flex items-center gap-2 rounded border p-2 text-xs ${
                    status === 'online' ? 'border-green-200 dark:border-green-900' :
                    status === 'offline' ? 'border-red-200 dark:border-red-900 bg-red-50/50 dark:bg-red-950/20' :
                    'border-yellow-200 dark:border-yellow-900'
                  }`}>
                    {status === 'online' ? <Wifi className="h-3 w-3 text-green-500 shrink-0" /> :
                     status === 'offline' ? <WifiOff className="h-3 w-3 text-red-500 shrink-0" /> :
                     <Activity className="h-3 w-3 text-yellow-500 animate-pulse shrink-0" />}
                    <span className="truncate font-medium">{svc.name}</span>
                    {health?.latencyMs != null && (
                      <span className="ml-auto text-muted-foreground shrink-0">{health.latencyMs}ms</span>
                    )}
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>

        {/* Intelligence Summary */}
        <Card className="col-span-12 xl:col-span-4">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Brain className="h-4 w-4" /> Market Intelligence
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* MiroFish */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">MiroFish Prediction</span>
              <div className="flex items-center gap-2">
                {mirofish ? (
                  <>
                    <span className={`font-bold ${biasColor}`}>{biasIcon}</span>
                    <Badge variant={mirofish.bias === 'BULLISH' ? 'default' : mirofish.bias === 'BEARISH' ? 'destructive' : 'secondary'}>
                      {mirofish.bias} ({(mirofish.confidence * 100).toFixed(0)}%)
                    </Badge>
                    {mirofish.stale && <Badge variant="outline" className="text-xs text-yellow-600">STALE</Badge>}
                  </>
                ) : (
                  <Badge variant="outline">Disconnected</Badge>
                )}
              </div>
            </div>

            {/* VIX Outlook */}
            {mirofish?.vixExpectation && (
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">VIX Outlook</span>
                <Badge variant="outline">{mirofish.vixExpectation}</Badge>
              </div>
            )}

            {/* Sector Rotation */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Sector Rotation</span>
              <div className="flex items-center gap-1">
                {rotation ? (
                  <>
                    <span className="text-xs text-green-600">{rotation.leadingSectors.length} Leading</span>
                    <span className="text-xs text-muted-foreground">/</span>
                    <span className="text-xs text-red-600">{rotation.laggingSectors.length} Lagging</span>
                    {rotation.transitions.length > 0 && (
                      <Badge variant="destructive" className="ml-1 text-xs">
                        {rotation.transitions.length} transition(s)
                      </Badge>
                    )}
                  </>
                ) : (
                  <Badge variant="outline">Disconnected</Badge>
                )}
              </div>
            </div>

            {/* Fundamentals */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Fundamental Gate</span>
              {fundamentals ? (
                <span className="text-xs">
                  <span className="text-green-600 font-mono">{fundamentals.clearedSymbols.length}</span> cleared /
                  <span className="text-red-600 font-mono ml-1">{Object.keys(fundamentals.blockedSymbols).length}</span> blocked
                </span>
              ) : (
                <Badge variant="outline">Not loaded</Badge>
              )}
            </div>

            {/* Narrative */}
            {mirofish?.narrativeSummary && (
              <p className="text-xs text-muted-foreground border-t pt-2 mt-2">
                {mirofish.narrativeSummary}
              </p>
            )}
          </CardContent>
        </Card>

        {/* Quick Actions */}
        <Card className="col-span-12 xl:col-span-3">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Zap className="h-4 w-4" /> Quick Actions
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button
              variant={killSwitchActive ? "default" : "destructive"}
              size="sm"
              className="w-full justify-start"
              onClick={async () => {
                try {
                  const newState = !killSwitchActive
                  await webClient.post('/intelligence/kill-switch', {
                    active: newState,
                    reason: newState ? 'Manual toggle from Command Center' : 'Re-enabled from Command Center',
                  })
                  setKillSwitchActive(newState)
                } catch {}
              }}
            >
              <Power className="h-3.5 w-3.5 mr-2" />
              {killSwitchActive ? 'Re-Enable Intelligence' : 'KILL SWITCH (Pure Technical)'}
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start" onClick={fullRefresh}>
              <RefreshCw className="h-3.5 w-3.5 mr-2" /> Refresh All Intelligence
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start"
              onClick={() => window.open('/tomic/dashboard', '_blank')}>
              <BarChart3 className="h-3.5 w-3.5 mr-2" /> Open TOMIC Dashboard
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start"
              onClick={() => window.open('/scalping', '_blank')}>
              <Activity className="h-3.5 w-3.5 mr-2" /> Open Scalping Dashboard
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start"
              onClick={() => window.open('/options-selling', '_blank')}>
              <Layers className="h-3.5 w-3.5 mr-2" /> Options Selling Workbench
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start"
              onClick={() => window.open('http://localhost:8000', '_blank')}>
              <ArrowRightLeft className="h-3.5 w-3.5 mr-2" /> Sector Rotation Map
            </Button>
            <Button variant="outline" size="sm" className="w-full justify-start"
              onClick={() => window.open('http://localhost:3000', '_blank')}>
              <Brain className="h-3.5 w-3.5 mr-2" /> MiroFish Prediction UI
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Row 2: TOMIC + Scalping + Positions */}
      <div className="grid grid-cols-12 gap-4">
        {/* TOMIC Status */}
        <Card className="col-span-12 lg:col-span-4">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Shield className="h-4 w-4" /> TOMIC Options Selling
              {tomicStatus?.running ? (
                <Badge className="ml-auto">Running</Badge>
              ) : (
                <Badge variant="secondary" className="ml-auto">Stopped</Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {tomicStatus ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <span className="text-xs text-muted-foreground">Mode</span>
                    <p className="text-sm font-mono">{tomicStatus.mode}</p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">Active Positions</span>
                    <p className="text-sm font-mono">{tomicStatus.activePositions}</p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">Daily P&L</span>
                    <p className={`text-sm font-mono ${tomicStatus.dailyPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {tomicStatus.dailyPnl >= 0 ? '+' : ''}{tomicStatus.dailyPnl.toFixed(0)}
                    </p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">Signal Quality</span>
                    <p className="text-sm font-mono">{tomicStatus.signalQuality}</p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">Dead Letters</span>
                    <p className={`text-sm font-mono ${tomicStatus.deadLetters > 0 ? 'text-red-600' : ''}`}>
                      {tomicStatus.deadLetters}
                    </p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">Circuit Breakers</span>
                    <p className={`text-sm font-mono ${tomicStatus.circuitBreakersTripped > 0 ? 'text-red-600' : 'text-green-600'}`}>
                      {tomicStatus.circuitBreakersTripped > 0 ? `${tomicStatus.circuitBreakersTripped} TRIPPED` : 'All Clear'}
                    </p>
                  </div>
                </div>

                {positionSummary && (
                  <div className="border-t pt-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <span className="text-xs text-muted-foreground">Local Broker</span>
                        <p className="text-sm font-mono">{positionSummary.broker}</p>
                      </div>
                      <div>
                        <span className="text-xs text-muted-foreground">Option Positions</span>
                        <p className="text-sm font-mono">
                          {positionSummary.optionPositions}/{positionSummary.totalPositions}
                        </p>
                      </div>
                      <div>
                        <span className="text-xs text-muted-foreground">Position P&L</span>
                        <p className={`text-sm font-mono ${positionSummary.totalPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {positionSummary.totalPnl >= 0 ? '+' : ''}{positionSummary.totalPnl.toFixed(0)}
                        </p>
                      </div>
                      <div>
                        <span className="text-xs text-muted-foreground">Margin Used</span>
                        <p className="text-sm font-mono">
                          {positionSummary.marginUsed > 0 ? positionSummary.marginUsed.toFixed(0) : '—'}
                        </p>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">TOMIC not connected</p>
            )}
          </CardContent>
        </Card>

        {/* Scalping Status */}
        <Card className="col-span-12 lg:col-span-4">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="h-4 w-4" /> Options Scalping
              <Badge variant="outline" className="ml-auto">Dashboard</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="text-xs text-muted-foreground">Auto-Trade</span>
                <p className="text-sm font-mono">{scalpingStatus?.autoEnabled ? 'ON' : 'OFF'}</p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Mode</span>
                <p className="text-sm font-mono uppercase">{scalpingStatus.mode}</p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Kill Switch</span>
                <p className={`text-sm font-mono ${scalpingStatus?.killSwitch ? 'text-red-600' : 'text-green-600'}`}>
                  {scalpingStatus?.killSwitch ? 'ACTIVE' : 'OFF'}
                </p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Regime</span>
                <p className="text-sm font-mono">{scalpingStatus?.regime || '—'}</p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Trades Today</span>
                <p className="text-sm font-mono">{scalpingStatus?.tradesCount ?? 0}</p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Realized P&L</span>
                <p className={`text-sm font-mono ${(scalpingStatus?.realizedPnl ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                  {(scalpingStatus?.realizedPnl ?? 0) >= 0 ? '+' : ''}{(scalpingStatus?.realizedPnl ?? 0).toFixed(0)}
                </p>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">Ghost Signals</span>
                <p className="text-sm font-mono">{scalpingStatus?.ghostSignals ?? 0}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Sector Rotation Snapshot */}
        <Card className="col-span-12 lg:col-span-4">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <ArrowRightLeft className="h-4 w-4" /> Sector Rotation Snapshot
            </CardTitle>
          </CardHeader>
          <CardContent>
            {rotation ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  {rotation.leadingSectors.length > 0 && (
                    <div>
                      <span className="text-xs text-green-600 font-semibold">Leading</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rotation.leadingSectors.map(s => (
                          <Badge key={s} variant="outline" className="text-xs text-green-600 py-0">{s.replace('NIFTY', '')}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {rotation.laggingSectors.length > 0 && (
                    <div>
                      <span className="text-xs text-red-600 font-semibold">Lagging</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rotation.laggingSectors.map(s => (
                          <Badge key={s} variant="outline" className="text-xs text-red-600 py-0">{s.replace('NIFTY', '')}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {rotation.improvingSectors?.length > 0 && (
                    <div>
                      <span className="text-xs text-blue-600 font-semibold">Improving</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rotation.improvingSectors.map(s => (
                          <Badge key={s} variant="outline" className="text-xs text-blue-600 py-0">{s.replace('NIFTY', '')}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {rotation.weakeningSectors?.length > 0 && (
                    <div>
                      <span className="text-xs text-yellow-600 font-semibold">Weakening</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rotation.weakeningSectors.map(s => (
                          <Badge key={s} variant="outline" className="text-xs text-yellow-600 py-0">{s.replace('NIFTY', '')}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">Sector Rotation service not connected</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Row 3: Alerts + Scenarios */}
      <div className="grid grid-cols-12 gap-4">
        {/* Active Alerts */}
        <Card className="col-span-12 lg:col-span-7">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" /> Live Alerts & Events
              {alerts.length > 0 && <Badge variant="destructive" className="ml-2">{alerts.length}</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {alerts.length > 0 ? (
              <div className="max-h-48 overflow-y-auto space-y-1">
                {alerts.slice(0, 20).map((alert, i) => (
                  <div key={i} className={`flex items-start gap-2 text-xs p-1.5 rounded ${
                    alert.level === 'error' ? 'bg-red-50 dark:bg-red-950/20' :
                    alert.level === 'warning' ? 'bg-yellow-50 dark:bg-yellow-950/20' :
                    'bg-blue-50 dark:bg-blue-950/20'
                  }`}>
                    {alert.level === 'error' ? <XCircle className="h-3 w-3 text-red-500 mt-0.5 shrink-0" /> :
                     alert.level === 'warning' ? <AlertTriangle className="h-3 w-3 text-yellow-500 mt-0.5 shrink-0" /> :
                     <CheckCircle2 className="h-3 w-3 text-blue-500 mt-0.5 shrink-0" />}
                    <span className="text-muted-foreground shrink-0">{alert.time}</span>
                    <span>{alert.message}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">No active alerts</p>
            )}
          </CardContent>
        </Card>

        {/* MiroFish Scenarios */}
        <Card className="col-span-12 lg:col-span-5">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Brain className="h-4 w-4" /> Active Scenarios
            </CardTitle>
          </CardHeader>
          <CardContent>
            {mirofish?.scenarios && mirofish.scenarios.length > 0 ? (
              <div className="space-y-2">
                {mirofish.scenarios.map((sc, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs border rounded p-2">
                    <Badge variant={sc.impact === 'POSITIVE' ? 'default' : sc.impact === 'NEGATIVE' ? 'destructive' : 'secondary'}
                      className="shrink-0 mt-0.5">
                      {(sc.probability * 100).toFixed(0)}%
                    </Badge>
                    <span>{sc.description}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">No active scenarios from MiroFish</p>
            )}

            {mirofish?.keyRisks && mirofish.keyRisks.length > 0 && (
              <div className="mt-3 pt-3 border-t">
                <span className="text-xs font-semibold text-red-600">Key Risks</span>
                <ul className="mt-1 space-y-1">
                  {mirofish.keyRisks.map((risk, i) => (
                    <li key={i} className="text-xs text-muted-foreground flex items-start gap-1">
                      <AlertTriangle className="h-3 w-3 text-red-400 mt-0.5 shrink-0" />
                      {risk}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
