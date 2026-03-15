import { create } from 'zustand'

interface MiroFishState {
  bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | null
  confidence: number
  vixExpectation: 'RISING' | 'FALLING' | 'STABLE' | null
  narrativeSummary: string
  scenarios: Array<{ description: string; probability: number; impact: string }>
  keyRisks: string[]
  timestamp: number
  stale: boolean
}

interface RotationState {
  sectors: Record<string, { quadrant: string; rsRatio: number; rsMomentum: number }>
  leadingSectors: string[]
  laggingSectors: string[]
  improvingSectors: string[]
  weakeningSectors: string[]
  transitions: Array<{ symbol: string; name?: string; from_quadrant: string; to_quadrant: string }>
  timestamp: number
  stale: boolean
}

interface FundamentalsState {
  clearedSymbols: string[]
  blockedSymbols: Record<string, string>
  timestamp: number
  stale: boolean
}

interface IntelligenceState {
  mirofish: MiroFishState | null
  rotation: RotationState | null
  fundamentals: FundamentalsState | null
  lastRefresh: number
  loading: boolean
  error: string | null

  setIntelligence: (data: any) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  reset: () => void
}

const initialState = {
  mirofish: null,
  rotation: null,
  fundamentals: null,
  lastRefresh: 0,
  loading: false,
  error: null,
}

export const useIntelligenceStore = create<IntelligenceState>((set) => ({
  ...initialState,

  setIntelligence: (data: any) => {
    if (!data) return

    const intelligence = data.intelligence || data

    set({
      mirofish: intelligence.mirofish
        ? {
            bias: intelligence.mirofish.bias,
            confidence: intelligence.mirofish.confidence ?? 0,
            vixExpectation: intelligence.mirofish.vix_expectation,
            narrativeSummary: intelligence.mirofish.narrative_summary ?? '',
            scenarios: intelligence.mirofish.scenarios ?? [],
            keyRisks: intelligence.mirofish.key_risks ?? [],
            timestamp: intelligence.mirofish.timestamp ?? 0,
            stale: intelligence.mirofish.stale ?? false,
          }
        : null,

      rotation: intelligence.rotation
        ? {
            sectors: Object.fromEntries(
              (intelligence.rotation.sectors ?? []).map((s: any) => [
                s.symbol,
                {
                  quadrant: s.quadrant,
                  rsRatio: s.rs_ratio,
                  rsMomentum: s.rs_momentum,
                },
              ])
            ),
            leadingSectors: intelligence.rotation.leading_sectors ?? [],
            laggingSectors: intelligence.rotation.lagging_sectors ?? [],
            improvingSectors: intelligence.rotation.improving_sectors ?? [],
            weakeningSectors: intelligence.rotation.weakening_sectors ?? [],
            transitions: (intelligence.rotation.transitions ?? []).map((t: any) => ({
              symbol: t.symbol,
              name: t.name,
              from_quadrant: t.from_quadrant ?? t.from ?? '',
              to_quadrant: t.to_quadrant ?? t.to ?? '',
            })),
            timestamp: intelligence.rotation.timestamp ?? 0,
            stale: intelligence.rotation.stale ?? false,
          }
        : null,

      fundamentals: intelligence.fundamentals
        ? {
            clearedSymbols: intelligence.fundamentals.cleared_symbols ?? [],
            blockedSymbols: intelligence.fundamentals.blocked_symbols ?? {},
            timestamp: intelligence.fundamentals.timestamp ?? 0,
            stale: intelligence.fundamentals.stale ?? false,
          }
        : null,

      lastRefresh: Date.now(),
      loading: false,
      error: null,
    })
  },

  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  reset: () => set(initialState),
}))
