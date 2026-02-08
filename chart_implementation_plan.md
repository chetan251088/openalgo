# TradingView-Style Chart Window - Implementation Plan

## 📋 Overview

**Feature**: Interactive chart window for each option strike with visual order placement, draggable TP/SL lines, and real-time position management.

**Goal**: Provide a professional charting interface for precise order entry and management, matching the speed and performance of the main scalping interface.

**Technology**: [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) - High-performance, canvas-based charting library.

---

## 🎯 Core Requirements

### Must-Have Features
1. ✅ Chart button next to each strike price
2. ✅ New window opens with real-time candlestick chart
3. ✅ Click BUY/SELL → Line follows cursor → Click to place order
4. ✅ Draggable order lines with price labels
5. ✅ X button on lines to close/cancel orders
6. ✅ Auto-show TP/SL lines after order placed
7. ✅ Drag TP/SL lines to adjust levels
8. ✅ Auto-cancel TP/SL when main order closed
9. ✅ MARKET vs LIMIT mode toggle
10. ✅ Real-time P&L display
11. ✅ WebSocket integration for live prices
12. ✅ Sync with main scalping interface

### Performance Targets
- Chart render: <50ms
- Order placement: <100ms
- Line drag: <16ms (60 FPS)
- WebSocket updates: <20ms latency
- Window open: <500ms

---

## 🏗️ Architecture

### Component Structure

```
chart_window.html (New File)
├── HTML Structure
│   ├── Chart container
│   ├── Control panel (BUY/SELL/LIMIT/MARKET)
│   ├── Position info panel
│   └── Order lines overlay
├── CSS Styling
│   ├── Dark theme matching main interface
│   ├── Line styles (order/TP/SL)
│   └── Control buttons
└── JavaScript
    ├── Chart initialization
    ├── WebSocket integration
    ├── Order placement logic
    ├── Line drawing & dragging
    ├── TP/SL management
    └── API communication
```

### Data Flow

```
Main Interface
    ↓ (Opens chart window)
Chart Window
    ↓ (Shares WebSocket connection)
Real-time Price Updates
    ↓ (Updates candlesticks)
User Interaction (Click/Drag)
    ↓ (Places/Modifies order)
OpenAlgo API
    ↓ (Returns order ID)
Chart Updates (Shows line)
    ↓ (Syncs back)
Main Interface Updates
```

---

## 📁 File Structure

### New Files to Create

```
openalgo/
├── chart_window.html           # Main chart window
├── blueprints/
│   └── chart.py               # Flask blueprint for chart routes
└── static/
    └── js/
        └── lightweight-charts.standalone.production.js  # CDN fallback
```

### Modified Files

```
openalgo/
├── scalping_interface.html    # Add "Chart" button to each strike row
└── app.py                     # Register chart blueprint
```

---

## 🔧 Implementation Phases

### Phase 1: Basic Chart Setup (Day 1, Morning)
**Goal**: Get chart displaying with real-time data

**Tasks**:
1. Create `chart_window.html` with basic structure
2. Integrate lightweight-charts library via CDN
3. Create Flask blueprint `/chart/<symbol>` route
4. Display candlestick chart with dummy data
5. Add WebSocket connection for real-time updates
6. Implement candlestick data updates

**Deliverable**: Working chart window with live price updates

---

### Phase 2: Interactive Order Placement (Day 1, Afternoon)
**Goal**: Click-to-place orders with visual feedback

**Tasks**:
1. Add BUY/SELL/MARKET/LIMIT control buttons
2. Implement "line follows cursor" on BUY/SELL click
3. Place order on second click
4. Draw horizontal line at order price
5. Add price label to line
6. Add X button for order cancellation
7. Connect to OpenAlgo `/api/v1/optionsorder` endpoint

**Deliverable**: Functional order placement via chart clicks

---

### Phase 3: Draggable Order Lines (Day 1, Evening)
**Goal**: Modify orders by dragging lines

**Tasks**:
1. Implement mouse down/move/up handlers on lines
2. Detect line hover state
3. Allow vertical drag (price change only)
4. Update order via API on drag complete
5. Visual feedback during drag (cursor change, highlight)
6. Snap to tick size (0.05 for options)

**Deliverable**: Draggable order modification

---

### Phase 4: TP/SL Management (Day 2, Morning)
**Goal**: Auto-show TP/SL lines, make them draggable

**Tasks**:
1. Auto-create TP/SL lines after order placed
2. Green line for Take Profit (above entry)
3. Red line for Stop Loss (below entry)
4. Make TP/SL lines draggable
5. Place bracket orders via API
6. Link TP/SL to main order ID
7. Auto-cancel TP/SL when main order closed

**Deliverable**: Full bracket order management

---

### Phase 5: Position Display & P&L (Day 2, Afternoon)
**Goal**: Show current position and live P&L

**Tasks**:
1. Add position info panel (top of chart)
2. Display: Symbol, Qty, Entry Price, Current Price, P&L
3. Real-time P&L calculation from WebSocket prices
4. Color-code P&L (green/red)
5. Show position line on chart (dotted line at entry)
6. Add "Close Position" button
7. Update on position changes from main interface

**Deliverable**: Complete position tracking

---

### Phase 6: Performance Optimization (Day 2, Evening)
**Goal**: Ensure 60 FPS, minimal lag

**Tasks**:
1. Implement throttling for WebSocket updates (max 10 FPS for chart)
2. Use `requestAnimationFrame` for smooth line dragging
3. Debounce API calls during drag (send only on release)
4. Lazy load chart data (only visible candles)
5. Optimize line rendering (canvas layer)
6. Profile with Chrome DevTools
7. Test with multiple chart windows open

**Deliverable**: Smooth, lag-free performance

---

### Phase 7: Integration & Sync (Day 3, Morning)
**Goal**: Bidirectional sync with main interface

**Tasks**:
1. Share WebSocket connection between windows
2. Broadcast order updates (main → chart)
3. Broadcast position updates (chart → main)
4. Use `window.opener` for parent communication
5. Handle window close cleanup
6. Persist chart settings in localStorage
7. Handle edge cases (window refresh, network loss)

**Deliverable**: Fully synchronized multi-window system

---

### Phase 8: Polish & Testing (Day 3, Afternoon)
**Goal**: Production-ready feature

**Tasks**:
1. Add loading states and error handling
2. Implement retry logic for failed orders
3. Add confirmation dialogs for important actions
4. Test all edge cases (market closed, invalid price, etc.)
5. Cross-browser testing (Chrome, Edge, Firefox)
6. Mobile responsive design (optional)
7. User testing and feedback
8. Documentation updates

**Deliverable**: Stable, production-ready chart window

---

## 💻 Technical Implementation Details

### 1. Chart Initialization

```javascript
// chart_window.html
const chart = LightweightCharts.createChart(document.getElementById('chart'), {
    width: window.innerWidth,
    height: window.innerHeight - 100,
    layout: {
        background: { color: '#0a0e27' },
        textColor: '#d1d4dc',
    },
    grid: {
        vertLines: { color: '#1e2235' },
        horzLines: { color: '#1e2235' },
    },
    timeScale: {
        timeVisible: true,
        secondsVisible: false,
    },
});

const candlestickSeries = chart.addCandlestickSeries({
    upColor: '#00ff88',
    downColor: '#ff4560',
    borderVisible: false,
    wickUpColor: '#00ff88',
    wickDownColor: '#ff4560',
});
```

### 2. WebSocket Price Updates

```javascript
// Use existing WebSocket from main interface via window.opener
const ws = window.opener.state.ws;

ws.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'QUOTE') {
        const time = Math.floor(Date.now() / 1000);
        const price = parseFloat(data.ltp);
        
        // Update candlestick (batched, 1-second intervals)
        updateCandlestick(time, price);
        
        // Update P&L
        updatePnL(price);
    }
});
```

### 3. Interactive Order Line

```javascript
// Order line with price label and close button
class OrderLine {
    constructor(chart, price, type, orderId) {
        this.chart = chart;
        this.price = price;
        this.type = type; // 'BUY' or 'SELL'
        this.orderId = orderId;
        
        // Create price line on chart
        this.priceLine = candlestickSeries.createPriceLine({
            price: price,
            color: type === 'BUY' ? '#00ff88' : '#ff4560',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `${type} @ ${price}`,
        });
        
        // Add drag handlers
        this.setupDragHandlers();
    }
    
    setupDragHandlers() {
        // Detect mouse down on line
        // Track mouse move
        // Update price on drag
        // Call API to modify order on mouse up
    }
    
    addCloseButton() {
        // Create X button overlay
        // Position at line intersection with price axis
        // Click handler to cancel order
    }
}
```

### 4. TP/SL Bracket Orders

```javascript
async function createBracketOrder(mainOrderId, entryPrice, tpOffset, slOffset) {
    const tpPrice = entryPrice + tpOffset;
    const slPrice = entryPrice - slOffset;
    
    // Create TP line (green, above entry)
    const tpLine = new OrderLine(chart, tpPrice, 'TP', null);
    tpLine.setColor('#00ff88');
    tpLine.setStyle('dashed');
    
    // Create SL line (red, below entry)
    const slLine = new OrderLine(chart, slPrice, 'SL', null);
    slLine.setColor('#ff4560');
    slLine.setStyle('dashed');
    
    // Link to main order
    orderMap.set(mainOrderId, { tp: tpLine, sl: slLine });
}

function cancelBracketOrders(mainOrderId) {
    const bracket = orderMap.get(mainOrderId);
    if (bracket) {
        bracket.tp.remove();
        bracket.sl.remove();
        orderMap.delete(mainOrderId);
    }
}
```

### 5. Cursor Following Line

```javascript
let followingMode = false;
let orderType = 'BUY';

function enableFollowMode(type) {
    followingMode = true;
    orderType = type;
    
    // Create temporary line that follows cursor
    chart.subscribeCrosshairMove((param) => {
        if (!followingMode) return;
        
        const price = param.seriesPrices.get(candlestickSeries);
        if (price) {
            updateFollowLine(price);
        }
    });
    
    // Place order on click
    chart.addEventListener('click', () => {
        if (followingMode) {
            placeOrderAtPrice(currentFollowPrice);
            followingMode = false;
        }
    });
}
```

---

## 🔌 API Integration

### Endpoints Used

```javascript
// Place order
POST /api/v1/optionsorder
{
    strategy: 'chart_trading',
    underlying: 'NIFTY',
    exchange: 'NSE_INDEX',
    expiry_date: '05FEB26',
    offset: 0,
    option_type: 'CE',
    action: 'BUY',
    quantity: '50',
    pricetype: 'LIMIT',
    price: '125.50',
    product: 'MIS'
}

// Modify order (drag line)
PUT /api/v1/modifyorder
{
    orderid: 'abc123',
    price: '126.00'
}

// Cancel order (X button)
DELETE /api/v1/cancelorder
{
    orderid: 'abc123'
}

// Get positions (sync)
POST /api/v1/positionbook
{
    apikey: '...'
}
```

---

## 🎨 UI/UX Design

### Control Panel Layout

```
┌──────────────────────────────────────┐
│ NIFTY 24500 CE - 05FEB26            │
├──────────────────────────────────────┤
│ [BUY] [SELL] | [MARKET] [LIMIT]     │
│ Qty: [−] 5 [+]  | Price: [125.50]   │
├──────────────────────────────────────┤
│ Position: +50 @ 125.00               │
│ Current: 126.50 | P&L: +75.00 (6%)  │
│ [Close Position]                     │
└──────────────────────────────────────┘
        ↓
┌─────────────CHART─────────────────┐
│ 127.00 ━━━━━━━━━━ [×] (TP green) │
│ 125.50 ─ ─ ─ ─ ─ ─ (position)    │
│ 125.00 ━━━━━━━━━━ [×] (order)    │
│ 123.00 ━━━━━━━━━━ [×] (SL red)   │
└───────────────────────────────────┘
```

### Color Scheme

```css
/* Match main interface */
--chart-bg: #0a0e27
--chart-text: #d1d4dc
--buy-color: #00ff88 (green)
--sell-color: #ff4560 (red)
--tp-color: #00ff88 (green dashed)
--sl-color: #ff4560 (red dashed)
--position-color: #667eea (purple dotted)
```

---

## ⚡ Performance Optimizations

### 1. Throttle WebSocket Updates
```javascript
let lastUpdate = 0;
const UPDATE_INTERVAL = 100; // 10 FPS max

ws.onmessage = (event) => {
    const now = Date.now();
    if (now - lastUpdate < UPDATE_INTERVAL) return;
    
    lastUpdate = now;
    updateChart(event.data);
};
```

### 2. Batch Candlestick Updates
```javascript
let pendingCandle = null;
let candleTimer = null;

function updateCandlestick(time, price) {
    if (!pendingCandle || pendingCandle.time !== time) {
        if (pendingCandle) {
            candlestickSeries.update(pendingCandle);
        }
        pendingCandle = { time, open: price, high: price, low: price, close: price };
    } else {
        pendingCandle.high = Math.max(pendingCandle.high, price);
        pendingCandle.low = Math.min(pendingCandle.low, price);
        pendingCandle.close = price;
    }
    
    // Flush every 1 second
    clearTimeout(candleTimer);
    candleTimer = setTimeout(() => {
        if (pendingCandle) {
            candlestickSeries.update(pendingCandle);
            pendingCandle = null;
        }
    }, 1000);
}
```

### 3. Smooth Line Dragging
```javascript
let rafId = null;

function onLineDrag(event) {
    if (rafId) return;
    
    rafId = requestAnimationFrame(() => {
        const newPrice = calculatePriceFromY(event.clientY);
        updateLineVisual(newPrice);
        rafId = null;
    });
}
```

---

## 🧪 Testing Plan

### Unit Tests
- [ ] Chart initialization
- [ ] WebSocket connection
- [ ] Order placement API calls
- [ ] Line drawing and dragging
- [ ] TP/SL creation and cancellation
- [ ] P&L calculation

### Integration Tests
- [ ] Main interface → Chart window sync
- [ ] Chart window → Main interface sync
- [ ] Multi-window scenarios
- [ ] WebSocket reconnection
- [ ] Order state consistency

### Manual Tests
- [ ] Place 10 orders rapidly (performance)
- [ ] Drag lines smoothly (60 FPS)
- [ ] Open 5 chart windows simultaneously
- [ ] Close orders from main interface
- [ ] Network disconnect/reconnect
- [ ] Window refresh behavior

### Browser Compatibility
- [ ] Chrome (latest)
- [ ] Edge (latest)
- [ ] Firefox (latest)
- [ ] Safari (optional, macOS only)

---

## 📦 Dependencies

### NPM Packages (CDN)
```html
<!-- Lightweight Charts -->
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>

<!-- Or self-hosted -->
<script src="/static/js/lightweight-charts.standalone.production.js"></script>
```

### Python Dependencies
```python
# No new dependencies required
# Uses existing Flask, WebSocket infrastructure
```

---

## 🚨 Risk Mitigation

### Potential Issues & Solutions

| Risk | Impact | Mitigation |
|------|--------|------------|
| WebSocket connection sharing between windows | High | Use `window.opener` to access parent's WebSocket, fallback to separate connection |
| Multiple orders placed rapidly | Medium | Debounce order placement (500ms), show loading state |
| Chart rendering lag with many lines | Medium | Limit to 10 lines max, use canvas compositing |
| Order state sync issues | High | Implement event-based state management, use order IDs as keys |
| Window closed but positions remain | Low | Handle `window.onbeforeunload`, sync state on close |
| Drag accuracy on different screen sizes | Low | Use relative coordinates, normalize to chart scale |

---

## 📚 Documentation Updates

### Files to Update
1. `SCALPING_INTERFACE_README.md` - Add chart window section
2. `walkthrough.md` - Chart usage guide
3. `task.md` - Mark as completed
4. New file: `CHART_WINDOW_GUIDE.md` - Detailed usage instructions

---

## 🎯 Success Criteria

**Feature is complete when:**
1. ✅ Chart opens in <500ms
2. ✅ Orders placed with <100ms feedback
3. ✅ Lines drag smoothly at 60 FPS
4. ✅ TP/SL auto-cancel on main order close
5. ✅ Real-time P&L accurate to 2 decimals
6. ✅ Syncs with main interface bidirectionally
7. ✅ No memory leaks after 1 hour of use
8. ✅ Works with 5 chart windows open simultaneously

---

## 🚀 Launch Checklist

- [ ] Code review completed
- [ ] All tests passing
- [ ] Performance profiling done
- [ ] Documentation updated
- [ ] User testing completed
- [ ] Edge cases handled
- [ ] Error logging implemented
- [ ] Analytics tracking added (optional)

---

**Estimated Effort**: 3 days (24 hours of focused development)
**Complexity**: High (8/10)
**Impact**: High - Game-changing feature for scalpers

**Next Steps**: Review this plan, approve, and begin Phase 1 implementation!
