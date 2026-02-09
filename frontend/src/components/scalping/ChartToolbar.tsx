import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useScalpingStore } from '@/stores/scalpingStore'

const TIMEFRAMES = [
  { label: '30s', sec: 30 },
  { label: '1m', sec: 60 },
  { label: '3m', sec: 180 },
  { label: '5m', sec: 300 },
  { label: '15m', sec: 900 },
  { label: '30m', sec: 1800 },
  { label: '1h', sec: 3600 },
] as const

interface ChartToolbarProps {
  showEma9: boolean
  showEma21: boolean
  showSupertrend: boolean
  showVwap: boolean
  onToggleEma9: () => void
  onToggleEma21: () => void
  onToggleSupertrend: () => void
  onToggleVwap: () => void
}

export function ChartToolbar({
  showEma9,
  showEma21,
  showSupertrend,
  showVwap,
  onToggleEma9,
  onToggleEma21,
  onToggleSupertrend,
  onToggleVwap,
}: ChartToolbarProps) {
  const chartInterval = useScalpingStore((s) => s.chartInterval)
  const setChartInterval = useScalpingStore((s) => s.setChartInterval)

  return (
    <div className="flex items-center gap-0.5 px-1 py-0.5 border-b bg-card/50 shrink-0">
      {/* Timeframe selector */}
      {TIMEFRAMES.map((tf) => (
        <Button
          key={tf.sec}
          variant="ghost"
          size="sm"
          className={`h-5 px-1.5 text-[10px] font-medium ${
            chartInterval === tf.sec
              ? 'text-foreground bg-muted'
              : 'text-muted-foreground/50'
          }`}
          onClick={() => setChartInterval(tf.sec)}
        >
          {tf.label}
        </Button>
      ))}

      <BarCloseCountdown intervalSec={chartInterval} />

      <div className="w-px h-3 bg-border/50 mx-0.5" />

      {/* Indicator toggles */}
      <IndicatorToggle label="EMA9" active={showEma9} color="text-amber-500" onClick={onToggleEma9} />
      <IndicatorToggle label="EMA21" active={showEma21} color="text-violet-500" onClick={onToggleEma21} />
      <IndicatorToggle label="ST" active={showSupertrend} color="text-cyan-500" onClick={onToggleSupertrend} />
      <IndicatorToggle label="VWAP" active={showVwap} color="text-pink-500" onClick={onToggleVwap} />
    </div>
  )
}

function BarCloseCountdown({ intervalSec }: { intervalSec: number }) {
  const [remaining, setRemaining] = useState(() => calcRemaining(intervalSec))

  useEffect(() => {
    setRemaining(calcRemaining(intervalSec))
    const id = setInterval(() => {
      setRemaining(calcRemaining(intervalSec))
    }, 250)
    return () => clearInterval(id)
  }, [intervalSec])

  const urgent = remaining <= 5

  return (
    <span
      className={`ml-1 text-[10px] font-mono tabular-nums ${
        urgent ? 'text-orange-400' : 'text-muted-foreground/70'
      }`}
    >
      {formatCountdown(remaining, intervalSec)}
    </span>
  )
}

function calcRemaining(intervalSec: number): number {
  const nowSec = Date.now() / 1000
  const candleStart = Math.floor(nowSec / intervalSec) * intervalSec
  const candleEnd = candleStart + intervalSec
  return Math.max(0, Math.ceil(candleEnd - nowSec))
}

function formatCountdown(sec: number, intervalSec: number): string {
  if (intervalSec >= 3600) {
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const s = sec % 60
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  if (intervalSec >= 60) {
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }
  return `${sec}s`
}

function IndicatorToggle({
  label,
  active,
  color,
  onClick,
}: {
  label: string
  active: boolean
  color: string
  onClick: () => void
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className={`h-5 px-1.5 text-[10px] font-medium ${active ? color : 'text-muted-foreground/50'}`}
      onClick={onClick}
    >
      {label}
    </Button>
  )
}
