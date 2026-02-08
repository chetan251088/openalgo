#!/usr/bin/env python3
"""
Mock WebSocket server for testing NIFTY and SENSEX (and optional CE/PE) data
without a live broker connection.

Sends market_data messages in the same format as the real OpenAlgo WebSocket proxy,
so the scalping interface and auto-trade window can be tested offline.

Usage:
    # From project root (uses uv)
    uv run python scripts/mock_websocket_server.py

    # Optional: custom port (default 8770; do not use 8765/8766/8767 - reserved for Kotak/Dhan/Zerodha)
    MOCK_WS_PORT=8770 uv run python scripts/mock_websocket_server.py

Then point the app to the mock server:
    - Set in .env: WEBSOCKET_URL=ws://127.0.0.1:8770
    - Or the scalping page uses /scalping/config which reads WEBSOCKET_URL

So: start this script, set WEBSOCKET_URL=ws://127.0.0.1:8770, restart Flask, open /scalping.
"""

import asyncio
import json
import os
import random
import time

try:
    import websockets
except ImportError:
    print("Install websockets: uv add websockets")
    raise SystemExit(1)


# 8770: mock only; 8765/8766/8767 are used by Kotak/Dhan/Zerodha (START_BOTH.bat)
MOCK_WS_PORT = int(os.getenv("MOCK_WS_PORT", "8770"))

# Base levels for random-walk (roughly realistic)
NIFTY_BASE = 24500.0
SENSEX_BASE = 72000.0
BANKNIFTY_BASE = 52000.0

# Volatility (max step per tick)
TICK_STEP = 2.0


def next_price(prev: float, base: float) -> float:
    """Random walk around base; clamp so it doesn't drift too far."""
    drift = (base - prev) * 0.02  # Slight mean reversion
    step = (random.random() - 0.5) * 2 * TICK_STEP + drift
    return max(1.0, round(prev + step, 2))


async def send_market_data(ws, symbol: str, exchange: str, ltp: float):
    """Send one market_data message (same shape as real proxy)."""
    msg = {
        "type": "market_data",
        "symbol": symbol,
        "exchange": exchange,
        "mode": 1,
        "data": {
            "ltp": ltp,
            "timestamp": int(time.time() * 1000),
        },
        "broker": "mock",
    }
    await ws.send(json.dumps(msg))


async def mock_index_loop(ws, symbol: str, exchange: str, base: float, state: dict):
    """Send periodic mock LTP updates for one index."""
    key = f"{symbol}_{exchange}"
    if key not in state:
        state[key] = base
    try:
        while True:
            state[key] = next_price(state[key], base)
            await send_market_data(ws, symbol, exchange, state[key])
            await asyncio.sleep(random.uniform(0.2, 0.8))  # 1–5 ticks per second
    except websockets.exceptions.ConnectionClosed:
        pass


async def handle_client(ws):
    """Handle one WebSocket client: auth + subscribe, then stream mock data in background."""
    client_id = id(ws)
    print(f"[mock] Client connected: {client_id}")
    mock_tasks = []

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = data.get("action")
            if action == "authenticate":
                await ws.send(json.dumps({
                    "type": "auth",
                    "status": "success",
                    "broker": "mock",
                    "user_id": "mock_user",
                }))
                continue
            if action == "subscribe":
                symbols = data.get("symbols") or data.get("instruments") or []
                if symbols and isinstance(symbols[0], dict):
                    sym_list = [s.get("symbol") for s in symbols if s.get("symbol")]
                else:
                    sym_list = [s.get("symbol", s) if isinstance(s, dict) else s for s in symbols]

                indices = []
                for sym in sym_list:
                    if sym in ("NIFTY", "NIFTY 50", "Nifty 50"):
                        indices.append(("NIFTY", "NSE_INDEX", NIFTY_BASE))
                    elif sym in ("SENSEX", "BSE SENSEX"):
                        indices.append(("SENSEX", "BSE_INDEX", SENSEX_BASE))
                    elif sym in ("BANKNIFTY", "BANK NIFTY"):
                        indices.append(("BANKNIFTY", "NSE_INDEX", BANKNIFTY_BASE))

                if not indices:
                    indices = [
                        ("NIFTY", "NSE_INDEX", NIFTY_BASE),
                        ("SENSEX", "BSE_INDEX", SENSEX_BASE),
                    ]

                state = {}
                for symbol, exchange, base in indices:
                    t = asyncio.create_task(mock_index_loop(ws, symbol, exchange, base, state))
                    mock_tasks.append(t)
                await ws.send(json.dumps({"status": "subscribed", "count": len(sym_list) or 2}))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        for t in mock_tasks:
            t.cancel()
        try:
            await asyncio.gather(*mock_tasks, return_exceptions=True)
        except Exception:
            pass
        print(f"[mock] Client disconnected: {client_id}")


async def main():
    host = "127.0.0.1"
    print(f"Mock WebSocket server: ws://{host}:{MOCK_WS_PORT}")
    print("Sends NIFTY and SENSEX (and BANKNIFTY if subscribed) LTP updates.")
    print("Port 8770 is for mock only (8765/8766/8767 = Kotak/Dhan/Zerodha).")
    print("Set WEBSOCKET_URL=ws://127.0.0.1:8770 in .env and restart Flask to use mock data.")
    async with websockets.serve(handle_client, host, MOCK_WS_PORT, ping_interval=20, ping_timeout=20):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
