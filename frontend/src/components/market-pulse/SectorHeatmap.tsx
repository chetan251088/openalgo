import { useState } from 'react'
import type { SectorData } from '@/api/market-pulse'
import { cn } from '@/lib/utils'

interface SectorHeatmapProps {
  sectors: SectorData[]
}

export function SectorHeatmap({ sectors }: SectorHeatmapProps) {
  const [period, setPeriod] = useState<'1d' | '5d' | '20d'>('5d')

  const getValue = (sector: SectorData) => {
    if (period === '1d') return sector.return_1d ?? 0
    if (period === '20d') return sector.return_20d ?? 0
    return sector.return_5d
  }

  const sorted = [...sectors].sort((left, right) => getValue(right) - getValue(left))
  const maxAbs = Math.max(...sorted.map((sector) => Math.abs(getValue(sector))), 1)

  return (
    <section className="rounded-2xl border border-[#1f3340] bg-[#0d141d]/90 p-4 font-mono">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-[0.28em] text-[#7fa2b1]">
          Sector Heatmap
        </span>
        <div className="flex items-center gap-1 rounded-full border border-[#223847] bg-[#0b1219] p-1">
          {(['1d', '5d', '20d'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setPeriod(value)}
              className={cn(
                'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.22em] transition-colors',
                period === value
                  ? 'bg-[#164e63]/40 text-[#67e8f9]'
                  : 'text-[#6b8797] hover:text-[#d8eef6]',
              )}
            >
              {value}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {sorted.map((sector) => {
          const value = getValue(sector)
          const width = (Math.abs(value) / maxAbs) * 100
          const positive = value >= 0
          return (
            <div key={sector.key} className="grid grid-cols-[78px_minmax(0,1fr)_70px] items-center gap-3 text-xs">
              <span className="text-right text-[#9ac0cd]">{sector.key}</span>
              <div className="h-4 overflow-hidden rounded-full bg-[#15232d]">
                <div
                  className={cn(
                    'h-4 rounded-full transition-all',
                    positive ? 'bg-[linear-gradient(90deg,#14532d,#22c55e)]' : 'bg-[linear-gradient(90deg,#7f1d1d,#ef4444)]',
                  )}
                  style={{ width: `${width}%` }}
                />
              </div>
              <span className={cn('text-right', positive ? 'text-[#4ade80]' : 'text-[#f87171]')}>
                {value >= 0 ? '+' : ''}
                {value.toFixed(1)}%
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
