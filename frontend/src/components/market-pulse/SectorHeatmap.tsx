import type { SectorData } from '@/api/market-pulse'
import { useState } from 'react'

interface SectorHeatmapProps {
  sectors: SectorData[]
}

export function SectorHeatmap({ sectors }: SectorHeatmapProps) {
  const [period, setPeriod] = useState<'1d' | '5d' | '20d'>('1d')

  const getReturnValue = (sector: SectorData): number => {
    switch (period) {
      case '1d':
        return sector.return_1d ?? 0
      case '5d':
        return sector.return_5d
      case '20d':
        return sector.return_20d ?? 0
    }
  }

  const sorted = [...sectors].sort((a, b) => getReturnValue(b) - getReturnValue(a))

  const getColor = (value: number) => {
    if (value >= 2) return 'bg-green-600'
    if (value >= 1) return 'bg-green-500'
    if (value >= 0) return 'bg-green-400'
    if (value >= -1) return 'bg-red-400'
    return 'bg-red-600'
  }

  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-mono text-sm font-bold text-white">Sector Heatmap</h3>
        <div className="flex gap-2">
          {(['1d', '5d', '20d'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-2 py-1 text-xs rounded ${
                period === p ? 'bg-blue-600 text-white' : 'bg-[#0d1117] text-gray-400 hover:text-gray-300'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        {sorted.map((sector) => {
          const value = getReturnValue(sector)
          return (
            <div key={sector.key} className="flex items-center gap-3">
              <span className="w-24 text-xs font-mono text-gray-400">{sector.name}</span>
              <div className="flex-1 bg-[#0d1117] rounded h-5 overflow-hidden">
                <div
                  className={`h-full ${getColor(value)} flex items-center justify-end px-2 text-xs font-bold text-black`}
                  style={{ width: `${Math.max(Math.abs(value) * 15, 20)}%` }}
                >
                  {value >= 0 && value.toFixed(2)}%
                </div>
              </div>
              <span className="w-12 text-right text-xs font-mono text-gray-400">{value.toFixed(2)}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
