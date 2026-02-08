# Chart Window Interactive Trading - Task Breakdown

## Phase 1: Basic Chart Setup ✅
- [x] Chart window creation with LightweightCharts
- [x] WebSocket data integration
- [x] Candlestick rendering
- [x] Real-time updates

## Phase 2: Order Placement ✅
- [x] Cursor-following order line
- [x] BUY/SELL hotkeys (B/S)
- [x] MARKET vs LIMIT order types
- [x] Auto-limit price from cursor position
- [x] Tick size rounding (0.05)
- [x] Correct lot size calculation
- [x] API integration with `/api/v1/placeorder`

## Phase 3: Advanced Order Management ✅
- [x] Draggable order lines
  - [x] HTML overlay system
  - [x] Drag handlers with tick snapping
  - [x] Modify API integration
  - [x] Visual feedback during drag
- [x] TP/SL controls
  - [x] Popup menu on line click
  - [x] Set Take Profit line
  - [x] Set Stop Loss line
  - [x] Chart click placement
- [x] Live P&L display
  - [x] Real-time P&L calculation
  - [x] Visual display with color coding
  - [x] Auto-updates every second
- [x] Position management
  - [x] Close button (X) on lines via popup
  - [x] Hotkey X to close all positions
  - [x] Chart scroll/zoom overlay updates

## Bug Fixes ✅
- [x] Fix WebSocket connection port (8765)
- [x] Fix live P&L data parsing
- [x] Fix closeAllPositions implementation
- [/] Fix exitPosition quantity parsing (Implemented, pending final verification)

## Status: Handover Ready 📦
