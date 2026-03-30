export interface StrategyLegTemplate {
  action: 'buy' | 'sell'
  qty: number
  type: 'CE' | 'PE'
  strikeOffset: number // 0 = ATM, +1 = one step OTM for CE (higher strike), -1 = one step OTM for PE
}

export interface StrategyDefinition {
  id: string
  name: string
  description: string
  legs: StrategyLegTemplate[]
}

export const STRATEGIES: StrategyDefinition[] = [
  {
    id: 'long_call',
    name: 'Long Call',
    description: 'Bullish strategy: buy an ATM call. Unlimited upside, limited downside to premium paid.',
    legs: [
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 0 },
    ],
  },
  {
    id: 'long_put',
    name: 'Long Put',
    description: 'Bearish strategy: buy an ATM put. Profits as price falls, limited downside to premium paid.',
    legs: [
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: 0 },
    ],
  },
  {
    id: 'short_call',
    name: 'Short Call',
    description: 'Neutral-to-bearish strategy: sell an ATM call. Collects premium, exposed to unlimited loss if price rises.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 0 },
    ],
  },
  {
    id: 'short_put',
    name: 'Short Put',
    description: 'Neutral-to-bullish strategy: sell an ATM put. Collects premium, exposed to loss if price falls.',
    legs: [
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: 0 },
    ],
  },
  {
    id: 'long_straddle',
    name: 'Long Straddle',
    description: 'Volatility play: buy ATM call and put. Profits from large moves in either direction.',
    legs: [
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: 0 },
    ],
  },
  {
    id: 'short_straddle',
    name: 'Short Straddle',
    description: 'Neutral strategy: sell ATM call and put. Profits from low volatility, capped by premium collected.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: 0 },
    ],
  },
  {
    id: 'long_strangle',
    name: 'Long Strangle',
    description: 'Volatility play: buy OTM call and OTM put. Cheaper than straddle, requires a larger move to profit.',
    legs: [
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 1 },
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: -1 },
    ],
  },
  {
    id: 'short_strangle',
    name: 'Short Strangle',
    description: 'Neutral strategy: sell OTM call and OTM put. Wider breakeven range than short straddle.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 1 },
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: -1 },
    ],
  },
  {
    id: 'bull_call_spread',
    name: 'Bull Call Spread',
    description: 'Bullish limited-risk strategy: buy ATM call, sell OTM call. Reduces cost, caps upside profit.',
    legs: [
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 1 },
    ],
  },
  {
    id: 'bear_call_spread',
    name: 'Bear Call Spread',
    description: 'Bearish limited-risk strategy: sell ATM call, buy higher strike call. Collects net premium.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 1 },
    ],
  },
  {
    id: 'bull_put_spread',
    name: 'Bull Put Spread',
    description: 'Bullish credit spread: sell slightly OTM put, buy further OTM put. Profits if price stays above sold strike.',
    legs: [
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: -1 },
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: -2 },
    ],
  },
  {
    id: 'bear_put_spread',
    name: 'Bear Put Spread',
    description: 'Bearish debit spread: buy ATM put, sell OTM put. Reduces cost, caps downside profit.',
    legs: [
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: 0 },
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: -1 },
    ],
  },
  {
    id: 'iron_condor',
    name: 'Iron Condor',
    description: 'Neutral range-bound strategy: sell OTM strangle, buy further OTM strangle. Profits if price stays within range.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 1 },
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 2 },
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: -1 },
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: -2 },
    ],
  },
  {
    id: 'iron_butterfly',
    name: 'Iron Butterfly',
    description: 'Neutral strategy: sell ATM straddle, buy OTM wings. Higher premium collected than iron condor but narrower profit zone.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: 0 },
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 2 },
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: -2 },
    ],
  },
  {
    id: 'jade_lizard',
    name: 'Jade Lizard',
    description: 'Neutral-to-bullish: sell OTM put, sell ATM call, buy OTM call. No upside risk if credit exceeds call spread width.',
    legs: [
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: -1 },
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 1 },
    ],
  },
  {
    id: 'synthetic_long',
    name: 'Synthetic Long',
    description: 'Synthetic long stock: buy ATM call, sell ATM put. Mimics long stock exposure with no upfront capital outlay.',
    legs: [
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'sell', qty: 1, type: 'PE', strikeOffset: 0 },
    ],
  },
  {
    id: 'synthetic_short',
    name: 'Synthetic Short',
    description: 'Synthetic short stock: sell ATM call, buy ATM put. Mimics short stock exposure with options.',
    legs: [
      { action: 'sell', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'buy', qty: 1, type: 'PE', strikeOffset: 0 },
    ],
  },
  {
    id: 'ratio_call_spread',
    name: 'Ratio Call Spread',
    description: 'Moderately bullish: buy 1 ATM call, sell 2 OTM calls. Can be entered for credit; profits if price rises modestly.',
    legs: [
      { action: 'buy', qty: 1, type: 'CE', strikeOffset: 0 },
      { action: 'sell', qty: 2, type: 'CE', strikeOffset: 1 },
    ],
  },
]
