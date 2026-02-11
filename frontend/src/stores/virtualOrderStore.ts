import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { VirtualTPSL, TriggerOrder } from '@/types/scalping'

interface VirtualOrderState {
  // Virtual TP/SL per position
  virtualTPSL: Record<string, VirtualTPSL>
  // Trigger orders (fire when price crosses level)
  triggerOrders: Record<string, TriggerOrder>
}

interface VirtualOrderActions {
  // Virtual TP/SL
  setVirtualTPSL: (order: VirtualTPSL) => void
  removeVirtualTPSL: (id: string) => void
  updateVirtualTPSL: (id: string, updates: Partial<VirtualTPSL>) => void
  getTPSLForSymbol: (symbol: string) => VirtualTPSL | undefined

  // Trigger orders
  addTriggerOrder: (order: TriggerOrder) => void
  removeTriggerOrder: (id: string) => void
  updateTriggerOrder: (id: string, updates: Partial<TriggerOrder>) => void
  clearTriggerOrders: () => void

  // Bulk
  clearAll: () => void
  clearForSymbol: (symbol: string) => void
}

type VirtualOrderStore = VirtualOrderState & VirtualOrderActions

export const useVirtualOrderStore = create<VirtualOrderStore>()(
  persist(
    (set, get) => ({
      virtualTPSL: {},
      triggerOrders: {},

      setVirtualTPSL: (order) =>
        set((s) => ({
          virtualTPSL: { ...s.virtualTPSL, [order.id]: order },
        })),

      removeVirtualTPSL: (id) =>
        set((s) => {
          const { [id]: _, ...rest } = s.virtualTPSL
          return { virtualTPSL: rest }
        }),

      updateVirtualTPSL: (id, updates) =>
        set((s) => {
          const existing = s.virtualTPSL[id]
          if (!existing) return s
          return {
            virtualTPSL: {
              ...s.virtualTPSL,
              [id]: { ...existing, ...updates },
            },
          }
        }),

      getTPSLForSymbol: (symbol) => {
        const entries = Object.values(get().virtualTPSL)
          .filter((o) => o.symbol === symbol)
          .sort((a, b) => b.createdAt - a.createdAt)
        return entries[0]
      },

      addTriggerOrder: (order) =>
        set((s) => ({
          triggerOrders: { ...s.triggerOrders, [order.id]: order },
        })),

      removeTriggerOrder: (id) =>
        set((s) => {
          const { [id]: _, ...rest } = s.triggerOrders
          return { triggerOrders: rest }
        }),

      updateTriggerOrder: (id, updates) =>
        set((s) => {
          const existing = s.triggerOrders[id]
          if (!existing) return s
          return {
            triggerOrders: {
              ...s.triggerOrders,
              [id]: { ...existing, ...updates },
            },
          }
        }),

      clearTriggerOrders: () => set({ triggerOrders: {} }),

      clearAll: () => set({ virtualTPSL: {}, triggerOrders: {} }),

      clearForSymbol: (symbol) =>
        set((s) => {
          const tpsl = Object.fromEntries(
            Object.entries(s.virtualTPSL).filter(([, v]) => v.symbol !== symbol)
          )
          const triggers = Object.fromEntries(
            Object.entries(s.triggerOrders).filter(([, v]) => v.symbol !== symbol)
          )
          return { virtualTPSL: tpsl, triggerOrders: triggers }
        }),
    }),
    {
      name: 'openalgo-scalping-orders',
      version: 2,
      // Trigger orders are session-scoped and should never survive reloads.
      partialize: (state) => ({
        virtualTPSL: state.virtualTPSL,
      }),
      migrate: (persistedState) => {
        const state = (persistedState ?? {}) as Partial<VirtualOrderState>
        return {
          virtualTPSL: state.virtualTPSL ?? {},
          triggerOrders: {},
        }
      },
    }
  )
)
