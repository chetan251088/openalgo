# OpenAlgo Scalping & Auto-Trading Framework
## Comprehensive Technical Documentation

**Last Updated:** February 7, 2026  
**Version:** 2.0 (with Adaptive Presets)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Auto-Trading System](#auto-trading-system)
5. [Manual Trading](#manual-trading)
6. [Mock Replay System](#mock-replay-system)
7. [Logging & Analytics](#logging--analytics)
8. [Data Flow](#data-flow)
9. [Configuration](#configuration)
10. [API Reference](#api-reference)

---

## Overview

The OpenAlgo Scalping Framework is a sophisticated algorithmic trading system designed for Indian options markets (NIFTY/SENSEX). It provides both manual and automated trading capabilities with advanced features like adaptive strategy selection, multi-stage trailing stop-loss, market regime detection, and comprehensive analytics.

### Key Features

✅ **Dual Trading Modes**
- Manual trading via scalping interface and chart window
- Fully automated trading with AI-driven strategy selection

✅ **Adaptive Strategy System**
- 4 distinct trading presets (Sniper, Balanced, Scalper, Auto-Adaptive)
- Real-time strategy switching based on market conditions and performance

✅ **Advanced Risk Management**
- 5-stage trailing stop-loss system
- Dynamic position sizing
- Consecutive loss breakers
- Daily loss limits

✅ **Market Intelligence**
- Real-time regime detection (TRENDING/VOLATILE/RANGING)
- Momentum velocity analysis
- No-trade zone detection (chop filter)
- Order book imbalance analysis

✅ **Backtesting & Analytics**
- Mock replay server for historical data replay
- Comprehensive trade logging with enriched metadata
- Performance analytics with equity curves
- Separate databases for auto vs manual trades

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Flask Backend (Python)                    │
│  ├─ Broker API Integration (24+ brokers)                     │
│  ├─ WebSocket Proxy (Port 8765/8766/8767/8770)              │
│  ├─ REST API (/api/v1/*)                                     │
│  └─ Database Layer (SQLite)                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ├─ WebSocket Ticks
                              ├─ Order/Position Updates
                              └─ Market Data
                              │
┌─────────────────────────────────────────────────────────────┐
│                    Frontend Layer                            │
│  ├─ Jinja2 Templates (Standalone HTML)                      │
│  │   ├─ auto_trading_window.html (~12,800 lines)            │
│  │   ├─ scalping_interface.html                             │
│  │   ├─ chart_window.html                                   │
│  │   └─ mock_replay.html                                    │
│  └─ React SPA (/react) - Analytics Dashboard                │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

**Backend:**
- Python 3.12+ with Flask
- SQLite databases (7 separate DBs)
- WebSocket for real-time data
- ZeroMQ message bus

**Frontend:**
- Standalone HTML/CSS/JS (Jinja2 templates)
- Lightweight Charts library (TradingView)
- React 19 + TypeScript (analytics pages)
- Tailwind CSS 4 + DaisyUI

---

## Core Components

### 1. Auto Trading Window (`auto_trading_window.html`)

**Purpose:** Main automated trading interface with dual-side (CE/PE) charting and execution

**Key Features:**
- Real-time tick processing for both Call and Put options
- Dual-chart display with synchronized time axis
- Paper mode and Live mode support
- Auto-trading with configurable presets
- Manual override capabilities

**State Management:**
Central `autoState` object (~80+ fields) manages:
- Position tracking (paper and live)
- Momentum detection
- Trailing stop-loss stages
- P&L calculations
- Trade history
- Market regime

**Launch:** Opens as popup from scalping interface or directly via URL with params

---

### 2. Scalping Interface (`scalping_interface.html`)

**Purpose:** Manual scalping dashboard with quick order entry

**Key Features:**
- Strike selection grid
- Quick BUY/SELL buttons
- Position display
- Auto-trading window launcher
- Real-time P&L tracking

**Use Case:** Intraday option scalping during market hours

---

### 3. Chart Window (`chart_window.html`)

**Purpose:** Advanced charting with manual trading controls

**Key Features:**
- Full-featured candlestick charts
- Order placement directly from chart
- Position visualization
- TP/SL management
- Manual trade logging

**Use Case:** Technical analysis-based manual trading

---

### 4. Mock Replay Server (`scripts/historify_replay_server.py`)

**Purpose:** Historical data replay for backtesting during after-market hours

**Key Features:**
- Replays 1-minute OHLC data from DuckDB
- Synthetic option price generation using Black-Scholes
- WebSocket server on port 8770
- Playback speed control
- Multiple symbol support

**Database:** `db/historify.duckdb`

---

## Auto-Trading System

### Trading Presets

The system includes 4 distinct trading strategies:

#### 1. **🎯 Sniper (Quality)**
**Philosophy:** Quality over quantity - only the best setups

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Momentum Ticks | 10 | High conviction entries |
| Cooldown | 2 minutes | Selective trading |
| Max Trades/Min | 1 | Quality focus |
| R:R Ratio | 3:1 (12:4) | Large winners |
| Regime Filter | TRENDING only | Trade only with trend |
| Filters | All enabled | Strict entry criteria |

**Best For:** Strong trending markets, risk-averse traders

#### 2. **⚖️ Balanced Trader**
**Philosophy:** Moderate frequency with selective entry

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Momentum Ticks | 7 | Standard confirmation |
| Cooldown | 45 seconds | Regular trading |
| Max Trades/Min | 3 | Balanced frequency |
| R:R Ratio | 2.5:1 (10:4) | Good reward-risk |
| Regime Filter | TRENDING + VOLATILE | Skip RANGING only |
| Filters | Underlying + Regime | Core filters |

**Best For:** Normal market conditions, balanced approach

#### 3. **⚡ Momentum Scalper**
**Philosophy:** High frequency, tight control

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Momentum Ticks | 4 | Quick entries |
| Cooldown | 15 seconds | High frequency |
| Max Trades/Min | 10 | Maximum activity |
| R:R Ratio | 2:1 (6:3) | Quick profits |
| Regime Filter | All regimes | Always active |
| Filters | Minimal | Fast execution |

**Best For:** Volatile markets, scalping strategies

#### 4. **🤖 Auto (Adaptive)**
**Philosophy:** Intelligent strategy switching based on performance and conditions

**Decision Tree:**
```
IF consecutive_losses >= 3
  → Switch to Sniper (quality mode)
ELSE IF consecutive_losses >= 2
  → Switch to Balanced (reduce frequency)
ELSE IF win_streak >= 4 AND regime == TRENDING
  → Switch to Scalper (capitalize on momentum)
ELSE IF recent_win_rate >= 70%
  → Stay aggressive (Scalper/Balanced)
ELSE IF recent_win_rate <= 30%
  → Go conservative (Sniper)
ELSE
  → Regime-based selection:
     TRENDING → Sniper (quality trend trades)
     VOLATILE → Balanced (selective)
     RANGING → Scalper (quick scalps)
```

**Evaluation Frequency:** Every 10 seconds

**Best For:** All market conditions, hands-free optimization

---

### Multi-Stage Trailing Stop-Loss

The system implements a sophisticated 5-stage trailing system:

#### Stage 1: Move to Breakeven (BE)
- **Trigger:** Profit >= `beMinProfit` (min of `stage1Trigger` and 1.5 pts)
- **Action:** SL moves to entry ± `beBufferPts` (0.5 pt)
- **Delay:** `beDelayMs` (default 3s) to avoid premature BE

#### Stage 2: Lock Profit
- **Trigger:** Profit >= `stage1Trigger` pts (e.g., 3 pts for Scalper)
- **Action:** SL at entry + `stage2SL` pts (e.g., +0.5 pt locked)

#### Stage 3: Trailing
- **Trigger:** Profit >= `stage2Trigger` pts (e.g., 2 pts)
- **Action:** Trail at price ± `stage3Distance` pts (e.g., 1.5 pt)

#### Stage 4: Tight Trailing
- **Trigger:** Profit >= `stage3Trigger` pts (e.g., 3.5 pts)
- **Action:** Trail at price ± `stage4Distance` pts (e.g., 1 pt)

#### Stage 5: Acceleration Mode
- **Trigger:** `accelMovePts` pts in `accelTimeMs` ms (e.g., 2.5 pts in 5s)
- **Action:** Ultra-tight trail at price ± `accelDistance` (e.g., 0.5 pt)

#### Win-Streak Bonus
- **Trigger:** 3+ consecutive wins + stage 3 reached
- **Action:** Wider trail at `winStreakTrailDistance` (e.g., 2 pt) to let winners run

---

### Market Regime Detection

**Window:** 60-second rolling window

**Classification:**
```javascript
range = high - low (over 60s window)
directionality = |upTicks - downTicks| / totalTicks

IF range >= regimeVolatileThreshold (default 5)
  IF directionality > 0.3
    → TRENDING
  ELSE
    → VOLATILE
ELSE IF range < regimeRangingThreshold (default 2)
  → RANGING
ELSE
  IF directionality > 0.25
    → TRENDING
  ELSE
    → RANGING
```

**Impact on Trading:**
- **TRENDING:** Normal operation, all presets active
- **VOLATILE:** Balanced/Scalper active, wider stops
- **RANGING:** Extra momentum confirmation (+2 ticks), tight stops

---

### Entry Logic

**Momentum Detection:**
```javascript
// Track consecutive ticks in same direction
if (currentPrice > lastPrice)
  momentumCount[side]++
else if (currentPrice < lastPrice)
  momentumCount[side] = 0  // Reset

// Velocity filter
velocity = priceChange / momentumWindowMs
if (velocity < momentumMinMovePts)
  block entry  // Too slow
```

**Entry Conditions (ALL must pass):**
1. ✅ Momentum ticks >= threshold (4-10 depending on preset)
2. ✅ Momentum velocity >= `momentumMinMovePts` (0.5-1.5 pts)
3. ✅ NOT in no-trade zone (30s range >= `noTradeZoneRangePts`)
4. ✅ Cooldown expired
5. ✅ Max trades per minute not exceeded
6. ✅ Regime filter passes (if enabled)
7. ✅ Consecutive loss limit not hit
8. ✅ Optional filters: underlying, candle, RS, imbalance

**Entry Execution:**
- **Paper Mode:** Create virtual position in `autoState.paperPositions[side]`
- **Live Mode:** Place MARKET order via broker API, track in `autoState.pendingOrders`

---

### Exit Logic

**Exit Triggers:**
1. **Trailing SL Hit:** Price crosses current trail level
2. **Fixed TP:** Price >= entry + `tpPoints`
3. **Fixed SL:** Price <= entry - `slPoints`
4. **Max Duration:** Trade open > `tradeMaxDurationMs` (default 2 min)
5. **Max Loss:** Single trade loss >= `perTradeMaxLoss`
6. **Daily Loss:** Total daily loss >= `dailyMaxLoss`
7. **Manual Exit:** User clicks close button

**Exit Execution:**
- **Paper Mode:** Close virtual position, calculate P&L
- **Live Mode:** Place SELL order, poll for fill confirmation

**P&L Calculation:**
```javascript
// Options are always BUY side
pnl = (exitPrice - entryPrice) * quantity

// Sanity check: cap at 2x SL worth
maxLoss = quantity * slPoints * 2
if (pnl < -maxLoss) pnl = -maxLoss
```

---

## Manual Trading

### Scalping Interface

**Entry Flow:**
1. Select strike from grid
2. Click BUY CE/PE button
3. Order placed via broker API
4. Position appears when filled
5. **No automatic ENTRY log** (only for auto-trades)

**Exit Flow:**
1. Click SELL button or Close Position
2. Exit order placed
3. **Manual exit log** saved to `manual_trade_logs.db`

### Chart Window

**Entry Flow:**
1. Position fetched from broker
2. Displayed on chart
3. **ENTRY log** created automatically (one-time per symbol)

**Exit Flow:**
1. Click "Close Position" button
2. Exit order executed
3. **EXIT log** created with P&L

**Database:** `db/manual_trade_logs.db`

---

## Mock Replay System

### Purpose
Backtest auto-trading strategies using historical data during after-market hours.

### Components

#### 1. Replay Server (`scripts/historify_replay_server.py`)
- Reads 1-minute OHLC from `db/historify.duckdb`
- Generates synthetic option ticks using Black-Scholes
- WebSocket server on port 8770
- Supports multiple concurrent clients

#### 2. Mock Replay UI (`mock_replay.html`)
- Chart with timeframe selector (5s to 5m candles)
- Playback speed control
- Symbol selection
- Auto-trade launch panel

#### 3. Integration
```
mock_replay.html → Launch Auto Trade
  ↓
auto_trading_window.html?wsUrl=ws://127.0.0.1:8770
  ↓
Replay server sends synthetic option ticks
  ↓
Auto-trading window processes ticks (Paper mode forced)
  ↓
Trades logged to ai_scalper_logs.db
```

### Mock Mode Detection
```javascript
// Auto-detected via WebSocket URL
isMockMode = wsUrl.includes(':8770') || wsUrl.includes('mock')

if (isMockMode) {
  autoState.paperMode = true  // Force paper mode
  state.isMockMode = true
  // Disable data stall check (replay can be slower)
}
```

---

## Logging & Analytics

### Database Structure

**7 Separate Databases:**

1. **`openalgo.db`** - Main app (users, orders, positions, settings)
2. **`logs.db`** - Traffic and API logs
3. **`latency.db`** - Latency monitoring
4. **`sandbox.db`** - Analyzer/sandbox virtual trading
5. **`historify.duckdb`** - Historical market data (DuckDB)
6. **`ai_scalper_logs.db`** - **Auto-trade logs** (ENTRY/EXIT)
7. **`manual_trade_logs.db`** - **Manual trade logs** (ENTRY/EXIT)

### Auto-Trade Logging

**ENTRY Log Fields:**
```json
{
  "type": "ENTRY",
  "mode": "PAPER" | "LIVE",
  "side": "CE" | "PE",
  "symbol": "NIFTY10FEB2625700CE",
  "action": "BUY",
  "qty": 75,
  "price": 100.50,
  "tpPoints": 6,
  "slPoints": 3,
  
  // Preset context
  "preset": "momentum_scalper",
  "presetLabel": "⚡ Momentum Scalper",
  "adaptivePreset": "sniper_quality",  // If using auto_adaptive
  "adaptivePresetLabel": "🎯 Sniper (Quality)",
  
  // Entry decision context
  "entryReason": "Auto momentum",
  "momentumTicks": 4,
  "momentumCount": 5,
  "momentumThreshold": 4,
  "momentumVelocity": 2.3,
  
  // Market regime
  "regime": "TRENDING",
  "regimeMode": "all",
  
  // Filter states (at entry time)
  "noTradeZoneActive": false,
  "underlyingFilterOk": true,
  "candleConfirmOk": null,
  "relativeStrengthOk": null,
  
  // Session context
  "consecutiveLosses": 0,
  "winStreak": 2,
  "sessionPnl": 450,
  "tradeNumber": 15,
  "isReEntry": false,
  
  // Order book
  "bidAskRatio": 1.2,
  "spread": 0.10
}
```

**EXIT Log Fields:**
```json
{
  "type": "EXIT",
  "mode": "PAPER" | "LIVE",
  "side": "CE" | "PE",
  "symbol": "NIFTY10FEB2625700CE",
  "action": "SELL",
  "qty": 75,
  "price": 103.50,
  "pnl": 225,  // (103.50 - 100.50) × 75
  "holdMs": 45000,
  
  // Preset info
  "preset": "momentum_scalper",
  "presetLabel": "⚡ Momentum Scalper",
  "adaptivePreset": "sniper_quality",
  "adaptivePresetLabel": "🎯 Sniper (Quality)",
  
  // Exit reason
  "exitReason": "Trailing SL",
  "reason": "Stage 3 trail",
  
  // Trade performance
  "entryPrice": 100.50,
  "exitPrice": 103.50,
  "profitPts": 3.0,
  "profitPct": 2.99,
  "tpPoints": 6,
  "slPoints": 3,
  "riskRewardRatio": 2.0,
  
  // Exit quality metrics
  "regime": "TRENDING",
  "trailStage": 3,
  "highWaterMark": 104.50,
  "maxProfitPts": 4.0,
  "maxDrawdownPts": 1.0,
  "partialExitDone": false,
  
  // Trade quality (1-5 stars)
  "tradeQuality": 4,  // Based on % of TP achieved
  
  // Session outcome
  "consecutiveLosses": 0,
  "winStreak": 3,
  "sessionPnl": 675
}
```

**ADAPTIVE Log (Strategy Switch):**
```json
{
  "type": "ADAPTIVE",
  "fromPreset": "momentum_scalper",
  "toPreset": "sniper_quality",
  "toPresetLabel": "🎯 Sniper (Quality)",
  "reason": "3+ consecutive losses - switching to quality-only mode",
  "regime": "VOLATILE",
  "consecutiveLosses": 3,
  "winStreak": 0,
  "recentWinRate": 20.0,
  "recentTrades": 10
}
```

### Analytics Endpoints

**Backend API:**
- `GET /ai_scalper/analytics` - Auto-trade analytics
- `GET /manual_trades/analytics` - Manual trade analytics
- `POST /ai_scalper/logs` - Batch insert auto-trade logs
- `POST /manual_trades/logs` - Batch insert manual trade logs

**React Pages:**
- `/auto-trade/analytics` - Auto-trade dashboard
- `/manual-trades/analytics` - Manual trade dashboard

---

## Data Flow

### Real-Time Trading Flow

```
1. WebSocket Tick Received
   ↓
2. handleAutoTradeTick(side, ltp)
   ↓
3. updateAutoMomentum(side, ltp)
   ├─ momentumCount[side]++
   ├─ getMomentumVelocity(side)
   └─ isNoTradeZone() check
   ↓
4. updateRegimeDetection(side)
   └─ Classify: TRENDING/VOLATILE/RANGING
   ↓
5. updateAdaptivePreset() [if auto_adaptive]
   └─ Switch strategy if needed
   ↓
6. autoCanEnter(side) - Entry Guardrails
   ├─ Cooldown check
   ├─ Max trades/min check
   ├─ Regime filter check
   ├─ Consecutive loss check
   └─ Max loss check
   ↓
7. placeAutoEntry(side, lots, reason)
   ├─ Paper: enterPaperPosition()
   └─ Live: placeOrderAtPrice()
   ↓
8. recordAutoEntry(side) + logAutoTrade()
   ↓
9. Tick Loop (every tick while position open)
   ↓
10. updateAutoTrailing(side, price)
    ├─ Stage 1: Check BE trigger
    ├─ Stage 2: Check profit lock
    ├─ Stage 3: Update trail
    ├─ Stage 4: Tight trail
    └─ Stage 5: Accel mode
    ↓
11. checkVirtualTPSL(side, price)
    ↓
12. executeVirtualTPSL(side, reason)
    ↓
13. closeAutoPosition(side, reason)
    ├─ Paper: closePaperPosition()
    └─ Live: closePosition()
    ↓
14. recordAutoExit() + logAutoTrade()
    ├─ Calculate P&L
    ├─ Update equity curve
    ├─ Track win/loss streaks
    └─ Update session stats
    ↓
15. updateAutoSummaryStats()
    └─ Render equity curve, stats panel
```

---

## Configuration

### Environment Variables

Key settings in `.env`:

```bash
# Broker Configuration
BROKER_API_KEY=your_key
BROKER_API_SECRET=your_secret

# WebSocket
WEBSOCKET_HOST=127.0.0.1
WEBSOCKET_PORT=8765

# Mock Replay
MOCK_REPLAY_WS_URL=ws://127.0.0.1:8770

# Database Paths
DATABASE_URL=db/openalgo.db
LOGS_DB=db/logs.db
AI_SCALPER_LOGS_DB=db/ai_scalper_logs.db
MANUAL_TRADE_LOGS_DB=db/manual_trade_logs.db
```

### Auto-Trading Configuration

**In `autoState` object:**

```javascript
// Core settings
momentumTicks: 7,              // Ticks needed for entry
cooldownMs: 30000,             // 30s between trades
maxTradesPerMin: 5,            // Rate limit
tpPoints: 8,                   // Take profit
slPoints: 4,                   // Stop loss
entryLots: 2,                  // Position size

// Risk management
perTradeMaxLoss: 5000,         // ₹5,000 max loss per trade
dailyMaxLoss: 20000,           // ₹20,000 max daily loss
maxConsecLosses: 5,            // Stop after 5 losses
consecutiveLossBreaker: 3,     // Boost threshold after 3 losses

// Filters
underlyingFilterEnabled: true,
candleConfirmEnabled: false,
relativeStrengthEnabled: false,
noTradeZoneEnabled: true,
regimeDetectionEnabled: true,

// Trailing
trailStaged: true,             // Enable multi-stage trailing
trailDistance: 2.0,            // Base trail distance
beDelayMs: 2000,               // Breakeven delay
winStreakWideTrail: 3,         // Win streak threshold

// Adaptive (if using auto_adaptive preset)
regimeTradingMode: 'all',      // 'all', 'trending_only', 'trending_volatile'
currentAdaptivePreset: null,   // Active sub-preset
adaptiveLastUpdate: 0          // Last switch timestamp
```

---

## API Reference

### WebSocket Messages

**Subscribe to Option:**
```json
{
  "action": "subscribe",
  "symbols": [
    {
      "symbol": "NIFTY10FEB2625700CE",
      "exchange": "NFO"
    }
  ]
}
```

**Tick Data Received:**
```json
{
  "symbol": "NIFTY10FEB2625700CE",
  "ltp": 103.50,
  "volume": 125000,
  "oi": 2500000,
  "bid": 103.45,
  "ask": 103.55,
  "timestamp": 1738948800000
}
```

### REST API

**Place Order:**
```http
POST /api/v1/placeorder
Content-Type: application/json

{
  "apikey": "YOUR_API_KEY",
  "symbol": "NIFTY10FEB2625700CE",
  "exchange": "NFO",
  "action": "BUY",
  "quantity": 75,
  "price": "0",
  "pricetype": "MARKET",
  "product": "MIS"
}
```

**Get Position:**
```http
GET /api/v1/positions
Headers:
  X-API-KEY: YOUR_API_KEY
```

**Log Auto-Trade:**
```http
POST /ai_scalper/logs
Content-Type: application/json

{
  "events": [
    {
      "type": "ENTRY",
      "mode": "PAPER",
      "side": "CE",
      "symbol": "NIFTY10FEB2625700CE",
      "qty": 75,
      "price": 100.50,
      ...
    }
  ]
}
```

---

## Performance Considerations

### Tick Processing Optimization

**Throttling:**
- `updateAutoStatsUI()` throttled to 250ms
- `updateRegimeDetection()` throttled to 250ms
- Direct calls from `recordAutoExit()` bypass throttle (immediate UI update)

**Data Structures:**
- Recent prices stored in arrays (max 200 ticks)
- Momentum count tracked per side
- Trade history limited to 200 trades in memory

### Database Performance

**Write Batching:**
- Logs queued in memory (max 400 events)
- Batch insert every 1.2s via `sendBeacon()` or `fetch()`
- Reduces DB write overhead

**Indexes:**
```sql
CREATE INDEX idx_logs_type ON ai_scalper_logs(type);
CREATE INDEX idx_logs_side ON ai_scalper_logs(side);
CREATE INDEX idx_logs_preset ON ai_scalper_logs(preset);
CREATE INDEX idx_logs_timestamp ON ai_scalper_logs(ts);
```

---

## Known Issues & Limitations

### Browser vs Broker P&L Discrepancy

**Issue:** Browser-calculated P&L differs from broker's reported P&L

**Causes:**
1. **Fill price slippage** - Market orders may fill at different price than LTP
2. **Brokerage charges** - ₹20/order + STT + exchange fees + GST
3. **Position-level averaging** - Broker uses position-level avg, browser uses per-trade tracking

**Estimated Charges:**
```
Per trade (round-trip):
- Brokerage: ₹40 (₹20 × 2 legs)
- STT: 0.0625% of sell value
- Exchange: 0.053% of turnover
- GST: 18% on brokerage + exchange
= Approx ₹50-80 per trade
```

**Mitigation:**
- Show "Net P&L (est.)" in UI with estimated charges deducted
- Use broker API P&L as source of truth for actual profitability
- Browser P&L useful for real-time tracking and strategy validation

### LIVE Mode Entry Price Tracking

**Issue:** `position.average_price` from broker API is position-level average, not per-trade

**Solution:**
```javascript
// NEVER use position.average_price for per-trade P&L in LIVE mode
// ALWAYS use autoState.liveEntry[side].avg

function getAutoEntryPrice(side) {
  if (autoState.paperMode) {
    return autoState.paperPositions[side]?.average_price || 0;
  } else {
    // LIVE mode: use liveEntry, NOT position.average_price
    return autoState.liveEntry[side]?.avg || 0;
  }
}
```

### Hot Path Performance

**Duplicate Function Names:**
- JS silently overrides duplicate function definitions
- Always check for existing functions before adding new ones
- The `updateAutoStatsUI` duplication bug caused P&L display to freeze

**State Reset Ordering:**
- ALWAYS log/capture state BEFORE resetting fields
- Example: Capture `positionHighPrice` before resetting to 0

---

## Future Enhancements

### Planned Features

1. **Machine Learning Integration**
   - Trade outcome prediction
   - Dynamic parameter optimization
   - Pattern recognition

2. **Advanced Order Types**
   - Iceberg orders
   - TWAP/VWAP execution
   - Bracket orders with OCO

3. **Multi-Instrument Support**
   - Spread trading (CE/PE, Calendar)
   - Equity + Futures hedging
   - Multi-strike strategies

4. **Enhanced Analytics**
   - Real-time trade distribution heatmaps
   - Time-of-day performance breakdown
   - Correlat with market events

5. **Cloud Deployment**
   - VPS hosting with 24/7 uptime
   - Cloud-based backtesting
   - Mobile app integration

---

## Troubleshooting

### Common Issues

**1. Auto-trading not triggering:**
- Check momentum threshold settings
- Verify cooldown not active
- Check regime filter (if enabled)
- Look for "last block reason" in UI

**2. Paper mode P&L not updating:**
- Check console for JS errors
- Verify tick data flowing (WebSocket connected)
- Ensure `updateAutoStatsUI()` not throttled excessively

**3. Mock replay not working:**
- Verify replay server running on port 8770
- Check `wsUrl` parameter passed to auto trading window
- Ensure Paper mode enabled (forced in mock mode)

**4. Manual trades not showing in analytics:**
- Verify trades completed (both ENTRY and EXIT)
- Check `manual_trade_logs.db` exists
- Ensure logs flushed (wait 2-3 seconds after trade)

---

## Conclusion

The OpenAlgo Scalping Framework represents a sophisticated, production-ready algorithmic trading system designed specifically for Indian options markets. Its combination of adaptive strategy selection, advanced risk management, comprehensive logging, and backtesting capabilities makes it a powerful tool for both manual and automated trading.

The system's modular architecture allows for easy extension and customization, while maintaining stability and performance under real-time market conditions. With proper configuration and risk management, it provides traders with a significant edge in fast-moving options markets.

**For Support:**
- Documentation: https://docs.openalgo.in
- GitHub: https://github.com/marketcalls/openalgo
- Discord: OpenAlgo Community

---

**Document Version:** 2.0  
**Last Updated:** February 7, 2026  
**Author:** Technical Documentation Team  
**Status:** Production
