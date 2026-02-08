# Phase 3: Advanced Chart Order Management

## Overview
Implement advanced order management features with draggable lines, TP/SL controls, live P&L display, and quick position closing.

## User Review Required

> [!WARNING]
> **Technical Limitation**: LightweightCharts library does NOT natively support draggable price lines. We'll need to implement a custom solution using HTML overlays positioned on top of the chart.

> [!IMPORTANT]
> **Complexity Notice**: This phase involves significant complexity:
> - Custom drag-and-drop implementation
> - HTML overlay positioning synchronized with chart
> - Real-time P&L calculation
> - Multi-order state management (main order + TP + SL)
> - WebSocket integration for order updates

**Estimated effort**: 2-3 hours of implementation and testing.

## Proposed Changes

### Architecture

#### Order State Management
- Add `orders` Map to track all orders (pending and filled)
- Add `positions` Map to track open positions
- Structure:
  ```javascript
  {
    orderId: {
      type: 'limit' | 'market',
      action: 'BUY' | 'SELL',
      price: number,
      quantity: number,
      status: 'pending' | 'filled' | 'cancelled',
      priceLine: object,
      overlay: HTMLElement,
      tpOrder: object | null,
      slOrder: object | null
    }
  }
  ```

---

### [MODIFY] [chart_window.html](file:///c:/algo/openalgov2/openalgo/chart_window.html)

#### 1. Draggable Order Lines

**Implementation approach**:
- Create HTML overlay div positioned absolutely over chart
- Use CSS transforms to position at price level
- Implement drag handlers with `mousedown`, `mousemove`, `mouseup`
- Convert pixel position back to price using `coordinateToPrice()`
- Call modify order API when drag completes

**Features**:
- Only pending LIMIT orders are draggable
- Visual feedback: cursor changes to `ns-resize`
- Price snaps to tick size (0.05) during drag
- Toast notification on successful modify

#### 2. TP/SL Controls

**Popup menu structure**:
```html
<div class="order-controls-popup">
  <button class="set-tp-btn">Set TP</button>
  <button class="set-sl-btn">Set SL</button>
  <button class="close-order-btn">✕ Close</button>
</div>
```

**Interaction flow**:
1. Click/hover on order line → popup appears
2. Click "Set TP" → enable TP line placement (green dashed)
3. Click chart → TP order placed
4. Click "Set SL" → enable SL line placement (red dashed)
5. Click chart → SL order placed

**Linked dragging**:
- Store TP/SL references with main order
- When main order dragged, calculate TP/SL offset
- Update TP/SL lines to maintain offset
- Modify all three orders via API

#### 3. Live P&L Display

**Calculation**:
```javascript
pnl = (currentPrice - entryPrice) * quantity * (action === 'BUY' ? 1 : -1)
```

**Display on line**:
- Text overlay at right edge of line
- Format: `₹+1,234.50 (+2.5%)` or `₹-567.80 (-1.2%)`
- Color: green for profit, red for loss
- Updates every tick from WebSocket

#### 4. Close Button on Lines

**For filled orders (positions)**:
- Show `✕` button on the right side of line
- Click → confirm and call close position API
- Remove all associated lines (main + TP + SL)
- Update position state

#### 5. Hotkey X to Close All

**Behavior**:
- Press X when NOT in order placement mode
- Show confirmation dialog: "Close all positions?"
- Call close position API for each open position
- Clear all position lines from chart

---

### [MODIFY] CSS Additions

#### Draggable Line Overlay
```css
.order-line-overlay {
  position: absolute;
  height: 2px;
  left: 0;
  right: 0;
  cursor: ns-resize;
  z-index: 10;
}

.order-line-overlay.dragging {
  height: 4px;
  box-shadow: 0 0 10px rgba(255, 255, 255, 0.5);
}
```

#### P&L Label
```css
.pnl-label {
  position: absolute;
  right: 10px;
  padding: 4px 8px;
  background: rgba(0, 0, 0, 0.8);
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  pointer-events: none;
}

.pnl-label.profit { color: #00ff88; }
.pnl-label.loss { color: #ff4560; }
```

#### Controls Popup
```css
.order-controls-popup {
  position: absolute;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px;
  display: flex;
  gap: 8px;
  z-index: 100;
}
```

---

### API Integration

#### Order Modify
- Endpoint: `/api/v1/modifyorder`
- Called when user drags order line
- Updates order price

#### Position Close  
- Endpoint: `/api/v1/closeposition`
- Called when user clicks X button or presses X key
- Closes active position

#### Order Status Updates
- Listen to WebSocket for order status changes
- Update line appearance when order fills
- Switch from "pending" to "position" mode

---

## Verification Plan

### Manual Testing
1. **Draggable Lines**:
   - Place LIMIT order
   - Drag line up/down
   - Verify price updates in increments of 0.05
   - Check modify API is called

2. **TP/SL Controls**:
   - Click on order line → popup appears
   - Set TP → green line appears
   - Set SL → red line appears
   - Drag main order → verify TP/SL follow

3. **Live P&L**:
   - Place and fill MARKET order
   - Verify P&L appears on line
   - Watch real-time updates as price changes
   - Check color changes (green/red)

4. **Close Functions**:
   - Click X on position line → position closes
   - Press X key → all positions close
   - Verify all lines removed

### Edge Cases
- Multiple positions on same instrument
- TP/SL price validation (TP > entry for BUY, TP < entry for SELL)
- WebSocket disconnect during active position
- Rapid order modifications
