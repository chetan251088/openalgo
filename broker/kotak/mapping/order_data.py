import json

from broker.kotak.mapping.transform_data import map_exchange
from database.token_db import get_oa_symbol, get_symbol
from utils.logging import get_logger

logger = get_logger(__name__)


def _parse_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _parse_int(value, default=0):
    parsed = _parse_float(value)
    if parsed is None:
        return default
    return int(parsed)


def _first_numeric(position: dict, keys: list[str]):
    for key in keys:
        if key not in position:
            continue
        parsed = _parse_float(position.get(key))
        if parsed is not None:
            return parsed
    return None


def _normalize_exchange(value: str | None) -> str:
    if not value:
        return ""
    mapped = map_exchange(value)
    if mapped:
        return mapped
    value_upper = str(value).strip().upper().replace("-", "_")
    direct_valid = {"NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", "NSE_INDEX", "BSE_INDEX"}
    return value_upper if value_upper in direct_valid else str(value)


def _resolve_symbol(position: dict, exchange: str) -> str:
    token = str(position.get("tok", "") or position.get("token", "")).strip()
    symbol = str(position.get("trdSym", "") or position.get("symbol", "")).strip()

    # Prefer token-to-symbol mapping when token is present.
    if token:
        if exchange:
            resolved = get_symbol(token, exchange)
            if resolved:
                return resolved

        for ex in ("NFO", "NSE", "BFO", "BSE", "CDS", "MCX"):
            resolved = get_symbol(token, ex)
            if resolved:
                return resolved

    # Fallback: map broker symbol to OpenAlgo symbol.
    if symbol and exchange:
        oa_symbol = get_oa_symbol(symbol, exchange)
        if oa_symbol:
            return oa_symbol

    return symbol


def map_order_data(order_data):
    """
    Processes and modifies a list of order dictionaries based on specific conditions.

    Parameters:
    - order_data: A list of dictionaries, where each dictionary represents an order.

    Returns:
    - The modified order_data with updated 'tradingsymbol' and 'product' fields.
    """
    # Check if 'data' is None
    # if order_data has key 'data' and its value is None

    if not isinstance(order_data, dict):
        return order_data

    status_value = str(order_data.get("stat", order_data.get("status", ""))).lower()
    if status_value in {"not_ok", "notok", "error"}:
        logger.info("No data available.")
        order_data = {}  # or set it to an empty list if it's supposed to be a list
        return order_data

    if order_data.get("data") is None:
        # Handle the case where there is no data
        # For example, you might want to display a message to the user
        # or pass an empty list or dictionary to the template.
        logger.info("No data available.")
        order_data = {}  # or set it to an empty list if it's supposed to be a list
    else:
        order_data = order_data["data"]

    if order_data:
        for order in order_data:
            # Extract the instrument_token and exchange for the current order
            symboltoken = order["tok"]
            exchange = _normalize_exchange(order.get("exSeg"))
            order["exSeg"] = exchange

            # Use the get_symbol function to fetch the symbol from the database
            symbol_from_db = get_symbol(symboltoken, exchange) if exchange else None

            # Check if a symbol was found; if so, update the trading_symbol in the current order
            if symbol_from_db:
                order["trdSym"] = symbol_from_db
            else:
                logger.info(
                    f"Symbol not found for token {symboltoken} and exchange {exchange}. Keeping original trading symbol."
                )
    return order_data


def calculate_order_statistics(order_data):
    """
    Calculates statistics from order data, including totals for buy orders, sell orders,
    completed orders, open orders, and rejected orders.

    Parameters:
    - order_data: A list of dictionaries, where each dictionary represents an order.

    Returns:
    - A dictionary containing counts of different types of orders.
    """
    # Initialize counters
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    if order_data:
        for order in order_data:
            # Count buy and sell orders
            if order["trnsTp"] == "B":
                order["trnsTp"] = "BUY"
                total_buy_orders += 1
            elif order["trnsTp"] == "S":
                order["trnsTp"] = "SELL"
                total_sell_orders += 1

            # Count orders based on their status
            if order["ordSt"] == "complete":
                total_completed_orders += 1
            elif order["ordSt"] == "open":
                total_open_orders += 1
            elif order["ordSt"] == "rejected":
                total_rejected_orders += 1

    # Compile and return the statistics
    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


def transform_order_data(orders):
    # Directly handling a dictionary assuming it's the structure we expect
    if isinstance(orders, dict):
        # Convert the single dictionary into a list of one dictionary
        orders = [orders]

    transformed_orders = []

    for order in orders:
        # Make sure each item is indeed a dictionary
        if not isinstance(order, dict):
            logger.warning(
                f"Warning: Expected a dict, but found a {type(order)}. Skipping this item."
            )
            continue
        if order.get("prcTp") == "MKT":
            order["prcTp"] = "MARKET"
        elif order.get("prcTp") == "L":
            order["prcTp"] = "LIMIT"
        elif order.get("prcTp") == "SL":
            order["prcTp"] = "SL"
        elif order.get("prcTp") == "SL-M":
            order["prcTp"] = "SL-M"

        # For limit orders, show the order price (prc) instead of average price (avgPrc)
        # avgPrc is only relevant for executed orders
        order_price = order.get("avgPrc", 0.0)
        if order.get("prcTp") in ["LIMIT", "SL"]:
            # If order is not executed/complete, use the limit price
            if order.get("ordSt") != "complete":
                order_price = order.get("prc", 0.0)

        transformed_order = {
            "symbol": order.get("trdSym", ""),
            "exchange": order.get("exSeg", ""),
            "action": order.get("trnsTp", ""),
            "quantity": order.get("qty", 0),
            "price": order_price,
            "trigger_price": order.get("trgPrc", 0.0),
            "pricetype": order.get("prcTp", ""),
            "product": order.get("prod", ""),
            "orderid": order.get("nOrdNo", ""),
            "order_status": order.get("ordSt", ""),
            "timestamp": order.get("ordEntTm", ""),
        }

        transformed_orders.append(transformed_order)

    return transformed_orders


def map_trade_data(trade_data):
    """
    Processes and modifies a list of order dictionaries based on specific conditions.

    Parameters:
    - order_data: A list of dictionaries, where each dictionary represents an order.

    Returns:
    - The modified order_data with updated 'tradingsymbol' and 'product' fields.
    """
    if not isinstance(trade_data, dict):
        return trade_data

    status_value = str(trade_data.get("stat", trade_data.get("status", ""))).lower()
    if status_value in {"not_ok", "notok", "error"}:
        logger.info("No data available.")
        trade_data = {}  # or set it to an empty list if it's supposed to be a list
        return trade_data
        # Check if 'data' is None
    if trade_data.get("data") is None:
        # Handle the case where there is no data
        # For example, you might want to display a message to the user
        # or pass an empty list or dictionary to the template.
        logger.info("No data available.")
        trade_data = {}  # or set it to an empty list if it's supposed to be a list
    else:
        trade_data = trade_data["data"]

    if trade_data:
        for order in trade_data:
            # Extract the instrument_token and exchange for the current order
            symbol = order["tok"]
            exchange = _normalize_exchange(order.get("exSeg"))
            order["exSeg"] = exchange
            logger.info(f"{symbol}")
            logger.info(f"{exchange}")
            # Use the get_symbol function to fetch the symbol from the database
            symbol_from_db = get_symbol(symbol, exchange) if exchange else None
            logger.info(f"{symbol_from_db}")
            # Check if a symbol was found; if so, update the trading_symbol in the current order
            if symbol_from_db:
                order["trdSym"] = symbol_from_db
            else:
                logger.info(
                    f"Unable to find the symbol {symbol} and exchange {exchange}. Keeping original trading symbol."
                )

            # Map transaction type regardless of symbol lookup result
            if order["trnsTp"] == "B":
                order["trnsTp"] = "BUY"
            elif order["trnsTp"] == "S":
                order["trnsTp"] = "SELL"
    logger.info(f"{trade_data}")
    return trade_data


def transform_tradebook_data(tradebook_data):
    transformed_data = []

    for trade in tradebook_data:
        transformed_trade = {
            "symbol": trade.get("trdSym", ""),
            "exchange": trade.get("exSeg", ""),
            "product": trade.get("prod", ""),
            "action": trade.get("trnsTp", ""),
            "quantity": trade.get("fldQty", 0),
            "average_price": trade.get("avgPrc", 0.0),
            "trade_value": float(trade.get("fldQty", 0.0)) * float(trade.get("avgPrc", 0.0)),
            "orderid": trade.get("nOrdNo", ""),
            "timestamp": trade.get("exTm", ""),
        }
        transformed_data.append(transformed_trade)
    return transformed_data


def map_position_data(position_data):
    return map_order_data(position_data)


def transform_positions_data(positions_data):
    transformed_data = []
    for position in positions_data:
        fl_buy_qty = _parse_int(position.get("flBuyQty", position.get("dayBuyQty", 0)))
        fl_sell_qty = _parse_int(position.get("flSellQty", position.get("daySellQty", 0)))
        cf_buy_qty = _parse_int(position.get("cfBuyQty", position.get("carryBuyQty", 0)))
        cf_sell_qty = _parse_int(position.get("cfSellQty", position.get("carrySellQty", 0)))

        net_qty = (fl_buy_qty - fl_sell_qty) + (cf_buy_qty - cf_sell_qty)
        total_buy_qty = fl_buy_qty + cf_buy_qty
        total_sell_qty = fl_sell_qty + cf_sell_qty
        buy_amt = _first_numeric(
            position,
            ["buyAmt", "flBuyAmt", "cfBuyAmt", "buyValue", "totalBuyValue"],
        ) or 0.0
        sell_amt = _first_numeric(
            position,
            ["sellAmt", "flSellAmt", "cfSellAmt", "sellValue", "totalSellValue"],
        ) or 0.0

        average_price = _first_numeric(
            position,
            [
                "avgnetprice",
                "avgNetPrice",
                "avgPrice",
                "netAvgPrice",
                "buyAvg",
                "sellAvg",
            ],
        ) or 0.0
        if net_qty > 0 and total_buy_qty > 0:
            average_price = round(buy_amt / total_buy_qty, 2)
        elif net_qty < 0 and total_sell_qty > 0:
            average_price = round(sell_amt / total_sell_qty, 2)
        elif net_qty != 0:
            average_price = 0.0

        ltp = _first_numeric(
            position,
            [
                "ltp",
                "last_price",
                "lastPrice",
                "lp",
                "ltPrc",
                "netLtp",
                "close",
                "last_rate",
                "lastRate",
                "lastTradedPrice",
                "ltpPrice",
            ],
        )
        if ltp is None:
            ltp = 0.0

        pnl = _first_numeric(
            position,
            [
                "pnl",
                "mtm",
                "mtmPnl",
                "mtom",
                "m2m",
                "m2mPnl",
                "urMtm",
                "urmtm",
                "urMTM",
                "urMtom",
                "urmtom",
                "rpnl",
                "rPnl",
                "bookedPnl",
                "booked_pl",
                "unrealizedPnl",
                "unrealized_pnl",
                "realizedPnl",
                "realized_pnl",
            ],
        )

        # Open position fallback when broker doesn't provide direct P&L.
        if pnl is None and net_qty != 0 and average_price > 0 and ltp > 0:
            if net_qty > 0:
                pnl = (ltp - average_price) * net_qty
            else:
                pnl = (average_price - ltp) * abs(net_qty)

        # Closed/legacy fallback if explicit P&L and LTP are unavailable.
        if pnl is None:
            pnl = sell_amt - buy_amt

        exchange = _normalize_exchange(position.get("exSeg"))
        symbol = _resolve_symbol(position, exchange)

        transformed_position = {
            "symbol": symbol,
            "exchange": exchange,
            "product": position.get("prod", ""),
            "quantity": net_qty,
            "average_price": round(average_price, 2),
            "ltp": round(ltp, 2),
            "pnl": round(pnl, 2),
        }

        transformed_data.append(transformed_position)

    return transformed_data


def transform_holdings_data(holdings_data):
    transformed_data = []
    logger.info("Holdings Data")
    logger.info(f"{holdings_data}")
    for holding in holdings_data:
        transformed_position = {
            "symbol": holding.get("displaySymbol", ""),
            "exchange": holding.get("exchangeSegment", ""),
            "quantity": holding.get("quantity", 0),
            "product": holding.get("instrumentType", ""),
            "pnl": round(
                (float(holding.get("mktValue", 0.0)) - float(holding.get("holdingCost", 0.0))), 2
            ),
            "pnlpercent": round(
                (
                    (float(holding.get("mktValue", 0.0)) - float(holding.get("holdingCost", 0.0)))
                    / float(holding.get("holdingCost", 0.0))
                    * 100
                )
                if float(holding.get("holdingCost", 0.0)) != 0
                else 0,
                2,
            ),
        }

        transformed_data.append(transformed_position)
    logger.info("Holdings Data")
    logger.info(f"{transformed_data}")
    return transformed_data


def map_portfolio_data(portfolio_data):
    """
    Processes and modifies a list of Portfolio dictionaries based on specific conditions and
    ensures both holdings and totalholding parts are transmitted in a single response.

    Parameters:
    - portfolio_data: A dictionary, where keys are 'holdings' and 'totalholding',
                      and values are lists/dictionaries representing the portfolio information.

    Returns:
    - The modified portfolio_data with 'product' fields changed for 'holdings' and 'totalholding' included.
    """
    # Check if 'data' is None or doesn't contain 'holdings'
    if portfolio_data.get("data") is None:
        logger.info("No data available.")
        # Return an empty structure or handle this scenario as needed
        return {}

    # Directly work with 'data' for clarity and simplicity
    holdings = portfolio_data["data"]

    # Modify 'product' field for each holding if applicable

    for portfolio in holdings:
        token = portfolio["instrumentToken"]

        exchange = map_exchange(portfolio["exchangeSegment"])
        portfolio["exchangeSegment"] = exchange
        symbol_from_db = get_symbol(token, exchange)

        # Check if a symbol was found; if so, update the trading_symbol in the current order
        if symbol_from_db:
            portfolio["symbol"] = symbol_from_db
        if portfolio["instrumentType"] == "Equity":
            portfolio["instrumentType"] = "CNC"  # Modify 'product' field
        else:
            logger.info("Kotak Portfolio - Product Value for Delivery Not Found or Changed.")

    # The function already works with 'data', which includes 'holdings' and 'totalholding',
    # so we can return 'data' directly without additional modifications.

    return holdings


def calculate_portfolio_statistics(holdings_data):
    totalholdingvalue = sum(item["mktValue"] for item in holdings_data)
    totalinvvalue = sum(item["holdingCost"] for item in holdings_data)
    totalprofitandloss = sum(item["mktValue"] - item["holdingCost"] for item in holdings_data)

    totalpnlpercentage = (totalprofitandloss / totalinvvalue) * 100 if totalinvvalue != 0 else 0

    # To avoid division by zero in the case when total_investment_value is 0
    totalpnlpercentage = round(totalpnlpercentage, 2)

    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }
