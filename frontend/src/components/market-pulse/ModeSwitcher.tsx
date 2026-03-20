interface ModeSwitcherProps {
  mode: 'swing' | 'day'
  onChange: (mode: 'swing' | 'day') => void
}

export function ModeSwitcher({ mode, onChange }: ModeSwitcherProps) {
  return (
    <div className="flex gap-2 bg-[#161b22] border border-[#30363d] rounded p-1">
      {(['swing', 'day'] as const).map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={`px-3 py-1 text-xs font-mono rounded transition-colors ${
            mode === m
              ? 'bg-blue-600 text-white'
              : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          {m.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
