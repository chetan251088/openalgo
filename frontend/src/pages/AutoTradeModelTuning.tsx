import { Calendar, Play, RefreshCw, Settings } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  applyModelTuningRecommendation,
  fetchModelTuningRecommendations,
  fetchModelTuningStatus,
  runModelTuning,
} from '@/api/ai-scalper'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
import type { ModelTuningRun, ModelTuningStatus } from '@/types/ai-scalper'

const PROVIDERS = [
  { label: 'OpenAI', value: 'openai' },
  { label: 'Anthropic', value: 'anthropic' },
  { label: 'Ollama (Local)', value: 'ollama' },
]

const SCHEDULE_TYPES = [
  { label: 'Off', value: 'off' },
  { label: 'Interval', value: 'interval' },
  { label: 'Daily', value: 'daily' },
]

const UNDERLYING_PRESETS = [
  { label: 'Auto (use current)', value: 'AUTO' },
  { label: 'NIFTY', value: 'NIFTY' },
  { label: 'SENSEX', value: 'SENSEX' },
]

function formatTime(value?: string | null) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('en-IN')
}

function formatValue(value: unknown) {
  if (value === null || value === undefined) return '--'
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toString() : value.toFixed(2)
  }
  return String(value)
}

export default function AutoTradeModelTuning() {
  const [status, setStatus] = useState<ModelTuningStatus | null>(null)
  const [runs, setRuns] = useState<ModelTuningRun[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  const [enabled, setEnabled] = useState(true)
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [autoApplyPaper, setAutoApplyPaper] = useState(true)
  const [applyClamps, setApplyClamps] = useState(true)
  const [minTrades, setMinTrades] = useState('30')
  const [notifyEmail, setNotifyEmail] = useState(false)
  const [notifyTelegram, setNotifyTelegram] = useState(false)
  const [underlyingPreset, setUnderlyingPreset] = useState('AUTO')

  const [scheduleType, setScheduleType] = useState('off')
  const [intervalMins, setIntervalMins] = useState('30')
  const [dailyTime, setDailyTime] = useState('20:00')

  const hydratedRef = useRef(false)

  const latestRun = useMemo(() => status?.last_run ?? runs[0], [status, runs])
  const currentConfig = status?.current ?? {}
  const modeLabel =
    status?.paper_mode === undefined || status?.paper_mode === null
      ? '--'
      : status.paper_mode
        ? 'Paper'
        : 'Live'

  const loadStatus = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await fetchModelTuningStatus()
      setStatus(data)
      if (!hydratedRef.current) {
        setEnabled(data.enabled)
        setProvider(data.provider || 'openai')
        setModel(data.model ?? '')
        setBaseUrl(data.base_url ?? '')
        setAutoApplyPaper(data.auto_apply_paper)
        setApplyClamps(data.apply_clamps)
        setMinTrades(String(data.min_trades ?? 30))
        setNotifyEmail(data.notify_email)
        setNotifyTelegram(data.notify_telegram)
        setUnderlyingPreset(data.underlying ?? 'AUTO')
        const schedule = data.schedule ?? { enabled: false }
        if (!schedule.enabled) {
          setScheduleType('off')
        } else if (schedule.type === 'daily') {
          setScheduleType('daily')
          setDailyTime(schedule.time_of_day ?? '20:00')
        } else {
          setScheduleType('interval')
          const mins = schedule.interval_s ? Math.max(1, Math.round(schedule.interval_s / 60)) : 30
          setIntervalMins(String(mins))
        }
        hydratedRef.current = true
      }
    } catch (error) {
      console.error('Failed to load model tuning status', error)
      toast.error('Failed to load model tuning status')
    } finally {
      setIsLoading(false)
    }
  }, [])

  const loadRuns = useCallback(async () => {
    try {
      const data = await fetchModelTuningRecommendations(20)
      setRuns(data.runs ?? [])
    } catch (error) {
      console.error('Failed to load model tuning runs', error)
      toast.error('Failed to load model tuning runs')
    }
  }, [])

  useEffect(() => {
    loadStatus()
    loadRuns()
  }, [loadStatus, loadRuns])

  const buildPayload = (runNow: boolean) => {
    const intervalValue = Number.parseInt(intervalMins, 10)
    const minTradesValue = Number.parseInt(minTrades, 10)
    return {
      enabled,
      provider,
      model: model || undefined,
      base_url: baseUrl || undefined,
      auto_apply_paper: autoApplyPaper,
      apply_clamps: applyClamps,
      min_trades: Number.isFinite(minTradesValue) ? minTradesValue : 30,
      notify_email: notifyEmail,
      notify_telegram: notifyTelegram,
      underlying: underlyingPreset,
      schedule_type: scheduleType,
      interval_s:
        scheduleType === 'interval' && Number.isFinite(intervalValue)
          ? intervalValue * 60
          : undefined,
      time_of_day: scheduleType === 'daily' ? dailyTime : undefined,
      run_now: runNow,
    }
  }

  const handleRun = async () => {
    setIsSaving(true)
    try {
      const response = await runModelTuning(buildPayload(true))
      if (response.status !== 'success') {
        toast.error(response.message || 'Failed to run model tuning')
      } else {
        toast.success('Model tuning queued')
      }
      await loadStatus()
      await loadRuns()
    } catch (error) {
      console.error('Failed to run model tuning', error)
      toast.error('Failed to run model tuning')
    } finally {
      setIsSaving(false)
    }
  }

  const handleSaveSchedule = async () => {
    setIsSaving(true)
    try {
      const response = await runModelTuning(buildPayload(false))
      if (response.status !== 'success') {
        toast.error(response.message || 'Failed to update schedule')
      } else {
        toast.success('Model tuning schedule updated')
      }
      await loadStatus()
    } catch (error) {
      console.error('Failed to save schedule', error)
      toast.error('Failed to update schedule')
    } finally {
      setIsSaving(false)
    }
  }

  const handleApply = async (runId?: string) => {
    if (!runId) return
    setIsSaving(true)
    try {
      const response = await applyModelTuningRecommendation(runId)
      if (response.status !== 'success') {
        toast.error(response.message || 'Failed to apply recommendation')
      } else {
        toast.success('Recommendation applied')
      }
      await loadStatus()
      await loadRuns()
    } catch (error) {
      console.error('Failed to apply recommendation', error)
      toast.error('Failed to apply recommendation')
    } finally {
      setIsSaving(false)
    }
  }

  const changes = Object.entries(latestRun?.recommendations ?? {})

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">AutoTrade Model Tuning</h1>
          <p className="text-sm text-muted-foreground">
            Use cloud models to score performance and suggest safer parameter improvements.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={loadStatus} disabled={isLoading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button onClick={handleRun} disabled={isSaving}>
            <Play className="mr-2 h-4 w-4" />
            Run Tuning
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Settings className="h-4 w-4" />
              Model Settings
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Enabled</p>
              <Select value={enabled ? 'on' : 'off'} onValueChange={(value) => setEnabled(value === 'on')}>
                <SelectTrigger>
                  <SelectValue placeholder="Enabled" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="on">On</SelectItem>
                  <SelectItem value="off">Off</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Provider</p>
              <Select value={provider} onValueChange={setProvider}>
                <SelectTrigger>
                  <SelectValue placeholder="Provider" />
                </SelectTrigger>
                <SelectContent>
                  {PROVIDERS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 md:col-span-2">
              <p className="text-xs text-muted-foreground">Model</p>
              <Input
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="gpt-4o-mini / claude-3-5-haiku"
              />
            </div>
            <div className="space-y-2 md:col-span-2">
              <p className="text-xs text-muted-foreground">Base URL (optional)</p>
              <Input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="http://127.0.0.1:11434"
              />
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Min Trades</p>
              <Input value={minTrades} onChange={(event) => setMinTrades(event.target.value)} />
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Auto-Apply (Paper)</p>
              <Select
                value={autoApplyPaper ? 'on' : 'off'}
                onValueChange={(value) => setAutoApplyPaper(value === 'on')}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Auto apply" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="on">On</SelectItem>
                  <SelectItem value="off">Off</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Safety Clamps</p>
              <Select
                value={applyClamps ? 'on' : 'off'}
                onValueChange={(value) => setApplyClamps(value === 'on')}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Clamps" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="on">On</SelectItem>
                  <SelectItem value="off">Off</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Notify Email</p>
              <Select
                value={notifyEmail ? 'on' : 'off'}
                onValueChange={(value) => setNotifyEmail(value === 'on')}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Email notify" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="on">On</SelectItem>
                  <SelectItem value="off">Off</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Notify Telegram</p>
              <Select
                value={notifyTelegram ? 'on' : 'off'}
                onValueChange={(value) => setNotifyTelegram(value === 'on')}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Telegram notify" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="on">On</SelectItem>
                  <SelectItem value="off">Off</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2 md:col-span-2">
              <p className="text-xs text-muted-foreground">Underlying Preset</p>
              <Select value={underlyingPreset} onValueChange={setUnderlyingPreset}>
                <SelectTrigger>
                  <SelectValue placeholder="Underlying preset" />
                </SelectTrigger>
                <SelectContent>
                  {UNDERLYING_PRESETS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Calendar className="h-4 w-4" />
              Schedule
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Schedule Type</p>
              <Select value={scheduleType} onValueChange={setScheduleType}>
                <SelectTrigger>
                  <SelectValue placeholder="Schedule" />
                </SelectTrigger>
                <SelectContent>
                  {SCHEDULE_TYPES.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {scheduleType === 'interval' && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">Interval (mins)</p>
                <Input value={intervalMins} onChange={(event) => setIntervalMins(event.target.value)} />
              </div>
            )}
            {scheduleType === 'daily' && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">Time (HH:MM)</p>
                <Input value={dailyTime} onChange={(event) => setDailyTime(event.target.value)} />
              </div>
            )}
            <div className="flex items-end gap-2 md:col-span-2">
              <Button variant="secondary" onClick={handleSaveSchedule} disabled={isSaving}>
                Save Schedule
              </Button>
              {status?.schedule?.enabled && (
                <Badge variant="secondary">
                  Next: {formatTime(status.schedule.next_run_time ?? undefined)}
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Latest Recommendation</CardTitle>
          <div className="flex items-center gap-2">
            {latestRun?.status && <Badge variant="secondary">{latestRun.status}</Badge>}
            {latestRun?.score !== null && latestRun?.score !== undefined && (
              <Badge variant="secondary">Score {latestRun.score}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <span>Run: {formatTime(latestRun?.created_iso)}</span>
            <span>Provider: {latestRun?.provider ?? '--'}</span>
            <span>Mode: {modeLabel}</span>
          </div>
          <div className="text-sm">{latestRun?.notes || 'No notes provided.'}</div>
          <div className="grid gap-2 md:grid-cols-2">
            {changes.length ? (
              changes.map(([key, value]) => (
                <div key={key} className="rounded-md border border-border px-3 py-2 text-sm">
                  <div className="text-xs text-muted-foreground">{key}</div>
                  <div className="font-medium">
                    {formatValue(currentConfig[key])} → {formatValue(value)}
                  </div>
                </div>
              ))
            ) : (
              <div className="text-sm text-muted-foreground">No recommendations yet.</div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              onClick={() => handleApply(latestRun?.run_id)}
              disabled={!latestRun?.run_id || latestRun?.applied || isSaving || !changes.length}
            >
              Apply Recommendation
            </Button>
            {latestRun?.applied && (
              <Badge className="bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/20">
                Applied {formatTime(latestRun.applied_iso ?? undefined)}
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run History</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Applied</TableHead>
                <TableHead>Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.length ? (
                runs.map((run) => (
                  <TableRow key={run.run_id}>
                    <TableCell>{formatTime(run.created_iso)}</TableCell>
                    <TableCell>{run.status}</TableCell>
                    <TableCell>{run.score ?? '--'}</TableCell>
                    <TableCell>{run.applied ? 'Yes' : 'No'}</TableCell>
                    <TableCell className="max-w-[320px] truncate">
                      {run.error ? `Error: ${run.error}` : run.notes || '--'}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                    No tuning runs yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
