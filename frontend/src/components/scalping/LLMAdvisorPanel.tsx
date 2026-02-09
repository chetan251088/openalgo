import { useState, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { telegramApi } from '@/api/telegram'
import {
  fetchModelTuningStatus,
  fetchModelTuningRecommendations,
  runModelTuning,
  applyModelTuningRecommendation,
} from '@/api/ai-scalper'
import type { ModelTuningRun } from '@/types/ai-scalper'
import type { AutoTradeConfigFields } from '@/lib/scalpingPresets'

export function LLMAdvisorPanel() {
  const [loading, setLoading] = useState(false)
  const [provider, setProvider] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<ModelTuningRun | null>(null)
  const [error, setError] = useState<string | null>(null)

  const updateConfig = useAutoTradeStore((s) => s.updateConfig)
  const config = useAutoTradeStore((s) => s.config)
  const underlying = useScalpingStore((s) => s.underlying)

  // Fetch advisor status
  const checkStatus = useCallback(async () => {
    try {
      const status = await fetchModelTuningStatus()
      setProvider(status.provider || 'none')
      if (status.last_run) setLastRun(status.last_run)
      setError(null)
    } catch (err) {
      setError('Could not reach advisor')
    }
  }, [])

  // Run tuning and get recommendations
  const runAdvisor = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await runModelTuning({ underlying })
      if (result.status === 'success' && result.run_id) {
        // Fetch the latest recommendations
        const recs = await fetchModelTuningRecommendations(1)
        if (recs.runs?.length > 0) {
          setLastRun(recs.runs[0])
        }
      } else {
        setError(result.message || 'Tuning failed')
      }
    } catch (err) {
      setError('Advisor request failed')
    } finally {
      setLoading(false)
    }
  }, [underlying])

  // Apply recommendation to auto-trade config
  const applyRecommendation = useCallback(async () => {
    if (!lastRun?.run_id) return
    setLoading(true)
    try {
      await applyModelTuningRecommendation(lastRun.run_id)
      // Apply changes to local config
      const changes = lastRun.recommendations
      if (changes && typeof changes === 'object') {
        const configUpdate: Partial<AutoTradeConfigFields> = {}
        for (const [rawKey, rawValue] of Object.entries(changes)) {
          if (!(rawKey in config)) continue
          const key = rawKey as keyof AutoTradeConfigFields
          const currentValue = config[key]

          if (typeof currentValue === 'number') {
            const next = typeof rawValue === 'number' ? rawValue : Number(rawValue)
            if (Number.isFinite(next)) {
              ;(configUpdate as Record<string, number>)[key] = next
            }
            continue
          }

          if (typeof currentValue === 'boolean') {
            let next: boolean | null = null
            if (typeof rawValue === 'boolean') {
              next = rawValue
            } else if (typeof rawValue === 'number') {
              next = rawValue !== 0
            } else if (typeof rawValue === 'string') {
              const normalized = rawValue.trim().toLowerCase()
              if (['true', '1', 'yes', 'on', 'enabled'].includes(normalized)) next = true
              if (['false', '0', 'no', 'off', 'disabled'].includes(normalized)) next = false
            }
            if (next != null) {
              ;(configUpdate as Record<string, boolean>)[key] = next
            }
          }
        }
        if (Object.keys(configUpdate).length > 0) {
          updateConfig(configUpdate)
          if (config.telegramAlertsTune) {
            const changedKeys = Object.keys(configUpdate).join(', ')
            void telegramApi.sendBroadcast({
              message: `[AUTO TUNE] Applied recommendations for ${underlying}: ${changedKeys}`,
            }).catch(() => {})
          }
        }
      }
      setLastRun((prev) => (prev ? { ...prev, applied: true } : prev))
      setError(null)
    } catch (err) {
      setError('Failed to apply recommendation')
    } finally {
      setLoading(false)
    }
  }, [lastRun, updateConfig, config, underlying])

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium text-muted-foreground uppercase">LLM Advisor</span>
        {provider && (
          <Badge variant="outline" className="text-[9px] h-3.5 px-1">
            {provider}
          </Badge>
        )}
      </div>

      <div className="flex gap-1">
        <Button
          variant="outline"
          size="sm"
          className="h-5 text-[9px] flex-1"
          onClick={checkStatus}
          disabled={loading}
        >
          Check Status
        </Button>
        <Button
          variant="secondary"
          size="sm"
          className="h-5 text-[9px] flex-1"
          onClick={runAdvisor}
          disabled={loading}
        >
          {loading ? 'Running...' : 'Tune Config'}
        </Button>
      </div>

      {error && (
        <div className="text-[9px] text-red-500 px-1">{error}</div>
      )}

      {lastRun && (
        <div className="p-1.5 bg-muted/50 rounded text-[9px] space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Score</span>
            <span className="font-bold">{lastRun.score?.toFixed(1) ?? '—'}</span>
          </div>

          {lastRun.notes && (
            <p className="text-muted-foreground leading-tight">{lastRun.notes}</p>
          )}

          {Object.keys(lastRun.recommendations || {}).length > 0 && (
            <div className="space-y-0.5">
              <span className="text-muted-foreground">Changes:</span>
              {Object.entries(lastRun.recommendations).map(([key, value]) => (
                <div key={key} className="flex justify-between pl-1">
                  <span className="text-muted-foreground">{key}</span>
                  <span className="font-mono">{typeof value === 'number' ? value : String(value)}</span>
                </div>
              ))}
            </div>
          )}

          {!lastRun.applied && Object.keys(lastRun.recommendations || {}).length > 0 && (
            <Button
              variant="default"
              size="sm"
              className="h-5 text-[9px] w-full"
              onClick={applyRecommendation}
              disabled={loading}
            >
              Apply Recommendation
            </Button>
          )}

          {lastRun.applied && (
            <Badge variant="default" className="text-[9px] h-3.5">Applied</Badge>
          )}
        </div>
      )}
    </div>
  )
}
