#!/usr/bin/env python3
"""
Historify Replay WebSocket server: stream real 1m data from Historify as LTP ticks.

Use after you have downloaded 1m data into Historify (e.g. NSE index). Replays that
data in the same message shape as the live proxy so charts/scalping work after hours.

Usage (from project root):
    uv run python scripts/historify_replay_server.py

    # Optional env
    MOCK_WS_PORT=8770
    MOCK_REPLAY_SPEED=1.0       # 1=real time, 5=5x, 0=as fast as possible
    MOCK_REPLAY_START_DATE=    # YYYY-MM-DD (default: use full range from Historify)
    MOCK_REPLAY_END_DATE=      # YYYY-MM-DD

Then set WEBSOCKET_URL=ws://127.0.0.1:8770 in .env and restart Flask. Connect from
/scalping or Historify Charts; subscribe with the same symbol/exchange as in Historify
(e.g. NIFTY 50, NSE_INDEX). Only symbols that have 1m data in Historify will stream.
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime

try:
    import websockets
except ImportError:
    print("Install websockets: uv add websockets")
    raise SystemExit(1)

# Add project root so we can import database and services
import sys
from pathlib import Path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Load .env (e.g. .env.dhan when running with Dhan instance's Historify DB)
from dotenv import load_dotenv
load_dotenv()
load_dotenv(_root / ".env.dhan")

MOCK_WS_PORT = int(os.getenv("MOCK_WS_PORT", "8770"))
MOCK_REPLAY_SPEED = float(os.getenv("MOCK_REPLAY_SPEED", "1.0"))
MOCK_REPLAY_START_DATE = os.getenv("MOCK_REPLAY_START_DATE", "").strip()
MOCK_REPLAY_END_DATE = os.getenv("MOCK_REPLAY_END_DATE", "").strip()


def _parse_date(s: str):
    if not s:
        return None
    try:
        return int(datetime.strptime(s, "%Y-%m-%d").timestamp())
    except ValueError:
        return None


def _run_replay_worker(
    symbol: str,
    exchange: str,
    queue,
    loop,
    start_ts,
    end_ts,
    speed: float,
    regime: str = "none",
):
    """Run in a thread: iterate replay_ticks and put each msg into the asyncio queue."""
    from services.mock_replay.replay_engine import replay_ticks
    for msg in replay_ticks(
        symbol=symbol,
        exchange=exchange,
        start_ts=start_ts,
        end_ts=end_ts,
        speed=speed,
        regime=regime or "none",
    ):
        try:
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop).result(timeout=1.0)
        except Exception:
            break


def _run_index_plus_synthetic_worker(
    index_symbol: str,
    index_exchange: str,
    option_symbols: list,
    queue,
    loop,
    start_ts,
    end_ts,
    speed: float,
    regime: str,
):
    """
    One thread: replay index ticks (with regime) and for each tick emit synthetic CE/PE
    for option_symbols (no Historify data). option_symbols: list of (symbol, exchange) NFO.
    """
    from services.mock_replay.replay_engine import replay_ticks
    from services.mock_replay.synthetic_options import synthetic_option_price
    for msg in replay_ticks(
        symbol=index_symbol,
        exchange=index_exchange,
        start_ts=start_ts,
        end_ts=end_ts,
        speed=speed,
        regime=regime or "none",
    ):
        try:
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop).result(timeout=1.0)
            ts_ms = msg.get("data", {}).get("timestamp", 0)
            spot = msg.get("data", {}).get("ltp", 0)
            ts_sec = ts_ms // 1000
            for opt_sym, opt_exch in option_symbols:
                premium = synthetic_option_price(spot, opt_sym, ts_sec)
                if premium is not None:
                    opt_msg = {
                        "type": "market_data",
                        "symbol": opt_sym,
                        "exchange": opt_exch or "NFO",
                        "mode": 1,
                        "data": {"ltp": premium, "timestamp": ts_ms},
                        "broker": "historify_replay",
                    }
                    asyncio.run_coroutine_threadsafe(queue.put(opt_msg), loop).result(timeout=1.0)
        except Exception:
            break


def _normalize_subscription(symbol, exchange):
    """Map common names to Historify catalog style. Return (symbol, exchange) for DB lookup."""
    s = (symbol or "").strip().upper()
    e = (exchange or "").strip().upper()
    if s in ("NIFTY", "NIFTY 50", "NIFTY50", "NIFTY-INDEX"):
        return ("NIFTY", e or "NSE_INDEX")
    if s in ("SENSEX", "BSE SENSEX"):
        return ("SENSEX", e or "BSE_INDEX")
    if s in ("BANKNIFTY", "BANK NIFTY"):
        return ("BANK NIFTY", e or "NSE_INDEX")
    return (s or symbol, e or "NSE_INDEX")


async def handle_client(ws):
    """Handle one WebSocket client: auth + subscribe, then stream Historify replay."""
    client_id = id(ws)
    print(f"[historify-replay] Client connected: {client_id}")
    queue = asyncio.Queue()
    workers = []
    loop = asyncio.get_event_loop()
    # Track accumulated subscriptions across multiple subscribe messages
    pending_synthetic = []  # option symbols waiting for an index to drive them
    active_index = None     # (db_symbol, db_exchange, first_ts, last_ts)

    async def sender():
        try:
            while True:
                msg = await queue.get()
                await ws.send(json.dumps(msg))
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass

    sender_task = asyncio.create_task(sender())

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
                    "broker": "historify_replay",
                    "user_id": "replay_user",
                }))
                continue

            if action == "subscribe":
                symbols = data.get("symbols") or data.get("instruments") or []
                if symbols and isinstance(symbols[0], dict):
                    sub_list = [(s.get("symbol"), s.get("exchange")) for s in symbols if s.get("symbol")]
                else:
                    sub_list = [
                        (s.get("symbol", s) if isinstance(s, dict) else s, None)
                        for s in symbols
                    ]
                regime = (data.get("regime") or "").strip().lower() or "none"
                speed = float(data.get("speed") or MOCK_REPLAY_SPEED)
                start_date = data.get("start_date") or MOCK_REPLAY_START_DATE
                end_date = data.get("end_date") or MOCK_REPLAY_END_DATE
                start_ts = _parse_date(start_date) if start_date else _parse_date(MOCK_REPLAY_START_DATE)
                end_ts = _parse_date(end_date) if end_date else _parse_date(MOCK_REPLAY_END_DATE)

                from services.mock_replay.replay_engine import get_replay_range
                from services.mock_replay.synthetic_options import parse_option_symbol

                index_with_data = []  # (db_symbol, db_exchange, first_ts, last_ts)
                option_with_data = []
                option_synthetic = []  # (raw_symbol, exchange) for NFO without data

                for sym, exch in sub_list:
                    e = (exch or "").strip().upper()
                    is_nfo = e == "NFO" or "NFO" in (e or "")
                    if is_nfo:
                        parsed = parse_option_symbol(sym)
                        if parsed:
                            r = get_replay_range(sym, e or "NFO")
                            if r and r.get("first_timestamp"):
                                option_with_data.append((sym, e or "NFO", r.get("first_timestamp"), r.get("last_timestamp")))
                            else:
                                option_synthetic.append((sym, e or "NFO"))
                        continue
                    db_symbol, db_exchange = _normalize_subscription(sym, exch)
                    r = get_replay_range(db_symbol, db_exchange)
                    if not r and db_symbol == "NIFTY 50":
                        r = get_replay_range("NIFTY-INDEX", db_exchange)
                        if r:
                            db_symbol = "NIFTY-INDEX"
                    if not r:
                        continue
                    first_ts = r.get("first_timestamp")
                    last_ts = r.get("last_timestamp")
                    if first_ts and last_ts:
                        index_with_data.append((db_symbol, db_exchange, first_ts, last_ts))

                # Accumulate synthetic options across subscribe calls
                if option_synthetic:
                    pending_synthetic.extend(option_synthetic)
                if index_with_data:
                    active_index = index_with_data[0]

                # Combine: if we have pending synthetics from earlier subscribe + index from this one (or vice versa)
                all_synthetic = option_synthetic + ([s for s in pending_synthetic if s not in option_synthetic] if not option_synthetic else [])
                effective_index = index_with_data[0] if index_with_data else active_index

                if index_with_data:
                    all_first = min(r[2] for r in index_with_data)
                    all_last = max(r[3] for r in index_with_data)
                else:
                    all_first = all_last = None
                ts_start = start_ts if start_ts is not None else all_first
                ts_end = end_ts if end_ts is not None else all_last
                replay_count = 0

                # Start index+synthetic worker if we have both
                if effective_index and all_synthetic:
                    db_sym, db_ex, first_ts, last_ts = effective_index
                    a = ts_start if ts_start is not None else first_ts
                    b = ts_end if ts_end is not None else last_ts
                    if a is not None and b is not None and a <= last_ts and b >= first_ts:
                        t = threading.Thread(
                            target=_run_index_plus_synthetic_worker,
                            args=(db_sym, db_ex, all_synthetic, queue, loop, a, b, speed, regime),
                            daemon=True,
                        )
                        t.start()
                        workers.append(t)
                        replay_count += 1 + len(all_synthetic)
                        pending_synthetic.clear()
                        print(f"[historify-replay] Index {db_sym} + {len(all_synthetic)} synthetic CE/PE (regime={regime})")
                # Stream standalone indexes (skip the one used for synthetic)
                for i, (db_sym, db_ex, first_ts, last_ts) in enumerate(index_with_data):
                    if effective_index and all_synthetic and (db_sym, db_ex) == (effective_index[0], effective_index[1]):
                        continue  # already streaming via _run_index_plus_synthetic_worker
                    a = ts_start if ts_start is not None else first_ts
                    b = ts_end if ts_end is not None else last_ts
                    if a is not None and b is not None and a <= last_ts and b >= first_ts:
                        t = threading.Thread(
                            target=_run_replay_worker,
                            args=(db_sym, db_ex, queue, loop, a, b, speed, regime),
                            daemon=True,
                        )
                        t.start()
                        workers.append(t)
                        replay_count += 1
                        print(f"[historify-replay] Replaying {db_sym} {db_ex} (regime={regime})")
                for opt_sym, opt_ex, first_ts, last_ts in option_with_data:
                    a, b = (ts_start if ts_start is not None else first_ts), (ts_end if ts_end is not None else last_ts)
                    if a <= last_ts and (b is None or b >= first_ts):
                        t = threading.Thread(
                            target=_run_replay_worker,
                            args=(opt_sym, opt_ex, queue, loop, a, b, speed, regime),
                            daemon=True,
                        )
                        t.start()
                        workers.append(t)
                        replay_count += 1
                        print(f"[historify-replay] Replaying option {opt_sym} from Historify")

                await ws.send(json.dumps({
                    "status": "subscribed",
                    "count": replay_count,
                    "regime": regime,
                    "message": f"Replaying {replay_count} stream(s), regime={regime}",
                }))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
        print(f"[historify-replay] Client disconnected: {client_id}")


async def main():
    host = "127.0.0.1"
    print(f"Historify Replay WebSocket server: ws://{host}:{MOCK_WS_PORT}")
    print("Streams real 1m data from Historify (db/historify.duckdb).")
    print("Speed:", MOCK_REPLAY_SPEED if MOCK_REPLAY_SPEED > 0 else "max (no pacing)")
    if MOCK_REPLAY_START_DATE or MOCK_REPLAY_END_DATE:
        print("Date filter:", MOCK_REPLAY_START_DATE or "(start)", "to", MOCK_REPLAY_END_DATE or "(end)")
    print("Set WEBSOCKET_URL=ws://127.0.0.1:8770 and restart Flask to use replay.")
    async with websockets.serve(handle_client, host, MOCK_WS_PORT, ping_interval=20, ping_timeout=20):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
