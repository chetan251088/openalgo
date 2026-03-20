import type { MarketPulseData } from '@/api/market-pulse'
import { ModeSwitcher } from './ModeSwitcher'
import { RefreshCw } from 'lucide-react'

interface TickerBarProps {
  data: MarketPulseData | null
  mode: 'swing' | 'day'
  onModeChange: (mode: 'swing' | 'day') => void
  secondsAgo: number
  onRefresh: () => void
  isLoading: boolean
}

export function TickerBar({
  data,
  mode,
  onModeChange,
  secondsAgo,
  onRefresh,
  isLoading,
}: TickerBarProps) {
  const formatChange = (change: number | undefined) => {
    if (!change) return '0.00%'
    return `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`
  }

  const getTicker = (symbol: string) => {
    const item = data?.ticker[symbol]
    if (!item?.ltp) return null
    return {
      ltp: item.ltp.toFixed(2),
      change: formatChange(item.change_pct),
      changeColor: (item.change_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400',
    }
  }

  const mainTickers = ['NSE:NIFTY-INDEX', 'NSE:SENSEX-INDEX', 'NSE:BANKNIFTY-INDEX', 'NSE:INDIAVIX-INDEX', 'FOREX:USDINR']
  const topSectors = data?.sectors.slice(0, 4) ?? []

  return (
    <div className="bg-[#0d1117] border-b border-[#30363d] px-6 py-4 font-mono text-sm">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <ModeSwitcher mode={mode} onChange={onModeChange} />
          <span className="text-gray-500 text-xs">
            {secondsAgo}s ago {data?.updated_at && `(${new Date(data.updated_at).toLocaleTimeString()})`}
          </span>
        </div>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="flex items-center gap-2 px-3 py-1 rounded bg-blue-900/30 text-blue-400 hover:bg-blue-900/50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-12 gap-3 text-xs">
        {mainTickers.map((ticker) => {
          const info = getTicker(ticker)
          if (!info) return null
          const label = ticker.split(':')[1].replace('-INDEX', '')
          return (
            <div key={ticker} className="flex items-center gap-2">
              <span className="text-gray-400 w-20">{label}</span>
              <span className="font-bold text-white">{info.ltp}</span>
              <span className={info.changeColor}>{info.change}</span>
            </div>
          )
        })}

        {topSectors.map((sector) => (
          <div key={sector.key} className="flex items-center gap-2">
            <span className="text-gray-400 w-16">{sector.name.slice(0, 8)}</span>
            <span className={sector.return_1d ?? 0 >= 0 ? 'text-green-400' : 'text-red-400'}>
              {((sector.return_1d ?? 0) >= 0 ? '+' : '')}{(sector.return_1d ?? 0).toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
