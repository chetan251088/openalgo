import { Badge } from '@/components/ui/badge'
import { useAutoTradeStore } from '@/stores/autoTradeStore'
import { PRESETS } from '@/lib/scalpingPresets'
import { useMarketClock } from '@/hooks/useMarketClock'

export function AutoTradePresets() {
  const activePresetId = useAutoTradeStore((s) => s.activePresetId)
  const applyPreset = useAutoTradeStore((s) => s.applyPreset)
  const { isExpiryDay } = useMarketClock()

  return (
    <div className="space-y-1.5">
      {PRESETS.map((preset) => {
        const isActive = activePresetId === preset.id
        const isExpiry = preset.id === 'expiry'

        return (
          <button
            key={preset.id}
            type="button"
            onClick={() => applyPreset(preset.id)}
            className={`w-full text-left p-2 rounded border transition-colors ${
              isActive
                ? 'border-primary bg-primary/10'
                : 'border-border hover:border-primary/40 hover:bg-accent/30'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium">{preset.name}</span>
              <div className="flex items-center gap-1">
                {isExpiry && isExpiryDay && (
                  <Badge variant="destructive" className="text-[9px] h-3.5 px-1">
                    SUGGESTED
                  </Badge>
                )}
                {isActive && (
                  <Badge variant="default" className="text-[9px] h-3.5 px-1">
                    ACTIVE
                  </Badge>
                )}
              </div>
            </div>
            <p className="text-[10px] text-muted-foreground mt-0.5">{preset.description}</p>
          </button>
        )
      })}
    </div>
  )
}
