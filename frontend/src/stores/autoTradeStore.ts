import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DEFAULT_CONFIG, PRESETS, type AutoTradeConfigFields } from '@/lib/scalpingPresets'
import type { ActiveSide, MarketRegime, GhostSignal, TrailingStage, OptionsContext } from '@/types/scalping'

const MAX_GHOST_SIGNALS = 50
const MAX_DECISIONS = 120
const MAX_EXEC_SAMPLES = 60
const SIDE_KEYS: ActiveSide[] = ['CE', 'PE']

type SideNumberMap = Record<ActiveSide, number>

function initSideMap(initial = 0): SideNumberMap {
  return SIDE_KEYS.reduce((acc, side) => {
    acc[side] = initial
    return acc
  }, {} as SideNumberMap)
}

const TRUTHY_VALUES = new Set(['1', 'true', 'yes', 'on', 'enabled'])
const FALSY_VALUES = new Set(['0', 'false', 'no', 'off', 'disabled'])

function parseBooleanLike(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return Number.isFinite(value) ? value !== 0 : null
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (TRUTHY_VALUES.has(normalized)) return true
    if (FALSY_VALUES.has(normalized)) return false
  }
  return null
}

function sanitizeConfigPatch(
  patch: Partial<Record<keyof AutoTradeConfigFields, unknown>> | null | undefined
): Partial<AutoTradeConfigFields> {
  if (!patch || typeof patch !== 'object') return {}

  const source = patch as Record<string, unknown>
  const normalized: Partial<AutoTradeConfigFields> = {}

  for (const key of Object.keys(DEFAULT_CONFIG) as Array<keyof AutoTradeConfigFields>) {
    if (!(key in source)) continue
    const defaultValue = DEFAULT_CONFIG[key]
    const raw = source[key]

    if (typeof defaultValue === 'boolean') {
      const parsed = parseBooleanLike(raw)
      if (parsed != null) {
        ;(normalized as Record<string, boolean | number>)[key] = parsed
      }
      continue
    }

    if (typeof defaultValue === 'number') {
      const numeric =
        typeof raw === 'number'
          ? raw
          : typeof raw === 'string'
            ? Number(raw)
            : Number.NaN
      if (Number.isFinite(numeric)) {
        ;(normalized as Record<string, boolean | number>)[key] = numeric
      }
    }
  }

  return normalized
}

export interface AutoTradeDecisionCheck {
  id: string
  label: string
  pass: boolean
  value?: string
}

export interface AutoTradeDecisionSnapshot {
  timestamp: number
  side: ActiveSide
  symbol: string
  enter: boolean
  score: number
  minScore: number
  reason: string
  blockedBy?: string
  spread: number
  depthRatio: number | null
  expectedSlippage: number
  checks: AutoTradeDecisionCheck[]
  regime: MarketRegime
}

export interface AutoTradeExecutionSample {
  timestamp: number
  side: ActiveSide
  symbol: string
  spread: number
  expectedSlippage: number
  status: 'filled' | 'rejected' | 'exited'
  reason?: string
}

interface AutoTradeState {
  // Persisted config
  config: AutoTradeConfigFields
  activePresetId: string

  // Runtime (non-persisted)
  enabled: boolean
  mode: 'execute' | 'ghost'
  replayMode: boolean
  realizedPnl: number
  lastTradePnl: number | null
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
  sideEntryCount: SideNumberMap
  sideLastExitAt: SideNumberMap
  sideLossPnl: SideNumberMap
  killSwitch: boolean
  lockProfitEnabled: boolean
  lockProfitTriggered: boolean
  winsCount: number
  lossesCount: number
  dailyPeakPnl: number
  dailyDrawdown: number
  accountPeakPnl: number
  accountDrawdown: number
  autoPeakPnl: number
  autoDrawdown: number
  decisionHistory: AutoTradeDecisionSnapshot[]
  lastDecisionBySide: Partial<Record<ActiveSide, AutoTradeDecisionSnapshot>>
  executionSamples: AutoTradeExecutionSample[]
}

interface AutoTradeActions {
  // Config
  updateConfig: (partial: Partial<AutoTradeConfigFields>) => void
  applyPreset: (presetId: string) => void

  // Runtime
  setEnabled: (on: boolean) => void
  setMode: (mode: 'execute' | 'ghost') => void
  setReplayMode: (on: boolean) => void
  setKillSwitch: (on: boolean) => void
  setLockProfitEnabled: (on: boolean) => void
  updateRiskState: (currentPnl: number) => void
  setRegime: (regime: MarketRegime) => void
  setTrailStage: (stage: TrailingStage) => void
  setHighSinceEntry: (high: number) => void
  setOptionsContext: (ctx: OptionsContext | null) => void
  recordTrade: (pnl: number) => void
  recordTradeOutcome: (pnl: number) => void
  recordAutoEntry: (side: ActiveSide) => void
  recordAutoExit: (side: ActiveSide, pnl: number) => void
  pushDecision: (decision: AutoTradeDecisionSnapshot) => void
  pushExecutionSample: (sample: AutoTradeExecutionSample) => void
  addGhostSignal: (signal: GhostSignal) => void
  clearGhostSignals: () => void
  resetRuntime: () => void
}

type AutoTradeStore = AutoTradeState & AutoTradeActions

export const useAutoTradeStore = create<AutoTradeStore>()(
  persist(
    (set) => ({
      // Persisted
      config: { ...DEFAULT_CONFIG },
      activePresetId: 'balanced',

      // Runtime
      enabled: false,
      mode: 'ghost',
      replayMode: false,
      realizedPnl: 0,
      lastTradePnl: null,
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
      sideEntryCount: initSideMap(),
      sideLastExitAt: initSideMap(),
      sideLossPnl: initSideMap(),
      killSwitch: false,
      lockProfitEnabled: false,
      lockProfitTriggered: false,
      winsCount: 0,
      lossesCount: 0,
      dailyPeakPnl: 0,
      dailyDrawdown: 0,
      accountPeakPnl: 0,
      accountDrawdown: 0,
      autoPeakPnl: 0,
      autoDrawdown: 0,
      decisionHistory: [],
      lastDecisionBySide: {},
      executionSamples: [],

      updateConfig: (partial) =>
        set((s) => ({
          config: { ...s.config, ...sanitizeConfigPatch(partial) },
        })),

      applyPreset: (presetId) => {
        const preset = PRESETS.find((p) => p.id === presetId)
        if (!preset) return
        set({
          config: { ...DEFAULT_CONFIG, ...sanitizeConfigPatch(preset.config) },
          activePresetId: presetId,
        })
      },

      setEnabled: (on) => set({ enabled: on }),
      setMode: (mode) => set({ mode }),
      setReplayMode: (on) => set({ replayMode: on }),
      setKillSwitch: (on) => set({ killSwitch: on }),
      setLockProfitEnabled: (on) =>
        set((s) => ({
          lockProfitEnabled: on,
          lockProfitTriggered: on ? s.lockProfitTriggered : false,
        })),
      updateRiskState: (currentPnl) =>
        set((s) => {
          const accountPeak = Math.max(s.accountPeakPnl, currentPnl)
          const accountDrawdown = Math.max(0, accountPeak - currentPnl)
          const peak = Math.max(s.dailyPeakPnl, currentPnl)
          const drawdown = Math.max(0, peak - currentPnl)
          const lockTriggered = s.lockProfitEnabled && peak > 0
            ? s.lockProfitTriggered || drawdown >= peak * 0.4
            : s.lockProfitTriggered
          const limitHit = currentPnl < 0 && Math.abs(currentPnl) >= s.config.maxDailyLoss
          return {
            dailyPeakPnl: peak,
            dailyDrawdown: drawdown,
            accountPeakPnl: accountPeak,
            accountDrawdown,
            lockProfitTriggered: lockTriggered,
            killSwitch: s.killSwitch || limitHit,
          }
        }),
      setRegime: (regime) => set({ regime }),
      setTrailStage: (stage) => set({ trailStage: stage }),
      setHighSinceEntry: (high) => set({ highSinceEntry: high }),
      setOptionsContext: (ctx) => set({ optionsContext: ctx }),

      // Entry record (increments trade counters, typically called when order is placed)
      recordTrade: (pnl) =>
        set((s) => {
          const now = Date.now()
          const minuteReset = now - s.lastMinuteReset > 60_000 ? now : s.lastMinuteReset
          const perMin = now - s.lastMinuteReset > 60_000 ? 1 : s.tradesThisMinute + 1
          const realized = s.realizedPnl + pnl
          const peak = Math.max(s.dailyPeakPnl, realized)
          const drawdown = Math.max(0, peak - realized)
          const autoPeak = Math.max(s.autoPeakPnl, realized)
          const autoDrawdown = Math.max(0, autoPeak - realized)
          return {
            realizedPnl: realized,
            lastTradePnl: pnl,
            tradesCount: s.tradesCount + 1,
            consecutiveLosses: pnl < 0 ? s.consecutiveLosses + 1 : s.consecutiveLosses,
            lastTradeTime: now,
            tradesThisMinute: perMin,
            lastMinuteReset: minuteReset,
            lastLossTime: pnl < 0 ? now : s.lastLossTime,
            dailyPeakPnl: peak,
            dailyDrawdown: drawdown,
            autoPeakPnl: autoPeak,
            autoDrawdown,
          }
        }),

      // Exit/outcome record (updates pnl/loss streak without incrementing entry counters)
      recordTradeOutcome: (pnl) =>
        set((s) => {
          const now = Date.now()
          const realized = s.realizedPnl + pnl
          const peak = Math.max(s.dailyPeakPnl, realized)
          const drawdown = Math.max(0, peak - realized)
          const autoPeak = Math.max(s.autoPeakPnl, realized)
          const autoDrawdown = Math.max(0, autoPeak - realized)
          const lockTriggered = s.lockProfitEnabled && peak > 0
            ? s.lockProfitTriggered || drawdown >= peak * 0.4
            : s.lockProfitTriggered
          return {
            realizedPnl: realized,
            lastTradePnl: pnl,
            consecutiveLosses: pnl < 0 ? s.consecutiveLosses + 1 : 0,
            lastLossTime: pnl < 0 ? now : s.lastLossTime,
            dailyPeakPnl: peak,
            dailyDrawdown: drawdown,
            autoPeakPnl: autoPeak,
            autoDrawdown,
            lockProfitTriggered: lockTriggered,
            killSwitch: s.killSwitch,
            winsCount: pnl >= 0 ? s.winsCount + 1 : s.winsCount,
            lossesCount: pnl < 0 ? s.lossesCount + 1 : s.lossesCount,
          }
        }),

      recordAutoEntry: (side) =>
        set((s) => ({
          sideEntryCount: {
            ...s.sideEntryCount,
            [side]: s.sideEntryCount[side] + 1,
          },
        })),

      recordAutoExit: (side, pnl) =>
        set((s) => ({
          sideLastExitAt: {
            ...s.sideLastExitAt,
            [side]: Date.now(),
          },
          sideLossPnl: {
            ...s.sideLossPnl,
            [side]: pnl < 0 ? s.sideLossPnl[side] + Math.abs(pnl) : s.sideLossPnl[side],
          },
        })),

      pushDecision: (decision) =>
        set((s) => ({
          decisionHistory: [...s.decisionHistory.slice(-(MAX_DECISIONS - 1)), decision],
          lastDecisionBySide: {
            ...s.lastDecisionBySide,
            [decision.side]: decision,
          },
        })),

      pushExecutionSample: (sample) =>
        set((s) => ({
          executionSamples: [...s.executionSamples.slice(-(MAX_EXEC_SAMPLES - 1)), sample],
        })),

      addGhostSignal: (signal) =>
        set((s) => ({
          ghostSignals: [...s.ghostSignals.slice(-(MAX_GHOST_SIGNALS - 1)), signal],
        })),

      clearGhostSignals: () => set({ ghostSignals: [] }),

      resetRuntime: () =>
        set({
          enabled: false,
          mode: 'ghost',
          replayMode: false,
          realizedPnl: 0,
          lastTradePnl: null,
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
          sideEntryCount: initSideMap(),
          sideLastExitAt: initSideMap(),
          sideLossPnl: initSideMap(),
          killSwitch: false,
          lockProfitEnabled: false,
          lockProfitTriggered: false,
          winsCount: 0,
          lossesCount: 0,
          dailyPeakPnl: 0,
          dailyDrawdown: 0,
          accountPeakPnl: 0,
          accountDrawdown: 0,
          autoPeakPnl: 0,
          autoDrawdown: 0,
          decisionHistory: [],
          lastDecisionBySide: {},
          executionSamples: [],
        }),
    }),
    {
      name: 'openalgo-scalping-autotrade',
      partialize: (state) => ({
        config: state.config,
        activePresetId: state.activePresetId,
      }),
      merge: (persistedState, currentState) => {
        const persisted = (persistedState ?? {}) as Partial<AutoTradeStore>
        return {
          ...currentState,
          ...persisted,
          config: {
            ...DEFAULT_CONFIG,
            ...sanitizeConfigPatch(
              (persisted.config ?? {}) as Partial<Record<keyof AutoTradeConfigFields, unknown>>
            ),
          },
          activePresetId: persisted.activePresetId ?? currentState.activePresetId,
        }
      },
    }
  )
)
