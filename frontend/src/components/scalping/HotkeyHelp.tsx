import { useEffect } from 'react'

interface HotkeyHelpProps {
  open: boolean
  onClose: () => void
}

const HOTKEYS = [
  { key: 'B', description: 'Buy on active side (CE/PE)' },
  { key: 'S', description: 'Sell on active side' },
  { key: 'R', description: 'Reversal (close + enter opposite)' },
  { key: 'C', description: 'Close active side position' },
  { key: 'X', description: 'Close ALL positions' },
  { key: 'W', description: 'Toggle floating trade widget' },
  { key: 'Tab', description: 'Toggle active side (CE ↔ PE)' },
  { key: '↑', description: 'CE direct market buy (with virtual TP/SL)' },
  { key: '↓', description: 'PE direct market buy (with virtual TP/SL)' },
  { key: '1', description: 'Set quantity to 1 lot' },
  { key: '2', description: 'Set quantity to 2 lots' },
  { key: '3', description: 'Set quantity to 3 lots' },
  { key: '?', description: 'Toggle this help overlay' },
  { key: 'Esc', description: 'Close this overlay' },
]

export function HotkeyHelp({ open, onClose }: HotkeyHelpProps) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: overlay dismissal
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center"
      onClick={onClose}
    >
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: stop propagation */}
      <div
        className="bg-card border rounded-lg shadow-xl p-4 max-w-sm w-full mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold">Keyboard Shortcuts</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground text-xs"
          >
            ESC
          </button>
        </div>

        <div className="space-y-1">
          {HOTKEYS.map(({ key, description }) => (
            <div key={key} className="flex items-center gap-3 py-0.5">
              <kbd className="px-1.5 py-0.5 bg-muted rounded text-xs font-mono min-w-[2.5rem] text-center shrink-0">
                {key}
              </kbd>
              <span className="text-xs text-muted-foreground">{description}</span>
            </div>
          ))}
        </div>

        <p className="text-[10px] text-muted-foreground mt-3 border-t pt-2">
          Hotkeys are disabled when typing in input fields.
          Click on CE/PE chart to set active side.
        </p>
      </div>
    </div>
  )
}
