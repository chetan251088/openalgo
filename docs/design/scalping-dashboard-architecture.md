# Scalping Dashboard - Architecture & File Reference

## Overview

The Unified Options Scalping Dashboard is a single React 19 page at `/scalping` that replaces 3 monolithic Jinja2 HTML files (~29,000 lines) with ~6,200 lines of clean TypeScript. It provides real-time option chain, 3 live charts (Index + CE + PE), manual hotkey trading, auto-trade engine with ghost signals, and options context integration.

## Stats

| Category | Files | Lines |
|----------|-------|-------|
| Pages | 2 | 62 |
| Components | 22 | 3,151 |
| Hooks | 10 | 1,226 |
| Stores | 3 | 436 |
| Lib (pure functions) | 5 | 1,112 |
| Types | 1 | 151 |
| API | 1 | 42 |
| **TOTAL** | **44** | **6,180** |

## Replaces

- `templates/scalping/scalping_interface.html` (8,278 lines) - Option chain hub
- `templates/scalping/chart_window.html` (7,212 lines) - Charts + orders
- `templates/scalping/auto_trading_window.html` (13,480 lines) - Auto-trade engine
- Total replaced: ~29,000 lines

---

## Route & Entry Points

| Entry | File | Details |
|-------|------|---------|
| React route | `frontend/src/App.tsx:236` | `<Route path="/scalping" element={<ScalpingDashboard />} />` under `FullWidthLayout` |
| Lazy import | `frontend/src/App.tsx:103` | `lazy(() => import('@/pages/scalping'))` |
| Flask route | `blueprints/react_app.py:486` | `/scalping` -> `serve_react_app()` (takes priority over old `scalping_bp`) |
| Nav link | `frontend/src/config/navigation.ts:43` | Zap icon, label "Scalping" |
| Tools card | `frontend/src/pages/Tools.tsx:7` | First tool card |

---

## File Structure

```
frontend/src/
  pages/scalping/
    ScalpingDashboard.tsx      [61 lines]  Top-level page, wires hooks + 3-panel layout
    index.ts                   [1 line]    Re-export

  components/scalping/
    index.ts                   [20 lines]  Barrel exports
    TopBar.tsx                 [150 lines] Index selector, expiry, Paper/Live, broker badge, P&L
    BottomBar.tsx              [92 lines]  Positions strip, session P&L, hotkey hints
    OptionChainPanel.tsx       [152 lines] Compact chain using useOptionChainLive
    ScalpingChainRow.tsx       [175 lines] React.memo row with flash LTP, B/S micro-buttons
    ChartPanel.tsx             [60 lines]  Container: Index(top) + CE(bottom-left) + PE(bottom-right)
    IndexChartView.tsx         [232 lines] Index chart with EMA9/21, Supertrend, VWAP
    OptionChartView.tsx        [213 lines] CE or PE chart with order overlays, active side glow
    ChartToolbar.tsx           [55 lines]  Indicator toggles for index chart
    FloatingTradeWidget.tsx    [281 lines] Draggable BUY/SELL/REV + lots + TP/SL inside chart
    ControlPanel.tsx           [51 lines]  Tabbed: Manual | Auto | Risk | Depth | Orders
    ManualTradeTab.tsx         [254 lines] Quick B/S, qty, TP/SL, order type, exit ladder
    AutoTradeTab.tsx           [147 lines] Enable/mode + status + context + ghost + presets + config
    AutoTradePresets.tsx       [49 lines]  5 preset cards (Sniper/Momentum/Balanced/Adaptive/Expiry)
    AutoTradeConfig.tsx        [154 lines] 11 accordion sections for ~60 config fields
    LLMAdvisorPanel.tsx        [162 lines] LLM advisor: status, tune config, apply recommendations
    GhostSignalOverlay.tsx     [129 lines] Non-intrusive auto opportunity alerts for manual mode
    OptionsContextPanel.tsx    [137 lines] Live PCR/GEX/IV/MaxPain display
    RiskPanel.tsx              [189 lines] Daily loss, profit protection, market clock zones
    DepthScoutTab.tsx          [191 lines] 5 or 20-level depth analytics
    OrdersTab.tsx              [180 lines] Broker + virtual + trigger orders with cancel
    HotkeyHelp.tsx             [78 lines]  Hotkey reference dialog (? key)

  hooks/
    useScalpingHotkeys.ts      [167 lines] Keyboard shortcuts, active-side-aware
    useAutoTradeEngine.ts      [197 lines] Auto-trade tick processing (execute or ghost mode)
    useVirtualTPSL.ts          [241 lines] Virtual TP/SL + trigger monitoring per tick
    useCandleBuilder.ts        [86 lines]  Build OHLC candles from WS ticks
    useTechnicalIndicators.ts  [59 lines]  Computes EMA/VWAP/Supertrend/RSI from candles
    useScalpingPositions.ts    [100 lines] Position tracking + live P&L
    useOptionsContext.ts       [122 lines] Polls OI/PCR/GEX/IV/MaxPain every 30-60s
    useTradeLogger.ts          [72 lines]  In-memory ring buffer + async backend flush
    useMarketClock.ts          [67 lines]  Time-of-day hot zones, expiry day detection
    useDepth20.ts              [115 lines] Dhan 20-level / others 5-level depth

  stores/
    scalpingStore.ts           [190 lines] UI state: underlying, strike, side, quantity, panels
    autoTradeStore.ts          [141 lines] Auto-trade config (persisted) + runtime (non-persisted)
    virtualOrderStore.ts       [105 lines] Virtual TP/SL + trigger orders (persisted)

  lib/
    autoTradeEngine.ts         [482 lines] Pure: entry/exit/trailing/regime/ghost/imbalance
    technicalIndicators.ts     [201 lines] Pure: EMA, VWAP, Supertrend, RSI, ADX
    scalpingPresets.ts         [217 lines] 5 presets + AutoTradeConfigFields interface (~60 fields)
    marketClock.ts             [137 lines] Hot zones, expiry calendar, zone sensitivity
    candleUtils.ts             [75 lines]  Candle building utilities

  types/
    scalping.ts                [151 lines] All scalping TypeScript interfaces

  api/
    ai-scalper.ts              [42 lines]  AI scalper analytics/tuning API (existing, reused)
```

---

## Architecture

### Layout (3-Panel Resizable)

```
+------+-------------------------------+---------------+
| Left |          Center               |     Right     |
| 18%  |           52%                 |      30%      |
+------+-------------------------------+---------------+
| Option|  Index Chart (40% height)    | Control Panel |
| Chain |  EMA9/21 + Supertrend + VWAP | [Manual|Auto| |
| Panel |----------------------------  |  Risk|Depth|  |
|       | CE Chart    | PE Chart       |  Orders]      |
| click |  (50%)      |  (50%)         |               |
| to    |  order      |  order         | Ghost signals |
| select|  overlays   |  overlays      | Options ctx   |
+------+-------------------------------+---------------+
| BottomBar: Positions | P&L | Hotkeys                 |
+-------------------------------------------------------+
```

- Layout: `react-resizable-panels` v4.5.1 via shadcn `Resizable*` wrappers
- Rendered inside `FullWidthLayout` which provides `h-screen flex flex-col overflow-hidden`
- All panels resizable via drag handles

### State Management (3 Zustand Stores)

1. **`scalpingStore`** - UI state (not persisted)
   - `underlying`: NIFTY | SENSEX | BANKNIFTY | FINNIFTY
   - `expiry`, `expiryWeek`, `selectedStrike`, `activeSide`
   - `selectedCESymbol`, `selectedPESymbol`
   - `quantity`, `lotSize`, `orderType`, `product`, `paperMode`
   - `controlTab`, `hotkeysEnabled`

2. **`autoTradeStore`** - Config persisted, runtime not
   - **Persisted**: `config` (~60 fields via `AutoTradeConfigFields`), `activePresetId`
   - **Runtime**: `enabled`, `mode`, `realizedPnl`, `regime`, `consecutiveLosses`, `tradesCount`, `trailStage`, `ghostSignals[]`, `optionsContext`, `lastTradeTime`, `tradesThisMinute`, `lastLossTime`

3. **`virtualOrderStore`** - Persisted
   - `virtualTPSL`: Map of virtual TP/SL orders
   - `triggerOrders`: Map of trigger orders
   - Actions: set/remove/update

### Data Flow

```
Broker WS -> ZMQ -> WebSocket 8765 -> MarketDataManager singleton
                                            |
                    +----------+------------+----------+
                    |          |            |          |
             useOptionChainLive  useCandleBuilder  useAutoTradeEngine
               (chain data)    (chart candles)    (entry/exit logic)
                    |          |            |          |
              OptionChainPanel  ChartPanel   AutoTradeTab
```

- Single WebSocket via existing `MarketDataManager` singleton
- `MarketDataManager` auto-discovers port from `/api/websocket/config`
- Charts use `lightweight-charts` v5.1.0 with ref-based updates (no React re-renders per tick)
- Auto-trade engine uses `useRef` callbacks for zero-React tick path

### Auto-Trade Engine

**Pure functions** in `lib/autoTradeEngine.ts`:
- `calculateMomentum()` - tick direction + velocity
- `detectRegime()` - TRENDING / VOLATILE / RANGING / UNKNOWN
- `isNoTradeZone()` - price range too narrow
- `calculateIndexBias()` - EMA/RSI/Supertrend directional score
- `optionsContextFilter()` - PCR/MaxPain/GEX entry filter
- `getContextAwareTrailDistance()` - IV-adjusted trailing distance
- `optionsEarlyExitCheck()` - IV spike, PCR flip, max pain exit
- `shouldEnterTrade()` - master entry decision (score-based)
- `calculateTrailingStop()` - 5-stage: INITIAL -> BREAKEVEN -> LOCK -> TRAIL -> TIGHT
- `checkImbalanceFilter()` - bid/ask depth ratio filter
- `generateGhostSignal()` - signal for manual mode display

**Hook** `useAutoTradeEngine.ts`:
- Execute mode: fires real orders via `tradingApi.placeOrder`
- Ghost mode: generates `GhostSignal` only (no execution)
- Safety checks: min gap, rate limit, cooldown, daily loss, consecutive losses

### 5-Stage Trailing SL

```
INITIAL (fixed SL) -> BREAKEVEN (SL at entry+buffer)
  -> LOCK (SL locks profit) -> TRAIL (SL follows price)
    -> TIGHT (tight trailing step)
```

Each stage triggered by profit thresholds configured in presets.

### Options Context Layer

`useOptionsContext` polls backend every 30-60s:
- `/api/v1/oi_tracker` -> PCR, OI changes
- `/api/v1/max_pain` -> max pain strike, distance
- `/api/v1/gex` -> gamma strikes, GEX flip zones, net GEX
- `/api/v1/iv_chart` -> ATM IV, IV percentile, skew
- `/api/v1/straddle_chart` -> straddle price (expected range)

3 integration points in auto-trade engine:
1. **Entry Filter**: PCR direction, max pain proximity, GEX walls
2. **Dynamic Trail**: Widen SL in high IV, tighten near gamma walls
3. **Early Exit**: IV spike, PCR flip, approaching max pain

### Auto-Trade Config (~60 Fields)

Organized in 11 accordion sections in `AutoTradeConfig.tsx`:
1. Entry Conditions (momentum count/velocity, min score, max spread)
2. Trailing SL 5-Stage (8 params)
3. Breakeven (trigger, buffer)
4. Risk (daily loss, per-trade loss, max trades/day, max trades/min, min gap, cooldown, cooling off, position size)
5. Imbalance Filter (enabled, threshold)
6. Regime Detection (period, ranging threshold)
7. Index Bias (enabled, weight)
8. Options Context (PCR thresholds, max pain, GEX wall, IV spike)
9. Time-of-Day (hot zones, sensitivity multiplier)
10. No-Trade Zone (enabled, range, period)
11. Re-Entry (enabled, delay, max per side)
12. Telegram Alerts (entry, exit, tune)

5 presets: Sniper Quality, Momentum Scalper, Balanced Trader, Auto-Adaptive, Expiry Day

### Keyboard Hotkeys

| Key | Action |
|-----|--------|
| B | Buy on active side |
| S | Sell on active side |
| C | Close position on active side |
| X | Close ALL positions |
| R | One-click reversal (close + opposite) |
| Tab | Toggle active side (CE <-> PE) |
| ArrowUp/Down | Move strike selection |
| W | Toggle floating trade widget |
| 1/2/3 | Quantity presets |
| ? | Hotkey help overlay |
| Esc | Deselect / close dialog |

### Existing Reused Assets

| Asset | Location | Used For |
|-------|----------|----------|
| `MarketDataManager` | `lib/MarketDataManager.ts` | Single WebSocket for all data |
| `useOptionChainLive` | `hooks/useOptionChainLive.ts` | Option chain real-time data |
| `useMarketData` | `hooks/useMarketData.ts` | Chart ticks, depth data |
| `useLivePrice` | `hooks/useLivePrice.ts` | Position P&L |
| `tradingApi` | `api/trading.ts` | All order/position operations |
| `optionChainApi` | `api/option-chain.ts` | Chain + expiry fetching |
| `ai-scalper API` | `api/ai-scalper.ts` | LLM advisor analytics/tuning |
| `useSocket` | `hooks/useSocket.ts` | Order event notifications |
| `useAuthStore` | `stores/authStore.ts` | API key, auth state, broker name |
| `Resizable*` | `components/ui/resizable.tsx` | Panel layout |
| `lightweight-charts` | package.json v5.1.0 | 3 chart instances |
| All shadcn/ui | `components/ui/*` | Tabs, Card, Badge, Dialog, etc. |

### Backend APIs (All Existing)

| API | Endpoint | Purpose |
|-----|----------|---------|
| Option Chain | `/api/v1/optionchain` | Chain data |
| Place Order | `/api/v1/placeorder` | Execute trades |
| Close Position | `/api/v1/closeposition` | Close positions |
| Position Book | `/api/v1/positionbook` | Current positions |
| Market Depth | `/api/v1/depth` | 5-level depth |
| WebSocket Config | `/api/websocket/config` | WS port discovery |
| OI Tracker | `/api/v1/oi_tracker` | PCR, OI changes |
| Max Pain | `/api/v1/max_pain` | Max pain strike |
| GEX | `/api/v1/gex` | Gamma exposure |
| IV Chart | `/api/v1/iv_chart` | Implied volatility |
| Straddle | `/api/v1/straddle_chart` | Straddle prices |
| AI Advisor | `/ai_scalper/advisor_stub` | LLM parameter tuning |
| Model Tuning | `/ai_scalper/model/*` | Status, run, apply, recommendations |
| Telegram Notify | `/api/v1/telegram/notify` | Custom alerts |

---

## Known Issues / TODO

1. **Panel layout bug**: Left (chain) and right (control) panels may appear stuck/invisible - resizable panel sizing needs investigation
2. **Chart overlays**: ChartOrderOverlay, SpeedTape, MomentumMeter, ScalpPnLBanner components planned but not yet created
3. **Floating trade widget**: Created but needs real-time position data integration
4. **Export trade log**: CSV/JSON export from useTradeLogger not yet wired to UI
5. **Equity curve mini-chart**: Planned for BottomBar
6. **Panel size persistence**: Save/restore panel sizes to localStorage
7. **Responsive mode**: Collapse chain on narrow screens, tab mode for charts

---

## Multi-Broker Architecture

Each broker runs as a separate Flask instance:

| Instance | Flask Port | WebSocket Port | .env file |
|----------|-----------|----------------|-----------|
| Kotak | 5000 | 8765 | `.env.kotak` |
| Dhan | 5001 | 8766 | `.env.dhan` |
| Zerodha | 5002 | 8767 | `.env.zerodha` |

React frontend is **fully broker-agnostic**: reads WebSocket URL from `/api/websocket/config`, connects to the right port automatically. Broker name from auth session for display.
