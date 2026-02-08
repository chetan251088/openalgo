# Mock WebSocket Data for NIFTY and SENSEX (Testing)

You can test the scalping interface and auto-trade window **without a live broker** using either:

| Option | Data | When to use |
|--------|------|-------------|
| **Simple mock** | Random-walk LTP (no Historify) | Quick smoke test; no setup. |
| **Historify replay** | Real 1m data from Historify | After-hours testing with your downloaded Nifty/index (or CE/PE) 1m data. |

## Option A: Simple mock (random data)

### 1. Start the mock WebSocket server

From the project root:

```bash
uv run python scripts/mock_websocket_server.py
```

By default it listens on **port 8770**. (Ports 8765, 8766, 8767 are reserved for Kotak, Dhan, Zerodha in multi-instance setup — see `START_BOTH.bat`. Mock stays on 8770 so it never conflicts.) To use another port:

```bash
MOCK_WS_PORT=8775 uv run python scripts/mock_websocket_server.py
```

You should see:

```
Mock WebSocket server: ws://127.0.0.1:8770
Sends NIFTY and SENSEX (and BANKNIFTY if subscribed) LTP updates.
Port 8770 is for mock only (8765/8766/8767 = Kotak/Dhan/Zerodha).
```

Leave this terminal running.

### 2. Point the app to the mock server

Set the WebSocket URL to the mock server in your environment:

- **Option A – .env**  
  Add or change:

  ```env
  WEBSOCKET_URL=ws://127.0.0.1:8770
  ```

  The scalping UI gets `wsUrl` from `/scalping/config`, which uses `WEBSOCKET_URL`. Restart Flask after changing `.env`.

- **Option B – Same machine, override only for testing**  
  Keep your real `WEBSOCKET_URL` in `.env` for live use. For a test run, start Flask with:

  ```bash
  set WEBSOCKET_URL=ws://127.0.0.1:8770
  uv run app.py
  ```

  (On Linux/macOS use `export WEBSOCKET_URL=ws://127.0.0.1:8770`.)

### 3. Open the scalping page

1. Start Flask (if not already): `uv run app.py` (or your usual `.bat`).
2. Open **http://127.0.0.1:5000/scalping**.
3. Enter your API key and click **Connect**.
4. The mock server will respond to **authenticate** and **subscribe**; you should see “Connected” and the **index LTP** (e.g. NIFTY / SENSEX) updating with random-walk values.

Index symbols (NIFTY, SENSEX, BANKNIFTY) will receive continuous mock LTP updates. Option chain symbols from the real API (expiries, strikes) still come from the REST API; only **WebSocket ticks** are mocked.

---

## Option B: Historify replay (real 1m data)

Use this when you have already downloaded 1m data into Historify (e.g. NSE index for the last 30 days). The replay server reads that data and streams it as LTP ticks so you can test after market hours with **real** prices.

### 1. Ensure Historify has 1m data

- In the app, go to **Historify**, add symbols (e.g. NIFTY 50 for NSE_INDEX), run a download job for **1m** and the date range you want.
- Verify at **http://127.0.0.1:5001/historify/charts?exchange=NSE_INDEX&interval=1m** (or your instance port).

### 2. Start the Historify replay server

From the project root (use the same env as your Historify DB, e.g. `.env.dhan` if you pulled data with Dhan):

```bash
uv run python scripts/historify_replay_server.py
```

Optional env:

- `MOCK_WS_PORT=8770` — port (default 8770).
- `MOCK_REPLAY_SPEED=1.0` — 1 = real time, 5 = 5x faster, 0 = as fast as possible.
- `MOCK_REPLAY_START_DATE=2025-01-01` / `MOCK_REPLAY_END_DATE=2025-01-31` — limit replay to this range (default: full range in Historify).

You should see:

```
Historify Replay WebSocket server: ws://127.0.0.1:8770
Streams real 1m data from Historify (db/historify.duckdb).
```

### 3. Start the app and use mock

You normally start the app with **run_dhan.bat** or **run_kotak.bat** (they use `.env.dhan` / `.env.kotak`). To use **mock** replay and **mock trades** without touching those files:

**Option A – Use the mock batch scripts (recommended)**

1. **Terminal 1 – Replay server**  
   ```batch
   run_replay_server.bat
   ```  
   Uses `.env.mock` or `.env.dhan` so the replay server reads the same Historify DB as the app.

2. **Terminal 2 – OpenAlgo app in mock mode**  
   ```batch
   run_mock.bat
   ```  
   Creates `.env.mock` from `.env.dhan` (if missing) with `WEBSOCKET_URL=ws://127.0.0.1:8770` and starts the app. App runs on port **5001** (same as Dhan).

3. **Browser**  
   - **Mock Replay UI (chart + mock trades):** http://127.0.0.1:5001/mock-replay  
   - Trades placed here go only to **db/mock_trading.db** (orders, positions, trades). No real broker.

**Option B – Manual (reuse run_dhan.bat)**

1. Edit `.env.dhan`: set `WEBSOCKET_URL = 'ws://127.0.0.1:8770'`.
2. Terminal 1: `uv run --env-file .env.dhan python scripts/historify_replay_server.py`
3. Terminal 2: `run_dhan.bat`
4. Open http://127.0.0.1:5001/mock-replay. When done testing, set `WEBSOCKET_URL` back to `ws://127.0.0.1:8766` in `.env.dhan`.

### CE/PE (options)

- **Index only:** If you only downloaded NSE index (e.g. NIFTY 50), only the **index** will replay. CE/PE option symbols will not get ticks (we cannot derive real option prices from index alone).
- **To replay CE/PE:** Download **1m data for the specific option symbols** (e.g. NFO strikes) in Historify; then the replay server will stream those too when you subscribe.
- **Synthetic CE/PE (future):** A later enhancement could add a rough option pricer (e.g. Black–Scholes using index as spot) to fake CE/PE from index data; that would be for testing only, not real prices.

---

## Message format (for reference)

The mock server sends the same shape as the real WebSocket proxy:

```json
{
  "type": "market_data",
  "symbol": "NIFTY",
  "exchange": "NSE_INDEX",
  "mode": 1,
  "data": {
    "ltp": 24502.5,
    "timestamp": 1712572800000
  },
  "broker": "mock"
}
```

- **NIFTY** → `exchange`: `NSE_INDEX`
- **SENSEX** → `exchange`: `BSE_INDEX`
- **BANKNIFTY** → `exchange`: `NSE_INDEX`

The scalping page uses `symbol === state.selectedIndex` to update the index LTP (e.g. when `selectedIndex` is `NIFTY` or `SENSEX`).

## Behaviour

- **Random walk**: Index LTP moves up/down around a base (NIFTY ~24500, SENSEX ~72000, BANKNIFTY ~52000) with a small mean reversion so it doesn’t drift away forever.
- **Tick rate**: One update every 0.2–0.8 seconds per index (roughly 1–5 ticks per second).
- **No real broker**: No orders, positions, or margin; use only for UI and logic testing (e.g. index display, auto-trade conditions that depend on index ticks).

## Resuming live data

1. Stop the mock server (Ctrl+C).
2. In `.env`, remove `WEBSOCKET_URL` or set it back to your real proxy (e.g. `ws://127.0.0.1:8765` for Kotak, or 8766/8767 for Dhan/Zerodha).
3. Restart Flask and reconnect from the scalping page.

## Troubleshooting

| Issue | Check |
|-------|--------|
| “WebSocket disconnected” or no index LTP | Mock server running on the port you set in `WEBSOCKET_URL`? Flask restarted after changing `.env`? |
| Index LTP still “-” | After Connect, does the page send **subscribe** with symbols? Mock server logs “Client connected” and then streams; if you don’t subscribe, it still starts NIFTY and SENSEX by default. |
| Port in use | Use a different port: `MOCK_WS_PORT=8775 uv run python scripts/mock_websocket_server.py` and set `WEBSOCKET_URL=ws://127.0.0.1:8775`. **Do not use 8765/8766/8767** — those are for Kotak/Dhan/Zerodha. |

## Optional: mock CE/PE symbols

The current script only mocks **index** symbols (NIFTY, SENSEX, BANKNIFTY). To mock option symbols (e.g. NFO/BFO CE/PE), you would extend `scripts/mock_websocket_server.py`: parse `symbols` from subscribe, detect NFO/BFO symbols, and run a similar random-walk loop for each with a suitable base price (e.g. 100–200 for options). The message format stays the same; only `symbol` and `exchange` change.
