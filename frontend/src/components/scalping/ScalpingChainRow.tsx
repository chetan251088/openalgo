import { memo, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import type { OptionData } from '@/types/scalping'

interface ScalpingChainRowProps {
  strike: number
  ce: OptionData | null
  pe: OptionData | null
  isATM: boolean
  isSelected: boolean
  maxOI: number
  maxVol: number
  onSelectStrike: (strike: number, ceSymbol: string | null, peSymbol: string | null) => void
  onSelectCE: (strike: number, ceSymbol: string | null) => void
  onSelectPE: (strike: number, peSymbol: string | null) => void
}

function formatInLakhs(value: number | undefined): string {
  if (value == null || value === 0) return '-'

  const abs = Math.abs(value)
  if (abs >= 100000) {
    if (abs >= 1000000) return `${(value / 100000).toFixed(1)}L`
    return `${(value / 100000).toFixed(2)}L`
  }

  if (abs >= 1000) return `${(value / 1000).toFixed(1)}K`
  return value.toLocaleString('en-IN')
}

function FlashCell({
  value,
  prevRef,
  className,
  format = 'price',
}: {
  value: number | undefined
  prevRef: React.MutableRefObject<number>
  className?: string
  format?: 'price' | 'int'
}) {
  const cellRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (value == null || value === prevRef.current) return
    const el = cellRef.current
    if (!el) return

    const dir = value > prevRef.current ? 'flash-green' : 'flash-red'
    el.classList.add(dir)
    const t = setTimeout(() => el.classList.remove(dir), 300)
    prevRef.current = value
    return () => clearTimeout(t)
  }, [value, prevRef])

  const display =
    value == null
      ? '-'
      : format === 'int'
        ? value.toLocaleString('en-IN')
        : value.toFixed(2)

  return (
    <span ref={cellRef} className={cn('tabular-nums transition-colors', className)}>
      {display}
    </span>
  )
}

function HeatBar({
  value,
  max,
  side,
  kind,
}: {
  value: number
  max: number
  side: 'CE' | 'PE'
  kind: 'oi' | 'volume'
}) {
  if (!value || !max) return null
  const pct = Math.min((value / max) * 100, 100)
  const intensity = Math.min(0.2 + pct / 180, 0.75)

  return (
    <div
      className={cn(
        'absolute inset-y-0 pointer-events-none transition-[width,opacity] duration-200',
        side === 'CE'
          ? kind === 'oi'
            ? 'right-0 bg-gradient-to-l from-emerald-500 to-transparent'
            : 'right-0 bg-gradient-to-l from-teal-500 to-transparent'
          : kind === 'oi'
            ? 'left-0 bg-gradient-to-r from-rose-500 to-transparent'
            : 'left-0 bg-gradient-to-r from-orange-500 to-transparent',
        pct > 72 && 'animate-pulse'
      )}
      style={{ width: `${pct}%`, opacity: intensity }}
    />
  )
}

// Custom comparator: only re-render when displayed values actually change
function areEqual(prev: ScalpingChainRowProps, next: ScalpingChainRowProps): boolean {
  if (prev.strike !== next.strike) return false
  if (prev.isATM !== next.isATM) return false
  if (prev.isSelected !== next.isSelected) return false
  if (prev.maxOI !== next.maxOI) return false
  if (prev.maxVol !== next.maxVol) return false
  if (prev.onSelectStrike !== next.onSelectStrike) return false
  if (prev.onSelectCE !== next.onSelectCE) return false
  if (prev.onSelectPE !== next.onSelectPE) return false
  // Compare actual displayed values, not object references
  if (prev.ce?.ltp !== next.ce?.ltp) return false
  if (prev.ce?.oi !== next.ce?.oi) return false
  if (prev.ce?.volume !== next.ce?.volume) return false
  if (prev.pe?.ltp !== next.pe?.ltp) return false
  if (prev.pe?.oi !== next.pe?.oi) return false
  if (prev.pe?.volume !== next.pe?.volume) return false
  return true
}

export const ScalpingChainRow = memo(function ScalpingChainRow({
  strike,
  ce,
  pe,
  isATM,
  isSelected,
  maxOI,
  maxVol,
  onSelectStrike,
  onSelectCE,
  onSelectPE,
}: ScalpingChainRowProps) {
  const ceLtpRef = useRef(ce?.ltp ?? 0)
  const peLtpRef = useRef(pe?.ltp ?? 0)
  const cePressure = Math.min((((ce?.oi ?? 0) / maxOI + (ce?.volume ?? 0) / maxVol) / 2) * 100, 100)
  const pePressure = Math.min((((pe?.oi ?? 0) / maxOI + (pe?.volume ?? 0) / maxVol) / 2) * 100, 100)

  return (
    <div
      className={cn(
        'grid grid-cols-[1fr_1fr_1fr_50px_1fr_1fr_1fr] items-center gap-0 h-7 text-xs hover:bg-accent/50 border-b border-border/30',
        isATM && 'bg-yellow-500/10 border-y border-yellow-500/30',
        isSelected && 'bg-primary/10 ring-1 ring-primary/30'
      )}
    >
      {/* CE OI */}
      <div
        className="relative flex items-center justify-end h-full px-1.5 overflow-hidden cursor-pointer"
        onClick={() => onSelectCE(strike, ce?.symbol ?? null)}
        title={ce?.symbol ?? 'Select CE'}
      >
        <HeatBar value={ce?.oi ?? 0} max={maxOI} side="CE" kind="oi" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {formatInLakhs(ce?.oi)}
        </span>
      </div>

      {/* CE Volume */}
      <div
        className="relative flex items-center justify-end h-full px-1.5 overflow-hidden cursor-pointer"
        onClick={() => onSelectCE(strike, ce?.symbol ?? null)}
        title={ce?.symbol ?? 'Select CE'}
      >
        <HeatBar value={ce?.volume ?? 0} max={maxVol} side="CE" kind="volume" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {formatInLakhs(ce?.volume)}
        </span>
      </div>

      {/* CE LTP */}
      <div
        className="relative flex items-center justify-end h-full px-1.5 overflow-hidden cursor-pointer"
        onClick={() => onSelectCE(strike, ce?.symbol ?? null)}
        title={ce?.symbol ?? 'Select CE'}
      >
        <div
          className="absolute inset-y-0 right-0 bg-gradient-to-l from-emerald-500/35 to-transparent transition-[width] duration-200"
          style={{ width: `${cePressure}%` }}
        />
        <FlashCell value={ce?.ltp} prevRef={ceLtpRef} className="relative z-10 font-medium" />
      </div>

      {/* Strike */}
      <div
        className={cn(
          'text-center font-bold tabular-nums px-1 cursor-pointer',
          isATM && 'text-yellow-500'
        )}
        onClick={() => onSelectStrike(strike, ce?.symbol ?? null, pe?.symbol ?? null)}
        title="Load CE and PE for this strike"
      >
        {strike}
      </div>

      {/* PE LTP */}
      <div
        className="relative flex items-center justify-start h-full px-1.5 overflow-hidden cursor-pointer"
        onClick={() => onSelectPE(strike, pe?.symbol ?? null)}
        title={pe?.symbol ?? 'Select PE'}
      >
        <div
          className="absolute inset-y-0 left-0 bg-gradient-to-r from-rose-500/35 to-transparent transition-[width] duration-200"
          style={{ width: `${pePressure}%` }}
        />
        <FlashCell value={pe?.ltp} prevRef={peLtpRef} className="relative z-10 font-medium" />
      </div>

      {/* PE Volume */}
      <div
        className="relative flex items-center justify-start h-full px-1.5 overflow-hidden cursor-pointer"
        onClick={() => onSelectPE(strike, pe?.symbol ?? null)}
        title={pe?.symbol ?? 'Select PE'}
      >
        <HeatBar value={pe?.volume ?? 0} max={maxVol} side="PE" kind="volume" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {formatInLakhs(pe?.volume)}
        </span>
      </div>

      {/* PE OI */}
      <div
        className="relative flex items-center justify-start h-full px-1.5 overflow-hidden cursor-pointer"
        onClick={() => onSelectPE(strike, pe?.symbol ?? null)}
        title={pe?.symbol ?? 'Select PE'}
      >
        <HeatBar value={pe?.oi ?? 0} max={maxOI} side="PE" kind="oi" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {formatInLakhs(pe?.oi)}
        </span>
      </div>
    </div>
  )
}, areEqual)
