import importlib
import os
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from database.auth_db import get_auth_token_broker
from database.token_db import get_token
from utils.constants import VALID_EXCHANGES
from utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Rate limiter: max 3 broker history API requests per second
# Uses minimum interval between calls to prevent burst requests
_last_history_call: float = 0.0
_MIN_HISTORY_INTERVAL = 0.35  # 350ms between calls (~3 req/sec, evenly spaced)
_HISTORY_RATE_LOCK = threading.Lock()
_BROKER_MIN_HISTORY_INTERVAL = {
    "zerodha": float(os.getenv("ZERODHA_HISTORY_MIN_INTERVAL", "1.5")),
}
_HISTORY_RETRY_ATTEMPTS = max(1, int(os.getenv("HISTORY_RETRY_ATTEMPTS", "3")))
_HISTORY_RETRY_BASE_DELAY = float(os.getenv("HISTORY_RETRY_BASE_DELAY", "1.5"))


def _enforce_rate_limit(broker: str | None = None):
    """Block until enough time has passed since the last request (~3 per second).

    Uses slot-reservation pattern: the lock is held only long enough to read and
    advance _last_history_call, then released before sleeping.  This lets multiple
    worker threads sleep concurrently (pipelined) instead of serialising behind the
    lock, which previously turned every sleep into a stall for all other threads.
    """
    global _last_history_call
    broker_key = (broker or "").strip().lower()
    min_interval = max(_MIN_HISTORY_INTERVAL, _BROKER_MIN_HISTORY_INTERVAL.get(broker_key, 0.0))

    with _HISTORY_RATE_LOCK:
        now = time.monotonic()
        # Determine when this call is allowed to proceed
        ready_at = max(now, _last_history_call + min_interval)
        # Reserve this slot so the next caller queues after it
        _last_history_call = ready_at
        sleep_secs = ready_at - now

    # Sleep OUTSIDE the lock so other threads can reserve their own slots concurrently
    if sleep_secs > 0:
        time.sleep(sleep_secs)


def _is_history_rate_limit_error(error: Exception | str) -> bool:
    message = str(error).lower()
    return "too many requests" in message or "rate limit" in message or "429" in message


def validate_symbol_exchange(symbol: str, exchange: str) -> tuple[bool, str | None]:
    """
    Validate that a symbol exists for the given exchange.

    Args:
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, NFO)

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Validate exchange
    exchange_upper = exchange.upper()
    if exchange_upper not in VALID_EXCHANGES:
        return False, f"Invalid exchange '{exchange}'. Must be one of: {', '.join(VALID_EXCHANGES)}"

    # Validate symbol exists in master contract
    token = get_token(symbol, exchange_upper)
    if token is None:
        return (
            False,
            f"Symbol '{symbol}' not found for exchange '{exchange}'. Please verify the symbol name and ensure master contracts are downloaded.",
        )

    return True, None


def import_broker_module(broker_name: str) -> Any | None:
    """
    Dynamically import the broker-specific data module.

    Args:
        broker_name: Name of the broker

    Returns:
        The imported module or None if import fails
    """
    try:
        module_path = f"broker.{broker_name}.api.data"
        broker_module = importlib.import_module(module_path)
        return broker_module
    except ImportError as error:
        logger.error(f"Error importing broker module '{module_path}': {error}")
        return None


def get_history_with_auth(
    auth_token: str,
    feed_token: str | None,
    broker: str,
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    strict_symbol_validation: bool = False,
) -> tuple[bool, dict[str, Any], int]:
    """
    Get historical data for a symbol using provided auth tokens.

    Args:
        auth_token: Authentication token for the broker API
        feed_token: Feed token for market data (if required by broker)
        broker: Name of the broker
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, BSE)
        interval: Time interval (e.g., 1m, 5m, 15m, 1h, 1d)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        strict_symbol_validation: If True, reject when symbol is not found in local token DB.
            If False (default), continue and let broker validate symbol.

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # Validate symbol/exchange before broker API call.
    # Keep this non-blocking by default because token DB can lag behind live contracts,
    # which would otherwise break legitimate history loads in charts.
    is_valid, error_msg = validate_symbol_exchange(symbol, exchange)
    if not is_valid:
        if strict_symbol_validation:
            return False, {"status": "error", "message": error_msg}, 400
        logger.warning(
            "History validation warning (continuing with broker request): "
            f"symbol={symbol}, exchange={exchange}, reason={error_msg}"
        )

    broker_module = import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        # Initialize broker's data handler based on broker's requirements
        if hasattr(broker_module.BrokerData.__init__, "__code__"):
            # Check number of parameters the broker's __init__ accepts
            param_count = broker_module.BrokerData.__init__.__code__.co_argcount
            if param_count > 2:  # More than self and auth_token
                data_handler = broker_module.BrokerData(auth_token, feed_token)
            else:
                data_handler = broker_module.BrokerData(auth_token)
        else:
            # Fallback to just auth token if we can't inspect
            data_handler = broker_module.BrokerData(auth_token)

        # Call the broker's get_history method with retry/backoff on broker throttling
        last_error = None
        for attempt in range(1, _HISTORY_RETRY_ATTEMPTS + 1):
            try:
                df = data_handler.get_history(symbol, exchange, interval, start_date, end_date)
                break
            except Exception as e:
                last_error = e
                if attempt < _HISTORY_RETRY_ATTEMPTS and _is_history_rate_limit_error(e):
                    delay = _HISTORY_RETRY_BASE_DELAY * attempt
                    logger.warning(
                        "Broker history rate limited for %s %s:%s %s. Retrying in %.2fs (%d/%d)",
                        broker,
                        exchange,
                        symbol,
                        interval,
                        delay,
                        attempt,
                        _HISTORY_RETRY_ATTEMPTS,
                    )
                    _enforce_rate_limit(broker)
                    time.sleep(delay)
                    continue
                raise

        if last_error and "df" not in locals():
            raise last_error

        if not isinstance(df, pd.DataFrame):
            raise ValueError("Invalid data format returned from broker")

        # Ensure all responses include 'oi' field, set to 0 if not present
        if "oi" not in df.columns:
            df["oi"] = 0

        return True, {"status": "success", "data": df.to_dict(orient="records")}, 200
    except Exception as e:
        logger.error(f"Error in broker_module.get_history: {e}")
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_history_from_db(
    symbol: str, exchange: str, interval: str, start_date: str, end_date: str
) -> tuple[bool, dict[str, Any], int]:
    """
    Get historical data from DuckDB/Historify database.

    Args:
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, BSE)
        interval: Time interval (e.g., 1m, 5m, 15m, 1h, D, W, M, Q, Y)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    try:
        from datetime import date, datetime

        from database.historify_db import get_ohlcv

        # Convert dates to timestamps (handle both string and date objects)
        if isinstance(start_date, date):
            start_dt = datetime.combine(start_date, datetime.min.time())
        else:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")

        if isinstance(end_date, date):
            end_dt = datetime.combine(end_date, datetime.min.time())
        else:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Set end_date to end of day
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

        start_timestamp = int(start_dt.timestamp())
        end_timestamp = int(end_dt.timestamp())

        # Get data from DuckDB
        df = get_ohlcv(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )

        if df.empty:
            return (
                False,
                {
                    "status": "error",
                    "message": f"No data found for {symbol}:{exchange} interval {interval} in local database. Download data first using Historify.",
                },
                404,
            )

        # Ensure 'oi' column exists
        if "oi" not in df.columns:
            df["oi"] = 0

        # Reorder columns to match API response format
        columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
        df = df[columns]

        return True, {"status": "success", "data": df.to_dict(orient="records")}, 200

    except Exception as e:
        logger.error(f"Error fetching history from DB: {e}")
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_history(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    api_key: str | None = None,
    auth_token: str | None = None,
    feed_token: str | None = None,
    broker: str | None = None,
    source: str = "api",
) -> tuple[bool, dict[str, Any], int]:
    """
    Get historical data for a symbol.
    Supports both API-based authentication and direct internal calls.

    Args:
        symbol: Trading symbol
        exchange: Exchange (e.g., NSE, BSE)
        interval: Time interval (e.g., 1m, 5m, 15m, 1h, D, W, M, Q, Y)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        api_key: OpenAlgo API key (for API-based calls)
        auth_token: Direct broker authentication token (for internal calls)
        feed_token: Direct broker feed token (for internal calls)
        broker: Direct broker name (for internal calls)
        source: Data source - 'api' (broker, default) or 'db' (DuckDB/Historify)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # Source: 'db' - Fetch from DuckDB/Historify database
    if source == "db":
        return get_history_from_db(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

    # Case 1: API-based authentication
    if api_key and not (auth_token and broker):
        AUTH_TOKEN, FEED_TOKEN, broker_name = get_auth_token_broker(
            api_key, include_feed_token=True
        )
        if AUTH_TOKEN is None:
            return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403
        _enforce_rate_limit(broker_name)
        return get_history_with_auth(
            AUTH_TOKEN, FEED_TOKEN, broker_name, symbol, exchange, interval, start_date, end_date
        )

    # Case 2: Direct internal call with auth_token and broker
    elif auth_token and broker:
        _enforce_rate_limit(broker)
        return get_history_with_auth(
            auth_token, feed_token, broker, symbol, exchange, interval, start_date, end_date
        )

    # Case 3: Invalid parameters
    else:
        return (
            False,
            {
                "status": "error",
                "message": "Either api_key or both auth_token and broker must be provided",
            },
            400,
        )
