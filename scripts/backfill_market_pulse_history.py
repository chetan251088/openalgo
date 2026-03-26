#!/usr/bin/env python
"""
One-time backfill script for Market Pulse historical data.

Populates historify.duckdb with daily OHLCV for all symbols used by Market Pulse:
  - 5 indices (NIFTY, SENSEX, BANKNIFTY, INDIAVIX, FINNIFTY)
  - 12 sector indices
  - 50 Nifty constituents

Usage:
    uv run python scripts/backfill_market_pulse_history.py
    uv run python scripts/backfill_market_pulse_history.py --days 420
    uv run python scripts/backfill_market_pulse_history.py --api-key YOUR_KEY

After running, set MARKET_PULSE_HISTORY_SOURCE=db in .env for fast local reads.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.historify_db import upsert_market_data
from services.market_pulse_config import INDEX_SYMBOLS, SECTOR_INDICES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _load_constituents() -> list[dict]:
    base = Path(__file__).resolve().parent.parent
    path = base / "data" / "nifty50_constituents.json"
    with open(path) as f:
        data = json.load(f)
    return data.get("constituents", [])


def _get_api_key(cli_key: str | None = None) -> str:
    """Get OpenAlgo API key: CLI arg > env var > .env file."""
    if cli_key:
        return cli_key

    key = os.getenv("APP_KEY")
    if key:
        return key

    # Try loading from .env file
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("APP_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        return key

    raise RuntimeError(
        "API key not found. Set APP_KEY in .env, or pass --api-key"
    )


def _build_symbol_list() -> list[tuple[str, str, str, int]]:
    """Build (label, symbol, exchange, days) list for all Market Pulse symbols."""
    symbols = []

    # Indices — need longer history for MAs and percentiles
    for key, info in INDEX_SYMBOLS.items():
        days = 420 if key != "FINNIFTY" else 180
        symbols.append((f"index:{key}", info["symbol"], info["exchange"], days))

    # Sector indices
    for key, info in SECTOR_INDICES.items():
        symbols.append((f"sector:{key}", info["symbol"], info["exchange"], 80))

    # Nifty 50 constituents
    for item in _load_constituents():
        symbols.append((
            f"constituent:{item['symbol']}",
            item["symbol"],
            item["exchange"],
            420,
        ))

    return symbols


def backfill(days_override: int | None = None, api_key_override: str | None = None) -> None:
    """Fetch and store daily history for all Market Pulse symbols."""
    from services.history_service import get_history

    api_key = _get_api_key(api_key_override)
    symbols = _build_symbol_list()

    logger.info("Backfilling %d symbols into historify.duckdb", len(symbols))

    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, (label, symbol, exchange, default_days) in enumerate(symbols, 1):
        days = days_override or default_days
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        logger.info(
            "[%d/%d] Fetching %s (%s:%s) — %d days",
            i, len(symbols), label, symbol, exchange, days,
        )

        try:
            ok, data, status_code = get_history(
                symbol=symbol,
                exchange=exchange,
                interval="D",
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                api_key=api_key,
                source="api",  # Force broker API (not DuckDB)
            )

            if not ok or data.get("status") != "success":
                msg = data.get("message", f"HTTP {status_code}") if isinstance(data, dict) else str(data)
                logger.warning("  FAILED: %s", msg)
                fail_count += 1
                continue

            candles = data.get("data", [])
            if not candles:
                logger.warning("  SKIP: no candles returned")
                skip_count += 1
                continue

            df = pd.DataFrame(candles)
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")

            rows = upsert_market_data(df, symbol, exchange, "D")
            logger.info("  OK: %d candles, %d upserted", len(candles), rows)
            success_count += 1

        except Exception as e:
            logger.error("  ERROR: %s", e)
            fail_count += 1

        # Rate limit — be gentle with broker API
        if i % 10 == 0:
            logger.info("  (pausing 2s to respect rate limits)")
            time.sleep(2)
        else:
            time.sleep(0.3)

    logger.info(
        "Backfill complete: %d success, %d failed, %d skipped (total: %d)",
        success_count, fail_count, skip_count, len(symbols),
    )
    if success_count > 0:
        logger.info(
            "Next step: set MARKET_PULSE_HISTORY_SOURCE=db in your .env file"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill Market Pulse history into DuckDB"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override lookback days for all symbols (default: per-symbol optimal)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="OpenAlgo API key (default: reads APP_KEY from .env)",
    )
    args = parser.parse_args()
    backfill(days_override=args.days, api_key_override=args.api_key)
