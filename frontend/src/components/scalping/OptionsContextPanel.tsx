import { Badge } from '@/components/ui/badge'
import { useAutoTradeStore } from '@/stores/autoTradeStore'

export function OptionsContextPanel() {
  const ctx = useAutoTradeStore((s) => s.optionsContext)

  if (!ctx) {
    return (
      <div className="text-center text-[10px] text-muted-foreground py-2">
        Options context loading...
      </div>
    )
  }

  const age = Math.round((Date.now() - ctx.lastUpdated) / 1000)
  const ageStr = age < 60 ? `${age}s` : `${Math.round(age / 60)}m`

  return (
    <div className="space-y-1.5 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-medium text-foreground">Options Context</span>
        <span className="text-[9px] text-muted-foreground">Updated {ageStr} ago</span>
      </div>

      {/* PCR Gauge */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">PCR</span>
        <div className="flex items-center gap-1">
          <div className="w-16 h-2 bg-muted rounded overflow-hidden relative">
            <div
              className={`absolute top-0 h-full rounded ${
                ctx.pcr > 1.0 ? 'bg-red-500' : 'bg-green-500'
              }`}
              style={{
                width: `${Math.min(100, (ctx.pcr / 2) * 100)}%`,
              }}
            />
            {/* Center mark at PCR=1.0 */}
            <div className="absolute left-1/2 top-0 w-px h-full bg-foreground/30" />
          </div>
          <span
            className={`font-bold tabular-nums ${
              ctx.pcr > 1.2 ? 'text-red-500' : ctx.pcr < 0.8 ? 'text-green-500' : 'text-foreground'
            }`}
          >
            {ctx.pcr.toFixed(2)}
          </span>
        </div>
      </div>

      {/* Max Pain */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">Max Pain</span>
        <span className="font-mono tabular-nums">
          {ctx.maxPainStrike.toFixed(0)}{' '}
          <span
            className={`text-[10px] ${ctx.spotVsMaxPain > 0 ? 'text-green-500' : 'text-red-500'}`}
          >
            ({ctx.spotVsMaxPain > 0 ? '+' : ''}{ctx.spotVsMaxPain.toFixed(0)})
          </span>
        </span>
      </div>

      {/* GEX */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">Net GEX</span>
        <span
          className={`font-bold tabular-nums ${
            ctx.netGEX > 50 ? 'text-green-500' : ctx.netGEX < -50 ? 'text-red-500' : 'text-foreground'
          }`}
        >
          {ctx.netGEX > 0 ? '+' : ''}{ctx.netGEX.toFixed(0)}
        </span>
      </div>

      {/* ATM IV */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">ATM IV</span>
        <span className="font-bold tabular-nums">{ctx.atmIV.toFixed(1)}%</span>
      </div>

      {/* IV Skew */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">IV Skew</span>
        <span
          className={`tabular-nums ${
            ctx.ivSkew > 2 ? 'text-green-500' : ctx.ivSkew < -2 ? 'text-red-500' : 'text-foreground'
          }`}
        >
          {ctx.ivSkew > 0 ? '+' : ''}{ctx.ivSkew.toFixed(1)}
        </span>
      </div>

      {/* CE/PE IV */}
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-green-500">CE IV: {ctx.ceIV.toFixed(1)}%</span>
        <span className="text-red-500">PE IV: {ctx.peIV.toFixed(1)}%</span>
      </div>

      {/* Straddle */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">Straddle</span>
        <span className="font-mono tabular-nums">{ctx.straddlePrice.toFixed(1)}</span>
      </div>

      {/* IV Percentile */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">IV %ile</span>
        <div className="flex items-center gap-1">
          <div className="w-12 h-1.5 bg-muted rounded overflow-hidden">
            <div
              className={`h-full rounded ${
                ctx.ivPercentile > 70 ? 'bg-red-500' : ctx.ivPercentile > 30 ? 'bg-yellow-500' : 'bg-green-500'
              }`}
              style={{ width: `${ctx.ivPercentile}%` }}
            />
          </div>
          <span className="tabular-nums">{ctx.ivPercentile.toFixed(0)}</span>
        </div>
      </div>

      {/* Gamma strikes */}
      {ctx.topGammaStrikes.length > 0 && (
        <div>
          <span className="text-[10px] text-muted-foreground">Top Gamma:</span>
          <div className="flex flex-wrap gap-1 mt-0.5">
            {ctx.topGammaStrikes.slice(0, 5).map((s) => (
              <Badge key={s} variant="outline" className="text-[9px] h-3.5 px-1 font-mono">
                {s}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
