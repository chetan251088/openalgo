import { cn } from '@/lib/utils'

interface ModeSwitcherProps {
  mode: 'swing' | 'day'
  onChange: (mode: 'swing' | 'day') => void
}

const MODES = [
  { value: 'swing' as const, label: 'Swing', description: 'Multi-day holds' },
  { value: 'day' as const, label: 'Day', description: 'Intraday only' },
]

export function ModeSwitcher({ mode, onChange }: ModeSwitcherProps) {
  return (
    <div className="flex items-center gap-1 rounded-full border border-[#28404f] bg-[#0d1720] p-1">
      {MODES.map((item) => (
        <button
          key={item.value}
          type="button"
          onClick={() => onChange(item.value)}
          title={item.description}
          className={cn(
            'rounded-full px-3 py-1 text-[10px] uppercase tracking-[0.24em] transition-colors',
            mode === item.value
              ? 'bg-[#0f766e]/30 text-[#5eead4]'
              : 'text-[#6b8797] hover:text-[#d8eef6]',
          )}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}
