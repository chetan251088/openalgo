import {
  BarChart3,
  Filter,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { fetchAutoTradeAnalytics } from '@/api/ai-scalper'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useThemeStore } from '@/stores/themeStore'
import type { AutoTradeAnalyticsResponse } from '@/types/ai-scalper'

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
}

const RANGE_OPTIONS = [
  { label: 'Last 1 Day', value: '1d', days: 1 },
  { label: 'Last 7 Days', value: '7d', days: 7 },
  { label: 'Last 30 Days', value: '30d', days: 30 },
  { label: 'Last 90 Days', value: '90d', days: 90 },
  { label: 'All', value: 'all', days: null },
]

export default function AutoTradeAnalytics() {
  const { mode } = useThemeStore()
  const isDarkMode = mode === 'dark'
  const [isLoading, setIsLoading] = useState(false)
  const [analytics, setAnalytics] = useState<AutoTradeAnalyticsResponse | null>(null)

  const [modeFilter, setModeFilter] = useState('ALL')
  const [sourceFilter, setSourceFilter] = useState('ALL')
  const [sideFilter, setSideFilter] = useState('ALL')
  const [underlyingFilter, setUnderlyingFilter] = useState('')
  const [symbolFilter, setSymbolFilter] = useState('')
  const [range, setRange] = useState('7d')
  const [limit, setLimit] = useState('2000')
  const [bucketSize, setBucketSize] = useState('50')
  const [intervalMin, setIntervalMin] = useState('5')

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const equitySeriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const drawdownSeriesRef = useRef<ISeriesApi<'Area'> | null>(null)

  const sinceValue = useMemo(() => {
    const option = RANGE_OPTIONS.find((item) => item.value === range)
    if (!option?.days) return undefined
    const since = new Date(Date.now() - option.days * 24 * 60 * 60 * 1000)
    return since.toISOString()
  }, [range])

  const analyticsParams = useMemo(() => {
    const limitValue = Number.parseInt(limit, 10)
    const bucketValue = Number.parseFloat(bucketSize)
    const intervalValue = Number.parseInt(intervalMin, 10)
    return {
      limit: Number.isFinite(limitValue) ? limitValue : 2000,
      bucket: Number.isFinite(bucketValue) ? bucketValue : 50,
      interval_min: Number.isFinite(intervalValue) ? intervalValue : 5,
      mode: modeFilter === 'ALL' ? undefined : modeFilter,
      source: sourceFilter === 'ALL' ? undefined : sourceFilter,
      side: sideFilter === 'ALL' ? undefined : sideFilter,
      underlying: underlyingFilter || undefined,
      symbol: symbolFilter || undefined,
      since: sinceValue,
    }
  }, [
    limit,
    bucketSize,
    intervalMin,
    modeFilter,
    sourceFilter,
    sideFilter,
    underlyingFilter,
    symbolFilter,
    sinceValue,
  ])

  const loadAnalytics = useCallback(async (params = analyticsParams) => {
    setIsLoading(true)
    try {
      const data = await fetchAutoTradeAnalytics(params)
      setAnalytics(data)
    } catch (error) {
      console.error('Failed to load AutoTrade analytics', error)
      toast.error('Failed to load AutoTrade analytics')
    } finally {
      setIsLoading(false)
    }
  }, [analyticsParams])

  const initChart = useCallback(() => {
    if (!chartContainerRef.current) return
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }
    const container = chartContainerRef.current
    const chart = createChart(container, {
      width: container.offsetWidth,
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: isDarkMode ? '#a6adbb' : '#333',
      },
      grid: {
        vertLines: {
          color: isDarkMode ? 'rgba(166, 173, 187, 0.1)' : 'rgba(0, 0, 0, 0.1)',
        },
        horzLines: {
          color: isDarkMode ? 'rgba(166, 173, 187, 0.1)' : 'rgba(0, 0, 0, 0.1)',
        },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: isDarkMode ? 'rgba(166, 173, 187, 0.2)' : 'rgba(0, 0, 0, 0.2)',
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          width: 1,
          color: isDarkMode ? 'rgba(166, 173, 187, 0.5)' : 'rgba(0, 0, 0, 0.3)',
          style: 2,
          labelVisible: false,
        },
        horzLine: {
          width: 1,
          color: isDarkMode ? 'rgba(166, 173, 187, 0.5)' : 'rgba(0, 0, 0, 0.3)',
        },
      },
    })

    const equitySeries = chart.addSeries(AreaSeries, {
      lineColor: '#00d4ff',
      topColor: 'rgba(0, 212, 255, 0.35)',
      bottomColor: 'rgba(0, 212, 255, 0.0)',
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => formatCurrency(price),
      },
    })

    const drawdownSeries = chart.addSeries(AreaSeries, {
      lineColor: '#ff6b6b',
      topColor: 'rgba(255, 107, 107, 0.0)',
      bottomColor: 'rgba(255, 107, 107, 0.35)',
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => formatCurrency(price),
      },
    })

    chartRef.current = chart
    equitySeriesRef.current = equitySeries
    drawdownSeriesRef.current = drawdownSeries

    const handleResize = () => {
      if (chartRef.current && container) {
        chartRef.current.applyOptions({ width: container.offsetWidth })
      }
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [isDarkMode])

  useEffect(() => {
    const cleanup = initChart()
    return () => {
      cleanup?.()
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [initChart])

  useEffect(() => {
    const equity = analytics?.equity ?? []
    if (!equitySeriesRef.current || !drawdownSeriesRef.current) return
    const equitySeries = equity
      .filter((point) => point.time)
      .map((point) => ({
        time: point.time as UTCTimestamp,
        value: point.value,
      }))
    const drawdownSeries = equity
      .filter((point) => point.time)
      .map((point) => ({
        time: point.time as UTCTimestamp,
        value: point.drawdown,
      }))
    equitySeriesRef.current.setData(equitySeries)
    drawdownSeriesRef.current.setData(drawdownSeries)
  }, [analytics])

  useEffect(() => {
    loadAnalytics(analyticsParams)
  }, [])

  const summary = analytics?.summary

  const distribution = analytics?.distribution ?? []
  const maxDistributionCount = Math.max(1, ...distribution.map((d) => d.count))

  const timeBuckets = analytics?.time_buckets ?? []
  const reasonBreakdown = analytics?.reason_breakdown ?? []
  const sideBreakdown = analytics?.side_breakdown ?? []

  const insights = useMemo(() => {
    if (!summary) return []
    const notes: string[] = []
    if (summary.total_trades < 20) {
      notes.push('Low sample size — collect more trades before tuning.')
    }
    if (summary.win_rate < 45) {
      notes.push('Win rate is low — consider stricter entry filters or higher momentum ticks.')
    }
    if (summary.profit_factor !== null && summary.profit_factor < 1) {
      notes.push('Profit factor < 1 — increase average win or reduce loss size.')
    }
    if (summary.avg_hold_s !== null && summary.avg_hold_s < 10) {
      notes.push('Avg hold time is very short — increase BE delay or trail distance.')
    }
    if (summary.max_drawdown > Math.abs(summary.total_pnl) && summary.total_pnl > 0) {
      notes.push('Drawdown exceeds net P&L — tighten risk or reduce max lots.')
    }
    if (!notes.length) {
      notes.push('Metrics look stable — keep monitoring and scale cautiously.')
    }
    return notes
  }, [summary])

  const getPnlBadge = (value: number) =>
    value >= 0 ? (
      <Badge className="bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/20">
        <TrendingUp className="mr-1 h-3 w-3" />
        {formatCurrency(value)}
      </Badge>
    ) : (
      <Badge className="bg-rose-500/20 text-rose-200 hover:bg-rose-500/20">
        <TrendingDown className="mr-1 h-3 w-3" />
        {formatCurrency(value)}
      </Badge>
    )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">AutoTrade Analytics</h1>
          <p className="text-sm text-muted-foreground">
            Visualize AutoTrade performance, breakdowns, and momentum over time.
          </p>
        </div>
        <Button onClick={() => loadAnalytics(analyticsParams)} disabled={isLoading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          Apply Filters
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Filter className="h-4 w-4" />
            Filters
          </CardTitle>
          <Badge variant="secondary">Limit {analytics?.limit ?? analyticsParams.limit}</Badge>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3 lg:grid-cols-6">
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Mode</p>
            <Select value={modeFilter} onValueChange={setModeFilter}>
              <SelectTrigger>
                <SelectValue placeholder="All" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                <SelectItem value="PAPER">Paper</SelectItem>
                <SelectItem value="LIVE">Live</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Source</p>
            <Select value={sourceFilter} onValueChange={setSourceFilter}>
              <SelectTrigger>
                <SelectValue placeholder="All" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                <SelectItem value="local">Local</SelectItem>
                <SelectItem value="server">Server</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Side</p>
            <Select value={sideFilter} onValueChange={setSideFilter}>
              <SelectTrigger>
                <SelectValue placeholder="All" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                <SelectItem value="CE">CE</SelectItem>
                <SelectItem value="PE">PE</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Underlying</p>
            <Input
              placeholder="NIFTY / SENSEX"
              value={underlyingFilter}
              onChange={(event) => setUnderlyingFilter(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Symbol</p>
            <Input
              placeholder="NFO:NIFTY..."
              value={symbolFilter}
              onChange={(event) => setSymbolFilter(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Range</p>
            <Select value={range} onValueChange={setRange}>
              <SelectTrigger>
                <SelectValue placeholder="Last 7 Days" />
              </SelectTrigger>
              <SelectContent>
                {RANGE_OPTIONS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Limit</p>
            <Input
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
              placeholder="2000"
            />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Bucket (₹)</p>
            <Input
              value={bucketSize}
              onChange={(event) => setBucketSize(event.target.value)}
              placeholder="50"
            />
          </div>
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">Time Bucket (min)</p>
            <Input
              value={intervalMin}
              onChange={(event) => setIntervalMin(event.target.value)}
              placeholder="5"
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Total P&L</CardTitle>
          </CardHeader>
          <CardContent>{summary ? getPnlBadge(summary.total_pnl) : '--'}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Trades</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">
            {summary?.total_trades ?? '--'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Win Rate</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">
            {summary ? `${summary.win_rate.toFixed(1)}%` : '--'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Profit Factor</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">
            {summary?.profit_factor ?? '--'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Avg P&L</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">
            {summary ? formatCurrency(summary.avg_pnl) : '--'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Max Drawdown</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">
            {summary ? formatCurrency(summary.max_drawdown) : '--'}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BarChart3 className="h-4 w-4" />
            Equity Curve & Drawdown
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div ref={chartContainerRef} className="h-[320px]" />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">PnL Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-end gap-1 h-40">
              {distribution.map((bucket) => (
                <div key={bucket.bucket} className="flex-1">
                  <div
                    className="rounded bg-cyan-500/70"
                    style={{ height: `${(bucket.count / maxDistributionCount) * 100}%` }}
                    title={`₹${bucket.bucket} : ${bucket.count}`}
                  />
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
              {distribution.slice(0, 6).map((bucket) => (
                <span key={bucket.bucket}>
                  {bucket.bucket >= 0 ? '+' : ''}
                  {bucket.bucket}: {bucket.count}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Side Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Side</TableHead>
                  <TableHead>Trades</TableHead>
                  <TableHead>Win%</TableHead>
                  <TableHead>P&L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sideBreakdown.map((row) => (
                  <TableRow key={row.side}>
                    <TableCell>{row.side}</TableCell>
                    <TableCell>{row.count}</TableCell>
                    <TableCell>{row.win_rate.toFixed(1)}%</TableCell>
                    <TableCell>{formatCurrency(row.pnl)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Exit Reasons</CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-40">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Reason</TableHead>
                    <TableHead>Trades</TableHead>
                    <TableHead>P&L</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {reasonBreakdown.map((row) => (
                    <TableRow key={row.reason}>
                      <TableCell>{row.reason}</TableCell>
                      <TableCell>{row.count}</TableCell>
                      <TableCell>{formatCurrency(row.pnl)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Time-of-Day Performance</CardTitle>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Bucket</TableHead>
                  <TableHead>Trades</TableHead>
                  <TableHead>Win%</TableHead>
                  <TableHead>Avg P&L</TableHead>
                  <TableHead>Total P&L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {timeBuckets.map((row) => (
                  <TableRow key={row.bucket}>
                    <TableCell>{row.bucket}</TableCell>
                    <TableCell>{row.count}</TableCell>
                    <TableCell>{row.win_rate.toFixed(1)}%</TableCell>
                    <TableCell>{formatCurrency(row.avg_pnl)}</TableCell>
                    <TableCell>{formatCurrency(row.pnl)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </ScrollArea>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Auto Insights</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="list-disc space-y-2 pl-4 text-sm text-muted-foreground">
            {insights.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
