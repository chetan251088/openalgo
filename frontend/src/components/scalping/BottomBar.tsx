import { useScalpingStore } from '@/stores/scalpingStore'
import type { ScalpingPosition } from '@/types/scalping'

interface BottomBarProps {
  positions: ScalpingPosition[]
  totalPnl: number
  isLivePnl: boolean
}

export function BottomBar({ positions, totalPnl, isLivePnl }: BottomBarProps) {
  const activeSide = useScalpingStore((s) => s.activeSide)
  const hotkeysEnabled = useScalpingStore((s) => s.hotkeysEnabled)
  const paperMode = useScalpingStore((s) => s.paperMode)

  return (
    <div className="flex items-center justify-between px-3 py-1 border-t bg-card text-xs shrink-0">
      {/* Positions strip */}
      <div className="flex items-center gap-3 overflow-x-auto min-w-0">
        {positions.length === 0 ? (
          <span className="text-muted-foreground">No open positions</span>
        ) : (
          <>
            {positions.map((p) => (
              <div key={p.symbol} className="flex items-center gap-1 shrink-0">
                <span className={p.side === 'CE' ? 'text-green-500' : 'text-red-500'}>
                  {p.side}
                </span>
                <span className="font-mono">{p.symbol.slice(-10)}</span>
                <span className="text-muted-foreground">x{p.quantity}</span>
                <span className="text-muted-foreground">@{p.avgPrice.toFixed(1)}</span>
                <span
                  className={`font-bold tabular-nums ${
                    p.pnl > 0 ? 'text-green-500' : p.pnl < 0 ? 'text-red-500' : 'text-foreground'
                  }`}
                >
                  {p.pnl >= 0 ? '+' : ''}{p.pnl.toFixed(0)}
                  <span className="text-muted-foreground font-normal">
                    ({p.pnlPoints >= 0 ? '+' : ''}{p.pnlPoints.toFixed(1)}pts)
                  </span>
                </span>
              </div>
            ))}
            {positions.length > 0 && (
              <>
                <span className="text-border">|</span>
                <span className="text-muted-foreground">Total:</span>
                <span
                  className={`font-bold tabular-nums ${
                    totalPnl > 0 ? 'text-green-500' : totalPnl < 0 ? 'text-red-500' : 'text-foreground'
                  }`}
                >
                  {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(0)}
                </span>
                <span className={`text-[10px] ${isLivePnl ? 'text-green-500' : 'text-muted-foreground'}`}>
                  {isLivePnl ? 'LIVE' : 'REST'}
                </span>
              </>
            )}
          </>
        )}
      </div>

      {/* Center: Paper mode */}
      <div className="flex items-center gap-2 shrink-0">
        {paperMode && (
          <span className="text-blue-400 font-medium">PAPER MODE</span>
        )}
      </div>

      {/* Right: Hotkey hints */}
      <div className="flex items-center gap-2 text-muted-foreground shrink-0">
        {hotkeysEnabled ? (
          <>
            <span>
              Active: <span className="text-foreground font-medium">{activeSide}</span>
            </span>
            <span className="text-border">|</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">B</kbd>
            <span>Buy</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">S</kbd>
            <span>Sell</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">R</kbd>
            <span>Rev</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">C</kbd>
            <span>Close</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">X</kbd>
            <span>All</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">W</kbd>
            <span>Widget</span>
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">Tab</kbd>
            <span>Side</span>
          </>
        ) : (
          <span>Hotkeys disabled</span>
        )}
      </div>
    </div>
  )
}
