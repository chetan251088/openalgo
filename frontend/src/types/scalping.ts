import type { OptionChainResponse, OptionData, OptionStrike } from './option-chain'

// Supported underlyings
export type Underlying = 'NIFTY' | 'SENSEX' | 'BANKNIFTY' | 'FINNIFTY'
export type ActiveSide = 'CE' | 'PE'
export type OrderAction = 'BUY' | 'SELL'
export type ScalpingOrderType = 'MARKET' | 'LIMIT' | 'TRIGGER'
export type ProductType = 'MIS' | 'NRML'
export type ExpiryWeek = 'current' | 'next'
export type ControlTab = 'manual' | 'auto' | 'risk' | 'depth' | 'orders'

// Underlying -> exchange mapping
export interface UnderlyingConfig {
  symbol: string
  indexExchange: string
  optionExchange: string
  lotSize: number
}

export const UNDERLYING_MAP: Record<Underlying, UnderlyingConfig> = {
  NIFTY: { symbol: 'NIFTY', indexExchange: 'NSE_INDEX', optionExchange: 'NFO', lotSize: 65 },
  SENSEX: { symbol: 'SENSEX', indexExchange: 'BSE_INDEX', optionExchange: 'BFO', lotSize: 20 },
  BANKNIFTY: { symbol: 'BANKNIFTY', indexExchange: 'NSE_INDEX', optionExchange: 'NFO', lotSize: 30 },
  FINNIFTY: { symbol: 'FINNIFTY', indexExchange: 'NSE_INDEX', optionExchange: 'NFO', lotSize: 25 },
}

// Compact chain row data for scalping view
export interface ScalpingChainRow {
  strike: number
  ce: OptionData | null
  pe: OptionData | null
  isATM: boolean
  isSelected: boolean
}

// Position tracking
export interface ScalpingPosition {
  symbol: string
  exchange: string
  side: ActiveSide
  action: OrderAction
  quantity: number
  avgPrice: number
  ltp: number
  pnl: number
  pnlPoints: number
  product: ProductType
}

// Virtual TP/SL order
export interface VirtualTPSL {
  id: string
  symbol: string
  exchange: string
  side: ActiveSide
  action: OrderAction
  entryPrice: number
  quantity: number
  tpPrice: number | null
  slPrice: number | null
  tpPoints: number
  slPoints: number
  trailStage?: TrailingStage
  createdAt: number
  managedBy?: 'manual' | 'auto' | 'trigger' | 'hotkey' | 'ghost' | 'flow'
  autoEntryScore?: number
  autoEntryReason?: string
}

// Trigger order (fire when price crosses level)
export interface TriggerOrder {
  id: string
  symbol: string
  exchange: string
  side: ActiveSide
  action: OrderAction
  triggerPrice: number
  direction: 'above' | 'below'
  quantity: number
  tpPoints: number
  slPoints: number
  createdAt: number
}

// Trade record for logging
export interface TradeRecord {
  id: string
  timestamp: number
  symbol: string
  side: ActiveSide
  action: OrderAction
  price: number
  quantity: number
  trigger: 'manual' | 'auto' | 'ghost' | 'hotkey' | 'reversal'
  pnl?: number
  pnlPoints?: number
  exitReason?: string
  duration?: number
}

// Ghost signal from auto-trade engine in shadow mode
export interface GhostSignal {
  id: string
  timestamp: number
  side: ActiveSide
  action: OrderAction
  symbol: string
  strike: number
  score: number
  reason: string
  regime?: string
  pcr?: number
}

// Market regime
export type MarketRegime = 'TRENDING' | 'VOLATILE' | 'RANGING' | 'UNKNOWN'

// Trailing SL stages
export type TrailingStage =
  | 'INITIAL'
  | 'BREAKEVEN'
  | 'LOCK'
  | 'TRAIL'
  | 'TIGHT'
  | 'ACCELERATED'

// Options context from backend analytics
export interface OptionsContext {
  pcr: number
  oiChangeCE: number
  oiChangePE: number
  maxPainStrike: number
  spotVsMaxPain: number
  topGammaStrikes: number[]
  gexFlipZones: number[]
  netGEX: number
  atmIV: number
  ivPercentile: number
  ceIV: number
  peIV: number
  ivSkew: number
  straddlePrice: number
  lastUpdated: number
}

// Market clock zones
export interface MarketClockZone {
  label: string
  start: string // HH:MM IST
  end: string
  sensitivity: number // multiplier for auto-trade
}

// Re-export option chain types for convenience
export type { OptionChainResponse, OptionData, OptionStrike }
