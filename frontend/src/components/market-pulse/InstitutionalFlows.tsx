
import type {
  InstitutionalContextData,
  FiiDiiStreakData,
} from '@/api/market-pulse'

/* ── tiny sparkline bar chart ─────────────────────────────────── */
function Sparkline({ values }: { values: number[] }) {
  if (!values.length) return null
  const max = Math.max(...values.map(Math.abs), 1)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: '2px', height: '28px' }}>
      {values.map((v, i) => (
        <div
          key={i}
          style={{
            width: '6px',
            borderRadius: '1px',
            background: v >= 0 ? 'var(--clr-bull, #22c55e)' : 'var(--clr-bear, #ef4444)',
            height: `${Math.max(4, (Math.abs(v) / max) * 28)}px`,
            opacity: i === values.length - 1 ? 1 : 0.5 + (i / values.length) * 0.5,
          }}
        />
      ))}
    </div>
  )
}

/* ── streak badge ─────────────────────────────────────────────── */
function StreakBadge({ label, streak }: { label: string; streak: FiiDiiStreakData }) {
  if (streak.days === 0) return null
  const isBuy = streak.direction === 'buy'
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '4px',
        padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
        background: isBuy ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
        color: isBuy ? '#22c55e' : '#ef4444',
        border: `1px solid ${isBuy ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
      }}
    >
      {label}: {streak.days}-day {streak.direction}
      <span style={{ opacity: 0.7 }}>
        ₹{Math.abs(streak.cumulative).toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr
      </span>
    </span>
  )
}

/* ── flow strength gauge ──────────────────────────────────────── */
function FlowGauge({ strength }: { strength: number }) {
  const pct = ((strength + 100) / 200) * 100
  const label = strength > 20 ? 'FII Bullish' : strength < -20 ? 'FII Bearish' : 'Balanced'
  return (
    <div style={{ marginTop: '8px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', opacity: 0.6, marginBottom: '2px' }}>
        <span>FII Selling</span>
        <span>{label}</span>
        <span>FII Buying</span>
      </div>
      <div style={{ height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.08)', position: 'relative', overflow: 'hidden' }}>
        <div
          style={{
            position: 'absolute', left: 0, top: 0, height: '100%',
            width: `${Math.min(100, Math.max(0, pct))}%`,
            borderRadius: '3px',
            background: strength > 0
              ? 'linear-gradient(90deg, rgba(255,255,255,0.1), #22c55e)'
              : 'linear-gradient(90deg, #ef4444, rgba(255,255,255,0.1))',
            transition: 'width 0.6s ease',
          }}
        />
        <div style={{ position: 'absolute', left: '50%', top: 0, width: '2px', height: '100%', background: 'rgba(255,255,255,0.3)' }} />
      </div>
    </div>
  )
}

/* ── Sentiment badge for F&O ──────────────────────────────────── */
function SentimentBadge({ sentiment, score }: { sentiment: string; score: number }) {
  const colorMap: Record<string, string> = {
    'Highly Bullish': '#22c55e',
    'Mildly Bullish': '#86efac',
    'Neutral': '#94a3b8',
    'Mildly Bearish': '#fca5a5',
    'Highly Bearish': '#ef4444',
  }
  const color = colorMap[sentiment] || '#94a3b8'
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '4px',
        padding: '2px 10px', borderRadius: '12px', fontSize: '11px', fontWeight: 700,
        background: `${color}20`, color, border: `1px solid ${color}40`,
      }}
    >
      {sentiment}
      <span style={{ fontSize: '10px', opacity: 0.7 }}>({score})</span>
    </span>
  )
}

/* ── L/S ratio bar ────────────────────────────────────────────── */
function LSRatioBar({ long, short, label }: { long: number; short: number; label: string }) {
  const total = long + short || 1
  const longPct = (long / total) * 100
  return (
    <div style={{ marginBottom: '6px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', marginBottom: '2px' }}>
        <span style={{ color: '#22c55e' }}>{label} Long: {long.toLocaleString()}</span>
        <span style={{ opacity: 0.5 }}>L/S: {(long / Math.max(short, 1)).toFixed(2)}</span>
        <span style={{ color: '#ef4444' }}>Short: {short.toLocaleString()}</span>
      </div>
      <div style={{ height: '8px', borderRadius: '4px', display: 'flex', overflow: 'hidden' }}>
        <div style={{ width: `${longPct}%`, background: '#22c55e', borderRadius: '4px 0 0 4px' }} />
        <div style={{ width: `${100 - longPct}%`, background: '#ef4444', borderRadius: '0 4px 4px 0' }} />
      </div>
    </div>
  )
}

/* ── 45-day Heatmap ───────────────────────────────────────────── */
function FlowHeatmap({ data }: { data: { date: string; fii_net: number; dii_net: number }[] }) {
  if (!data.length) return null
  const maxAbs = Math.max(...data.map(d => Math.abs(d.fii_net)), 1)
  return (
    <div style={{ marginTop: '10px' }}>
      <div style={{ fontSize: '10px', opacity: 0.6, marginBottom: '4px' }}>FII 45-Day Flow Heatmap</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px' }}>
        {data.map((d, i) => {
          const intensity = d.fii_net / maxAbs
          const bg = intensity > 0
            ? `rgba(34,197,94,${Math.min(1, Math.abs(intensity) * 0.8 + 0.1)})`
            : `rgba(239,68,68,${Math.min(1, Math.abs(intensity) * 0.8 + 0.1)})`
          return (
            <div
              key={i}
              title={`${d.date}: FII ₹${d.fii_net.toFixed(0)} Cr`}
              style={{ width: '10px', height: '10px', borderRadius: '2px', background: bg, cursor: 'default' }}
            />
          )
        })}
      </div>
    </div>
  )
}

/* ── MAIN COMPONENT ───────────────────────────────────────────── */
interface Props {
  data: InstitutionalContextData | null
}

export default function InstitutionalFlows({ data }: Props) {
  if (!data) {
    return (
      <div style={{ padding: '16px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 700, margin: 0, opacity: 0.5 }}>
          🏛️ Institutional Flows
        </h3>
        <p style={{ fontSize: '12px', opacity: 0.4, margin: '8px 0 0' }}>Loading institutional data…</p>
      </div>
    )
  }

  const { fii_dii, fno_participant, heatmap_45d } = data
  const fiiColor = fii_dii.fii_net >= 0 ? '#22c55e' : '#ef4444'
  const diiColor = fii_dii.dii_net >= 0 ? '#22c55e' : '#ef4444'

  return (
    <div style={{ padding: '16px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: 700, margin: 0 }}>🏛️ Institutional Flows</h3>
        <span style={{ fontSize: '10px', opacity: 0.4 }}>{fii_dii.date}</span>
      </div>

      {fii_dii.available ? (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '10px' }}>
            <div style={{ padding: '10px', borderRadius: '6px', background: `${fiiColor}08`, border: `1px solid ${fiiColor}25` }}>
              <div style={{ fontSize: '10px', opacity: 0.6 }}>FII / FPI Net</div>
              <div style={{ fontSize: '20px', fontWeight: 800, color: fiiColor, fontVariantNumeric: 'tabular-nums' }}>
                ₹{fii_dii.fii_net.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr
              </div>
              <Sparkline values={fii_dii.fii_5d} />
            </div>
            <div style={{ padding: '10px', borderRadius: '6px', background: `${diiColor}08`, border: `1px solid ${diiColor}25` }}>
              <div style={{ fontSize: '10px', opacity: 0.6 }}>DII Net</div>
              <div style={{ fontSize: '20px', fontWeight: 800, color: diiColor, fontVariantNumeric: 'tabular-nums' }}>
                ₹{fii_dii.dii_net.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr
              </div>
              <Sparkline values={fii_dii.dii_5d} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '8px' }}>
            <StreakBadge label="FII" streak={fii_dii.fii_streak} />
            <StreakBadge label="DII" streak={fii_dii.dii_streak} />
          </div>
          <FlowGauge strength={fii_dii.flow_strength} />
        </>
      ) : (
        <p style={{ fontSize: '12px', opacity: 0.4 }}>FII/DII data unavailable — market may be closed.</p>
      )}

      {fno_participant.available && (
        <div style={{ marginTop: '14px', paddingTop: '12px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <span style={{ fontSize: '12px', fontWeight: 600, opacity: 0.8 }}>F&O Participant OI</span>
            <SentimentBadge sentiment={fno_participant.sentiment} score={fno_participant.sentiment_score} />
          </div>
          <LSRatioBar long={fno_participant.fii_index_futures.long} short={fno_participant.fii_index_futures.short} label="FII Idx Fut" />
          {fno_participant.fii_stock_futures && fno_participant.fii_stock_futures.long > 0 && (
            <LSRatioBar long={fno_participant.fii_stock_futures.long} short={fno_participant.fii_stock_futures.short} label="FII Stk Fut" />
          )}
        </div>
      )}

      {heatmap_45d.length > 0 && <FlowHeatmap data={heatmap_45d} />}
    </div>
  )
}
