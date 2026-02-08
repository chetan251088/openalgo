# Mock WebSocket Replay Using Historify (Design Plan)

**Status:** Plan only — implementation separate.  
**Goal:** Replay real historical data from OpenAlgo’s Historify as mock WebSocket ticks for testing charts and strategies, with configurable “market regimes” (choppy, volatility, momentum, VIX-style), without touching the real WebSocket port or live data path.

---

## 0. How It Works (Flow)

### Two mock options

| Option | Data source | Do you need Historify data first? |
|--------|-------------|-----------------------------------|
| **Simple mock** (current) | `scripts/mock_websocket_server.py` | **No.** It sends random-walk LTP for NIFTY/SENSEX/BANKNIFTY. No Historify. Just start the script and set `WEBSOCKET_URL=ws://127.0.0.1:8770`. |
| **Historify replay** (this plan) | Historify DuckDB `market_data` 1m | **Yes.** You must have 1m data in Historify first; then the replay server reads it and streams synthetic ticks. |

### Historify setup (so that replay has real data)

Historify stores OHLCV in a single shared DB, `db/historify.duckdb` (table `market_data`). In multi-instance setups (Kotak/Dhan/Zerodha), all instances use this same DB; one instance is enough to run download jobs (one pull). Data gets in by:

1. **Watchlist** — In the app, go to **Historify** (React `/historify`). Add symbols (e.g. NIFTY 50, NSE:NIFTY-INDEX, NFO symbols) to the watchlist.
2. **Download jobs** — Start a download job: symbol list + date range + interval (`1m` or `D`). The job uses your **broker** (via OpenAlgo `history_service` → broker's `get_history`) to fetch OHLCV from the broker API, then **upserts** into Historify. So: broker API → Historify DuckDB.
3. **Schedules** (optional) — Create a schedule to run downloads periodically (e.g. daily) so 1m/D data stays updated.
4. **Import** — You can also import from CSV or Parquet into Historify instead of using broker download.

For **mock replay** we only use **1m** data. So before using the Historify-based mock:

- Ensure 1m data exists for the symbols and date range you want to replay (run a download job or import for that range).

### Order of operations (Historify-based mock, when implemented)

1. **Pull real data into Historify first** — Use Historify UI: watchlist + download job (or schedule) for 1m (and optionally D). Wait until the job(s) complete so `market_data` has 1m bars for the symbols/dates you need.
2. **Start the mock replay server** — It will read from Historify (`get_ohlcv(..., "1m", start_ts, end_ts)`), generate synthetic ticks, and serve them on the mock WebSocket port (e.g. 8770).
3. **Point clients at the mock** — Set `WEBSOCKET_URL=ws://127.0.0.1:8770` (or use a Replay UI that connects to the mock) so charts/scalping consume replayed data instead of live.

So: **yes — for Historify-based mock you pull real data into Historify first, then start the mock WebSocket.** The current simple mock does not use Historify; you can run it anytime.

### Index vs CE/PE

- **Index (e.g. NIFTY 50, NSE_INDEX):** One 1m download in Historify is enough. Replay streams that as LTP ticks.
- **CE/PE options:** Option prices are not derivable from index level alone (they depend on strike, expiry, volatility). So either:
  - **Download CE/PE 1m** in Historify for the strikes you care about; the same replay server will then stream those symbols when subscribed, or
  - **Synthetic (optional later):** Add a rough option pricer (e.g. Black–Scholes with index as spot) to generate plausible but fake CE/PE from index data for testing only.

### CE/PE (options) vs index only

- **Index only (e.g. Nifty 1m):** We can replay the index as LTP ticks. No option contracts are streamed unless they have their own 1m data in Historify.
- **We do not derive CE/PE from index:** Option price depends on strike, expiry, volatility, etc. So we **cannot** mock realistic CE/PE LTP from Nifty index alone.
- **Two options:**
  1. **Download CE/PE 1m in Historify** for the strikes you care about (same as index: watchlist + download job for 1m). Then the replay server will stream those symbols too.
  2. **Synthetic option pricing (future):** We could add a rough Black‑Scholes (index = spot) so CE/PE get plausible but not real prices for testing UI/flows. Not implemented yet.

Recommendation: for after-hours testing with **index only**, use Historify replay with your Nifty index 1m data. For **options**, add the specific CE/PE symbols to Historify and download their 1m data, then replay will stream them.

---

## 1. Principles (Must Hold)

| Principle | Meaning |
|-----------|--------|
| **Real WS untouched** | Production WebSocket stays on `WEBSOCKET_PORT` (e.g. 8765). No code path in the live proxy is changed for mock. |
| **Separate mock port** | Mock replay server runs on a **dedicated port** that does **not** conflict with multi-instance setup: Kotak=8765, Dhan=8766, Zerodha=8767. Mock uses e.g. **8770** (`MOCK_WS_PORT`). Clients that want mock data connect explicitly to this URL. |
| **Historify as source** | Use existing Historify DuckDB (`db/historify.duckdb`, `market_data` 1m/D) as the **only** source of real OHLCV for replay. No duplicate storage of raw history. |
| **Opt-in only** | Mock is opt-in: user chooses “Mock replay” or connects to mock URL. Default app behaviour remains live broker + real WS. |
| **Charts and UI** | Mock data is used to drive **charts** (and optionally scalping/auto-trade UIs when in “replay mode”). Chart code can support a “replay” mode that subscribes to the mock server instead of the live WS. |

---

## 2. Data Flow (High Level)

```
┌─────────────────────┐     ┌──────────────────────────┐     ┌─────────────────────┐
│  Historify DuckDB   │     │  Mock Replay Service     │     │  Clients            │
│  db/historify.duckdb│────▶│  (separate process/port) │────▶│  Charts / Scalping  │
│  market_data (1m)   │     │  - Load 1m OHLCV        │     │  (when in Mock mode)│
└─────────────────────┘     │  - Apply regime          │     └─────────────────────┘
                            │  - Emit LTP “ticks”     │
                            │  Port: MOCK_WS_PORT     │
                            └──────────────────────────┘
                                         │
                            ┌────────────┴────────────┐
                            │  Optional: config DB    │
                            │  db/mock_replay.db      │
                            │  (saved sessions,       │
                            │   regime presets)      │
                            └────────────────────────┘
```

- **Read path:** Replay service reads 1m OHLCV from Historify via existing `historify_db.get_ohlcv(symbol, exchange, "1m", start_ts, end_ts)`.
- **No write to Historify:** Replay does not modify Historify. Optional small DB (e.g. SQLite) only for replay config/sessions if we want “save replay” or presets.

---

## 3. Historify as Source (What We Have)

- **DB:** `db/historify.duckdb`, table `market_data`.
- **Stored intervals:** `1m`, `D`. Other intervals (5m, 15m, etc.) are computed on-the-fly from 1m.
- **Columns:** `symbol`, `exchange`, `interval`, `timestamp` (epoch), `open`, `high`, `low`, `close`, `volume`, `oi`.
- **API:** `database.historify_db.get_ohlcv(symbol, exchange, interval, start_timestamp, end_timestamp)` → DataFrame.
- **Range:** `get_data_range(symbol, exchange, interval)` returns first/last timestamp and count.

Replay will use **1m** data only. From each 1m candle we can derive a synthetic tick stream (see below).

---

## 4. From 1m Candles to “Ticks” (Replay Semantics)

Historify does not store tick-by-tick LTP. We derive a **synthetic LTP stream** from 1m OHLCV:

- **Option A – One tick per candle:** Emit a single “tick” per 1m candle at `timestamp` with `ltp = close`. Simple; charts see step-wise moves every minute.
- **Option B – Intra-candle interpolation:** For each 1m candle, emit N ticks (e.g. 5–10) between `open` and `close` (e.g. linear or random walk inside the bar). Gives smoother charts and more “tick-like” behaviour for strategies that count ticks.
- **Option C – OHLC path:** Emit four ticks per candle: open → high → low → close (or a random permutation) with sub-minute timestamps. More realistic intra-bar movement.

**Recommendation:** Start with **Option B** (configurable N ticks per candle, linear or slight randomness). Option C can be a later enhancement.

Replay **speed** should be configurable (e.g. 1x real time, 5x, 10x, “as fast as possible”) by scaling the delay between emitted ticks.

---

## 5. Configurable “Mock Level” (Market Regimes)

User-visible “mock level” should map to **regime** parameters that we apply to the **same** Historify series (no need to store separate series per regime).

| User-facing “level” | Meaning | Implementation idea |
|---------------------|--------|----------------------|
| **Choppy** | Sideways, mean-reverting | Use date ranges that are sideways, or: smooth returns (e.g. blend close with previous close), add small noise. |
| **High volatility** | Large swings | Scale (high - low) or (close - open) by a factor &gt; 1; optionally scale time so “more happens” per minute. |
| **Low volatility** | Small range | Scale range down; or select a low-ATR period from Historify. |
| **High momentum** | Strong trend | Select a strong-trend date range from Historify; or apply trend filter (e.g. only use 1m bars where \|close - open\| &gt; threshold). |
| **Low momentum** | Weak trend | Opposite: use bars with small \|close - open\| or smooth the series. |
| **High VIX (proxy)** | Fear / big moves | Treat as “high volatility” + possibly more frequent large candles (e.g. bias selection toward high-range bars). |
| **Low VIX (proxy)** | Calm | Treat as “low volatility”. |

Implementation options:

- **Regime = date-range selector:** Pre-label some date ranges in config (e.g. “2024-01-15 NIFTY = high_vol”). Replay just picks that range; no transform. Simple but requires curation.
- **Regime = transform on the fly:** Take one chosen date range from Historify; apply a **transform** (volatility scale, smoothing, noise) before emitting ticks. One download supports many regimes.
- **Hybrid:** User picks symbol + date range from Historify (or “last N days”); then picks regime (choppy / high vol / low vol / high momentum / low momentum / high VIX / low VIX). Backend applies the corresponding transform to the 1m series, then replays.

A small **config store** (e.g. SQLite `db/mock_replay.db`) could hold:
- Preset names (e.g. “NIFTY high vol Jan 2024” → symbol, exchange, start_ts, end_ts, regime_id).
- Regime parameters (volatility_scale, momentum_smooth, etc.) so we don’t hardcode in code only.

---

## 6. Separation: Ports and Config

**Reserved ports (do not use for mock):**  
`START_BOTH.bat` and the per-broker run scripts use:
- **8765** — Kotak (port 5000)
- **8766** — Dhan (port 5001)
- **8767** — Zerodha (port 5002)

Mock must stay **separate** from these so that running Kotak/Dhan/Zerodha instances is unaffected.

- **Real WebSocket:** Unchanged. Multi-instance ports: Kotak 8765, Dhan 8766, Zerodha 8767. Each instance uses its own `WEBSOCKET_PORT` / `WEBSOCKET_URL`. Used by scalping/charts when not in mock mode.
- **Mock WebSocket:** Dedicated port **outside** the broker instance range, e.g. `MOCK_WS_PORT=8770` (env). Only the **mock replay server** listens here. Never use 8765/8766/8767 for mock.
- **App config:** Do **not** change `WEBSOCKET_URL` by default. Mock is used when:
  - User opens a dedicated “Replay” or “Mock” UI that explicitly connects to `ws://host:MOCK_WS_PORT`, or
  - User sets an optional env (e.g. `USE_MOCK_WS=1`) or a UI toggle that switches the scalping/chart WS URL to the mock server. Either way, it’s explicit and separate from the default.

Suggested env vars (all optional):

- `MOCK_WS_PORT` — port for mock replay server (default **8770**; must not be 8765/8766/8767, which are used by Kotak/Dhan/Zerodha).
- `MOCK_WS_HOST` — host (default 127.0.0.1).
- No change to `WEBSOCKET_URL` / `WEBSOCKET_PORT` for production.

---

## 7. Where Mock Data Is Consumed (Charts / Don’t Mess With Real WS)

- **Charts:** Today charts get live data from the same WebSocket proxy. To use mock data:
  - **Option 1:** A dedicated “Replay” or “Historify Replay” page that connects only to the mock server and renders charts from replayed ticks. Live chart code stays on real WS.
  - **Option 2:** Chart window (and optionally scalping) get a “Replay mode” toggle. When ON, they connect to `ws://host:MOCK_WS_PORT` (e.g. 8770) instead of `WEBSOCKET_URL`. When OFF, they use the real URL. So one code path, two connection targets; no change to the real WS server.
- **Scalping / Auto-trade:** If we want to test strategies on replayed data, same idea: in “Replay mode” the client connects to the mock port and receives the same message shape (`market_data` with `symbol`, `exchange`, `ltp`, etc.). No change to the real proxy.

We must **not**:
- Listen for mock on the same port as the real WS.
- Mix mock and live on the same connection.
- Change the real proxy’s behaviour based on a “mock” flag.

---

## 8. Optional: Persisting Replay Config (DB)

To avoid “mess” and keep replay logic separate, we can keep all replay state in a **small dedicated DB** (e.g. SQLite `db/mock_replay.db`), not in Historify:

- **Tables (example):**
  - `replay_presets`: id, name, symbol, exchange, start_ts, end_ts, regime (e.g. choppy, high_vol), speed_multiplier, created_at.
  - `replay_sessions`: id, preset_id, started_at, ended_at, client_id (optional).
- **Use:** Save/load replay sessions; list “last replayed” or “favourite” presets. Historify remains read-only for OHLCV.

This is optional for v1; we can start with in-memory or CLI/config-file only and add this DB when we add “Save replay” / “Resume” in the UI.

---

## 9. Implementation Phases (Suggested)

### Phase 1 – Replay server (no regimes yet)
- New service/script: **Mock Replay WebSocket server** (separate process or runnable by app).
- Listens on `MOCK_WS_PORT` only.
- Accepts: authenticate, subscribe (symbol list).
- **Data path:** For each subscribed symbol, call Historify `get_ohlcv(symbol, exchange, "1m", start_ts, end_ts)` for a configurable range (e.g. “last 1 day” or “this date”). Emit synthetic ticks (e.g. Option B: N ticks per candle) at configurable speed (1x, 5x, max).
- Message format: same as real proxy (`type: "market_data"`, `symbol`, `exchange`, `data.ltp`, `data.timestamp`).
- No change to Historify schema or to the real WS.

### Phase 2 – Regime parameters
- Add “regime” to the replay API: choppy, high_vol, low_vol, high_momentum, low_momentum, high_vix, low_vix.
- Implement transforms on the 1m series (scale volatility, smooth, add noise) before generating ticks.
- Optional: store presets in `db/mock_replay.db`.

### Phase 3 – UI for replay
- **Option A:** Dedicated “Historify Replay” or “Mock Replay” page: pick symbol + date range (from Historify catalog) + regime + speed → connect to mock WS → show chart (and optional LTP panel).
- **Option B:** Toggle on existing chart/scalping: “Use mock replay” which switches WS URL to mock server and shows a small “Replay config” (symbol, date, regime, speed).  
Prefer Option A first so we don’t touch existing chart/scalping connection logic; Option B can come later.

### Phase 4 (optional) – VIX proxy and presets
- If we have or add VIX (or India VIX) in Historify or elsewhere, use it to label “high VIX” / “low VIX” periods and offer them as presets. Otherwise keep “high VIX” = high volatility transform.
- Persist presets and last-used replay config in `mock_replay.db`.

---

## 10. File / Component Layout (Proposed)

Keep everything under a clear namespace so it’s obvious what’s mock vs live:

- **Service:** `services/mock_replay/` or `services/historify_replay/`
  - `replay_engine.py` — load 1m from Historify, apply regime, yield synthetic ticks.
  - `mock_ws_server.py` — WebSocket server on MOCK_WS_PORT; uses replay_engine; same message shape as real proxy.
- **Config (optional):** `database/mock_replay_db.py` + `db/mock_replay.db` for presets/sessions.
- **API (optional):** `blueprints/mock_replay.py` or under Historify blueprint: e.g. `GET /historify/replay/range?symbol=&exchange=` (returns available range from Historify), `POST /historify/replay/presets` (save/load from mock_replay DB). No impact on existing Historify or WS routes.
- **UI:** New page(s) under React or a simple HTML “Replay” page that connects to `ws://host:MOCK_WS_PORT` and configures symbol, date, regime, speed. Charts on that page consume only the mock WS.

Do **not** put mock logic inside `websocket_proxy/` or the main WS server.

---

## 11. Summary

| Item | Decision |
|------|----------|
| **Data source** | Historify DuckDB `market_data` 1m only (read-only). |
| **Tick generation** | Synthetic ticks from 1m OHLC (e.g. N ticks per candle, interpolated). |
| **Regimes** | Configurable: choppy, high/low vol, high/low momentum, high/low VIX (VIX as vol proxy if needed). Implement as transforms on the same 1m series. |
| **Port** | Mock on **8770** (or configurable `MOCK_WS_PORT`). Reserved: 8765=Kotak, 8766=Dhan, 8767=Zerodha — mock must not use these. |
| **Charts** | Use mock data by connecting charts to mock server (dedicated Replay page or “Replay mode” toggle). Don’t change real WS or live chart code paths for mock. |
| **Persistence** | Optional small DB for replay presets/sessions; Historify not modified. |
| **Phasing** | Phase 1: replay server + Historify 1m → ticks. Phase 2: regimes. Phase 3: UI. Phase 4: presets/VIX. |

This plan keeps mock replay fully separated from the real WebSocket and uses Historify as the single source of real data for replay, with configurable mock levels for testing different market conditions.
