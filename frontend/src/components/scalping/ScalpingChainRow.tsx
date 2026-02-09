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
}: {
  value: number
  max: number
  side: 'CE' | 'PE'
}) {
  if (!value || !max) return null
  const pct = Math.min((value / max) * 100, 100)

  return (
    <div
      className={cn(
        'absolute inset-y-0 pointer-events-none',
        side === 'CE'
          ? 'right-0 bg-gradient-to-l from-green-500/20 to-transparent'
          : 'left-0 bg-gradient-to-r from-red-500/20 to-transparent'
      )}
      style={{ width: `${pct}%` }}
    />
  )
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
}: ScalpingChainRowProps) {
  const ceLtpRef = useRef(ce?.ltp ?? 0)
  const peLtpRef = useRef(pe?.ltp ?? 0)

  return (
    <div
      className={cn(
        'grid grid-cols-[1fr_1fr_1fr_50px_1fr_1fr_1fr] items-center gap-0 h-7 text-xs cursor-pointer hover:bg-accent/50 border-b border-border/30',
        isATM && 'bg-yellow-500/10 border-y border-yellow-500/30',
        isSelected && 'bg-primary/10 ring-1 ring-primary/30'
      )}
      onClick={() => onSelectStrike(strike, ce?.symbol ?? null, pe?.symbol ?? null)}
    >
      {/* CE OI */}
      <div className="relative flex items-center justify-end h-full px-1.5 overflow-hidden">
        <HeatBar value={ce?.oi ?? 0} max={maxOI} side="CE" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {ce?.oi ? ce.oi.toLocaleString('en-IN') : '-'}
        </span>
      </div>

      {/* CE Volume */}
      <div className="relative flex items-center justify-end h-full px-1.5 overflow-hidden">
        <HeatBar value={ce?.volume ?? 0} max={maxVol} side="CE" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {ce?.volume ? ce.volume.toLocaleString('en-IN') : '-'}
        </span>
      </div>

      {/* CE LTP */}
      <div className="flex items-center justify-end h-full px-1.5">
        <FlashCell value={ce?.ltp} prevRef={ceLtpRef} className="font-medium" />
      </div>

      {/* Strike */}
      <div
        className={cn(
          'text-center font-bold tabular-nums px-1',
          isATM && 'text-yellow-500'
        )}
      >
        {strike}
      </div>

      {/* PE LTP */}
      <div className="flex items-center justify-start h-full px-1.5">
        <FlashCell value={pe?.ltp} prevRef={peLtpRef} className="font-medium" />
      </div>

      {/* PE Volume */}
      <div className="relative flex items-center justify-start h-full px-1.5 overflow-hidden">
        <HeatBar value={pe?.volume ?? 0} max={maxVol} side="PE" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {pe?.volume ? pe.volume.toLocaleString('en-IN') : '-'}
        </span>
      </div>

      {/* PE OI */}
      <div className="relative flex items-center justify-start h-full px-1.5 overflow-hidden">
        <HeatBar value={pe?.oi ?? 0} max={maxOI} side="PE" />
        <span className="relative z-10 tabular-nums text-muted-foreground">
          {pe?.oi ? pe.oi.toLocaleString('en-IN') : '-'}
        </span>
      </div>
    </div>
  )
})
