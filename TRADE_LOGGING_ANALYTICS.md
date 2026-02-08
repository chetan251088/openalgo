# Enhanced Trade Logging & Analytics

## Overview
Comprehensive logging of all auto-trade entries and exits with preset information, entry/exit reasons, filter states, and quality metrics for detailed analytics. Includes **Adaptive Auto preset** that automatically selects optimal trading style based on market conditions and performance.

---

## New Adaptive Auto Preset 🤖

The **Auto (Adaptive)** preset automatically switches between Sniper, Balanced, and Scalper modes based on:
- **Market regime** (TRENDING/VOLATILE/RANGING)
- **Consecutive losses** (3+ → Sniper, 2 → Balanced)
- **Win streaks** (4+ in TRENDING → Scalper)
- **Recent win rate** (70%+ → aggressive, 30%- → conservative)

### Adaptive Decision Tree
1. **3+ consecutive losses** → Switch to Sniper (quality-only filter)
2. **2 consecutive losses** → Switch to Balanced (reduce frequency)
3. **4+ win streak in TRENDING** → Switch to Scalper (capitalize on hot streak)
4. **High recent win rate** (≥70% last 10 trades) → Stay aggressive
5. **Low recent win rate** (≤30% last 10 trades) → Go conservative (Sniper)
6. **Default regime-based**:
   - **TRENDING** → Sniper (quality trend trades)
   - **VOLATILE** → Balanced (selective in volatility)
   - **RANGING** → Scalper (quick range scalps)

Adaptive changes logged as `type: 'ADAPTIVE'` with reason and metrics.

---

## New Fields Added to ENTRY Logs

### Preset Information
- **`preset`**: Preset key (e.g., `'sniper_quality'`, `'auto_adaptive'`)
- **`presetLabel`**: Human-readable preset name (e.g., `'🎯 Sniper (Quality)'`, `'🤖 Auto (Adaptive)'`)
- **`adaptivePreset`**: Active sub-preset when using auto_adaptive (e.g., `'sniper_quality'`)
- **`adaptivePresetLabel`**: Human-readable sub-preset label (e.g., `'🎯 Sniper (Quality)'`)

### Entry Decision Context
- **`entryReason`**: Why entry was taken (e.g., `'Auto momentum'`, `'Re-entry'`)
- **`momentumTicks`**: Configured momentum threshold
- **`momentumCount`**: Actual momentum ticks at entry
- **`momentumThreshold`**: Effective threshold after loss breaker boost
- **`momentumVelocity`**: Price change over momentum window (pts)
- **`regime`**: Market regime at entry (`'TRENDING'`, `'VOLATILE'`, `'RANGING'`)
- **`regimeMode`**: Preset regime filter (`'all'`, `'trending_only'`, `'trending_volatile'`)

### Filter States (OK/Not OK at entry time)
- **`noTradeZoneActive`**: Was 30s no-trade zone active? (true/false)
- **`underlyingFilterOk`**: Did underlying index pass filter? (true/false/null)
- **`candleConfirmOk`**: Did candle pattern confirm? (true/false/null)
- **`relativeStrengthOk`**: Did relative strength pass? (true/false/null)

### Trade Context
- **`consecutiveLosses`**: Number of consecutive losses at entry
- **`winStreak`**: Number of consecutive wins at entry
- **`sessionPnl`**: Cumulative P&L at entry time (₹)
- **`tradeNumber`**: Sequential trade number in session
- **`isReEntry`**: Was this a re-entry after profitable exit? (true/false)
- **`bidAskRatio`**: Order book imbalance ratio (bid/ask)
- **`spread`**: Top bid-ask spread (pts)

---

## New Fields Added to EXIT Logs

### Preset Information
- **`preset`**: Preset key used for the trade
- **`presetLabel`**: Human-readable preset name
- **`adaptivePreset`**: Active sub-preset when using auto_adaptive
- **`adaptivePresetLabel`**: Human-readable sub-preset label

### Exit Reason & Context
- **`exitReason`**: Why exit happened (e.g., `'TP hit'`, `'SL hit'`, `'Trailing SL'`, `'Time exit'`)
- **`regime`**: Market regime at exit
- **`regimeMode`**: Preset regime filter setting
- **`trailStage`**: Trailing stage reached (0-5)
- **`trailStaged`**: Was staged trailing enabled? (true/false)

### Trade Performance Metrics
- **`entryPrice`**: Entry price (₹)
- **`exitPrice`**: Exit price (₹)
- **`profitPts`**: Profit/loss in points (exit - entry)
- **`profitPct`**: Profit/loss percentage
- **`tpPoints`**: Configured TP target (pts)
- **`slPoints`**: Configured SL (pts)
- **`riskRewardRatio`**: TP/SL ratio (e.g., 2.5 for 10:4)

### Trade Quality Metrics
- **`highWaterMark`**: Maximum price reached during trade
- **`maxProfitPts`**: Maximum unrealized profit (highWaterMark - entry)
- **`maxDrawdownPts`**: Drawdown from high to exit (highWaterMark - exit)
- **`partialExitDone`**: Was partial profit-taking used? (true/false)
- **`tradeQuality`**: 1-5 star rating:
  - 5: Near full TP (≥80% of TP target)
  - 4: Partial TP (≥50% of TP target)
  - 3: Small profit (0 < profit < 50% TP)
  - 2: Small loss (loss < 50% of SL)
  - 1: Full SL or worse

### Session Context
- **`consecutiveLosses`**: Consecutive losses after this trade
- **`winStreak`**: Consecutive wins after this trade
- **`sessionPnl`**: Cumulative P&L after this trade (₹)
- **`tradeNumber`**: Sequential trade number
- **`holdMs`**: Trade duration in milliseconds
- **`pnl`**: Trade P&L (₹)

---

## Analytics Queries You Can Now Run

### 1. **Preset Performance Comparison**
```sql
-- Win rate, avg P&L, PF by preset (including adaptive sub-presets)
SELECT 
    COALESCE(adaptivePreset, preset) as effective_preset,
    COALESCE(adaptivePresetLabel, presetLabel) as preset_name,
    preset as parent_preset,
    COUNT(*) as total_trades,
    SUM(CASE WHEN pnl >= 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate_pct,
    AVG(pnl) as avg_pnl,
    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) / 
        ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as profit_factor,
    AVG(holdMs) / 1000.0 as avg_hold_seconds
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY effective_preset, preset_name, parent_preset
ORDER BY profit_factor DESC;
```

### 2. **Entry Reason Analysis**
```sql
-- Which entry reasons have best win rate?
SELECT 
    entryReason,
    preset,
    COUNT(*) as trades,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct,
    AVG(pnl) as avg_pnl,
    AVG(tradeQuality) as avg_quality_stars
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY entryReason, preset
HAVING trades >= 5
ORDER BY win_rate_pct DESC;
```

### 3. **Regime Performance**
```sql
-- Which regimes are most profitable per preset?
SELECT 
    preset,
    regime,
    COUNT(*) as trades,
    AVG(pnl) as avg_pnl,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct,
    AVG(profitPts) as avg_profit_pts,
    AVG(maxProfitPts) as avg_max_profit_pts,
    AVG(maxDrawdownPts) as avg_drawdown_pts
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY preset, regime
ORDER BY preset, avg_pnl DESC;
```

### 4. **Filter Effectiveness**
```sql
-- Do trades with all filters OK perform better?
SELECT 
    CASE 
        WHEN underlyingFilterOk = 1 AND candleConfirmOk = 1 AND relativeStrengthOk = 1 
        THEN 'All filters OK'
        WHEN underlyingFilterOk = 1 
        THEN 'Underlying OK only'
        ELSE 'Some filters failed'
    END as filter_status,
    COUNT(*) as trades,
    AVG(pnl) as avg_pnl,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct
FROM ai_scalper_logs
WHERE type = 'EXIT' AND preset = 'sniper_quality'
GROUP BY filter_status
ORDER BY avg_pnl DESC;
```

### 5. **Exit Reason Breakdown**
```sql
-- What causes exits and their P&L?
SELECT 
    exitReason,
    preset,
    COUNT(*) as trades,
    AVG(pnl) as avg_pnl,
    AVG(profitPts) as avg_profit_pts,
    AVG(trailStage) as avg_trail_stage
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY exitReason, preset
ORDER BY preset, trades DESC;
```

### 6. **Trailing Stage Analysis**
```sql
-- Which trailing stage produces best exits?
SELECT 
    trailStage,
    preset,
    COUNT(*) as exits,
    AVG(pnl) as avg_pnl,
    AVG(profitPts) as avg_profit_pts,
    AVG(maxProfitPts - profitPts) as avg_giveback_pts
FROM ai_scalper_logs
WHERE type = 'EXIT' AND trailStaged = 1
GROUP BY trailStage, preset
ORDER BY preset, trailStage;
```

### 7. **Risk-Reward Achievement**
```sql
-- How often do we achieve target R:R?
SELECT 
    preset,
    riskRewardRatio as target_rr,
    COUNT(*) as trades,
    AVG(profitPts / slPoints) as avg_actual_rr,
    SUM(CASE WHEN profitPts >= tpPoints * 0.8 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as hit_tp_pct,
    SUM(CASE WHEN profitPts <= -slPoints * 0.8 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as hit_sl_pct
FROM ai_scalper_logs
WHERE type = 'EXIT' AND riskRewardRatio IS NOT NULL
GROUP BY preset, riskRewardRatio
ORDER BY preset;
```

### 8. **Consecutive Loss Pattern**
```sql
-- Performance after N consecutive losses
SELECT 
    consecutiveLosses,
    preset,
    COUNT(*) as trades,
    AVG(pnl) as avg_pnl,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct,
    AVG(momentumThreshold) as avg_momentum_threshold
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY consecutiveLosses, preset
HAVING trades >= 3
ORDER BY preset, consecutiveLosses;
```

### 9. **Trade Quality Distribution**
```sql
-- How many 5-star vs 1-star trades per preset?
SELECT 
    preset,
    tradeQuality as stars,
    COUNT(*) as trades,
    AVG(pnl) as avg_pnl,
    AVG(profitPts) as avg_profit_pts
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY preset, tradeQuality
ORDER BY preset, tradeQuality DESC;
```

### 10. **Momentum Velocity Correlation**
```sql
-- Does higher momentum velocity = better outcome?
SELECT 
    CASE 
        WHEN momentumVelocity >= 3.0 THEN 'High (≥3pts)'
        WHEN momentumVelocity >= 1.5 THEN 'Medium (1.5-3pts)'
        ELSE 'Low (<1.5pts)'
    END as velocity_bucket,
    preset,
    COUNT(*) as trades,
    AVG(pnl) as avg_pnl,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct
FROM ai_scalper_logs
WHERE type = 'EXIT'
GROUP BY velocity_bucket, preset
ORDER BY preset, avg_pnl DESC;
```

### 11. **Adaptive Preset Effectiveness**
```sql
-- How well does the adaptive preset choose? Compare to fixed presets
SELECT 
    'Adaptive (Auto)' as strategy,
    COUNT(*) as trades,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct,
    AVG(pnl) as avg_pnl,
    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) / 
        ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as profit_factor
FROM ai_scalper_logs
WHERE type = 'EXIT' AND preset = 'auto_adaptive'
UNION ALL
SELECT 
    presetLabel as strategy,
    COUNT(*) as trades,
    AVG(CASE WHEN pnl >= 0 THEN 100.0 ELSE 0 END) as win_rate_pct,
    AVG(pnl) as avg_pnl,
    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) / 
        ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as profit_factor
FROM ai_scalper_logs
WHERE type = 'EXIT' AND preset IN ('sniper_quality', 'balanced_trader', 'momentum_scalper')
GROUP BY presetLabel
ORDER BY profit_factor DESC;
```

### 12. **Adaptive Switching Analysis**
```sql
-- Track adaptive preset switches and outcomes
SELECT 
    fromPreset,
    toPresetLabel as switched_to,
    reason,
    COUNT(*) as switch_count,
    AVG(recentWinRate) as avg_win_rate_at_switch
FROM ai_scalper_logs
WHERE type = 'ADAPTIVE'
GROUP BY fromPreset, toPresetLabel, reason
ORDER BY switch_count DESC;
```

---

## Backend Database Schema

The `/ai_scalper/logs` endpoint stores all logged data in:
- **Database**: `db/ai_scalper_logs.db`
- **Table**: `ai_scalper_logs`
- **Columns**: All fields above stored in `meta_json` JSONB column + core fields as top-level columns

### Access Analytics
```python
# Flask route for analytics dashboard
GET /ai_scalper/analytics?preset=sniper_quality&date=2026-02-06

# Returns JSON with aggregated metrics
```

---

## Usage Notes

### Browser Console
All trades are logged to browser console with full detail:
```javascript
console.log('Trade logged:', event);
```

### Export Trade Log
Browser stores last 200 trades, can export via UI:
```javascript
exportAutoTradeLog(); // Downloads JSON file
```

### Live Monitoring
Watch trades in real-time in the Auto panel's trade log section (shows last 20 trades).

---

## Benefits for Strategy Optimization

1. **Preset Tuning**: See which preset performs best in which conditions
2. **Filter Optimization**: Identify which filters actually improve win rate
3. **Regime Adaptation**: Know which regimes to trade vs avoid per preset
4. **Exit Optimization**: Fine-tune trailing stages based on actual performance
5. **Risk Management**: Track consecutive loss patterns and adjust breakers
6. **Entry Quality**: Measure if strict filters (Sniper) truly outperform loose filters (Scalper)

All data is now captured for comprehensive backtesting and forward testing analysis!
