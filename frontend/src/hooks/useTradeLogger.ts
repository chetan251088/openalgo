import { useRef, useCallback } from 'react'
import type { TradeRecord } from '@/types/scalping'

const MAX_TRADES = 500

/**
 * In-memory ring buffer for trade records.
 * Stores last 500 trades for pattern analysis.
 * Does NOT persist across page reloads (speed first).
 */
export function useTradeLogger() {
  const tradesRef = useRef<TradeRecord[]>([])

  const logTrade = useCallback((trade: Omit<TradeRecord, 'id'>) => {
    const record: TradeRecord = {
      ...trade,
      id: `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    }

    tradesRef.current.push(record)
    if (tradesRef.current.length > MAX_TRADES) {
      tradesRef.current = tradesRef.current.slice(-MAX_TRADES)
    }

    return record
  }, [])

  const getTrades = useCallback(() => {
    return [...tradesRef.current]
  }, [])

  const getRecentTrades = useCallback((count: number) => {
    return tradesRef.current.slice(-count)
  }, [])

  const clearTrades = useCallback(() => {
    tradesRef.current = []
  }, [])

  const exportAsJSON = useCallback(() => {
    return JSON.stringify(tradesRef.current, null, 2)
  }, [])

  const getStats = useCallback(() => {
    const trades = tradesRef.current
    if (trades.length === 0)
      return { count: 0, winRate: 0, avgPnl: 0, totalPnl: 0, bestTrade: 0, worstTrade: 0 }

    const withPnl = trades.filter((t) => t.pnl !== undefined)
    const wins = withPnl.filter((t) => (t.pnl ?? 0) > 0)
    const totalPnl = withPnl.reduce((s, t) => s + (t.pnl ?? 0), 0)
    const pnls = withPnl.map((t) => t.pnl ?? 0)

    return {
      count: trades.length,
      winRate: withPnl.length > 0 ? (wins.length / withPnl.length) * 100 : 0,
      avgPnl: withPnl.length > 0 ? totalPnl / withPnl.length : 0,
      totalPnl,
      bestTrade: pnls.length > 0 ? Math.max(...pnls) : 0,
      worstTrade: pnls.length > 0 ? Math.min(...pnls) : 0,
    }
  }, [])

  return {
    logTrade,
    getTrades,
    getRecentTrades,
    clearTrades,
    exportAsJSON,
    getStats,
  }
}
