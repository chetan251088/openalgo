// frontend/src/pages/SignalEngine.tsx
// Signal Engine — Phase 1–3 (OBSERVE / MANUAL / AUTO)
// 5-layer professional trading signal dashboard with auto-execute toggle

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  History,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  XCircle,
  Zap,
} from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ExecuteMode = 'OBSERVE' | 'MANUAL' | 'AUTO'

interface Leg {
  action: 'buy' | 'sell'
  type: 'CE' | 'PE'
  strike: number
  symbol: string
  ltp: number
  exchange: string
}

interface LegData {
  legs: Leg[]
  net_credit: number
  max_loss: number
  max_loss_per_lot: number
  lot_size: number
  error: string | null
}

interface Signal {
  symbol: string
  exchange: string
  dte: number
  regime: string
  iv_rank: number | null
  iv_rank_label: string
  directional_bias: string
  directional_confidence: number
  spot: number | null
  vix_current: number | null
  market_quality_score: number | null
  strategy: { name: string; description: string; confidence: number }
  max_pain: number | null
  oi_walls: { ce_walls: number[]; pe_walls: number[] }
  sd_range_1: { lo: number; hi: number; dte: number; iv_used: number } | null
  capital_preservation_flags: { rule: string; action: string; severity: string }[]
  favorable_to_trade: boolean
  no_trade_reasons: string[]
  updated_at: string
  errors: string[]
}

interface SignalResponse {
  status: string
  signal: Signal
  legs: LegData
  execute_mode: ExecuteMode
  has_open_position: boolean
  settings: {
    execute_mode: ExecuteMode
    default_lots: number
    max_lots: number
    risk_pct: number
    product: string
  }
}

interface HistoryRecord {
  id: number
  ts: string
  symbol: string
  strategy: string
  regime: string
  iv_rank: number | null
  favorable: number
  executed: number
  exec_mode: string
  lots: number
  net_credit: number | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const REGIME_META: Record<string, { label: string; color: string; bg: string }> = {
  range_bound:   { label: 'RANGE-BOUND',  color: 'text-sky-400',     bg: 'bg-sky-400/10 border-sky-400/20' },
  trending_up:   { label: 'TRENDING UP',  color: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/20' },
  trending_down: { label: 'TRENDING DOWN',color: 'text-red-400',     bg: 'bg-red-400/10 border-red-400/20' },
  volatile:      { label: 'VOLATILE',     color: 'text-amber-400',   bg: 'bg-amber-400/10 border-amber-400/20' },
  pre_event:     { label: 'PRE-EVENT',    color: 'text-purple-400',  bg: 'bg-purple-400/10 border-purple-400/20' },
  unknown:       { label: 'UNKNOWN',      color: 'text-zinc-400',    bg: 'bg-zinc-400/10 border-zinc-400/20' },
}

const fmt = (n: number | null | undefined, dec = 0) =>
  n == null ? '—' : n.toLocaleString('en-IN', { maximumFractionDigits: dec })

const fmtTime = (iso: string) => {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata',
    }) + ' IST'
  } catch { return iso }
}

const fmtDateTime = (ts: string) => {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('en-IN', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
      timeZone: 'Asia/Kolkata',
    })
  } catch { return ts }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RegimeBadge({ regime }: { regime: string }) {
  const r = REGIME_META[regime] ?? REGIME_META.unknown
  return (
    <span className={cn('inline-block px-2.5 py-1 rounded-md text-xs font-bold tracking-wider border', r.color, r.bg)}>
      {r.label}
    </span>
  )
}

function IVRankBar({ iv_rank, label }: { iv_rank: number | null; label: string }) {
  if (iv_rank == null) return <span className="text-zinc-500 text-sm">—</span>
  const isHigh = iv_rank >= 70, isLow = iv_rank <= 30
  const col = isHigh ? 'text-red-400' : isLow ? 'text-emerald-400' : 'text-amber-400'
  const badge = isHigh ? 'bg-red-500/15 text-red-400 border-red-500/30'
    : isLow ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
  return (
    <div className="flex items-center gap-3 w-full">
      <span className={cn('text-xl font-bold tabular-nums shrink-0', col)}>{iv_rank.toFixed(0)}<span className="text-sm font-normal">th</span></span>
      <div className="flex-1 min-w-0"><Progress value={iv_rank} className="h-2" /></div>
      <Badge variant="outline" className={cn('text-xs font-bold shrink-0', badge)}>{label}</Badge>
    </div>
  )
}

function LegTable({ legs, net_credit, max_loss }: { legs: Leg[]; net_credit: number; max_loss: number }) {
  if (!legs.length) return <div className="text-zinc-500 text-xs">No legs available</div>
  return (
    <div className="space-y-1.5">
      {legs.map((leg, i) => (
        <div key={i} className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <span className={cn('px-1.5 py-0.5 rounded text-xs font-bold',
              leg.action === 'sell' ? 'bg-red-500/20 text-red-400' : 'bg-emerald-500/20 text-emerald-400')}>
              {leg.action.toUpperCase()}
            </span>
            <span className={cn('font-bold', leg.type === 'CE' ? 'text-sky-400' : 'text-orange-400')}>
              {leg.type}
            </span>
            <span className="text-zinc-300 tabular-nums">{fmt(leg.strike)}</span>
            <span className="text-zinc-500 truncate max-w-[140px]">{leg.symbol.split(':').pop()}</span>
          </div>
          <span className="text-zinc-400 tabular-nums ml-2">₹{leg.ltp?.toFixed(1) ?? '—'}</span>
        </div>
      ))}
      <Separator className="bg-zinc-800 my-2" />
      <div className="flex justify-between text-xs">
        <div>
          <span className="text-zinc-500">Net Credit </span>
          <span className={cn('font-bold tabular-nums', net_credit >= 0 ? 'text-emerald-400' : 'text-red-400')}>
            ₹{net_credit.toFixed(1)}
          </span>
        </div>
        <div>
          <span className="text-zinc-500">Max Loss </span>
          <span className="font-bold text-red-400 tabular-nums">₹{max_loss.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Mode toggle
// ---------------------------------------------------------------------------

const MODE_META: Record<ExecuteMode, { label: string; color: string; activeCls: string; warn?: string }> = {
  OBSERVE: { label: 'OBSERVE', color: 'text-zinc-400', activeCls: 'bg-zinc-700 text-zinc-100' },
  MANUAL:  { label: 'MANUAL',  color: 'text-amber-400',  activeCls: 'bg-amber-600 text-white' },
  AUTO:    { label: 'AUTO ⚡', color: 'text-red-400',    activeCls: 'bg-red-600 text-white',
             warn: 'AUTO mode will place REAL orders automatically when the signal is favorable. Make sure your lot size is correct before enabling.' },
}

function ModeToggle({
  current,
  onChange,
}: {
  current: ExecuteMode
  onChange: (m: ExecuteMode) => void
}) {
  const [confirmMode, setConfirmMode] = useState<ExecuteMode | null>(null)

  const handleClick = (mode: ExecuteMode) => {
    if (mode === current) return
    if (MODE_META[mode].warn) {
      setConfirmMode(mode)
    } else {
      onChange(mode)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex rounded-md overflow-hidden border border-zinc-700">
        {(['OBSERVE', 'MANUAL', 'AUTO'] as ExecuteMode[]).map((m) => (
          <button
            key={m}
            onClick={() => handleClick(m)}
            className={cn(
              'flex-1 px-3 py-2 text-xs font-bold transition-colors',
              current === m
                ? MODE_META[m].activeCls
                : 'bg-zinc-800 text-zinc-500 hover:bg-zinc-700',
            )}
          >
            {MODE_META[m].label}
          </button>
        ))}
      </div>

      {/* Confirm dialog for AUTO */}
      {confirmMode && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 space-y-2">
          <div className="flex items-start gap-2 text-xs text-red-300">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-red-400" />
            <span>{MODE_META[confirmMode].warn}</span>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              className="bg-red-600 hover:bg-red-700 text-white text-xs h-7"
              onClick={() => { onChange(confirmMode); setConfirmMode(null) }}
            >
              Enable {confirmMode}
            </Button>
            <Button
              size="sm" variant="outline"
              className="border-zinc-600 text-zinc-400 text-xs h-7"
              onClick={() => setConfirmMode(null)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {current === 'AUTO' && (
        <div className="flex items-center gap-1.5 text-xs text-red-400">
          <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
          AUTO mode active — orders will be placed automatically
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Confirm trade button panel
// ---------------------------------------------------------------------------

function ConfirmPanel({
  legs, settings, symbol, exchange, dte, onSuccess,
}: {
  signal?: Signal
  legs: LegData
  settings: SignalResponse['settings']
  symbol: string
  exchange: string
  dte: number
  onSuccess: () => void
}) {
  const [lots, setLots] = useState(settings.default_lots)
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null)

  const mutation = useMutation({
    mutationFn: async () => {
      const res = await fetch('/signal-engine/api/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, exchange, dte, lots }),
      })
      const json = await res.json()
      if (!res.ok && json.status === 'error') throw new Error(json.message)
      return json
    },
    onSuccess: (data) => {
      setResult({ success: data.status === 'success', message: data.errors?.join(', ') || 'Orders placed' })
      onSuccess()
    },
    onError: (err: Error) => {
      setResult({ success: false, message: err.message })
    },
  })

  if (!legs.legs.length) return null

  return (
    <Card className="bg-zinc-900 border-emerald-600/40 border-2">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm text-emerald-400 flex items-center gap-2">
          <Zap className="h-4 w-4" />
          Ready to Trade — Confirm Order
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <LegTable legs={legs.legs} net_credit={legs.net_credit} max_loss={legs.max_loss} />
        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-500">Lots:</span>
          <div className="flex rounded overflow-hidden border border-zinc-700">
            {[1, 2, 3, 5].filter(l => l <= settings.max_lots).map(l => (
              <button key={l} onClick={() => setLots(l)}
                className={cn('px-3 py-1 text-xs font-semibold transition-colors',
                  lots === l ? 'bg-indigo-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700')}>
                {l}
              </button>
            ))}
          </div>
          <span className="text-xs text-zinc-500">
            Max loss: ₹{((legs.max_loss_per_lot || 0) * lots).toLocaleString('en-IN')}
          </span>
        </div>

        {result && (
          <div className={cn('text-xs px-3 py-2 rounded border',
            result.success
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
              : 'bg-red-500/10 border-red-500/30 text-red-400')}>
            {result.message}
          </div>
        )}

        <Button
          className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending || !!result?.success}
        >
          {mutation.isPending ? <RefreshCw className="h-4 w-4 animate-spin mr-2" /> : null}
          {result?.success ? '✓ Order Placed' : `Place ${lots} Lot${lots > 1 ? 's' : ''} — ${settings.product}`}
        </Button>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// History table
// ---------------------------------------------------------------------------

function HistoryPanel({ symbol }: { symbol: string }) {
  const { data } = useQuery({
    queryKey: ['se-history', symbol],
    queryFn: async () => {
      const r = await fetch('/signal-engine/api/history?limit=10')
      const j = await r.json()
      return j.records as HistoryRecord[]
    },
    refetchInterval: 30_000,
  })

  if (!data?.length) return (
    <div className="text-xs text-zinc-500 py-2">No execution history yet.</div>
  )

  return (
    <div className="space-y-1">
      {data.map((rec) => (
        <div key={rec.id} className="flex items-center justify-between text-xs py-1.5 border-b border-zinc-800/60 last:border-0">
          <div className="flex items-center gap-2 min-w-0">
            {rec.executed
              ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
              : <Clock className="h-3.5 w-3.5 text-zinc-500 shrink-0" />}
            <span className="text-zinc-300 truncate">{rec.strategy || '—'}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {rec.executed && rec.lots > 0 && (
              <span className="text-zinc-500">{rec.lots}L</span>
            )}
            {rec.net_credit != null && (
              <span className={rec.net_credit >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                ₹{rec.net_credit.toFixed(0)}
              </span>
            )}
            <span className={cn('px-1.5 py-0.5 rounded font-bold',
              rec.exec_mode === 'AUTO' ? 'bg-red-500/20 text-red-400'
              : rec.exec_mode === 'MANUAL' ? 'bg-amber-500/20 text-amber-400'
              : 'bg-zinc-700 text-zinc-400')}>
              {rec.exec_mode || 'OBS'}
            </span>
            <span className="text-zinc-600">{fmtDateTime(rec.ts)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchSignalFull(symbol: string, dte: number): Promise<SignalResponse> {
  const r = await fetch(`/signal-engine/api/signal?symbol=${symbol}&dte=${dte}`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const j = await r.json()
  if (j.status !== 'success') throw new Error(j.message || 'Error')
  return j as SignalResponse
}

async function patchSettings(body: Record<string, string | number>): Promise<void> {
  await fetch('/signal-engine/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SignalEngine() {
  const [symbol, setSymbol]       = useState('NIFTY')
  const [dte, setDte]             = useState(4)
  const [refreshing, setRefreshing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const qc = useQueryClient()

  const { data, isLoading, error, refetch } = useQuery<SignalResponse>({
    queryKey: ['signal-engine-full', symbol, dte],
    queryFn: () => fetchSignalFull(symbol, dte),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const signal    = data?.signal
  const legs      = data?.legs
  const settings  = data?.settings
  const execMode  = (data?.execute_mode ?? 'OBSERVE') as ExecuteMode

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await fetch(`/signal-engine/api/signal/refresh?symbol=${symbol}&dte=${dte}`, { method: 'POST' })
      await refetch()
    } finally { setRefreshing(false) }
  }

  const handleModeChange = async (mode: ExecuteMode) => {
    await patchSettings({ execute_mode: mode })
    qc.invalidateQueries({ queryKey: ['signal-engine-full'] })
  }

  const busy = isLoading || refreshing

  return (
    <div className="min-h-full bg-zinc-950 text-zinc-100 p-4 md:p-6 max-w-5xl mx-auto overflow-y-auto">

      {/* ── Header ─────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <span className="text-2xl">⚡</span>
            TRADE SIGNAL ENGINE
            <Badge variant="outline"
              className={cn('text-xs font-bold ml-1 border',
                execMode === 'AUTO'   ? 'border-red-500/50 text-red-400 bg-red-500/10' :
                execMode === 'MANUAL' ? 'border-amber-500/50 text-amber-400 bg-amber-500/10' :
                                        'border-zinc-600 text-zinc-400')}>
              {execMode}
            </Badge>
          </h1>
          <p className="text-xs text-zinc-500 mt-0.5">
            5-layer decision engine · {execMode === 'OBSERVE' ? 'Display only' : execMode === 'MANUAL' ? 'Confirm to trade' : 'Auto-executing'}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Symbol */}
          <div className="flex rounded-md overflow-hidden border border-zinc-700">
            {['NIFTY','BANKNIFTY','SENSEX'].map(s => (
              <button key={s} onClick={() => setSymbol(s)}
                className={cn('px-3 py-1.5 text-xs font-semibold transition-colors',
                  symbol === s ? 'bg-indigo-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700')}>
                {s}
              </button>
            ))}
          </div>
          {/* DTE */}
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-zinc-500">DTE</span>
            <div className="flex rounded-md overflow-hidden border border-zinc-700">
              {[1,2,4,7,14].map(d => (
                <button key={d} onClick={() => setDte(d)}
                  className={cn('px-2.5 py-1.5 text-xs font-semibold transition-colors',
                    dte === d ? 'bg-indigo-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700')}>
                  {d}
                </button>
              ))}
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={handleRefresh} disabled={busy}
            className="border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 h-8">
            <RefreshCw className={cn('h-3.5 w-3.5 mr-1.5', busy && 'animate-spin')} />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowHistory(h => !h)}
            className="border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 h-8">
            <History className="h-3.5 w-3.5 mr-1.5" />
            History
          </Button>
        </div>
      </div>

      {/* Loading / Error */}
      {isLoading && (
        <div className="flex items-center justify-center h-48 text-zinc-500 text-sm">
          <RefreshCw className="h-4 w-4 mr-2 animate-spin" />Computing signal…
        </div>
      )}
      {error && !isLoading && (
        <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {String(error)}
        </div>
      )}

      {signal && settings && !isLoading && (
        <div className="space-y-4">

          {/* ── Execute Mode Toggle ─────────── */}
          <Card className="bg-zinc-900 border-zinc-800">
            <CardContent className="p-4">
              <div className="flex items-start gap-4 flex-col sm:flex-row sm:items-center justify-between">
                <div>
                  <div className="text-sm font-semibold text-zinc-300 mb-0.5">Execution Mode</div>
                  <div className="text-xs text-zinc-500">
                    OBSERVE = watch · MANUAL = you confirm · AUTO = places orders automatically
                  </div>
                </div>
                <div className="w-full sm:w-72">
                  <ModeToggle
                    current={execMode}
                    onChange={handleModeChange}
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ── 4 metric cards ──────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Card className="bg-zinc-900 border-zinc-800">
              <CardContent className="p-4 space-y-2">
                <div className="text-xs text-zinc-500 uppercase tracking-wide">Regime</div>
                <RegimeBadge regime={signal.regime} />
              </CardContent>
            </Card>
            <Card className="bg-zinc-900 border-zinc-800">
              <CardContent className="p-4 space-y-2">
                <div className="text-xs text-zinc-500 uppercase tracking-wide">IV Rank (VIX pct)</div>
                <IVRankBar iv_rank={signal.iv_rank} label={signal.iv_rank_label} />
              </CardContent>
            </Card>
            <Card className="bg-zinc-900 border-zinc-800">
              <CardContent className="p-4 space-y-2">
                <div className="text-xs text-zinc-500 uppercase tracking-wide">Direction</div>
                <div className="flex items-center gap-2">
                  {signal.directional_bias === 'BULLISH'
                    ? <TrendingUp className="h-4 w-4 text-emerald-400" />
                    : signal.directional_bias === 'BEARISH'
                    ? <TrendingDown className="h-4 w-4 text-red-400" />
                    : <span className="text-zinc-500 text-lg">–</span>}
                  <span className={cn('font-bold text-sm',
                    signal.directional_bias === 'BULLISH' ? 'text-emerald-400' :
                    signal.directional_bias === 'BEARISH' ? 'text-red-400' : 'text-zinc-400')}>
                    {signal.directional_bias}
                  </span>
                  <span className="text-xs text-zinc-500">{signal.directional_confidence}%</span>
                </div>
              </CardContent>
            </Card>
            <Card className="bg-zinc-900 border-zinc-800">
              <CardContent className="p-4 space-y-1">
                <div className="text-xs text-zinc-500 uppercase tracking-wide">Market Quality</div>
                <div className={cn('text-2xl font-bold tabular-nums',
                  (signal.market_quality_score ?? 0) >= 70 ? 'text-emerald-400' :
                  (signal.market_quality_score ?? 0) >= 50 ? 'text-amber-400' : 'text-red-400')}>
                  {fmt(signal.market_quality_score)}<span className="text-sm font-normal text-zinc-500">/100</span>
                </div>
                <div className="text-xs text-zinc-500 flex items-center gap-1">
                  <Clock className="h-3 w-3" />{fmtTime(signal.updated_at)}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* ── Signal + Strategy ───────────── */}
          <Card className={cn('border-2',
            signal.favorable_to_trade ? 'bg-zinc-900 border-emerald-600/40' : 'bg-zinc-900 border-zinc-700')}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold text-zinc-300 flex items-center justify-between gap-2">
                <span>SIGNAL</span>
                <span className="text-xs font-normal text-zinc-500">
                  {signal.symbol} · {signal.dte}d DTE · {fmt(signal.spot, 2)} spot
                  {data?.has_open_position && (
                    <span className="ml-2 text-amber-400">· position open</span>
                  )}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Favorable */}
              <div className={cn('flex items-center gap-2 px-4 py-3 rounded-lg border',
                signal.favorable_to_trade
                  ? 'bg-emerald-500/10 border-emerald-500/30'
                  : 'bg-red-500/10 border-red-500/30')}>
                {signal.favorable_to_trade
                  ? <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0" />
                  : <XCircle className="h-5 w-5 text-red-400 shrink-0" />}
                <span className={cn('font-bold text-sm',
                  signal.favorable_to_trade ? 'text-emerald-400' : 'text-red-400')}>
                  {signal.favorable_to_trade ? 'FAVORABLE TO TRADE' : 'NOT FAVORABLE TO TRADE'}
                </span>
              </div>
              {signal.no_trade_reasons.map((r, i) => (
                <div key={i} className="flex items-start gap-2 px-3 py-2 rounded bg-zinc-800/50 text-xs text-zinc-400">
                  <span className="shrink-0 text-zinc-500">•</span>{r}
                </div>
              ))}

              <Separator className="bg-zinc-800" />

              {/* Strategy */}
              <div className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-base font-bold text-white">{signal.strategy.name}</span>
                  <span className={cn('text-sm font-semibold tabular-nums shrink-0',
                    signal.strategy.confidence >= 80 ? 'text-emerald-400' :
                    signal.strategy.confidence >= 60 ? 'text-amber-400' : 'text-zinc-500')}>
                    {signal.strategy.confidence}%
                  </span>
                </div>
                <p className="text-xs text-zinc-400">{signal.strategy.description}</p>
                <Progress value={signal.strategy.confidence} className="h-1.5" />
              </div>

              {/* Options metrics */}
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-1">
                <div className="space-y-1">
                  <div className="text-xs text-zinc-500">Max Pain</div>
                  <div className="text-sm font-bold text-amber-400 tabular-nums">{fmt(signal.max_pain)}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-zinc-500">CE Resistance</div>
                  <div className="text-sm font-bold text-red-400 tabular-nums">
                    {signal.oi_walls.ce_walls.length > 0 ? fmt(Math.min(...signal.oi_walls.ce_walls)) : '—'}
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-zinc-500">PE Support</div>
                  <div className="text-sm font-bold text-emerald-400 tabular-nums">
                    {signal.oi_walls.pe_walls.length > 0 ? fmt(Math.max(...signal.oi_walls.pe_walls)) : '—'}
                  </div>
                </div>
                {signal.sd_range_1 && (
                  <div className="col-span-2 sm:col-span-3 space-y-1">
                    <div className="text-xs text-zinc-500">
                      1SD Range ({signal.sd_range_1.dte}d · IV {signal.sd_range_1.iv_used}%)
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-emerald-400 tabular-nums">{fmt(signal.sd_range_1.lo)}</span>
                      <div className="flex-1 relative h-px bg-zinc-700">
                        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-white" />
                      </div>
                      <span className="text-sm font-bold text-red-400 tabular-nums">{fmt(signal.sd_range_1.hi)}</span>
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* ── Legs preview ────────────────── */}
          {legs && legs.legs.length > 0 && (
            <Card className="bg-zinc-900 border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-zinc-400">Suggested Legs</CardTitle>
              </CardHeader>
              <CardContent>
                <LegTable legs={legs.legs} net_credit={legs.net_credit} max_loss={legs.max_loss} />
              </CardContent>
            </Card>
          )}

          {/* ── Confirm panel (MANUAL mode + favorable) */}
          {execMode === 'MANUAL' && signal.favorable_to_trade && legs && settings && !data?.has_open_position && (
            <ConfirmPanel
              signal={signal} legs={legs} settings={settings}
              symbol={symbol} exchange="NFO" dte={dte}
              onSuccess={() => qc.invalidateQueries({ queryKey: ['signal-engine-full'] })}
            />
          )}

          {/* ── Auto-execute status banner */}
          {execMode === 'AUTO' && (
            <div className={cn('flex items-center gap-2 px-4 py-3 rounded-lg border',
              signal.favorable_to_trade && !data?.has_open_position
                ? 'bg-red-500/10 border-red-500/30 text-red-400'
                : 'bg-zinc-800 border-zinc-700 text-zinc-500')}>
              <div className={cn('h-2 w-2 rounded-full shrink-0',
                signal.favorable_to_trade && !data?.has_open_position
                  ? 'bg-red-500 animate-pulse' : 'bg-zinc-600')} />
              {data?.has_open_position
                ? 'Position already open — auto-execute skipped'
                : signal.favorable_to_trade
                ? `AUTO: will place order at next cycle (${settings.default_lots} lot, ${settings.product})`
                : 'AUTO: waiting for favorable signal'}
            </div>
          )}

          {/* ── Side panels ─────────────────── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card className="bg-zinc-900 border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-zinc-400">Capital Preservation Checks</CardTitle>
              </CardHeader>
              <CardContent>
                {signal.capital_preservation_flags.length === 0 ? (
                  <div className="flex items-center gap-2 text-xs text-zinc-500 py-1">
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                    All checks OK
                  </div>
                ) : (
                  <div className="space-y-2">
                    {signal.capital_preservation_flags.map((f, i) => (
                      <div key={i} className={cn('flex items-start gap-2 px-3 py-2 rounded-lg text-xs border',
                        f.severity === 'high'
                          ? 'bg-red-500/10 border-red-500/30 text-red-400'
                          : 'bg-amber-500/10 border-amber-500/30 text-amber-400')}>
                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                        <div>
                          <div className="font-semibold">{f.rule}</div>
                          <div className="text-zinc-400 mt-0.5">{f.action}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="bg-zinc-900 border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-zinc-400">OI Walls (Top 3)</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div>
                  <div className="text-xs text-zinc-500 mb-1.5">CE — Resistance</div>
                  <div className="flex flex-wrap gap-1.5">
                    {signal.oi_walls.ce_walls.length > 0
                      ? signal.oi_walls.ce_walls.map((s, i) => (
                          <span key={i} className="px-2 py-0.5 rounded bg-red-500/15 border border-red-500/30 text-red-400 text-xs font-mono">{fmt(s)}</span>
                        ))
                      : <span className="text-zinc-500 text-xs">No data</span>}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-zinc-500 mb-1.5">PE — Support</div>
                  <div className="flex flex-wrap gap-1.5">
                    {signal.oi_walls.pe_walls.length > 0
                      ? signal.oi_walls.pe_walls.map((s, i) => (
                          <span key={i} className="px-2 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-xs font-mono">{fmt(s)}</span>
                        ))
                      : <span className="text-zinc-500 text-xs">No data</span>}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* ── Position management reference */}
          <Card className="bg-zinc-900 border-zinc-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-zinc-400">Position Management Rules (Layer 5)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                {[
                  { label: 'Profit Target', value: '50% of max credit', color: 'text-emerald-400' },
                  { label: 'Time Stop',     value: 'Close at 1 DTE',    color: 'text-amber-400'   },
                  { label: 'Loss Stop',     value: '2× credit received', color: 'text-red-400'    },
                  { label: 'Delta Breach',  value: '±0.30/lot → hedge', color: 'text-purple-400'  },
                ].map(item => (
                  <div key={item.label} className="p-2.5 rounded bg-zinc-800/60 space-y-1">
                    <div className="text-zinc-500">{item.label}</div>
                    <div className={cn('font-semibold', item.color)}>{item.value}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* ── History ─────────────────────── */}
          {showHistory && (
            <Card className="bg-zinc-900 border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-zinc-400">Signal History (last 10)</CardTitle>
              </CardHeader>
              <CardContent>
                <HistoryPanel symbol={symbol} />
              </CardContent>
            </Card>
          )}

          {/* Footer */}
          <div className="flex flex-wrap items-center gap-4 px-1 text-xs text-zinc-600">
            {signal.vix_current != null && (
              <span>VIX <span className={cn('font-bold',
                signal.vix_current >= 20 ? 'text-red-400' :
                signal.vix_current >= 14 ? 'text-amber-400' : 'text-emerald-400')}>
                {signal.vix_current.toFixed(2)}</span></span>
            )}
            {signal.spot != null && (
              <span>{symbol} <span className="font-bold text-zinc-400">{fmt(signal.spot, 2)}</span></span>
            )}
            <span className="ml-auto">Auto-refreshes every 60s · {execMode} mode</span>
          </div>

          {signal.errors.length > 0 && (
            <div className="p-3 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs space-y-1">
              {signal.errors.map((e, i) => <div key={i}>⚠ {e}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
