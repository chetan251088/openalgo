# Advanced Trading Presets Implementation Plan

## Overview
Three new trading presets to complement existing expiry/normal presets:
- **Sniper Mode**: Quality-focused, high R:R, fewer selective trades
- **Balanced Trader**: Moderate frequency with quality filters, best for normal conditions
- **Momentum Scalper**: Velocity-focused, high frequency, tight risk control

## Quick Comparison Table

| Feature | 🎯 Sniper | ⚖️ Balanced | ⚡ Scalper |
|---------|-----------|-------------|-----------|
| **Momentum Ticks** | 10 (strict) | 7 (moderate) | 4 (relaxed) |
| **Cooldown** | 120s (2min) | 45s | 15s |
| **Max Trades/Min** | 1 | 3 | 10 |
| **R:R Target** | 15:5 (3:1) | 10:4 (2.5:1) | 6:3 (2:1) |
| **Regime Mode** | TRENDING only | TRENDING + VOLATILE | ALL |
| **Filters** | ALL enabled | Selective (Underlying + Regime) | Minimal (Regime only) |
| **Min Move** | 2.5pts | 1.5pts | 0.5pts |
| **Trailing** | Wide (give room) | Balanced | Tight (lock fast) |
| **Best For** | Choppy markets, patient traders | Normal conditions, balanced approach | High volatility, active monitoring |
| **Trade Style** | Sniper - wait for perfect setup | Balanced - regular opportunities | Scalper - rapid fire |

---

## 1. SNIPER MODE - "Quality Trades"

### Philosophy
Wait patiently for high-probability setups with strong confirmation across multiple filters. Trade only the best opportunities with better risk-reward ratio.

### Target Use Case
- Choppy/uncertain markets where quick reversals are common
- When you want to avoid overtrading
- Focus on capturing major moves only
- Better for psychological comfort (fewer trades to manage)

### Parameter Configuration

#### Entry Filters (STRICT)
```javascript
momentumTicks: 10              // Current: 6 → Needs STRONG momentum (10 ticks)
candleTicks: 8                 // Current: 5 → Stricter candle confirmation
underlyingTicks: 3             // Current: 2 → Index must confirm strongly
momentumMinMovePts: 2.5        // Current: 1.0-1.5 → Minimum 2.5pts move required
```

#### Trade Frequency (REDUCED)
```javascript
minGapMs: 180000               // Current: 3000-4000 → 3 MINUTE gap between trades
maxTradesPerMin: 1             // Current: 5-6 → Max 1 trade per minute
cooldownMs: 120000             // Current: 45000 → 2 MINUTE cooldown after trade
flipCooldownMs: 180000         // Current: 15000 → 3 MINUTE before flip
```

#### Quality Filters (ALL ENABLED)
```javascript
underlyingFilterEnabled: true
candleConfirmEnabled: true
relativeStrengthEnabled: true
imbalanceEnabled: true
regimeDetectionEnabled: true
```

#### No-Trade Zone (WIDER - Skip chop)
```javascript
noTradeZoneEnabled: true
noTradeZoneRangePts: 4.0       // Current: 2.0 → Skip if 30s range < 4pts
```

#### Regime Detection (TRENDING ONLY)
```javascript
regimeVolatileThreshold: 6     // Classify volatile markets
regimeRangingThreshold: 2.5    // Classify ranging markets
// NEW: Only enter in TRENDING regime (implement in code)
regimeTradingMode: 'trending_only'  // Don't trade RANGING/VOLATILE
```

#### Risk Management (BETTER R:R)
```javascript
tpPoints: 15                   // Current: 8-10 → Target 15 points
slPoints: 5                    // Current: 4-5 → Stop 5 points (3:1 R:R)
profitLockRs: 100              // Current: 60 → Lock profit at 100₹ (1.3pts for NIFTY)
```

#### Trailing (WIDER - Give room)
```javascript
trailStaged: true
trailStage1Trigger: 3.0        // Current: 1.5-2.0 → BE after 3pts
trailStage2Trigger: 5.0        // Current: 3.0-4.0 → Lock profit at 5pts
trailStage2SL: 1.5             // Current: 0.5-1.0 → Lock 1.5pt profit
trailStage3Trigger: 10.0       // Current: 5.0-6.0 → Trail at 10pts
trailStage3Distance: 4.0       // Current: 2.5-3.0 → Trail 4pts behind
trailStage4Distance: 3.0       // Current: 1.5-2.0 → Tighten to 3pts
trailAccelMovePts: 6           // Current: 3-4 → Accelerate at 6pts
trailAccelTimeMs: 15000        // Current: 8000-10000 → In 15s
trailAccelDistance: 2.5        // Current: 1.0-1.5 → Trail 2.5pts
winStreakTrailDistance: 5.0    // Current: 3.5-4.0 → Wide trail on streak
```

#### Other Settings
```javascript
minFlipHoldMs: 10000           // Current: 5000 → Hold 10s before flip
avgWindowMs: 30000             // Current: 15000 → 30s momentum window
avgIntervalMs: 8000            // Current: 5000 → 8s interval
tradeMaxDurationMs: 300000     // Current: 180000 → 5 minute max hold
consecutiveLossBreaker: 2      // Current: 3 → Stop after 2 losses (be cautious)
```

---

## 2. BALANCED TRADER MODE - "Middle Ground"

### Philosophy
Best of both worlds - captures good opportunities with decent frequency while maintaining quality standards. Not too aggressive, not too conservative. Suitable for most normal trading conditions.

### Target Use Case
- Normal trading days with moderate volatility
- When you want regular opportunities but not crazy scalping
- Trending or volatile markets (skips only ranging/choppy)
- Good default for most live trading scenarios

### Parameter Configuration

#### Entry Filters (MODERATE)
```javascript
momentumTicks: 7               // Between Sniper's 10 and Scalper's 4
candleTicks: 5                 // Standard confirmation
underlyingTicks: 2             // Standard index confirm
momentumMinMovePts: 1.5        // Moderate velocity filter
```

#### Trade Frequency (BALANCED)
```javascript
minGapMs: 10000                // 10s gap (vs Sniper's 3min, Scalper's 5s)
maxTradesPerMin: 3             // 3 trades/min (vs Sniper's 1, Scalper's 10)
cooldownMs: 45000              // 45s cooldown (vs 2min/15s)
flipCooldownMs: 30000          // 30s flip (vs 3min/8s)
```

#### Quality Filters (SELECTIVE)
```javascript
underlyingFilterEnabled: true   // Keep index filter
candleConfirmEnabled: false     // Skip for speed
relativeStrengthEnabled: false  // Skip for speed
imbalanceEnabled: false         // Skip for speed
regimeDetectionEnabled: true    // Keep regime for safety
```

#### No-Trade Zone (MODERATE)
```javascript
noTradeZoneEnabled: true
noTradeZoneRangePts: 2.5       // Standard chop filter
```

#### Regime Detection (TRENDING + VOLATILE)
```javascript
regimeVolatileThreshold: 5     // Classify volatile markets
regimeRangingThreshold: 2      // Classify ranging markets
regimeTradingMode: 'trending_volatile'  // Skip RANGING only
```

#### Risk Management (2.5:1 R:R)
```javascript
tpPoints: 10                   // Target 10 points
slPoints: 4                    // Stop 4 points (2.5:1 R:R)
profitLockRs: 60               // Lock at 60₹ (~0.8pts)
```

#### Trailing (BALANCED - Room but responsive)
```javascript
trailStaged: true
trailStage1Trigger: 2.0        // BE after 2pts
trailStage2Trigger: 3.5        // Lock profit at 3.5pts
trailStage2SL: 1.0             // Lock 1pt profit
trailStage3Trigger: 6.0        // Trail at 6pts
trailStage3Distance: 2.5       // Trail 2.5pts behind
trailStage4Distance: 1.5       // Tighten to 1.5pts
trailAccelMovePts: 4           // Accelerate at 4pts
trailAccelTimeMs: 10000        // In 10s
trailAccelDistance: 1.5        // Trail 1.5pts
winStreakTrailDistance: 3.5    // Moderate trail on streak
```

#### Other Settings
```javascript
minFlipHoldMs: 6000            // 6s before flip
avgWindowMs: 20000             // 20s momentum window
avgIntervalMs: 5000            // 5s interval
tradeMaxDurationMs: 180000     // 3 minute max hold
consecutiveLossBreaker: 3      // Stop after 3 losses
```

---

## 3. MOMENTUM SCALPER MODE - "High Frequency"

### Philosophy
Capitalize on rapid momentum bursts with high trade frequency. Accept smaller moves but with tight risk control. Trade velocity, not just direction.

### Target Use Case
- Strong trending/volatile markets with clear momentum
- Expiry days with high volatility
- When you can actively monitor trades
- Quick in-and-out scalping mentality

### Parameter Configuration

#### Entry Filters (RELAXED)
```javascript
momentumTicks: 4               // Current: 6 → Faster entry (4 ticks)
candleTicks: 3                 // Current: 5 → Quick candle confirm
underlyingTicks: 1             // Current: 2 → Minimal index confirm
momentumMinMovePts: 0.5        // Current: 1.0-1.5 → Accept 0.5pt moves
```

#### Trade Frequency (MAXIMUM)
```javascript
minGapMs: 5000                 // Current: 3000-4000 → 5s gap minimum
maxTradesPerMin: 10            // Current: 5-6 → Up to 10/min
cooldownMs: 15000              // Current: 45000 → 15s cooldown
flipCooldownMs: 8000           // Current: 15000 → 8s before flip
```

#### Quality Filters (MINIMAL - Speed priority)
```javascript
underlyingFilterEnabled: false  // Skip for speed
candleConfirmEnabled: false     // Skip for speed
relativeStrengthEnabled: false  // Skip for speed
imbalanceEnabled: false         // Skip for speed
regimeDetectionEnabled: true    // Keep regime for safety
```

#### No-Trade Zone (MINIMAL - Trade even in chop)
```javascript
noTradeZoneEnabled: true
noTradeZoneRangePts: 1.0       // Current: 2.0 → Only skip extreme flatness
```

#### Regime Detection (TRADE ALL)
```javascript
regimeVolatileThreshold: 5     // Classify volatile markets
regimeRangingThreshold: 2      // Classify ranging markets
// NEW: Trade all regimes if momentum detected
regimeTradingMode: 'all'       // Trade TRENDING/VOLATILE/RANGING
```

#### Risk Management (TIGHT SCALP)
```javascript
tpPoints: 6                    // Current: 8-10 → Quick 6pt target
slPoints: 3                    // Current: 4-5 → Tight 3pt stop (2:1 R:R)
profitLockRs: 40               // Current: 60 → Lock early at 40₹ (0.5pts)
```

#### Trailing (AGGRESSIVE - Lock fast)
```javascript
trailStaged: true
trailStage1Trigger: 1.0        // Current: 1.5-2.0 → BE after 1pt
trailStage2Trigger: 2.0        // Current: 3.0-4.0 → Lock at 2pts
trailStage2SL: 0.5             // Current: 0.5-1.0 → Lock 0.5pt
trailStage3Trigger: 3.5        // Current: 5.0-6.0 → Trail at 3.5pts
trailStage3Distance: 1.5       // Current: 2.5-3.0 → Tight 1.5pts
trailStage4Distance: 1.0       // Current: 1.5-2.0 → Very tight 1pt
trailAccelMovePts: 2.5         // Current: 3-4 → Accelerate at 2.5pts
trailAccelTimeMs: 5000         // Current: 8000-10000 → Quick 5s
trailAccelDistance: 0.5        // Current: 1.0-1.5 → Ultra tight 0.5pt
winStreakTrailDistance: 2.0    // Current: 3.5-4.0 → Tight even on streak
```

#### Other Settings
```javascript
minFlipHoldMs: 3000            // Current: 5000 → Quick 3s flip
avgWindowMs: 10000             // Current: 15000 → Fast 10s window
avgIntervalMs: 3000            // Current: 5000 → Rapid 3s interval
tradeMaxDurationMs: 120000     // Current: 180000 → 2 minute max hold
consecutiveLossBreaker: 4      // Current: 3 → Allow 4 losses before pause
```

---

## 3. UI Implementation

### Preset Dropdown Addition
Update the preset selector to include:
```
- Sensex Expiry (fast)
- Sensex Normal
- Nifty Expiry (fast)
- Nifty Normal
- **[NEW] Sniper Mode** 🎯 (Quality trades, high R:R)
- **[NEW] Momentum Scalper** ⚡ (High frequency, tight risk)
```

### Visual Indicators
When preset is active, show badge:
- Sniper Mode: 🎯 badge with "SELECTIVE" label
- Momentum Scalper: ⚡ badge with "RAPID FIRE" label

### Preset Switch Confirmation
When switching to Sniper/Scalper with active position:
- Show warning modal: "Switching preset will affect active trade trailing. Continue?"
- Allow cancel to keep current preset

---

## 4. Code Changes Required

### File: `auto_trading_window.html`

#### A. Add new presets to AUTO_PRESETS object (~line 6487)
```javascript
sniper_quality: { /* parameters above */ },
momentum_scalper: { /* parameters above */ }
```

#### B. Add regime trading mode support (~line 7529 in autoCanEnter)
```javascript
// Check regime trading mode
if (autoState.regimeTradingMode === 'trending_only') {
    if (autoState.currentRegime !== 'TRENDING') {
        return false; // Block entry in non-trending regimes
    }
}
```

#### C. Update UI preset dropdown HTML (~line 2800)
Add two new options:
```html
<option value="sniper_quality">🎯 Sniper Mode (Quality)</option>
<option value="momentum_scalper">⚡ Momentum Scalper (High Freq)</option>
```

#### D. Add preset indicator badge (~line 2850)
```html
<div id="presetIndicator" class="preset-badge"></div>
```

#### E. Update preset application logic (~line 6684)
```javascript
autoState.regimeTradingMode = preset.regimeTradingMode || 'all';
```

---

## 5. Testing Strategy

### Sniper Mode Testing (Mock Replay)
1. Load choppy historical data (ranging market)
2. Enable Sniper preset
3. **Expected**: Very few trades (1-2 per 15min), only on strong breakouts
4. **Verify**: No entries during chop, wide trailing gives room

### Momentum Scalper Testing (Mock Replay)
1. Load volatile historical data (expiry day)
2. Enable Momentum Scalper preset
3. **Expected**: Many trades (6-10 per 15min), quick entries/exits
4. **Verify**: Rapid fire entries on momentum, tight trailing locks profit fast

### Live Testing Recommendation
1. Start with PAPER mode (already exists in code)
2. Test Sniper on normal market days
3. Test Momentum Scalper on expiry or high-vol days
4. Monitor win rate, avg hold time, max drawdown

---

## 6. Risk Warnings

### Sniper Mode Risks
- May miss trades during ranging markets (by design)
- Requires patience - fewer opportunities
- Wide trailing may miss profit if reversal is fast

### Momentum Scalper Risks
- Higher trade frequency = more brokerage costs
- Tight stops = more SL hits on noise
- Requires active monitoring (10 trades/min possible)
- Not suitable for all market conditions

---

## 7. Future Enhancements (Post-Implementation)

1. **Adaptive Preset**: Auto-switch between Sniper/Scalper based on detected regime
2. **Custom Preset Builder**: Let users create/save their own presets
3. **Preset Performance Analytics**: Track win rate, PF, avg R:R per preset
4. **Preset Backtesting**: Test presets on historical data before going live

---

## Implementation Checklist

- [ ] Add `sniper_quality` preset to AUTO_PRESETS
- [ ] Add `momentum_scalper` preset to AUTO_PRESETS
- [ ] Add `regimeTradingMode` field to autoState
- [ ] Implement regime mode check in `autoCanEnter()`
- [ ] Add preset options to UI dropdown
- [ ] Add preset indicator badge UI
- [ ] Update `applyAutoPreset()` to handle new fields
- [ ] Test in mock replay mode
- [ ] Document in README

---

**Ready to implement? Reply "proceed" to start coding.**
