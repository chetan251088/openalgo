# Scalping Unified Ops Checklist

Last updated: 2026-02-13
Scope: `/scalping-unified` multi-broker feed/execute model

## 1) Daily Startup Checklist

1. Start all broker instances you want available in selectors.
2. Verify listeners are up for Flask + WS ports.
3. Login once on each running instance.
4. Generate/confirm OpenAlgo API key on each instance `/apikey`.
5. Open `/scalping-unified` on any one instance.
6. Set `Feed`:
   - `Auto (Zerodha -> Dhan)` or explicit `Zerodha`/`Dhan`
7. Set `Exec`:
   - `Kotak` or `Dhan` or `Zerodha`
8. Confirm behavior:
   - option chain/charts follow `Feed`
   - orders/positions/P&L follow `Exec`
9. For TOMIC agents, choose one control instance (recommended Zerodha `:5002`) and keep loop enabled only there.

## 1A) Data Path Clarification (Unified Route)

1. Feed selector drives WebSocket source for live chart ticks via `/api/multibroker/ws-config`.
2. Feed selector also drives REST reads for option chain/expiry/history/multiquotes via `/api/multibroker/v1`.
3. Exec selector drives REST trading endpoints (place/modify/cancel, positionbook, orderbook, tradebook, funds) via `/api/multibroker/v1`.
4. Market orders are routed directly to execution broker; no cross-broker quote pre-check is required.

## 1B) TOMIC Control-Instance Checklist

1. Open TOMIC dashboards from one control instance:
   - `/tomic/dashboard`
   - `/tomic/agents`
   - `/tomic/risk`
2. Keep `TOMIC_SIGNAL_LOOP_ENABLED='true'` only on control instance.
3. Set `TOMIC_SIGNAL_LOOP_ENABLED='false'` on other running broker instances.
4. Ensure TOMIC keys exist in each `.env.*`:
   - `TOMIC_FEED_PRIMARY_WS`, `TOMIC_FEED_FALLBACK_WS`
   - `TOMIC_FEED_PRIMARY_API_KEY`, `TOMIC_FEED_FALLBACK_API_KEY`
   - `TOMIC_EXECUTION_REST`, `TOMIC_EXECUTION_API_KEY`, `TOMIC_ANALYTICS_REST`
5. Restart instances after env edits.

## 2) Quick Verification Commands

From each instance base URL:

1. `GET /api/multibroker/config`
2. `POST /api/multibroker/ws-config`
3. `POST /api/multibroker/v1`
4. `GET /tomic/status`
5. `GET /tomic/metrics`
6. `GET /tomic/signals/quality`

Expected:

1. `401` when not logged in.
2. `200` after login with valid session.
3. No `404` for `/api/multibroker/*` routes.
4. TOMIC status should show expected loop state for that instance.

## 3) New Broker Onboarding Checklist

When adding a new broker to unified selectors, complete all steps:

1. Assign unique ports:
   - Flask port
   - WebSocket port
   - ZMQ/stream port (if applicable)
2. Create broker env file:
   - `HOST_SERVER`
   - `FLASK_PORT`
   - `SESSION_COOKIE_NAME` (must be unique)
   - broker-specific credentials
   - broker-specific DB paths (recommended)
3. Add/verify run script:
   - `run_<broker>.bat` or equivalent
4. Ensure broker instance exposes:
   - `/api/v1/*` trading + market endpoints used by scalping
   - `/api/websocket/apikey`
   - `/api/websocket/config`
5. Register broker in backend unified proxy:
   - `blueprints/multi_broker.py`
   - add to `BROKER_IDS`
   - add to `DEFAULT_BROKER_URLS`
   - add Flask->WS port mapping if non-standard
6. Register broker in frontend unified store/UI:
   - `frontend/src/stores/multiBrokerStore.ts`
   - `frontend/src/components/scalping/TopBar.tsx`
7. Verify API key flow:
   - login on new broker instance
   - generate key on `/apikey`
   - confirm `/api/websocket/apikey` returns success
8. Build + restart:
   - `cd frontend && npm run build`
   - restart all running broker instances
9. Smoke test in `/scalping-unified`:
   - select new broker as `Exec`, place/cancel/modify order
   - if feed-capable, select as `Feed`, verify charts/chain
   - confirm positions/P&L alignment
10. Wire new broker for TOMIC role routing:
   - add correct feed/execution URLs in that broker `.env.*`
   - generate feed/execution API keys via target instance `/apikey`
   - if used as control instance, enable signal loop only there

## 4) Common Failures

1. `404 /api/multibroker/*`
   - running instance is stale; restart using updated code
2. `API key unavailable for <broker>` from `/api/multibroker/ws-config`
   - feed target broker missing login/API key; login and generate `/apikey` on that target
3. Feed connected but no chart/chain data
   - wrong WS mapping or unavailable target WS process
4. Orders placed on wrong broker
   - check current `Exec` selector and confirm request payload in network tab
5. Unified orders fail with `Invalid openalgo apikey`
   - target broker missing session or API key; login and regenerate `/apikey` on that target instance
6. Duplicate autonomous signals/trades from TOMIC
   - multiple instances have signal loop enabled; keep one control instance active
