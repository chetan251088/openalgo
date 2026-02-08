# OpenAlgo Multi-Instance Setup Guide

This setup allows you to run **Kotak** and **Dhan** broker instances simultaneously for performance comparison.

## Files Created

| File | Purpose |
|------|---------|
| `.env.kotak` | Kotak broker configuration (Port 5000) |
| `.env.dhan` | Dhan broker configuration (Port 5001) - **NEEDS CREDENTIALS** |
| `run_kotak.bat` | Launcher for Kotak instance |
| `run_dhan.bat` | Launcher for Dhan instance |
| `START_BOTH.bat` | Launch both instances at once |
| `SETUP_SECOND_INSTANCE.bat` | Interactive setup guide |

## Quick Start (3 Steps)

### Step 1: Get Dhan API Credentials

1. Login to [Dhan Web Platform](https://web.dhan.co/)
2. Navigate to: **Settings → API Management**
3. Copy your **Client ID** and **Access Token**
4. If you don't have an access token, create one

### Step 2: Update Dhan Configuration

Edit `.env.dhan` and replace these lines:

```env
BROKER_API_KEY = 'YOUR_DHAN_CLIENT_ID'
BROKER_API_SECRET = 'YOUR_DHAN_ACCESS_TOKEN'
```

With your actual credentials:

```env
BROKER_API_KEY = '1100123456'
BROKER_API_SECRET = 'eyJ0eXAiOiJKV1QiLCJh...'
```

### Step 3: Run Both Instances

**Option A: Launch Both at Once**
```batch
START_BOTH.bat
```

**Option B: Launch Separately**

Open **Terminal 1**:
```batch
run_kotak.bat
```

Open **Terminal 2**:
```batch
run_dhan.bat
```

## Access Points

### Kotak Instance (Port 5000)
- **Web UI**: http://127.0.0.1:5000
- **WebSocket**: ws://127.0.0.1:8765
- **ZeroMQ**: tcp://127.0.0.1:5555
- **Database**: `db/openalgo.db`

### Dhan Instance (Port 5001)
- **Web UI**: http://127.0.0.1:5001
- **WebSocket**: ws://127.0.0.1:8766
- **ZeroMQ**: tcp://127.0.0.1:5556
- **Database**: `db/openalgo_dhan.db`

## Complete Isolation

Each instance is completely isolated:

| Component | Kotak | Dhan |
|-----------|-------|------|
| Flask Port | 5000 | 5001 |
| WebSocket Port | 8765 | 8766 |
| ZeroMQ Port | 5555 | 5556 |
| Main Database | `openalgo.db` | `openalgo_dhan.db` |
| Logs Database | `logs.db` | `logs_dhan.db` |
| Latency Database | `latency.db` | `latency_dhan.db` |
| Sandbox Database | `sandbox.db` | `sandbox_dhan.db` |
| Historify Database | **Shared:** `db/historify.duckdb` (see below) | **Shared:** `db/historify.duckdb` |
| Session Cookie | `session` | `session_dhan` |
| CSRF Cookie | `csrf_token` | `csrf_token_dhan` |

### Historify: one DB, one API pull

Market data (OHLCV) is the same regardless of broker — it comes from the exchange. So:

- **One DB:** All instances (Kotak, Dhan, Zerodha) use the same Historify database: `db/historify.duckdb`. No need for separate `historify_kotak.duckdb`, `historify_dhan.duckdb`, etc. The bootstrap and default config use this shared path.
- **One pull:** Run Historify download jobs from **one** instance only (e.g. Kotak on port 5000). That instance uses its broker API to fetch 1m/D data and write to the shared DuckDB. The other instances just read from the same DB for charts and replay. Running the same download on all three would duplicate the same data and waste broker API calls.

To use a shared Historify DB, set the same path in each instance’s env (e.g. `HISTORIFY_DATABASE_URL = 'db/historify.duckdb'` or `HISTORIFY_DATABASE_PATH`). Historify API (watchlist, jobs, catalog) is still available on each instance; only the **storage** is shared.

## Performance Comparison Guide

### 1. Login to Both Instances

- Kotak: http://127.0.0.1:5000
- Dhan: http://127.0.0.1:5001

(You'll need to login separately to each instance)

### 2. Open Chart Trading Windows

**Kotak:**
```
http://127.0.0.1:5000/scalping
```

**Dhan:**
```
http://127.0.0.1:5001/scalping
```

### 3. Test Same Contract

Use identical contract on both:
- Symbol: NIFTY
- Expiry: Same expiry date
- Strike: Same strike (e.g., 24500)
- Type: Same option type (CE/PE)

### 4. Compare Metrics

| Metric | What to Measure |
|--------|----------------|
| **Order Placement** | Time from click to order confirmation |
| **Chart Line Display** | Time from order to line appearing on chart |
| **Order Cancellation** | Time from cancel click to order removal |
| **WebSocket Latency** | Price update speed and consistency |
| **API Errors** | WinError 10054, HTTP 500, timeouts |
| **Fill Speed** | Time from order placed to order filled |

### 5. Monitor Logs

Check console logs for:
- API response times
- Connection errors
- Failed fetch attempts
- Timeout occurrences

## Troubleshooting

### Port Already in Use

```
Error: Address already in use: 5000
```

**Solution**: Close the other instance first, or change port in `.env` file

### WebSocket Connection Failed

```
Error: WebSocket connection to 'ws://127.0.0.1:8766/' failed
```

**Solution**: 
1. Make sure Flask app is running
2. Check firewall settings
3. Verify WebSocket port in `.env` matches

### Database Locked

```
Error: database is locked
```

**Solution**: Each instance uses separate databases, but if this occurs:
1. Close the affected instance
2. Delete the `.db-shm` and `.db-wal` files
3. Restart the instance

### Wrong Broker Selected

If you see Kotak UI when expecting Dhan (or vice versa):

1. Check which terminal is running
2. Verify the `.env` file content
3. Restart the correct batch file

## Switching Between Brokers

To switch from Kotak to Dhan (or vice versa):

1. **Stop** the current instance (Ctrl+C in terminal)
2. **Run** the other batch file
3. The batch file automatically switches the `.env` configuration

## Notes

- You can run both instances **simultaneously** (recommended for comparison)
- Or run them **one at a time** (switch between brokers)
- Each instance requires **separate login**
- Each instance has its own **orderbook**, **positions**, and **history**
- API keys are **instance-specific** (regenerate after login)

## Files Reference

All configuration files are in the root directory:

```
C:\algo\openalgov2\openalgo\
├── .env              ← Current active config (auto-switched by batch files)
├── .env.kotak        ← Kotak configuration
├── .env.dhan         ← Dhan configuration (UPDATE THIS!)
├── run_kotak.bat     ← Start Kotak instance
├── run_dhan.bat      ← Start Dhan instance
└── START_BOTH.bat    ← Start both instances
```

## Security Notes

- Keep your `.env.dhan` file secure (contains API credentials)
- Both `.env.kotak` and `.env.dhan` use the same `APP_KEY` and `API_KEY_PEPPER`
- Each instance has separate user sessions (cookies)
- Login credentials are **not shared** between instances

## Next Steps

1. ✅ Files created
2. ⏳ **Update `.env.dhan` with Dhan credentials** ← YOU ARE HERE
3. ⏳ Run `START_BOTH.bat` or individual batch files
4. ⏳ Login to both instances
5. ⏳ Compare broker performance

---

**Need Help?**
- Run `SETUP_SECOND_INSTANCE.bat` for interactive guide
- Check console logs for detailed error messages
- Ensure you have valid Dhan API credentials
