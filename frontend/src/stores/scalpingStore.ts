import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { OptionChainResponse } from '@/types/option-chain'
import type {
  ActiveSide,
  ControlTab,
  ExpiryWeek,
  OrderAction,
  ProductType,
  ScalpingOrderType,
  Underlying,
} from '@/types/scalping'

interface ScalpingState {
  // Underlying & expiry
  underlying: Underlying
  expiry: string
  expiryWeek: ExpiryWeek
  expiries: string[] // fetched list
  chainStrikeCount: number

  // Derived from underlying (computed on change)
  optionExchange: string
  indexExchange: string
  lotSize: number

  // Selection
  selectedStrike: number | null
  activeSide: ActiveSide
  selectedCESymbol: string | null
  selectedPESymbol: string | null

  // Live option chain snapshot (non-persisted, shared across scalping modules)
  optionChainData: OptionChainResponse | null
  optionChainLastUpdate: number
  optionChainIsStreaming: boolean

  // Trading config
  quantity: number // in lots
  orderType: ScalpingOrderType
  product: ProductType
  tpPoints: number
  slPoints: number
  limitPrice: number | null // price for LIMIT/TRIGGER orders (set by chart click)
  pendingEntryAction: OrderAction | null // BUY/SELL arm state for chart placement
  pendingLimitPlacement: {
    symbol: string
    side: ActiveSide
    action: OrderAction
    orderId: string | null
    quantity: number
    entryPrice: number
    tpPoints: number
    slPoints: number
    createdAt: number
  } | null
  paperMode: boolean

  // UI state
  controlTab: ControlTab
  hotkeysEnabled: boolean
  showFloatingWidget: boolean
  floatingWidgetMinimized: boolean
  chainCollapsed: boolean
  controlCollapsed: boolean

  // Floating widget positions (persisted per chart)
  ceWidgetPos: { x: number; y: number }
  peWidgetPos: { x: number; y: number }

  // Chart config
  chartInterval: number // candle interval in seconds

  // Session P&L
  sessionPnl: number
  tradeCount: number
}

interface ScalpingActions {
  setUnderlying: (u: Underlying) => void
  setExpiry: (e: string) => void
  setExpiryWeek: (w: ExpiryWeek) => void
  setExpiries: (list: string[]) => void
  setChainStrikeCount: (count: number) => void
  setSelectedStrike: (strike: number | null) => void
  setActiveSide: (side: ActiveSide) => void
  toggleActiveSide: () => void
  setSelectedSymbols: (ce: string | null, pe: string | null) => void
  setOptionChainSnapshot: (
    data: OptionChainResponse | null,
    lastUpdate?: number,
    isStreaming?: boolean
  ) => void
  clearOptionChainSnapshot: () => void
  setQuantity: (q: number) => void
  incrementQuantity: () => void
  decrementQuantity: () => void
  setOrderType: (t: ScalpingOrderType) => void
  setProduct: (p: ProductType) => void
  setTpPoints: (pts: number) => void
  setSlPoints: (pts: number) => void
  setLimitPrice: (price: number | null) => void
  setPendingEntryAction: (action: OrderAction | null) => void
  setPendingLimitPlacement: (
    placement: {
      symbol: string
      side: ActiveSide
      action: OrderAction
      orderId: string | null
      quantity: number
      entryPrice: number
      tpPoints: number
      slPoints: number
      createdAt?: number
    } | null
  ) => void
  clearPendingLimitPlacement: () => void
  setPaperMode: (on: boolean) => void
  setControlTab: (tab: ControlTab) => void
  setHotkeysEnabled: (on: boolean) => void
  setShowFloatingWidget: (on: boolean) => void
  toggleFloatingWidget: () => void
  setFloatingWidgetMinimized: (on: boolean) => void
  toggleFloatingWidgetMinimized: () => void
  setChainCollapsed: (on: boolean) => void
  setControlCollapsed: (on: boolean) => void
  setCeWidgetPos: (pos: { x: number; y: number }) => void
  setPeWidgetPos: (pos: { x: number; y: number }) => void
  setLotSize: (size: number) => void
  setChartInterval: (sec: number) => void
  addSessionPnl: (pnl: number) => void
  incrementTradeCount: () => void
  resetSession: () => void
}

type ScalpingStore = ScalpingState & ScalpingActions

const UNDERLYING_CONFIG: Record<Underlying, { optionExchange: string; indexExchange: string; lotSize: number }> = {
  NIFTY: { optionExchange: 'NFO', indexExchange: 'NSE_INDEX', lotSize: 65 },
  SENSEX: { optionExchange: 'BFO', indexExchange: 'BSE_INDEX', lotSize: 10 },
  BANKNIFTY: { optionExchange: 'NFO', indexExchange: 'NSE_INDEX', lotSize: 30 },
  FINNIFTY: { optionExchange: 'NFO', indexExchange: 'NSE_INDEX', lotSize: 25 },
}

export const useScalpingStore = create<ScalpingStore>()(
  persist(
    (set) => ({
      // Defaults
      underlying: 'NIFTY',
      expiry: '',
      expiryWeek: 'current',
      expiries: [],
      chainStrikeCount: 15,
      optionExchange: 'NFO',
      indexExchange: 'NSE_INDEX',
      lotSize: 65,
      selectedStrike: null,
      activeSide: 'CE',
      selectedCESymbol: null,
      selectedPESymbol: null,
      optionChainData: null,
      optionChainLastUpdate: 0,
      optionChainIsStreaming: false,
      quantity: 1,
      orderType: 'MARKET',
      product: 'MIS',
      tpPoints: 8,
      slPoints: 5,
      limitPrice: null,
      pendingEntryAction: null,
      pendingLimitPlacement: null,
      paperMode: true,
      controlTab: 'manual',
      hotkeysEnabled: true,
      showFloatingWidget: true,
      floatingWidgetMinimized: true,
      chainCollapsed: false,
      controlCollapsed: false,
      ceWidgetPos: { x: 8, y: 8 },
      peWidgetPos: { x: 8, y: 8 },
      chartInterval: 60,
      sessionPnl: 0,
      tradeCount: 0,

      // Actions
      setUnderlying: (u) => {
        const cfg = UNDERLYING_CONFIG[u]
        set({
          underlying: u,
          optionExchange: cfg.optionExchange,
          indexExchange: cfg.indexExchange,
          lotSize: cfg.lotSize,
          selectedStrike: null,
          selectedCESymbol: null,
          selectedPESymbol: null,
          expiry: '',
          expiries: [],
          optionChainData: null,
          optionChainLastUpdate: 0,
          optionChainIsStreaming: false,
        })
      },
      setExpiry: (e) =>
        set((s) =>
          s.expiry === e
            ? s
            : {
                expiry: e,
                selectedStrike: null,
                selectedCESymbol: null,
                selectedPESymbol: null,
                optionChainData: null,
                optionChainLastUpdate: 0,
                optionChainIsStreaming: false,
              }
        ),
      setExpiryWeek: (w) =>
        set((s) => (s.expiryWeek === w ? s : { expiryWeek: w })),
      setExpiries: (list) =>
        set((s) => {
          if (s.expiries.length === list.length && s.expiries.every((value, idx) => value === list[idx])) {
            return s
          }
          return { expiries: list }
        }),
      setChainStrikeCount: (count) =>
        set((s) => {
          const next = Math.max(5, count)
          return s.chainStrikeCount === next ? s : { chainStrikeCount: next }
        }),
      setSelectedStrike: (strike) =>
        set((s) => (s.selectedStrike === strike ? s : { selectedStrike: strike })),
      setActiveSide: (side) => set({ activeSide: side }),
      toggleActiveSide: () =>
        set((s) => ({ activeSide: s.activeSide === 'CE' ? 'PE' : 'CE' })),
      setSelectedSymbols: (ce, pe) =>
        set((s) =>
          s.selectedCESymbol === ce && s.selectedPESymbol === pe
            ? s
            : { selectedCESymbol: ce, selectedPESymbol: pe }
        ),
      setOptionChainSnapshot: (data, lastUpdate, isStreaming) =>
        set((s) => {
          const nextLastUpdate = Number.isFinite(lastUpdate) ? (lastUpdate as number) : Date.now()
          const nextStreaming = typeof isStreaming === 'boolean' ? isStreaming : s.optionChainIsStreaming
          if (
            s.optionChainData === data &&
            s.optionChainLastUpdate === nextLastUpdate &&
            s.optionChainIsStreaming === nextStreaming
          ) {
            return s
          }
          return {
            optionChainData: data,
            optionChainLastUpdate: nextLastUpdate,
            optionChainIsStreaming: nextStreaming,
          }
        }),
      clearOptionChainSnapshot: () =>
        set((s) =>
          s.optionChainData === null && s.optionChainLastUpdate === 0 && !s.optionChainIsStreaming
            ? s
            : { optionChainData: null, optionChainLastUpdate: 0, optionChainIsStreaming: false }
        ),
      setQuantity: (q) => set({ quantity: Math.max(1, q) }),
      incrementQuantity: () => set((s) => ({ quantity: s.quantity + 1 })),
      decrementQuantity: () =>
        set((s) => ({ quantity: Math.max(1, s.quantity - 1) })),
      setOrderType: (t) =>
        set({
          orderType: t,
          ...(t === 'MARKET'
            ? { limitPrice: null, pendingEntryAction: null, pendingLimitPlacement: null }
            : {}),
        }),
      setProduct: (p) => set({ product: p }),
      setTpPoints: (pts) => set({ tpPoints: Math.max(0, pts) }),
      setSlPoints: (pts) => set({ slPoints: Math.max(0, pts) }),
      setLimitPrice: (price) =>
        set((s) => (s.limitPrice === price ? s : { limitPrice: price })),
      setPendingEntryAction: (action) =>
        set((s) => (s.pendingEntryAction === action ? s : { pendingEntryAction: action })),
      setPendingLimitPlacement: (placement) =>
        set((s) => {
          if (placement === null) {
            return s.pendingLimitPlacement === null ? s : { pendingLimitPlacement: null }
          }
          const next = {
            ...placement,
            createdAt: placement.createdAt ?? Date.now(),
          }
          const current = s.pendingLimitPlacement
          if (
            current &&
            current.symbol === next.symbol &&
            current.side === next.side &&
            current.action === next.action &&
            current.orderId === next.orderId &&
            current.quantity === next.quantity &&
            current.entryPrice === next.entryPrice &&
            current.tpPoints === next.tpPoints &&
            current.slPoints === next.slPoints
          ) {
            return s
          }
          return { pendingLimitPlacement: next }
        }),
      clearPendingLimitPlacement: () =>
        set((s) => (s.pendingLimitPlacement === null ? s : { pendingLimitPlacement: null })),
      setPaperMode: (on) =>
        set((s) => (s.paperMode === on ? s : { paperMode: on })),
      setControlTab: (tab) => set({ controlTab: tab }),
      setHotkeysEnabled: (on) => set({ hotkeysEnabled: on }),
      setShowFloatingWidget: (on) => set({ showFloatingWidget: on }),
      toggleFloatingWidget: () =>
        set((s) => ({ showFloatingWidget: !s.showFloatingWidget })),
      setFloatingWidgetMinimized: (on) =>
        set((s) => (s.floatingWidgetMinimized === on ? s : { floatingWidgetMinimized: on })),
      toggleFloatingWidgetMinimized: () =>
        set((s) => ({ floatingWidgetMinimized: !s.floatingWidgetMinimized })),
      setChainCollapsed: (on) => set({ chainCollapsed: on }),
      setControlCollapsed: (on) => set({ controlCollapsed: on }),
      setCeWidgetPos: (pos) => set({ ceWidgetPos: pos }),
      setPeWidgetPos: (pos) => set({ peWidgetPos: pos }),
      setLotSize: (size) => set({ lotSize: size }),
      setChartInterval: (sec) => set({ chartInterval: sec }),
      addSessionPnl: (pnl) =>
        set((s) => ({ sessionPnl: s.sessionPnl + pnl })),
      incrementTradeCount: () =>
        set((s) => ({ tradeCount: s.tradeCount + 1 })),
      resetSession: () => set({ sessionPnl: 0, tradeCount: 0 }),
    }),
    {
      name: 'openalgo-scalping',
      partialize: (state) => ({
        underlying: state.underlying,
        expiryWeek: state.expiryWeek,
        chainStrikeCount: state.chainStrikeCount,
        quantity: state.quantity,
        orderType: state.orderType,
        product: state.product,
        tpPoints: state.tpPoints,
        slPoints: state.slPoints,
        paperMode: state.paperMode,
        hotkeysEnabled: state.hotkeysEnabled,
        showFloatingWidget: state.showFloatingWidget,
        floatingWidgetMinimized: state.floatingWidgetMinimized,
        chartInterval: state.chartInterval,
        ceWidgetPos: state.ceWidgetPos,
        peWidgetPos: state.peWidgetPos,
      }),
    }
  )
)
