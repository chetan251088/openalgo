

import type { FundamentalEntry } from '@/api/market-pulse'

/* ── Quality Score color ──────────────────────────────────────── */
function scoreColor(score: number): string {
  if (score >= 70) return '#22c55e'
  if (score >= 50) return '#eab308'
  if (score >= 30) return '#f97316'
  return '#ef4444'
}

function scoreLabel(score: number): string {
  if (score >= 70) return 'Strong'
  if (score >= 50) return 'Fair'
  if (score >= 30) return 'Weak'
  return 'Poor'
}

/* ── Mini shareholding bars ───────────────────────────────────── */
function ShareholdingMini({ data }: { data: { promoter: number[]; fii: number[]; dii: number[] } }) {
  const latest = (arr: number[]) => arr.length > 0 ? arr[arr.length - 1] : 0
  const prev = (arr: number[]) => arr.length > 1 ? arr[arr.length - 2] : latest(arr)
  const delta = (arr: number[]) => latest(arr) - prev(arr)

  const rows = [
    { label: 'Promoter', val: latest(data.promoter), d: delta(data.promoter) },
    { label: 'FII', val: latest(data.fii), d: delta(data.fii) },
    { label: 'DII', val: latest(data.dii), d: delta(data.dii) },
  ]

  return (
    <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
      {rows.map(r => (
        <span key={r.label} style={{ fontSize: '9px', opacity: 0.6 }}>
          {r.label}: {r.val.toFixed(1)}%
          {r.d !== 0 && (
            <span style={{ color: r.d > 0 ? '#22c55e' : '#ef4444', marginLeft: '2px' }}>
              {r.d > 0 ? '▲' : '▼'}{Math.abs(r.d).toFixed(1)}
            </span>
          )}
        </span>
      ))}
    </div>
  )
}

/* ── MAIN COMPONENT ───────────────────────────────────────────── */
interface Props {
  symbol: string
  data: FundamentalEntry | null | undefined
  compact?: boolean
}

export default function FundamentalBadge({ symbol, data, compact = true }: Props) {
  if (!data) return null

  const color = scoreColor(data.quality_score)
  const label = scoreLabel(data.quality_score)

  if (compact) {
    return (
      <span
        title={`Quality: ${data.quality_score}/100 (${label}) | PE: ${data.pe ?? 'N/A'} | Cap: ${data.market_cap_tier}`}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: '3px',
          padding: '1px 6px', borderRadius: '4px', fontSize: '10px', fontWeight: 700,
          background: `${color}18`, color, border: `1px solid ${color}30`,
          cursor: 'default',
        }}
      >
        Q:{data.quality_score}
      </span>
    )
  }

  // Expanded view
  return (
    <div style={{
      padding: '8px 10px', borderRadius: '6px', marginTop: '6px',
      background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
    }}>
      {/* Score + label */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: '28px', height: '28px', borderRadius: '50%',
            background: `${color}20`, color, fontSize: '12px', fontWeight: 800,
          }}>
            {data.quality_score}
          </span>
          <div>
            <div style={{ fontSize: '11px', fontWeight: 600 }}>{symbol}</div>
            <div style={{ fontSize: '9px', opacity: 0.5 }}>{label} · {data.market_cap_tier} Cap</div>
          </div>
        </div>
      </div>

      {/* Key ratios */}
      <div style={{ display: 'flex', gap: '12px', marginTop: '6px', fontSize: '10px' }}>
        {data.pe != null && <span style={{ opacity: 0.7 }}>PE: <b>{data.pe.toFixed(1)}</b></span>}
        {data.price_strength?.vs_52w_high != null && (
          <span style={{ opacity: 0.7 }}>
            vs 52W High: <b style={{ color: data.price_strength.vs_52w_high > -10 ? '#22c55e' : '#ef4444' }}>
              {data.price_strength.vs_52w_high.toFixed(1)}%
            </b>
          </span>
        )}
        {data.price_strength?.vs_200dma != null && (
          <span style={{ opacity: 0.7 }}>
            vs 200DMA: <b style={{ color: data.price_strength.vs_200dma > 0 ? '#22c55e' : '#ef4444' }}>
              {data.price_strength.vs_200dma > 0 ? '+' : ''}{data.price_strength.vs_200dma.toFixed(1)}%
            </b>
          </span>
        )}
        {data.price_strength?.rvol != null && (
          <span style={{ opacity: 0.7 }}>
            RVOL: <b style={{ color: data.price_strength.rvol > 1.2 ? '#22c55e' : data.price_strength.rvol < 0.7 ? '#ef4444' : 'inherit' }}>
              {data.price_strength.rvol.toFixed(1)}x
            </b>
          </span>
        )}
      </div>

      {/* Shareholding (if available) */}
      {data.shareholding && (
        <ShareholdingMini data={data.shareholding} />
      )}
    </div>
  )
}
