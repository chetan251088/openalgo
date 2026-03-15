import { useState, useEffect, useCallback, useMemo } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { webClient } from '@/api/client'
import { useIntelligence } from '@/hooks/useIntelligence'
import {
  TrendingDown,
  TrendingUp,
  Minus,
  RefreshCw,
  Play,
  Shield,
  BarChart3,
  Brain,
  ArrowRightLeft,
} from 'lucide-react'

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY']
const STRATEGY_TYPES = [
  { value: 'IRON_CONDOR', label: 'Iron Condor' },
  { value: 'BULL_PUT_SPREAD', label: 'Bull Put Spread' },
  { value: 'BEAR_CALL_SPREAD', label: 'Bear Call Spread' },
  { value: 'SHORT_STRANGLE', label: 'Short Strangle' },
  { value: 'SHORT_STRADDLE', label: 'Short Straddle' },
]

interface PositionRow {
  symbol: string
  exchange: string
  product: string
  quantity: number
  pnl: number
}

export default function OptionsSelling() {
  const [underlying, setUnderlying] = useState('NIFTY')
  const [strategyType, setStrategyType] = useState('IRON_CONDOR')
  const [shortDelta, setShortDelta] = useState(0.25)
  const [wingDelta, setWingDelta] = useState(0.10)
  const [lots, setLots] = useState(1)
  const [positions, setPositions] = useState<PositionRow[]>([])
  const [loading, setLoading] = useState(false)
  const [todayPlan, setTodayPlan] = useState<any>(null)

  const { mirofish, rotation, fundamentals, refreshIntelligence } = useIntelligence(true)

  const fetchPositions = useCallback(async () => {
    try {
      const resp = await webClient.get('/tomic/positions')
      if (resp.data?.status === 'success') {
        setPositions(resp.data.positions || [])
      }
    } catch {
      // positions endpoint may not exist yet
    }
  }, [])

  const fetchTodayPlan = useCallback(async () => {
    try {
      const resp = await webClient.get('/tomic/plan')
      if (resp.data?.status === 'success') {
        setTodayPlan(resp.data.data)
      }
    } catch {
      // plan endpoint may not exist yet
    }
  }, [])

  useEffect(() => {
    fetchPositions()
    fetchTodayPlan()
    const interval = setInterval(fetchPositions, 30_000)
    return () => clearInterval(interval)
  }, [fetchPositions, fetchTodayPlan])

  const biasIcon = useMemo(() => {
    if (!mirofish?.bias) return <Minus className="h-4 w-4" />
    if (mirofish.bias === 'BULLISH') return <TrendingUp className="h-4 w-4 text-green-500" />
    if (mirofish.bias === 'BEARISH') return <TrendingDown className="h-4 w-4 text-red-500" />
    return <Minus className="h-4 w-4 text-gray-400" />
  }, [mirofish?.bias])

  const handlePlaceStrategy = useCallback(async () => {
    setLoading(true)
    try {
      // This would call the TOMIC signal endpoint or optionsmultiorder
      alert(`Strategy ${strategyType} on ${underlying} with delta ${shortDelta}/${wingDelta} x ${lots} lots — integration pending`)
    } finally {
      setLoading(false)
    }
  }, [underlying, strategyType, shortDelta, wingDelta, lots])

  return (
    <div className="container mx-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Options Selling Workbench</h1>
          <p className="text-sm text-muted-foreground">
            Build and manage options selling strategies with intelligence-driven insights
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refreshIntelligence()}>
          <RefreshCw className="h-4 w-4 mr-2" /> Refresh Intelligence
        </Button>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* Strategy Builder */}
        <div className="col-span-12 lg:col-span-5">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BarChart3 className="h-5 w-5" /> Strategy Builder
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">Underlying</label>
                  <Select value={underlying} onValueChange={setUnderlying}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {UNDERLYINGS.map(u => <SelectItem key={u} value={u}>{u}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Strategy</label>
                  <Select value={strategyType} onValueChange={setStrategyType}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {STRATEGY_TYPES.map(s => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div>
                <label className="text-xs text-muted-foreground">Short Delta: {shortDelta.toFixed(2)}</label>
                <Slider
                  value={[shortDelta * 100]}
                  onValueChange={(values: number[]) => setShortDelta((values[0] ?? shortDelta * 100) / 100)}
                  min={10} max={50} step={5}
                />
              </div>

              <div>
                <label className="text-xs text-muted-foreground">Wing Delta: {wingDelta.toFixed(2)}</label>
                <Slider
                  value={[wingDelta * 100]}
                  onValueChange={(values: number[]) => setWingDelta((values[0] ?? wingDelta * 100) / 100)}
                  min={5} max={30} step={5}
                />
              </div>

              <div>
                <label className="text-xs text-muted-foreground">Lots: {lots}</label>
                <Slider
                  value={[lots]}
                  onValueChange={(values: number[]) => setLots(values[0] ?? lots)}
                  min={1} max={10} step={1}
                />
              </div>

              <Button className="w-full" onClick={handlePlaceStrategy} disabled={loading}>
                <Play className="h-4 w-4 mr-2" />
                {loading ? 'Placing...' : `Place ${strategyType.replace(/_/g, ' ')}`}
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Intelligence Panel */}
        <div className="col-span-12 lg:col-span-3 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Brain className="h-4 w-4" /> MiroFish Signal
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {mirofish ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Bias</span>
                    <Badge variant={mirofish.bias === 'BULLISH' ? 'default' : mirofish.bias === 'BEARISH' ? 'destructive' : 'secondary'}>
                      {biasIcon} <span className="ml-1">{mirofish.bias}</span>
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Confidence</span>
                    <span className="text-sm font-mono">{(mirofish.confidence * 100).toFixed(0)}%</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">VIX Outlook</span>
                    <span className="text-sm">{mirofish.vixExpectation}</span>
                  </div>
                  {mirofish.narrativeSummary && (
                    <p className="text-xs text-muted-foreground mt-2">{mirofish.narrativeSummary}</p>
                  )}
                </>
              ) : (
                <p className="text-xs text-muted-foreground">No prediction available</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <ArrowRightLeft className="h-4 w-4" /> Sector Rotation
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {rotation ? (
                <>
                  {rotation.leadingSectors.length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">Leading</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rotation.leadingSectors.map(s => (
                          <Badge key={s} variant="outline" className="text-xs text-green-600">{s.replace('NIFTY', '')}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {rotation.laggingSectors.length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">Lagging</span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {rotation.laggingSectors.map(s => (
                          <Badge key={s} variant="outline" className="text-xs text-red-600">{s.replace('NIFTY', '')}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {rotation.transitions.length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground">Transitions</span>
                      {rotation.transitions.map((t, i) => (
                        <p key={i} className="text-xs">{t.symbol}: {t.from_quadrant} → {t.to_quadrant}</p>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-xs text-muted-foreground">No rotation data</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Shield className="h-4 w-4" /> Fundamental Gate
              </CardTitle>
            </CardHeader>
            <CardContent>
              {fundamentals ? (
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Cleared</span>
                    <span className="text-sm font-mono text-green-600">{fundamentals.clearedSymbols.length}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Blocked</span>
                    <span className="text-sm font-mono text-red-600">{Object.keys(fundamentals.blockedSymbols).length}</span>
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No fundamental data</p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Today's Plan */}
        <div className="col-span-12 lg:col-span-4">
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="text-sm">Today's Trade Plan</CardTitle>
              <CardDescription>Generated by TOMIC DailyPlanAgent</CardDescription>
            </CardHeader>
            <CardContent>
              {todayPlan?.plans?.length > 0 ? (
                <div className="space-y-2">
                  {todayPlan.plans.map((plan: any, i: number) => (
                    <div key={i} className={`rounded border p-2 text-xs ${plan.is_active ? 'border-blue-200 bg-blue-50/30 dark:bg-blue-950/20' : 'opacity-50'}`}>
                      <div className="flex items-center justify-between">
                        <span className="font-semibold">{plan.instrument}</span>
                        <Badge variant="outline" className="text-xs">{plan.strategy_type?.replace(/_/g, ' ')}</Badge>
                      </div>
                      <p className="text-muted-foreground mt-1">{plan.rationale}</p>
                      <div className="flex gap-3 mt-1 text-muted-foreground">
                        <span>Short Δ: {plan.short_delta_target?.toFixed(2)}</span>
                        <span>Lots: {plan.lots}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No plan generated yet. Plans are created at 9:45 AM.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Positions Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Active Option Positions</CardTitle>
        </CardHeader>
        <CardContent>
          {positions.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {positions.map((pos, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs">{pos.symbol}</TableCell>
                    <TableCell>{pos.exchange}</TableCell>
                    <TableCell>{pos.product}</TableCell>
                    <TableCell className="text-right font-mono">{pos.quantity}</TableCell>
                    <TableCell className={`text-right font-mono ${pos.pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {pos.pnl >= 0 ? '+' : ''}{pos.pnl?.toFixed(2)}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button size="sm" variant="outline" className="h-6 text-xs">Close</Button>
                        <Button size="sm" variant="outline" className="h-6 text-xs">Roll</Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">No active option positions</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
