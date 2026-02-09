# Unified Options Scalping Dashboard - Implementation Plan

## Context

The current options scalping system consists of 3 monolithic Jinja2 HTML files totaling ~29,000 lines:
- `scalping_interface.html` (8,278 lines) - Option chain hub with hotkeys
- `chart_window.html` (7,212 lines) - TradingView charts + order management
- `auto_trading_window.html` (13,480 lines) - Auto-trade engine with 100+ config params

**Problems:** 3 separate browser popups communicating via `window.opener.state`, ~12,000 lines of duplicated code (WebSocket, depth, indicators, P&L), each window has its own WebSocket connection, virtual TP/SL lost on tab close, no React integration.

**Goal:** Consolidate into a single React 19 page at `/scalping` - fast, robust, sleek. Target ~9,500 lines of clean TypeScript replacing 29,000 lines.

---

## Core Design Principles

1. **Speed first** - Zero lag. Ref-based tick processing, no React re-renders per tick, direct chart API updates
2. **Broker agnostic** - Works with Dhan (port 5001), Kotak (port 5000), Zerodha (port 5002). WebSocket URL from `/api/websocket/config`, no hardcoded broker logic in React. Broker badge shows which instance you're on.
3. **Manual-first, auto-pilot ready** - Seamless switch between manual hotkey trading and auto-pilot mode. Ghost signals show auto opportunities even in manual mode.
4. **Index options focused** - NIFTY, SENSEX, BANKNIFTY, FINNIFTY. Current week + next week only. Expiry day special mode.

---

## Multi-Broker Instance Architecture

Each broker runs as a separate Flask app with isolated ports and databases:

| Instance | Flask | WebSocket | ZMQ | .env file | Cookie |
|----------|-------|-----------|-----|-----------|--------|
| Kotak | 5000 | 8765 | 5555 | `.env.kotak` | `session` |
| Dhan | 5001 | 8766 | 5556 | `.env.dhan` | `session_dhan` |
| Zerodha | 5002 | 8767 | 5557 | `.env.zerodha` | `session_zerodha` |

The React frontend is **fully broker-agnostic**: it reads the WebSocket URL from `/api/websocket/config` on page load and connects to the right port automatically. The broker name is available from the auth session for display purposes.

---

## Architecture Decisions

- **Route:** `/scalping` under `<FullWidthLayout>` (full screen, no navbar chrome)
- **State:** 3 Zustand stores: `scalpingStore` (UI), `autoTradeStore` (persisted config), `virtualOrderStore` (persisted orders)
- **Data:** Reuse existing `MarketDataManager` singleton (single WebSocket, auto-discovers port)
- **API:** Reuse existing `tradingApi` + `optionChainApi` + `ai-scalper` APIs
- **Charts:** `lightweight-charts` v5.1.0 (already in deps) - 3 charts: Index + CE + PE
- **Layout:** `react-resizable-panels` via shadcn/ui `Resizable*` components
- **Auto-trade engine:** Pure TypeScript functions + React hook wiring
- **Trade logging:** In-memory ring buffer for speed, async flush to backend for LLM analysis
- **Paper/Live:** Simple toggle switch, same features in both modes

---

## Layout Design

```
+------------------------------------------------------------------------+
| TopBar: [NIFTY|SENSEX|BNIFTY|FINNIFTY] [CurWk|NxtWk] | Paper/Live | Broker: Dhan | P&L: +2,450 |
+----------+------------------------------------------+------------------+
|          |                                          |                  |
| Option   |  INDEX Chart (NIFTY/SENSEX spot)         |  Control Panel   |
| Chain    |  EMA9/21 + Supertrend + VWAP + RSI       |  [Manual|Auto]   |
| Panel    |  ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈     |  [Risk|Depth]    |
|          |  CE Chart (left)  |  PE Chart (right)    |  [Orders]        |
| [strikes |  order overlays   |  order overlays      |                  |
|  with    |  TP/SL lines      |  TP/SL lines         |  Ghost signals   |
|  1-click |  trigger lines    |  trigger lines       |  Options context |
|  B/S]    |                   |                      |  Quick B/S       |
|          |  * click chart to set active side *       |  TP/SL config    |
+----------+------------------------------------------+------------------+
| BottomBar: Positions strip | P&L | Market clock | Hotkeys: B=Buy S=Sell R=Rev C=Close X=All W=Widget |
+------------------------------------------------------------------------+
```

**3 charts** in center panel:
- **Top**: Index chart (full width) - reading direction, full indicator suite
- **Bottom**: CE chart (left half) + PE chart (right half) - option execution, order overlays
- Clicking on CE/PE chart sets that side as **active side** for hotkeys
- All panels resizable + collapsible

**Floating Chart Trade Widget** (inside each CE/PE chart, draggable):
```
┌─────────────────────────────────┐
│  CE 24000  LTP: 245.50  ▲▼     │  <- Symbol + live LTP + price flash
│  [BUY] [SELL] [REV]  [-] 2 [+] │  <- B/S + Reversal + lot stepper
│  TP: 30  SL: 15  [×Close]      │  <- Quick TP/SL config + close position
└─────────────────────────────────┘
```
- Semi-transparent, draggable anywhere within chart bounds
- REV = one-click reversal (close + enter opposite)
- TP/SL points editable inline, applied to next trade
- [×Close] closes active side position instantly

**In-Chart Scalper Overlays** (per CE/PE chart):
- **Breakeven line** - dotted line at entry price, distance-in-points label next to LTP
- **Scalp P&L banner** - top-right corner: "+120 (4.8 pts)" green/red, flashes on changes
- **Spread badge** - bid-ask spread indicator (green <2pts, yellow 2-5pts, red >5pts)
- **Momentum meter** - small gauge bar showing tick velocity + direction (green=bullish, red=bearish)
- **Speed tape** - thin strip at chart bottom showing last N trades with size + direction coloring
- **Last trade markers** - arrow markers on chart where your entries/exits happened
- **Quick Exit Ladder** - 3 partial exit lines (e.g., 50% at +20, 30% at +35, trail 20%)

---

## File Structure (~39 files)

```
frontend/src/
  pages/scalping/
    ScalpingDashboard.tsx          # Top-level page component
    index.ts                       # Re-export

  components/scalping/
    TopBar.tsx                     # Index toggle, expiry, Paper/Live, broker badge, P&L
    BottomBar.tsx                  # Positions + P&L + market clock + hotkey hints

    # Left panel
    OptionChainPanel.tsx           # Compact chain with 1-click trading
    ScalpingChainRow.tsx           # Memoized row (B/S buttons + hotkey highlight)

    # Center panel - 3 charts
    ChartPanel.tsx                 # Index + CE + PE chart container
    IndexChartView.tsx             # Index chart with full indicator suite
    OptionChartView.tsx            # CE or PE chart with order overlays
    ChartOrderOverlay.tsx          # TP/SL lines, trigger lines, exit ladder lines (draggable)
    ChartToolbar.tsx               # Indicator toggles, timeframe, follow mode
    FloatingTradeWidget.tsx        # Draggable BUY/SELL/REV + lots + TP/SL inside chart
    SpeedTape.tsx                  # Time & sales strip at bottom of option chart
    MomentumMeter.tsx              # Real-time tick velocity gauge inside chart
    ScalpPnLBanner.tsx             # Position P&L overlay on chart (top-right)

    # Right panel
    ControlPanel.tsx               # Tabbed container
    ManualTradeTab.tsx             # Quick B/S, qty, TP/SL point config, limit orders
    AutoTradeTab.tsx               # Auto-trade on/off + status + ghost signals
    AutoTradePresets.tsx           # Sniper/Balanced/Scalper/Adaptive + Expiry Day
    AutoTradeConfig.tsx            # Grouped config accordion
    GhostSignalOverlay.tsx         # Non-intrusive auto opportunity alerts in manual mode
    OptionsContextPanel.tsx        # Live PCR/GEX/IV/MaxPain display
    RiskPanel.tsx                  # Daily loss, profit protection, market clock zones
    DepthScoutTab.tsx              # Market depth analytics (5 or 20 level)
    OrdersTab.tsx                  # Open + virtual + trigger orders with cancel

  hooks/
    useScalpingHotkeys.ts          # Keyboard shortcuts, active-side-aware
    useAutoTradeEngine.ts          # Auto-trade tick processing (execute or shadow/ghost mode)
    useTechnicalIndicators.ts      # EMA, Supertrend, VWAP, RSI, ADX
    useVirtualTPSL.ts              # Virtual TP/SL + trigger line monitoring per tick
    useCandleBuilder.ts            # Build OHLC candles from WS ticks
    useScalpingPositions.ts        # Position tracking + live P&L
    useOptionsContext.ts           # Polls OI/PCR/GEX/IV/MaxPain every 30-60s
    useTradeLogger.ts              # In-memory ring buffer + async flush for LLM analysis
    useMarketClock.ts              # Time-of-day hot zones, expiry day detection
    useDepth20.ts                  # Dhan 20-level depth subscription (broker-aware)

  stores/
    scalpingStore.ts               # UI state (symbol, strike, active side, panels)
    autoTradeStore.ts              # Auto-trade config (~100 fields, persisted) + runtime
    virtualOrderStore.ts           # Virtual TP/SL + trigger orders (persisted)

  types/
    scalping.ts                    # All TypeScript interfaces

  lib/
    autoTradeEngine.ts             # Pure functions: entry/exit/trailing/regime/ghost
    technicalIndicators.ts         # Pure: EMA, VWAP, Supertrend, RSI, ADX
    scalpingPresets.ts             # 5 preset configs (incl. Expiry Day)
    candleUtils.ts                 # Candle building utilities
    marketClock.ts                 # Hot zone definitions, expiry calendar
```

---

## Reuse Map (Existing Assets)

| Asset | File | Reused For |
|-------|------|-----------|
| `useOptionChainLive` | `hooks/useOptionChainLive.ts` | Option chain real-time data |
| `MarketDataManager` | `lib/MarketDataManager.ts` | Single WebSocket for all data (auto-discovers port) |
| `useMarketData` | `hooks/useMarketData.ts` | Chart ticks, depth data |
| `useLivePrice` | `hooks/useLivePrice.ts` | Position P&L |
| `tradingApi` | `api/trading.ts` | All order/position/fund operations |
| `optionChainApi` | `api/option-chain.ts` | Chain + expiry fetching |
| `ai-scalper API` | `api/ai-scalper.ts` | Auto-trade analytics/tuning |
| Options backend APIs | `services/oi_tracker_service.py`, `gex_service.py`, `iv_chart_service.py` etc. | Options Context Layer |
| Dhan 20-level depth | `broker/dhan/streaming/dhan_websocket.py` (SUBSCRIBE_20_DEPTH) | Enhanced depth for Dhan |
| `useSocket` | `hooks/useSocket.ts` | Order event notifications |
| `useAuthStore` | `stores/authStore.ts` | API key, auth state, broker name |
| `PlaceOrderDialog` | `components/trading/PlaceOrderDialog.tsx` | Order confirmation dialog |
| `Resizable*` | `components/ui/resizable.tsx` | Panel layout |
| `lightweight-charts` | Already in package.json v5.1.0 | Chart rendering (3 instances) |
| All shadcn/ui | `components/ui/*` | Tabs, Card, Badge, Dialog, etc. |

---

## Phase 1: Foundation Shell + Option Chain
**~1,300 lines | Deliverable: Working page with real-time option chain + index selection**

### Files to create:
1. **`types/scalping.ts`** - Core interfaces: `ScalpingSymbol`, `StrikeData`, `ChainRow`, `OrderAction`, `ActiveSide`, `Underlying`
2. **`stores/scalpingStore.ts`** - Zustand store:
   - `underlying`: 'NIFTY' | 'SENSEX' | 'BANKNIFTY' | 'FINNIFTY' (default NIFTY)
   - `expiry`: string (auto-select current week)
   - `expiryWeek`: 'current' | 'next' (toggle)
   - `optionExchange`: 'NFO' | 'BFO' (auto from underlying)
   - `indexExchange`: 'NSE_INDEX' | 'BSE_INDEX' (auto from underlying)
   - `selectedStrike`, `selectedSide`, `selectedSymbol`
   - `activeSide`: 'CE' | 'PE' (set by clicking CE/PE chart)
   - `quantity`, `lotSize`, `orderType`
   - `chartMode`, `controlTab`, `hotkeysEnabled`
   - `paperMode`: boolean (Paper/Live toggle)
3. **`pages/scalping/ScalpingDashboard.tsx`** - Page shell with nested `ResizablePanelGroup`:
   - Horizontal: Chain | Center | Controls
   - Center vertical: Index chart area (top) | CE+PE charts (bottom)
   - Bottom bar outside panels
4. **`pages/scalping/index.ts`** - Re-export
5. **`components/scalping/TopBar.tsx`**:
   - 4 underlying buttons: NIFTY | SENSEX | BANKNIFTY | FINNIFTY (toggle style, highlighted active)
   - Expiry: Current Week | Next Week toggle
   - Paper/Live switch (simple toggle, Paper=blue tint, Live=green dot)
   - Broker badge (reads broker name from auth session)
   - Connection status (green/red dot)
   - Session P&L display
6. **`components/scalping/OptionChainPanel.tsx`** - Compact chain using `useOptionChainLive` hook. Columns: CE LTP | CE B/S | Strike | PE B/S | PE LTP. Click strike to select for charting.
7. **`components/scalping/ScalpingChainRow.tsx`** - `React.memo` row with flash-on-change LTP, highlight when selected, BUY/SELL micro-buttons
8. **`components/scalping/BottomBar.tsx`** - Placeholder with hotkey hint strip
9. **`components/scalping/index.ts`** - Barrel exports

### Files to modify:
- **`App.tsx`** (line 232): Add `<Route path="/scalping" element={<ScalpingDashboard />} />` under FullWidthLayout
- **`App.tsx`** (after line 63): Add lazy import `const ScalpingDashboard = lazy(() => import('@/pages/scalping/ScalpingDashboard'))`

### Key patterns:
- Underlying mapping: `NIFTY -> {index: 'NSE_INDEX', options: 'NFO'}`, `SENSEX -> {index: 'BSE_INDEX', options: 'BFO'}`, `BANKNIFTY -> {index: 'NSE_INDEX', options: 'NFO'}`, `FINNIFTY -> {index: 'NSE_INDEX', options: 'NFO'}`
- Use `useOptionChainLive(underlying, expiry, exchange, strikeCount, 'LTP')` for real-time chain
- Current/Next week auto-detection: fetch expiries, pick first two, default to first
- ResizablePanelGroup: `defaultSize={[18, 57, 25]}` for chain|chart|controls

### Test:
Navigate to `/scalping` -> NIFTY selected by default -> option chain loads with current week expiry -> real-time LTP updates -> click SENSEX switches chain -> broker badge shows correctly -> Paper/Live toggle works visually.

---

## Phase 2: Chart Integration (3 Charts) + In-Chart Overlays
**~2,200 lines | Deliverable: Live Index + CE + PE charts with indicators + scalper overlays**

### Files to create:
1. **`lib/candleUtils.ts`** - Pure functions: `buildCandleFromTick()`, `aggregateCandles()`, `mergeTickIntoCandle()`
2. **`lib/technicalIndicators.ts`** - Pure functions: `calculateEMA(data, period)`, `calculateSupertrend(candles, period, multiplier)`, `calculateVWAP(candles)`, `calculateRSI(closes, period)`, `calculateADX(candles, period)`
3. **`hooks/useCandleBuilder.ts`** - Builds 1-second OHLC candles from WebSocket ticks. Uses `useMarketData` subscription. Maintains candle array in `useRef`. Calls `requestAnimationFrame` for chart updates.
4. **`hooks/useTechnicalIndicators.ts`** - Computes indicators from candle data. Memoized with `useMemo`. Returns `{ ema9, ema21, supertrend, vwap, rsi }` series data.
5. **`components/scalping/ChartPanel.tsx`** - Container managing 3 chart instances:
   - Top: `IndexChartView` (full width, ~40% height) - subscribes to underlying index symbol
   - Bottom-left: `OptionChartView` for CE (~50% width)
   - Bottom-right: `OptionChartView` for PE (~50% width)
   - Clicking CE/PE chart sets `scalpingStore.activeSide` -> hotkeys target that side
   - Active chart has subtle highlighted border
6. **`components/scalping/IndexChartView.tsx`** - Index-specific chart:
   - Full indicator suite: EMA9/21, Supertrend, VWAP, RSI (as separate pane or overlay)
   - Subscribes to index symbol (e.g., NSE:NIFTY-INDEX)
   - No order overlays (read-only, for direction)
   - Larger candles, clear for quick reading
7. **`components/scalping/OptionChartView.tsx`** - Option-specific chart:
   - Candlestick + basic indicators (optional EMA)
   - Order overlay zone: TP/SL lines, trigger lines, position entry line
   - Click handler for placing limit/trigger orders at clicked price
   - Active side indicator (subtle glow when this chart is hotkey target)
8. **`components/scalping/ChartToolbar.tsx`** - Indicator toggles for index chart. Follow mode. Chart layout controls.
9. **`components/scalping/ChartOrderOverlay.tsx`** - Manages price lines on option charts:
   - Position entry line (solid, color-coded buy/sell)
   - **Breakeven line** (dotted at entry, shows point distance from LTP, green when profitable, red when not)
   - Virtual TP line (green dashed, draggable)
   - Virtual SL line (red dashed, draggable)
   - Trigger line (yellow dashed, draggable, with direction indicator above/below)
   - **Quick Exit Ladder lines** (up to 3 partial exit levels, green gradient light->dark, draggable):
     - Line 1: Exit X% at +N pts (e.g., 50% at +20)
     - Line 2: Exit Y% at +M pts (e.g., 30% at +35)
     - Line 3: Trail remaining Z%
   - **Last trade markers** - arrow up/down on chart at entry/exit candles
   - All lines show price label + point distance from entry
10. **`components/scalping/SpeedTape.tsx`** - Thin horizontal strip at bottom of each option chart:
    - Shows last ~50 trades (time & sales) as colored blocks
    - Green = buy-side trade, Red = sell-side trade
    - Block width proportional to trade size
    - Scrolls right-to-left as new trades arrive
    - Gives instant visual read on buying vs selling pressure
11. **`components/scalping/MomentumMeter.tsx`** - Small horizontal gauge bar inside chart (top-left corner):
    - Shows real-time tick velocity + direction
    - Green bar grows right with bullish momentum, red bar grows left with bearish
    - Calculated from last N ticks directional bias + velocity
    - Helps confirm before hitting Buy/Sell
    - Subtle, doesn't obstruct chart candles
12. **`components/scalping/ScalpPnLBanner.tsx`** - Position P&L overlay (top-right corner of each option chart):
    - Shows: "+120 (4.8 pts)" or "-55 (-2.2 pts)"
    - Green background when profitable, red when losing
    - Pulses/flashes on significant P&L changes (>5 pts move)
    - Shows quantity and entry price in smaller text below
    - Only visible when position is open on that side
13. **Spread badge** - Integrated into `OptionChartView`: small badge showing live bid-ask spread:
    - Green: < 2 pts (good fill expected)
    - Yellow: 2-5 pts (caution)
    - Red: > 5 pts (wide spread, bad fills)
    - Positioned next to LTP display

### Key patterns:
- Chart instances managed via `useRef<IChartApi>` (not state) for performance
- 3 separate `useCandleBuilder` instances (index, CE, PE)
- Index chart: `useMarketData` subscribes to underlying symbol (NIFTY, SENSEX etc.)
- CE/PE charts: `useMarketData` subscribes to option symbols from selected strike
- Active side: clicking CE chart -> `scalpingStore.setActiveSide('CE')`, clicking PE chart -> `setActiveSide('PE')`
- All chart updates go directly to lightweight-charts API via refs (zero React re-renders per tick)
- SpeedTape, MomentumMeter, ScalpPnLBanner render as absolute-positioned overlays on the chart container (not lightweight-charts primitives) - React components updated via `requestAnimationFrame` batching
- Breakeven line + exit ladder lines managed via lightweight-charts `PriceLine` API

### Test:
3 charts visible: Index on top with EMA/Supertrend/VWAP, CE bottom-left, PE bottom-right. Click CE chart -> border highlights -> hotkeys now target CE. Click PE chart -> switches. All candles building live from ticks. Speed tape shows trade flow. Momentum meter shows direction. Spread badge shows green/yellow/red. When position open: breakeven line visible, P&L banner shows live +/- points.

---

## Phase 3: Manual Trading + Hotkeys + Floating Widget + Order Types
**~1,500 lines | Deliverable: Full manual trading with floating chart controls**

### Files to create:
1. **`stores/virtualOrderStore.ts`** - Zustand with `persist`:
   - `virtualTPSL`: Map<string, {symbol, entryPrice, qty, action, tpPrice, slPrice, tpPoints, slPoints}>
   - `triggerOrders`: Map<string, {symbol, exchange, action, triggerPrice, direction:'above'|'below', tpPoints, slPoints, qty}>
   - `pendingLimitOrders`: Map<string, {symbol, exchange, action, limitPrice, tpPoints, slPoints, qty}>
   - Actions: set/remove/update for each type
2. **`hooks/useScalpingHotkeys.ts`** - Keyboard handler **aware of active side**:
   - B = Buy on **active side** (whichever CE/PE chart was last clicked)
   - S = Sell on **active side**
   - ArrowUp/Down = move strike selection in chain
   - C = close position on active side
   - X = close ALL positions
   - R = **one-click reversal** (close + enter opposite on active side)
   - W = toggle floating trade widget visibility
   - Tab = toggle active side (CE<->PE)
   - Escape = deselect / cancel
   - 1-3 = quantity presets
   - Skips when INPUT/TEXTAREA/SELECT focused
3. **`hooks/useVirtualTPSL.ts`** - Per-tick monitoring for ALL virtual order types:
   - Virtual TP/SL: when LTP crosses TP or SL -> fire MARKET close -> remove from store
   - Trigger orders: when LTP crosses trigger price in specified direction -> fire MARKET entry + auto-set TP/SL -> move to virtualTPSL
   - Pending limits: when LTP touches limit price -> fire MARKET order (simulating limit fill) + activate TP/SL
   - **Quick Exit Ladder**: monitors 3 partial exit levels per position. When LTP hits level 1 -> close X% qty. Level 2 -> close Y% qty. Level 3 -> activate trailing SL on remainder.
   - TP/SL points are user-configurable (not hardcoded)
4. **`hooks/useScalpingPositions.ts`** - Fetches positions via `tradingApi.getPositions`, filters to option positions, enriches with `useLivePrice` for real-time P&L. Auto-refreshes on Socket.IO order events.
5. **`hooks/useTradeLogger.ts`** - In-memory ring buffer (last 500 trades):
   - Logs: timestamp, symbol, side, action, price, qty, trigger (manual/auto/ghost), entryConditions snapshot, exitReason, P&L, trailStages, duration
   - Async flush to backend every 30s (non-blocking, does not impact trading speed)
   - Export as JSON for LLM analysis
6. **`components/scalping/ControlPanel.tsx`** - Tabbed panel: Manual | Auto | Risk | Depth | Orders
7. **`components/scalping/ManualTradeTab.tsx`**:
   - Quick BUY/SELL buttons for active side
   - Quantity input (lots, with +/- steppers)
   - **TP/SL point config**: Input fields for TP points and SL points (e.g., TP: 30, SL: 15). These are used whenever an order is placed.
   - Order type: MARKET (instant) | LIMIT (click chart to set price) | TRIGGER (click chart to set trigger above/below)
   - Product: MIS/NRML toggle
   - "Close Position" button for active side
   - "Close All" button
   - **Exit Ladder config**: 3 rows of [Exit %] at [+N pts], with the 3rd row being "Trail remaining"
8. **`components/scalping/FloatingTradeWidget.tsx`** - Draggable widget rendered inside each CE/PE chart:
   - **Position**: absolute-positioned over chart container, draggable via mouse/touch within chart bounds
   - **Semi-transparent** background (backdrop-blur), doesn't fully obscure candles behind it
   - **Contents**:
     - Symbol + live LTP (flashes green/red on tick direction)
     - **[BUY]** [SELL]** buttons - instant MARKET order on active chart's symbol
     - **[REV]** button - one-click reversal: closes current position AND enters opposite direction in single action. Critical for momentum reversals (saves 2 clicks + thinking time)
     - **Lot stepper**: [-] N [+] with current lot count, tap +/- to adjust
     - **TP/SL inline**: editable point values (e.g., TP: 30 | SL: 15), applied to next trade
     - **[×Close]** - close position on this chart's side instantly
   - **Behavior**:
     - Each CE/PE chart has its own widget instance
     - Widget remembers drag position per chart (stored in scalpingStore)
     - Auto-hides when no strike selected (or collapsed to minimal dot)
     - Clicking anywhere on the widget also sets that chart as active side
     - Keyboard shortcut (W) toggles widget visibility
9. **`components/scalping/OrdersTab.tsx`** - Shows:
   - Real broker orders (from `tradingApi.getOrders`)
   - Virtual TP/SL orders (from `virtualOrderStore`)
   - Trigger orders (from `virtualOrderStore`)
   - Pending limit orders (from `virtualOrderStore`)
   - Cancel individual or all per category
9. **Update `BottomBar.tsx`** - Positions strip: symbol | qty | avg | LTP | P&L (green/red). Total session P&L. Hotkey hints showing active side.

### Key patterns:
- **Active side hotkeys**: `useScalpingHotkeys` reads `scalpingStore.activeSide` to determine which symbol to trade
- Orders placed via `tradingApi.placeOrder({apikey, strategy:'Scalping', symbol, exchange, action, quantity, pricetype:'MARKET', product:'MIS'})`
- **Floating widget**: Each CE/PE chart renders a `FloatingTradeWidget` as an absolute-positioned child. Uses `onMouseDown` for drag, `transform: translate(x, y)` for position. Widget state (position, visibility) stored in `scalpingStore`.
- **One-click reversal**: REV button calls `closePosition()` then immediately `placeOrder()` with opposite action. Both fire in sequence (close must complete before entry). Shows loading spinner during execution.
- **Chart click -> order**: When in LIMIT mode, clicking CE/PE chart at a price level places a limit entry at that price with pre-configured TP/SL points. When in TRIGGER mode, clicking sets a trigger line.
- **Draggable TP/SL + Exit Ladder**: After placing, user can drag TP/SL lines AND exit ladder lines on chart. New point values update `virtualOrderStore`. Exit ladder monitors partial qty exits at each level.
- Virtual TP/SL fires MARKET close orders when triggered (because real TP/SL orders have margin issues)
- Trade logger runs entirely in-memory with async backend flush (zero impact on execution speed)
- **Last trade markers**: On each trade execution, add a marker to the chart via `series.setMarkers()` - green up-arrow for buy, red down-arrow for sell. Persisted in `useRef` array.

### Test:
Floating widget visible on CE chart -> click BUY on widget -> order fires -> position appears in bottom bar + P&L banner on chart shows live +/- -> breakeven line appears at entry -> drag TP line to adjust -> exit ladder lines show 3 levels -> price hits first ladder -> 50% closed -> price hits TP -> rest closed. Click REV on PE chart widget -> closes PE position + enters opposite direction. Drag widget to preferred corner -> position persists. Press W -> widget toggles visibility. Last trade arrows visible on chart at entry/exit points.

---

## Phase 4: Depth Scout + Risk + Market Clock
**~1,000 lines | Deliverable: Market depth analytics, risk controls, time awareness**

### Files to create:
1. **`hooks/useDepth20.ts`** - Broker-aware depth subscription:
   - Detects broker from auth session
   - Dhan: subscribes via `SUBSCRIBE_20_DEPTH` mode through `MarketDataManager` -> 20-level bid/ask
   - Zerodha/Kotak: falls back to 5-level depth via `tradingApi.getDepth` polling (2s)
   - Returns normalized `DepthData` with `levels: 5 | 20` indicator
2. **`hooks/useMarketClock.ts`** - Time-of-day awareness:
   - Tracks current time in IST
   - `isHotZone`: true during configurable hot periods (09:15-09:30, 12:30-13:00, 13:55-14:55, 15:00-15:30)
   - `isExpiryDay`: auto-detects from selected expiry vs today
   - `expiryHotZone`: special zones for expiry days (13:30-14:00 surprise zone, 14:00-15:00 panic zone)
   - `currentZone`: returns label for display ("Opening Momentum", "Afternoon Action", "Quiet", etc.)
   - Feeds into auto-trade engine for sensitivity adjustment
3. **`lib/marketClock.ts`** - Pure functions:
   - `getHotZones(isExpiryDay)` -> zone definitions
   - `getCurrentZone(time, isExpiryDay)` -> zone label + sensitivity multiplier
   - `isExpiryDate(expiry, today)` -> boolean
   - Nifty expiry: Thursday, Sensex expiry: Friday (configurable)
4. **`components/scalping/DepthScoutTab.tsx`**:
   - Shows depth for active side (CE or PE)
   - 5-level (all brokers) or 20-level (Dhan) horizontal bars
   - Bid bars (green, left) / Ask bars (red, right)
   - **Analytics** (especially powerful with 20-level):
     - Total bid vs ask ratio (directional bias)
     - Largest wall detection (support/resistance level)
     - Spread tracking (real-time bid-ask spread)
     - Depth imbalance score (sum of 20 bid levels vs 20 ask levels)
   - Level indicator: "5-Level" or "20-Level (Dhan)" badge
5. **`components/scalping/RiskPanel.tsx`**:
   - Daily P&L limit (auto-disable trading when hit)
   - Per-trade max loss (points)
   - Profit protection tiers (configurable: lock X% after reaching Y profit)
   - Cooling-off period after N consecutive losses
   - Session stats: trades today, win rate, avg P&L, best/worst trade
   - **Market Clock display**: Current zone, countdown to next hot zone, expiry day indicator

### Key patterns:
- Dhan 20-level: Extend `MarketDataManager` to handle `depth20` subscription mode. Dhan adapter already parses `DEPTH_20_BID` (code 41) and `DEPTH_20_ASK` (code 51). Surface through WebSocket proxy.
- Market clock feeds into auto-trade engine: hot zones increase entry sensitivity, quiet zones raise entry threshold
- Risk limits stored in `scalpingStore` (persisted subset via Zustand persist)

### Test:
Open Dhan instance -> Depth tab shows 20 levels with analytics. Switch to Zerodha -> falls back to 5 levels. Set daily loss -500 -> trade until limit -> trading disabled. Market clock shows current zone + countdown.

---

## Phase 5: Auto-Trade Engine + Ghost Signals + Options Context
**~3,000 lines | Deliverable: Full auto-trade with ghost signals in manual mode**

### The Options Context Layer

The auto-trade engine consumes data from existing options analytics backend services:

```typescript
interface OptionsContext {
  // OI Tracker (from /api/v1/oi_tracker)
  pcr: number                    // Put-Call Ratio
  oiChangeCE: number             // CE OI change
  oiChangePE: number             // PE OI change

  // Max Pain (from /api/v1/max_pain)
  maxPainStrike: number
  spotVsMaxPain: number          // Distance from max pain

  // GEX (from /api/v1/gex)
  topGammaStrikes: number[]      // High gamma = magnetic levels
  gexFlipZones: number[]         // Volatility shift zones
  netGEX: number                 // +ve = mean-reverting, -ve = trending

  // IV (from /api/v1/iv_chart)
  atmIV: number
  ivPercentile: number           // 0-100
  ceIV: number
  peIV: number
  ivSkew: number                 // CE IV - PE IV

  // Straddle (from /api/v1/straddle_chart)
  straddlePrice: number          // Expected range

  lastUpdated: number
}
```

### 3 Integration Points:

1. **Entry Filter** - `optionsContextFilter(side, context, config)`:
   - PCR-based directional filter
   - Max pain proximity filter (low momentum expected near max pain)
   - GEX wall detection (reversal likely near gamma walls)

2. **Dynamic Trailing SL** - `getContextAwareTrailDistance(baseDistance, context, config)`:
   - Widen SL when ATM IV is high (more volatility)
   - Tighten SL near gamma walls
   - Adjust based on straddle width

3. **Early Exit** - `optionsEarlyExitCheck(side, context, config)`:
   - Exit if IV spikes (event risk)
   - Exit if PCR flips against position
   - Exit if approaching max pain
   - Exit if GEX flip zone crossed

### Ghost Signals (ASSIST Mode for Manual Trading)

When auto-trade engine runs in **shadow mode** (manual trading active):
- Engine processes every tick as if it would trade, but does NOT execute
- Generates signals: "BUY CE momentum detected (score 7/10, PCR 0.85, TRENDING)"
- Displayed as non-intrusive overlay in the `GhostSignalOverlay` component
- Shows in control panel as a scrolling signal log
- Optional: subtle pulse on the option chain row when ghost signal fires for that strike
- User decides whether to act on ghost signals or ignore them

### Files to create:
1. **`types/scalping.ts`** (update) - Add `AutoTradeConfig`, `AutoTradeRuntime`, `OptionsContext`, `TradeRecord`, `EquityCurvePoint`, `MarketRegime`, `TrailingStage`, `GhostSignal`, `MarketClockZone`
2. **`stores/autoTradeStore.ts`** - Zustand with `persist` for config, non-persisted for runtime:
   - Config: ~60 grouped fields (entry, trailing, breakeven, risk, regime, index bias, options context, etc.)
   - Runtime: realizedPnl, regime, consecutiveLosses, trailStages, optionsContext, ghostSignals[], equityCurve[]
   - Actions: `updateConfig`, `applyPreset`, `resetRuntime`, `recordTrade`, `addGhostSignal`
3. **`lib/scalpingPresets.ts`** - 5 presets:
   - `SNIPER_QUALITY` - Tight entry conditions, wider SL, for sideways/quiet days
   - `MOMENTUM_SCALPER` - Fast entry, tight SL, for trending days
   - `BALANCED_TRADER` - Default middle ground
   - `AUTO_ADAPTIVE` - Regime-based, uses options context, adjusts dynamically
   - `EXPIRY_DAY` - Auto-activates on expiry, watches for post-13:30 surprises, tighter risk, faster exits
4. **`lib/autoTradeEngine.ts`** - Pure functions:
   - `shouldEnterTrade(side, ltp, config, runtime, indicators, optionsContext, marketClock)` -> `{enter, reason, score}`
   - `calculateTrailingStop(stage, entry, current, high, config, optionsContext)` -> `{newSL, newStage}`
   - `detectRegime(candles, config)` -> `MarketRegime`
   - `calculateMomentum(ticks, config)` -> `{direction, count, velocity}`
   - `isNoTradeZone(prices, rangePts)` -> boolean
   - `shouldExitTrade(side, ltp, config, runtime, optionsContext)` -> `{exit, reason}`
   - `calculateIndexBias(indexIndicators, config)` -> `{score, direction}`
   - `optionsContextFilter(side, context, config)` -> `{allowed, reason}`
   - `getContextAwareTrailDistance(baseDistance, context, config)` -> adjustedDistance
   - `optionsEarlyExitCheck(side, context, config)` -> `{exit, reason}`
   - `generateGhostSignal(side, ltp, config, runtime, indicators, optionsContext)` -> `GhostSignal | null`
5. **`hooks/useOptionsContext.ts`** - Polls backend every 30-60s:
   - Calls: `/api/v1/oi_tracker`, `/api/v1/max_pain`, `/api/v1/gex`, `/api/v1/iv_chart`, `/api/v1/straddle_chart`
   - Builds `OptionsContext` object
   - Pauses when market closed (`useMarketStatus`)
6. **`hooks/useAutoTradeEngine.ts`** - Two modes with **zero-React tick path**:
   - Registers a **raw callback** on `MarketDataManager` via `useRef` - bypasses React scheduling entirely
   - On each tick: runs pre-computed condition checks, if trigger fires -> `fetch()` fire-and-forget (no await) + instant chart line
   - **Pre-computation**: When conditions are 90% met, pre-builds the order payload so trigger tick only needs to fire `fetch()`
   - **Execute mode** (auto-trade ON): processes ticks, fires orders immediately via `tradingApi.placeOrder` (no await), shows instant "pending" chart line, updates to "confirmed" when response arrives async
   - **Shadow/Ghost mode** (manual trading): processes ticks, generates `GhostSignal`s only, no execution
   - Both modes use same pure functions from `autoTradeEngine.ts`
   - Switching from manual to auto: engine seamlessly starts executing (existing ghost analysis continues)
   - Switching from auto to manual: engine stops executing but continues ghost signals
   - **Target: ~2ms from tick arrival to order fire** (broker RTT of ~15-50ms is unavoidable)
7. **`components/scalping/AutoTradeTab.tsx`** - Enable/disable toggle. Paper/Live mode. Status: regime, momentum, last signal. Options Context badge (PCR, IV, MaxPain at a glance). Equity curve mini-chart. Trade history.
8. **`components/scalping/AutoTradePresets.tsx`** - 5 preset cards (incl. Expiry Day). Click to apply. Current highlighted. Expiry Day auto-suggested when detected.
9. **`components/scalping/AutoTradeConfig.tsx`** - Accordion sections: Entry | Trailing SL (5-stage) | Breakeven | Risk | Strike Selection | Regime Detection | Index Bias | Options Context Filters | Time-of-Day Zones | Partial Exit | Re-entry | No-Trade Zone | Averaging
10. **`components/scalping/GhostSignalOverlay.tsx`** - Non-intrusive signal display:
    - Floating card in control panel showing latest ghost signal
    - Signal log (scrollable, last 20 signals)
    - Each signal: timestamp, side, reason, score, key metrics
    - Optional: one-click "Take this trade" button to execute the ghost signal manually
11. **`components/scalping/OptionsContextPanel.tsx`** - Live options context: PCR gauge, Max Pain distance bar, GEX net, ATM IV, IV Skew, Straddle width

### Key patterns:
- Auto-trade config persisted in Zustand -> survives page reload
- Runtime state reset on page load
- Engine runs via `useRef` callbacks (not state) for zero re-render per tick
- Ghost signals: engine always runs analysis, mode only controls execution
- Seamless manual<->auto switch: no state reset, just toggle execution flag
- Expiry Day preset auto-suggested via `useMarketClock.isExpiryDay`

### Test:
Manual mode: ghost signals appear showing "BUY CE momentum detected" -> click "Take trade" -> order executes. Toggle auto-trade ON -> engine takes over, places entries, trails SL through 5 stages. Toggle back to manual -> engine stops executing but ghost signals continue. Switch to Expiry Day preset -> tighter parameters auto-apply. Options context shows PCR shifting -> engine adjusts entry filters.

---

## Phase 6: Polish + LLM Trade Analysis
**~700 lines | Deliverable: Trade analysis, responsive tweaks, final polish**

### Additions:
- **LLM Trade Analysis**: Export trade log (from `useTradeLogger`) as structured JSON. Send to LLM endpoint for pattern analysis: "Where do I win? Where do I lose? Time patterns? Regime patterns?" This uses existing LLM advisor infrastructure (OpenAI/Anthropic/Ollama config in .env).
- Equity curve chart in BottomBar (mini `lightweight-charts` instance)
- Session summary stats (win rate, avg P&L, Sharpe approximation, best/worst trade)
- Panel size persistence to localStorage
- Keyboard shortcut help overlay (? key)
- Toast notifications for auto-trade events and ghost signals
- Export trade log as CSV/JSON
- Responsive: collapse chain panel on narrow screens, tabs for all 3 charts instead of split

### Test:
Full end-to-end: Open `/scalping` on Dhan (port 5001) -> select NIFTY -> see 3 charts (Index + CE + PE) updating -> 20-level depth in Depth tab -> use hotkeys to trade -> ghost signals showing opportunities -> toggle auto-trade ON -> engine executes with options context -> toggle back to manual -> export trade log -> send to LLM -> get pattern analysis.

---

## Performance Strategy

### Ultra-Low Latency Auto-Trade Path

The auto-trade engine uses a **zero-React tick path** for minimum latency:

```
Broker WS -> ZMQ -> WebSocket 8765 -> MarketDataManager raw callback
  ~2ms       ~1ms       ~3ms              |
                                   useRef engine function (NO React scheduling)
                                          |
                              Pre-computed decision check (~0.1ms)
                                          |
                          ┌───────────────┴───────────────┐
                          |                               |
              fire placeOrder() (no await)     Instant "pending" line on chart
                  ~15-50ms broker RTT              ~1ms via ref
                          |
              Order confirmed async -> update line to "confirmed"
```

**Key optimizations**:
1. **Raw WebSocket callback** - Auto-trade engine registers directly on `MarketDataManager` via `useRef` callback, completely bypassing React's scheduling/reconciliation. The tick goes straight from WebSocket `onmessage` to the engine function.
2. **Pre-computed decisions** - When entry conditions are 90% met, engine pre-computes the order payload (`{symbol, exchange, action, qty, ...}`). When the final trigger tick arrives, it's a single `fetch()` call - no evaluation delay.
3. **Fire-and-forget orders** - `placeOrder()` fires without `await`. Chart immediately shows a "pending" order line (dashed). When the response arrives async, it updates to "confirmed" (solid line). User sees instant feedback.
4. **Concurrent chart update** - Order fire and chart line drawing happen in parallel, not sequentially.
5. **Virtual TP/SL in same callback** - TP/SL checking runs in the same raw callback as the engine, no separate monitoring loop.

**Total added latency from tick to order fire: ~2ms** (the remaining ~15-50ms is broker network RTT, unavoidable).

### General UI Performance

1. **`React.memo`** on chain rows - only re-render when that strike's data changes
2. **Zustand selectors** with shallow equality - components subscribe to specific slices
3. **Ref-based tick processing** - auto-trade engine + ghost signal generation use `useRef` callbacks, not state per tick
4. **Direct chart API updates** - `series.update(bar)` via ref for all 3 charts, bypasses React render cycle
5. **Single WebSocket** - `MarketDataManager` singleton (auto-discovers port from `/api/websocket/config`)
6. **`requestAnimationFrame` batching** - batch multiple ticks per frame for UI updates only (NOT for auto-trade decisions)
7. **In-memory trade logger** - ring buffer, async flush to backend (does not block execution)
8. **Virtual TP/SL in-memory** - no DB round-trip for TP/SL checks, pure JS price comparison per tick
9. **Floating widget updates** - LTP flash and P&L banner use CSS transitions, not React state updates per tick

---

## Backend Changes

**Minimal:**
- Modify `blueprints/scalping.py` to redirect `/scalping` to React app
- Extend WebSocket proxy to forward Dhan 20-level depth subscriptions (if not already handled)
- All other APIs already exist: optionchain, placeorder, closeposition, cancelorder, positionbook, depth, websocket/config, ai_scalper/*, oi_tracker, gex, max_pain, iv_chart, straddle_chart
- Jinja2 templates kept as fallback during transition

---

## Critical Files Reference

| Purpose | Path |
|---------|------|
| Route registration | `frontend/src/App.tsx` (line 225-232) |
| Full-width layout | `frontend/src/components/layout/FullWidthLayout.tsx` |
| WebSocket manager | `frontend/src/lib/MarketDataManager.ts` |
| Option chain hook | `frontend/src/hooks/useOptionChainLive.ts` |
| Market data hook | `frontend/src/hooks/useMarketData.ts` |
| Live price hook | `frontend/src/hooks/useLivePrice.ts` |
| Trading API | `frontend/src/api/trading.ts` |
| Option chain API | `frontend/src/api/option-chain.ts` |
| AI scalper API | `frontend/src/api/ai-scalper.ts` |
| Resizable panels | `frontend/src/components/ui/resizable.tsx` |
| Auth store | `frontend/src/stores/authStore.ts` |
| Socket provider | `frontend/src/components/socket/SocketProvider.tsx` |
| autoState reference | `templates/scalping/auto_trading_window.html` (lines 3404-3591) |
| Dhan 20-depth WS | `broker/dhan/streaming/dhan_websocket.py` (SUBSCRIBE_20_DEPTH) |
| Dhan adapter | `broker/dhan/streaming/dhan_adapter.py` |
| Broker instances | `.env.kotak` (5000), `.env.dhan` (5001), `.env.zerodha` (5002) |
| Scalping blueprint | `blueprints/scalping.py` |

---

## Verification

After each phase:
1. `cd frontend && npm run build` - TypeScript compiles without errors
2. `cd frontend && npm run lint` - Biome passes
3. Manual test at `http://127.0.0.1:5001/react/scalping` (Dhan) with live broker connection
4. Also test at `http://127.0.0.1:5000/react/scalping` (Kotak) to verify broker-agnostic behavior
5. Verify single WebSocket connection in browser DevTools Network tab
6. Verify zero-lag chart updates during high-volume market hours

After Phase 5 (auto-trade):
- Unit tests for `lib/autoTradeEngine.ts` and `lib/technicalIndicators.ts`
- Paper mode end-to-end: ghost signals in manual -> switch to auto -> entry -> trailing stages -> exit
- Verify ghost signals continue after switching back to manual
