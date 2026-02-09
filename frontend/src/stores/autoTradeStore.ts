import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DEFAULT_CONFIG, PRESETS, type AutoTradeConfigFields } from '@/lib/scalpingPresets'
import type { MarketRegime, GhostSignal, TrailingStage, OptionsContext } from '@/types/scalping'

interface AutoTradeState {
  // Persisted config
  config: AutoTradeConfigFields
  activePresetId: string

  // Runtime (non-persisted)
  enabled: boolean
  mode: 'execute' | 'ghost' // execute = auto-trade, ghost = shadow/manual mode
  realizedPnl: number
  regime: MarketRegime
  consecutiveLosses: number
  tradesCount: number
  trailStage: TrailingStage
  highSinceEntry: number
  ghostSignals: GhostSignal[]
  optionsContext: OptionsContext | null
  lastTradeTime: number
  tradesThisMinute: number
  lastMinuteReset: number
  lastLossTime: number
}

interface AutoTradeActions {
  // Config
  updateConfig: (partial: Partial<AutoTradeConfigFields>) => void
  applyPreset: (presetId: string) => void

  // Runtime
  setEnabled: (on: boolean) => void
  setMode: (mode: 'execute' | 'ghost') => void
  setRegime: (regime: MarketRegime) => void
  setTrailStage: (stage: TrailingStage) => void
  setHighSinceEntry: (high: number) => void
  setOptionsContext: (ctx: OptionsContext | null) => void
  recordTrade: (pnl: number) => void
  addGhostSignal: (signal: GhostSignal) => void
  clearGhostSignals: () => void
  resetRuntime: () => void
}

type AutoTradeStore = AutoTradeState & AutoTradeActions

const MAX_GHOST_SIGNALS = 50

export const useAutoTradeStore = create<AutoTradeStore>()(
  persist(
    (set) => ({
      // Persisted
      config: { ...DEFAULT_CONFIG },
      activePresetId: 'balanced',

      // Runtime (reset on page load)
      enabled: false,
      mode: 'ghost',
      realizedPnl: 0,
      regime: 'UNKNOWN',
      consecutiveLosses: 0,
      tradesCount: 0,
      trailStage: 'INITIAL',
      highSinceEntry: 0,
      ghostSignals: [],
      optionsContext: null,
      lastTradeTime: 0,
      tradesThisMinute: 0,
      lastMinuteReset: 0,
      lastLossTime: 0,

      updateConfig: (partial) =>
        set((s) => ({
          config: { ...s.config, ...partial },
        })),

      applyPreset: (presetId) => {
        const preset = PRESETS.find((p) => p.id === presetId)
        if (!preset) return
        set({
          config: { ...DEFAULT_CONFIG, ...preset.config },
          activePresetId: presetId,
        })
      },

      setEnabled: (on) => set({ enabled: on }),
      setMode: (mode) => set({ mode }),
      setRegime: (regime) => set({ regime }),
      setTrailStage: (stage) => set({ trailStage: stage }),
      setHighSinceEntry: (high) => set({ highSinceEntry: high }),
      setOptionsContext: (ctx) => set({ optionsContext: ctx }),

      recordTrade: (pnl) =>
        set((s) => {
          const now = Date.now()
          // Reset per-minute counter if a new minute
          const minuteReset = now - s.lastMinuteReset > 60_000 ? now : s.lastMinuteReset
          const perMin = now - s.lastMinuteReset > 60_000 ? 1 : s.tradesThisMinute + 1
          return {
            realizedPnl: s.realizedPnl + pnl,
            tradesCount: s.tradesCount + 1,
            consecutiveLosses: pnl < 0 ? s.consecutiveLosses + 1 : 0,
            lastTradeTime: now,
            tradesThisMinute: perMin,
            lastMinuteReset: minuteReset,
            lastLossTime: pnl < 0 ? now : s.lastLossTime,
          }
        }),

      addGhostSignal: (signal) =>
        set((s) => ({
          ghostSignals: [...s.ghostSignals.slice(-(MAX_GHOST_SIGNALS - 1)), signal],
        })),

      clearGhostSignals: () => set({ ghostSignals: [] }),

      resetRuntime: () =>
        set({
          realizedPnl: 0,
          regime: 'UNKNOWN',
          consecutiveLosses: 0,
          tradesCount: 0,
          trailStage: 'INITIAL',
          highSinceEntry: 0,
          ghostSignals: [],
          lastTradeTime: 0,
          tradesThisMinute: 0,
          lastMinuteReset: 0,
          lastLossTime: 0,
        }),
    }),
    {
      name: 'openalgo-scalping-autotrade',
      partialize: (state) => ({
        config: state.config,
        activePresetId: state.activePresetId,
      }),
    }
  )
)
