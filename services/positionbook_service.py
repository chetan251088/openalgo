import importlib
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

from database.auth_db import get_auth_token_broker
from utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)


def _extract_position_rows(raw_payload: Any) -> list[dict[str, Any]]:
    """
    Normalize broker position payloads to a list of rows.

    Brokers are inconsistent: some return list directly, some wrap under
    keys like `data` / `positions`, and some return empty objects for no data.
    """
    if isinstance(raw_payload, list):
        return [row for row in raw_payload if isinstance(row, dict)]

    if isinstance(raw_payload, dict):
        candidates = (
            raw_payload.get("data"),
            raw_payload.get("positions"),
            raw_payload.get("position"),
            raw_payload.get("result"),
            raw_payload.get("results"),
        )
        for candidate in candidates:
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
            if isinstance(candidate, dict):
                return [candidate]
        return []

    return []


def _is_no_positions_payload(raw_payload: Any) -> bool:
    """
    Detect broker responses that semantically mean "no open positions".
    """
    if isinstance(raw_payload, list):
        return len(raw_payload) == 0

    if isinstance(raw_payload, dict):
        parts = [
            str(raw_payload.get("message", "")),
            str(raw_payload.get("errorMessage", "")),
            str(raw_payload.get("status", "")),
            str(raw_payload.get("remarks", "")),
            str(raw_payload.get("data", "")),
            str(raw_payload.get("internalErrorMessage", "")),
            str(raw_payload.get("errorType", "")),
        ]
        text = " ".join(parts).lower()
        markers = (
            "no position",
            "no positions",
            "position not found",
            "no open position",
            "no data",
            "empty",
            "doesn't have any open position",
        )
        return any(marker in text for marker in markers)

    return False


def format_decimal(value):
    """Format numeric value to 2 decimal places"""
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return value


def format_position_data(position_data):
    """Format all numeric values in position data to 2 decimal places, except quantity fields"""
    # Fields that should remain as integers
    quantity_fields = {
        "quantity",
        "qty",
        "netqty",
        "net_qty",
        "buyqty",
        "buy_quantity",
        "sellqty",
        "sell_quantity",
        "daybuyqty",
        "daysellqty",
    }

    if isinstance(position_data, list):
        return [
            {
                key: int(value)
                if (key.lower() in quantity_fields and isinstance(value, (int, float)))
                else (format_decimal(value) if isinstance(value, (int, float)) else value)
                for key, value in item.items()
            }
            for item in position_data
        ]
    return position_data


def import_broker_module(broker_name: str) -> dict[str, Any] | None:
    """
    Dynamically import the broker-specific positionbook modules.

    Args:
        broker_name: Name of the broker

    Returns:
        Dictionary of broker functions or None if import fails
    """
    try:
        # Import API module
        api_module = importlib.import_module(f"broker.{broker_name}.api.order_api")
        # Import mapping module
        mapping_module = importlib.import_module(f"broker.{broker_name}.mapping.order_data")
        return {
            "get_positions": api_module.get_positions,
            "map_position_data": mapping_module.map_position_data,
            "transform_positions_data": mapping_module.transform_positions_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error(f"Error importing broker modules: {error}")
        return None


def get_positionbook_with_auth(
    auth_token: str, broker: str, original_data: dict[str, Any] = None
) -> tuple[bool, dict[str, Any], int]:
    """
    Get position book details using provided auth token.

    Args:
        auth_token: Authentication token for the broker API
        broker: Name of the broker
        original_data: Original request data (for sandbox mode, optional for internal calls)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # If in analyze mode AND we have original_data (API call), route to sandbox
    # If original_data is None (internal call), use live broker
    from database.settings_db import get_analyze_mode

    if get_analyze_mode() and original_data:
        from services.sandbox_service import sandbox_get_positions

        api_key = original_data.get("apikey")
        if not api_key:
            return (
                False,
                {
                    "status": "error",
                    "message": "API key required for sandbox mode",
                    "mode": "analyze",
                },
                400,
            )

        return sandbox_get_positions(api_key, original_data)

    broker_funcs = import_broker_module(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        # Get positions data using broker's implementation
        positions_data = broker_funcs["get_positions"](auth_token)

        if isinstance(positions_data, dict) and str(positions_data.get("status", "")).lower() in {
            "error",
            "failed",
            "failure",
        }:
            if _is_no_positions_payload(positions_data):
                return True, {"status": "success", "data": []}, 200
            return (
                False,
                {
                    "status": "error",
                    "message": positions_data.get("message", "Error fetching positions data"),
                },
                500,
            )

        if isinstance(positions_data, dict) and positions_data.get("errorType"):
            if _is_no_positions_payload(positions_data):
                return True, {"status": "success", "data": []}, 200
            return (
                False,
                {
                    "status": "error",
                    "message": positions_data.get("errorMessage", "Error fetching positions data"),
                },
                500,
            )

        normalized_positions = _extract_position_rows(positions_data)

        # Transform data using mapping functions
        positions_data = broker_funcs["map_position_data"](normalized_positions)
        positions_data = broker_funcs["transform_positions_data"](positions_data)

        # Format numeric values to 2 decimal places
        formatted_positions = format_position_data(positions_data)

        return True, {"status": "success", "data": formatted_positions}, 200
    except Exception as e:
        logger.error(f"Error processing positions data: {e}")
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_positionbook(
    api_key: str | None = None, auth_token: str | None = None, broker: str | None = None
) -> tuple[bool, dict[str, Any], int]:
    """
    Get position book details.
    Supports both API-based authentication and direct internal calls.

    Args:
        api_key: OpenAlgo API key (for API-based calls)
        auth_token: Direct broker authentication token (for internal calls)
        broker: Direct broker name (for internal calls)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # Case 1: API-based authentication
    if api_key and not (auth_token and broker):
        AUTH_TOKEN, broker_name = get_auth_token_broker(api_key)
        if AUTH_TOKEN is None:
            return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403
        original_data = {"apikey": api_key}
        return get_positionbook_with_auth(AUTH_TOKEN, broker_name, original_data)

    # Case 2: Direct internal call with auth_token and broker
    elif auth_token and broker:
        return get_positionbook_with_auth(auth_token, broker, None)

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
