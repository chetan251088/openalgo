/**
 * Auto-trade preset configurations.
 * Each preset is a partial AutoTradeConfig that gets merged with defaults.
 */

export interface AutoTradePreset {
  id: string
  name: string
  description: string
  config: Partial<AutoTradeConfigFields>
}

export interface AutoTradeConfigFields {
  // Entry
  entryMomentumCount: number
  entryMomentumVelocity: number
  entryMinScore: number
  entryMaxSpread: number

  // Volume influence
  volumeInfluenceEnabled: boolean
  volumeLookbackTicks: number
  indexVolumeMinRatio: number
  optionVolumeMinRatio: number
  sideVolumeDominanceRatio: number
  volumeScoreWeight: number

  // Trailing SL (5-stage)
  trailInitialSL: number
  trailBreakevenTrigger: number
  trailLockTrigger: number
  trailLockAmount: number
  trailStartTrigger: number
  trailStepSize: number
  trailTightTrigger: number
  trailTightStep: number

  // Breakeven
  breakevenTriggerPts: number
  breakevenBuffer: number

  // Risk
  maxDailyLoss: number
  perTradeMaxLoss: number
  maxTradesPerDay: number
  maxTradesPerMinute: number
  minGapMs: number
  cooldownAfterLossSec: number
  coolingOffAfterLosses: number
  maxPositionSize: number

  // Imbalance filter
  imbalanceFilterEnabled: boolean
  imbalanceThreshold: number

  // Telegram alerts
  telegramAlertsEntry: boolean
  telegramAlertsExit: boolean
  telegramAlertsTune: boolean

  // Regime
  regimeDetectionPeriod: number
  rangingThresholdPts: number

  // Index bias
  indexBiasEnabled: boolean
  indexBiasWeight: number

  // Options context
  optionsContextEnabled: boolean
  pcrBullishThreshold: number
  pcrBearishThreshold: number
  maxPainProximityFilter: number
  gexWallFilterEnabled: boolean
  ivSpikeExitEnabled: boolean
  ivSpikeThreshold: number

  // Time-of-day
  respectHotZones: boolean
  sensitivityMultiplier: number

  // No-trade zone
  noTradeZoneEnabled: boolean
  noTradeZoneRangePts: number
  noTradeZonePeriod: number

  // Re-entry
  reEntryEnabled: boolean
  reEntryDelaySec: number
  reEntryMaxPerSide: number
}

export const DEFAULT_CONFIG: AutoTradeConfigFields = {
  entryMomentumCount: 5,
  entryMomentumVelocity: 3,
  entryMinScore: 6,
  entryMaxSpread: 5,

  volumeInfluenceEnabled: true,
  volumeLookbackTicks: 20,
  indexVolumeMinRatio: 1.05,
  optionVolumeMinRatio: 1.15,
  sideVolumeDominanceRatio: 1.1,
  volumeScoreWeight: 1.0,

  trailInitialSL: 15,
  trailBreakevenTrigger: 10,
  trailLockTrigger: 20,
  trailLockAmount: 10,
  trailStartTrigger: 25,
  trailStepSize: 5,
  trailTightTrigger: 40,
  trailTightStep: 3,

  breakevenTriggerPts: 10,
  breakevenBuffer: 1,

  maxDailyLoss: 5000,
  perTradeMaxLoss: 500,
  maxTradesPerDay: 20,
  maxTradesPerMinute: 5,
  minGapMs: 4000,
  cooldownAfterLossSec: 45,
  coolingOffAfterLosses: 3,
  maxPositionSize: 3,

  imbalanceFilterEnabled: false,
  imbalanceThreshold: 1.8,

  telegramAlertsEntry: false,
  telegramAlertsExit: false,
  telegramAlertsTune: false,

  regimeDetectionPeriod: 50,
  rangingThresholdPts: 20,

  indexBiasEnabled: true,
  indexBiasWeight: 0.3,

  optionsContextEnabled: true,
  pcrBullishThreshold: 0.7,
  pcrBearishThreshold: 1.3,
  maxPainProximityFilter: 50,
  gexWallFilterEnabled: true,
  ivSpikeExitEnabled: true,
  ivSpikeThreshold: 5,

  respectHotZones: true,
  sensitivityMultiplier: 1.0,

  noTradeZoneEnabled: true,
  noTradeZoneRangePts: 15,
  noTradeZonePeriod: 20,

  reEntryEnabled: false,
  reEntryDelaySec: 30,
  reEntryMaxPerSide: 2,
}

export const PRESETS: AutoTradePreset[] = [
  {
    id: 'sniper',
    name: 'Sniper Quality',
    description: 'Tight entry, wider SL. For sideways/quiet days.',
    config: {
      entryMomentumCount: 7,
      entryMinScore: 8,
      trailInitialSL: 20,
      trailBreakevenTrigger: 15,
      maxTradesPerDay: 10,
      minGapMs: 6000,
      noTradeZoneRangePts: 20,
    },
  },
  {
    id: 'momentum',
    name: 'Momentum Scalper',
    description: 'Fast entry, tight SL. For trending days.',
    config: {
      entryMomentumCount: 3,
      entryMomentumVelocity: 5,
      entryMinScore: 5,
      trailInitialSL: 10,
      trailBreakevenTrigger: 7,
      trailStepSize: 3,
      maxTradesPerDay: 30,
      noTradeZoneEnabled: false,
    },
  },
  {
    id: 'balanced',
    name: 'Balanced Trader',
    description: 'Default middle ground. Good for most days.',
    config: {}, // Uses defaults
  },
  {
    id: 'adaptive',
    name: 'Auto-Adaptive',
    description: 'Uses options context + regime detection. Adjusts dynamically.',
    config: {
      optionsContextEnabled: true,
      indexBiasEnabled: true,
      indexBiasWeight: 0.4,
      respectHotZones: true,
      gexWallFilterEnabled: true,
      ivSpikeExitEnabled: true,
      reEntryEnabled: true,
    },
  },
  {
    id: 'adaptive-scalper',
    name: 'Adaptive Scalper',
    description: 'Aggressive scalping profile with lighter entry gates and faster re-entry.',
    config: {
      entryMomentumCount: 3,
      entryMomentumVelocity: 2,
      entryMinScore: 4,
      entryMaxSpread: 6,
      volumeLookbackTicks: 14,
      indexVolumeMinRatio: 1.0,
      optionVolumeMinRatio: 1.0,
      sideVolumeDominanceRatio: 1.0,
      volumeScoreWeight: 1.25,
      trailInitialSL: 10,
      trailBreakevenTrigger: 6,
      trailLockTrigger: 12,
      trailLockAmount: 6,
      trailStartTrigger: 15,
      trailStepSize: 3,
      trailTightTrigger: 24,
      trailTightStep: 2,
      maxTradesPerDay: 40,
      maxTradesPerMinute: 10,
      minGapMs: 1500,
      cooldownAfterLossSec: 15,
      coolingOffAfterLosses: 5,
      indexBiasEnabled: true,
      indexBiasWeight: 0.2,
      optionsContextEnabled: true,
      pcrBullishThreshold: 0.5,
      pcrBearishThreshold: 1.6,
      maxPainProximityFilter: 10,
      gexWallFilterEnabled: false,
      ivSpikeExitEnabled: false,
      respectHotZones: false,
      sensitivityMultiplier: 1.25,
      noTradeZoneEnabled: false,
      reEntryEnabled: true,
      reEntryDelaySec: 8,
      reEntryMaxPerSide: 6,
    },
  },
  {
    id: 'expiry',
    name: 'Expiry Day',
    description: 'Post-13:30 surprise watch, tighter risk, faster exits.',
    config: {
      entryMomentumCount: 3,
      entryMinScore: 5,
      trailInitialSL: 8,
      trailBreakevenTrigger: 5,
      trailStepSize: 2,
      trailTightStep: 1,
      maxDailyLoss: 3000,
      perTradeMaxLoss: 300,
      maxTradesPerDay: 15,
      maxTradesPerMinute: 8,
      minGapMs: 3000,
      cooldownAfterLossSec: 30,
      coolingOffAfterLosses: 2,
      sensitivityMultiplier: 1.5,
      noTradeZoneEnabled: false,
    },
  },
]
