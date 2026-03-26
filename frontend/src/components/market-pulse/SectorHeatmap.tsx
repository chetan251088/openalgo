
import type { SectorContextData } from '@/api/market-pulse'

/* ── Sector cell ──────────────────────────────────────────────── */
function SectorCell({ name, value, rs, flowHint, intensity }: {
  name: string; value: number; rs: number; flowHint: string; intensity: number
}) {
  const isPositive = value >= 0
  const bgColor = isPositive
    ? `rgba(34,197,94,${Math.min(0.5, Math.abs(intensity) * 0.4 + 0.05)})`
    : `rgba(239,68,68,${Math.min(0.5, Math.abs(intensity) * 0.4 + 0.05)})`
  const textColor = isPositive ? '#22c55e' : '#ef4444'

  return (
    <div
      style={{
        padding: '8px 10px', borderRadius: '6px', background: bgColor,
        border: `1px solid ${isPositive ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)'}`,
        cursor: 'default', transition: 'transform 0.15s ease',
      }}
      title={`RS vs Nifty: ${rs > 0 ? '+' : ''}${rs}% | ${flowHint}`}
    >
      <div style={{ fontSize: '11px', fontWeight: 600, marginBottom: '2px', opacity: 0.9 }}>{name}</div>
      <div style={{ fontSize: '16px', fontWeight: 800, color: textColor, fontVariantNumeric: 'tabular-nums' }}>
        {value > 0 ? '+' : ''}{value.toFixed(2)}%
      </div>
      <div style={{ fontSize: '9px', opacity: 0.5, marginTop: '2px' }}>{flowHint}</div>
    </div>
  )
}

/* ── Rotation Signal Banner ───────────────────────────────────── */
function RotationBanner({ signal, leaders, laggards }: {
  signal: string
  leaders: { name: string; pct: number }[]
  laggards: { name: string; pct: number }[]
}) {
  const isRiskOn = signal.includes('Risk-On') || signal.includes('Broad Rally')
  const bgColor = isRiskOn ? 'rgba(34,197,94,0.08)' : signal.includes('Risk-Off') || signal.includes('Sell-off') ? 'rgba(239,68,68,0.08)' : 'rgba(148,163,184,0.08)'
  const borderColor = isRiskOn ? 'rgba(34,197,94,0.2)' : signal.includes('Risk-Off') || signal.includes('Sell-off') ? 'rgba(239,68,68,0.2)' : 'rgba(148,163,184,0.15)'

  return (
    <div style={{
      padding: '8px 12px', borderRadius: '6px', background: bgColor,
      border: `1px solid ${borderColor}`, marginBottom: '12px',
    }}>
      <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '4px' }}>
        {isRiskOn ? '🟢' : signal.includes('Risk-Off') || signal.includes('Sell-off') ? '🔴' : '🟡'} {signal}
      </div>
      <div style={{ display: 'flex', gap: '16px', fontSize: '10px', opacity: 0.7 }}>
        <span>
          📈 Leaders: {leaders.map(l => `${l.name} (${l.pct > 0 ? '+' : ''}${l.pct.toFixed(1)}%)`).join(', ')}
        </span>
        <span>
          📉 Laggards: {laggards.map(l => `${l.name} (${l.pct > 0 ? '+' : ''}${l.pct.toFixed(1)}%)`).join(', ')}
        </span>
      </div>
    </div>
  )
}

/* ── MAIN COMPONENT ───────────────────────────────────────────── */
interface Props {
  data: SectorContextData | null
}

export default function SectorHeatmap({ data }: Props) {
  if (!data) {
    return (
      <div style={{ padding: '16px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 700, margin: 0, opacity: 0.5 }}>📊 Sector Heatmap</h3>
        <p style={{ fontSize: '12px', opacity: 0.4, margin: '8px 0 0' }}>Loading sector data…</p>
      </div>
    )
  }

  const { heatmap, rotation, performance } = data
  const niftyPct = performance?.nifty_today_pct ?? 0

  return (
    <div style={{ padding: '16px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 700, margin: 0 }}>📊 Sector Heatmap</h3>
        <span style={{
          fontSize: '11px', fontWeight: 600,
          color: niftyPct >= 0 ? '#22c55e' : '#ef4444',
        }}>
          NIFTY: {niftyPct > 0 ? '+' : ''}{niftyPct.toFixed(2)}%
        </span>
      </div>

      {/* Rotation Signal */}
      {rotation && (
        <RotationBanner
          signal={rotation.rotation_signal}
          leaders={rotation.leaders}
          laggards={rotation.laggards}
        />
      )}

      {/* Heatmap Grid */}
      {heatmap.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))', gap: '6px' }}>
          {heatmap.map((s) => (
            <SectorCell
              key={s.name}
              name={s.name}
              value={s.value}
              rs={s.rs}
              flowHint={s.flow_hint}
              intensity={s.intensity}
            />
          ))}
        </div>
      ) : (
        <p style={{ fontSize: '12px', opacity: 0.4 }}>Sector data unavailable.</p>
      )}
    </div>
  )
}
