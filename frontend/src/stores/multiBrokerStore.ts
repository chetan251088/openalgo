import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type UnifiedBroker = 'kotak' | 'dhan' | 'zerodha'
export type DataFeedMode = 'auto' | 'dhan' | 'zerodha'

interface MultiBrokerState {
  unifiedMode: boolean
  dataFeed: DataFeedMode
  executionBroker: UnifiedBroker
  setUnifiedMode: (enabled: boolean) => void
  setDataFeed: (feed: DataFeedMode) => void
  setExecutionBroker: (broker: UnifiedBroker) => void
}

export function resolveFeedBroker(feed: DataFeedMode): UnifiedBroker {
  if (feed === 'auto') return 'zerodha'
  return feed
}

export const useMultiBrokerStore = create<MultiBrokerState>()(
  persist(
    (set) => ({
      unifiedMode: false,
      dataFeed: 'auto',
      executionBroker: 'zerodha',
      setUnifiedMode: (enabled) => set((s) => (s.unifiedMode === enabled ? s : { unifiedMode: enabled })),
      setDataFeed: (feed) => set((s) => (s.dataFeed === feed ? s : { dataFeed: feed })),
      setExecutionBroker: (broker) =>
        set((s) => (s.executionBroker === broker ? s : { executionBroker: broker })),
    }),
    {
      name: 'openalgo-multi-broker',
      partialize: (state) => ({
        dataFeed: state.dataFeed,
        executionBroker: state.executionBroker,
      }),
    }
  )
)
