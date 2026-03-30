"""
Market Pulse data aggregation service.
Fetches from broker APIs (via existing OpenAlgo services) + external reference feeds.
Caches for 30 seconds.
"""

import csv
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests

from services.market_pulse_config import (
    CACHE_TTL_SECONDS,
    EXECUTION_SWING,
    INDEX_SYMBOLS,
    SECTOR_INDICES,
    USDINR_SYMBOL,
)

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _history_source_env() -> str:
    source = os.getenv("MARKET_PULSE_HISTORY_SOURCE", "auto").strip().lower()
    if source in {"auto", "db", "api"}:
        return source
    return "auto"

# ── In-memory cache ─────────────────────────────────────────────
_cache: dict[str, Any] = {}
_cache_ts: float = 0
_pcr_cache: float | None = None
_pcr_cache_ts: float = 0
_institutional_cache: dict[str, Any] | None = None
_institutional_cache_ts: float = 0
_options_context_cache: dict[str, Any] | None = None
_options_context_cache_ts: float = 0
_delivery_cache: dict[str, Any] | None = None
_delivery_cache_ts: float = 0
_intraday_history_cache: dict[tuple[str, str, str], tuple[float, pd.DataFrame | None]] = {}
_daily_history_cache: dict[tuple[str, str], tuple[float, pd.DataFrame | None]] = {}
_history_source_cache: dict[tuple[str, str, str], str] = {}
_PCR_CACHE_TTL_SECONDS = _int_env("MARKET_PULSE_PCR_CACHE_TTL_SECONDS", 300)
_DAY_BASE_CACHE_TTL_SECONDS = _int_env("MARKET_PULSE_DAY_BASE_CACHE_TTL_SECONDS", 300)
# Daily candles don't change during the session — cache for 1 hour by default.
# Only the latest (today's) candle can shift intraday; previous candles are immutable.
_DAILY_HISTORY_CACHE_TTL_SECONDS = _int_env("MARKET_PULSE_DAILY_HISTORY_CACHE_TTL", 3600)
_INSTITUTIONAL_CACHE_TTL_SECONDS = _int_env(
    "MARKET_PULSE_INSTITUTIONAL_CACHE_TTL_SECONDS",
    900,
)
_OPTIONS_CONTEXT_CACHE_TTL_SECONDS = _int_env(
    "MARKET_PULSE_OPTIONS_CONTEXT_CACHE_TTL_SECONDS",
    60,
)
_DELIVERY_CACHE_TTL_SECONDS = _int_env(
    "MARKET_PULSE_DELIVERY_CACHE_TTL_SECONDS",
    21_600,
)
_INTRADAY_CACHE_TTL_SECONDS = _int_env("MARKET_PULSE_INTRADAY_CACHE_TTL_SECONDS", 60)
_INTRADAY_CONTEXT_WORKERS = max(1, _int_env("MARKET_PULSE_INTRADAY_WORKERS", 6))
_INTRADAY_LOOKBACK_DAYS = _int_env("MARKET_PULSE_INTRADAY_LOOKBACK_DAYS", 7)
_INTRADAY_INTERVAL = os.getenv("MARKET_PULSE_INTRADAY_INTERVAL", "5m").strip() or "5m"
_HISTORY_FETCH_WORKERS = max(1, _int_env("MARKET_PULSE_HISTORY_WORKERS", 3))
_HISTORY_SOURCE_MODE = _history_source_env()
_DELIVERY_AVG_WINDOW = max(3, _int_env("MARKET_PULSE_DELIVERY_AVG_WINDOW", 10))
_FII_DII_HISTORY_URL = os.getenv(
    "MARKET_PULSE_FII_DII_HISTORY_URL",
    "https://raw.githubusercontent.com/MrChartist/fii-dii-data/main/data/history.json",
)
_NIFTY_HISTORY_CALENDAR_DAYS = 420
_SENSEX_HISTORY_CALENDAR_DAYS = 420
_BANKNIFTY_HISTORY_CALENDAR_DAYS = 180
_VIX_HISTORY_CALENDAR_DAYS = 420
_SECTOR_HISTORY_CALENDAR_DAYS = 80
_CONSTITUENT_HISTORY_CALENDAR_DAYS = 420
_DAY_NIFTY_HISTORY_CALENDAR_DAYS = 260
_DAY_SENSEX_HISTORY_CALENDAR_DAYS = 260
_DAY_BANKNIFTY_HISTORY_CALENDAR_DAYS = 120
_DAY_VIX_HISTORY_CALENDAR_DAYS = 260
_DAY_SECTOR_HISTORY_CALENDAR_DAYS = 45
_DAY_CONSTITUENT_HISTORY_CALENDAR_DAYS = 80
_DAY_CONSTITUENT_HISTORY_LIMIT_PER_SIDE = max(
    4,
    _int_env("MARKET_PULSE_DAY_CONSTITUENT_HISTORY_LIMIT_PER_SIDE", 4),
)
_ANNUAL_LOOKBACK_SESSIONS = 252
_NSE_ARCHIVE_BASE_URLS = (
    "https://nsearchives.nseindia.com/products/content",
    "https://archives.nseindia.com/products/content",
)


def _is_cache_valid() -> bool:
    return (time.time() - _cache_ts) < CACHE_TTL_SECONDS and bool(_cache)


def _is_day_base_cache_reusable() -> bool:
    return (time.time() - _cache_ts) < _DAY_BASE_CACHE_TTL_SECONDS and bool(_cache)


def _load_json(filename: str) -> dict:
    """Load a JSON file from the data/ directory."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", filename)
    with open(path, "r") as f:
        return json.load(f)


def _safe_float(value: Any) -> float | None:
    """Convert JSON values to float when possible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _latest_history_trade_day(history: pd.DataFrame | None) -> date | None:
    """Extract the most recent session date from a history frame."""
    if history is None or history.empty or "timestamp" not in history.columns:
        return None

    timestamps = history["timestamp"]
    if pd.api.types.is_numeric_dtype(timestamps):
        numeric = pd.to_numeric(timestamps, errors="coerce")
        if numeric.dropna().empty:
            return None
        # Market history is stored in Unix seconds for this stack; if we ever
        # encounter millisecond epochs, normalize them before parsing.
        unit = "ms" if float(numeric.dropna().abs().max()) > 10**11 else "s"
        parsed = pd.to_datetime(numeric, unit=unit, errors="coerce")
    else:
        parsed = pd.to_datetime(timestamps, errors="coerce")

    parsed = parsed.dropna()
    if parsed.empty:
        return None
    return parsed.max().date()


def _daily_history_is_stale(history: pd.DataFrame | None) -> bool:
    """Reject local daily-history snapshots that lag the expected prior session."""
    latest_day = _latest_history_trade_day(history)
    if latest_day is None:
        return False

    expected_day = _previous_business_day()
    lag_days = _business_day_lag(latest_day, expected_day)
    return lag_days > 1


def _get_constituents() -> list[dict]:
    """Load Nifty 50 constituent list."""
    data = _load_json("nifty50_constituents.json")
    return data.get("constituents", [])


def _get_events() -> list[dict]:
    """Load market events calendar."""
    data = _load_json("market_events.json")
    return data.get("events", [])


def _parse_institutional_date(value: str | None) -> datetime | None:
    """Parse published FII/DII dates from the external dataset."""
    if not value:
        return None
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _previous_business_day(reference: date | None = None) -> date:
    """Return the most recent weekday before the given date."""
    current = (reference or date.today()) - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _business_day_lag(latest_day: date | None, expected_day: date) -> int:
    """Count missing weekdays between the latest available and expected dates."""
    if latest_day is None or latest_day >= expected_day:
        return 0
    lag = 0
    cursor = latest_day + timedelta(days=1)
    while cursor <= expected_day:
        if cursor.weekday() < 5:
            lag += 1
        cursor += timedelta(days=1)
    return lag


def _candidate_business_days(
    count: int,
    reference: date | None = None,
) -> list[date]:
    """Return the most recent weekdays including the reference date."""
    days: list[date] = []
    current = reference or date.today()
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return days


def _nse_delivery_archive_urls(trade_day: date) -> list[str]:
    stamp = trade_day.strftime("%d%m%Y")
    filename = f"sec_bhavdata_full_{stamp}.csv"
    return [f"{base}/{filename}" for base in _NSE_ARCHIVE_BASE_URLS]


def _normalize_archive_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        normalized[str(key).strip()] = str(value).strip() if value is not None else ""
    return normalized


def _parse_nse_delivery_csv(
    csv_text: str,
    symbols: set[str],
) -> dict[str, dict[str, Any]]:
    """Extract delivery rows for a symbol set from the official NSE bhav file."""
    extracted: dict[str, dict[str, Any]] = {}
    reader = csv.DictReader(csv_text.splitlines())
    for raw_row in reader:
        row = _normalize_archive_row(raw_row)
        symbol = row.get("SYMBOL", "").upper()
        if symbol not in symbols:
            continue
        if row.get("SERIES", "").upper() != "EQ":
            continue

        trade_dt = _parse_institutional_date(row.get("DATE1"))
        extracted[symbol] = {
            "symbol": symbol,
            "date": trade_dt.date().isoformat() if trade_dt else None,
            "delivery_pct": _safe_float(row.get("DELIV_PER")),
            "delivery_qty": _safe_float(row.get("DELIV_QTY")),
            "traded_qty": _safe_float(row.get("TTL_TRD_QNTY")),
        }
    return extracted


def _fetch_delivery_archive_day(
    trade_day: date,
    symbols: set[str],
) -> dict[str, dict[str, Any]]:
    """Fetch one official NSE bhavcopy file and extract delivery data."""
    headers = {
        "User-Agent": "OpenAlgo-MarketPulse/1.0",
        "Accept": "text/csv,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    for url in _nse_delivery_archive_urls(trade_day):
        try:
            response = requests.get(url, timeout=12, headers=headers)
            if response.status_code != 200:
                continue
            if "SYMBOL" not in response.text[:200]:
                continue
            rows = _parse_nse_delivery_csv(response.text, symbols)
            if rows:
                return rows
        except Exception:
            continue
    return {}


def _summarize_delivery_history(
    history_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Compute latest delivery % and its trailing 10-session baseline."""
    summary: dict[str, dict[str, Any]] = {}
    for symbol, rows in history_by_symbol.items():
        if not rows:
            continue

        latest = rows[0]
        trailing_delivery = [
            row.get("delivery_pct")
            for row in rows[1 : _DELIVERY_AVG_WINDOW + 1]
            if isinstance(row.get("delivery_pct"), (int, float))
        ]
        avg_delivery_pct_10d = (
            round(sum(trailing_delivery) / len(trailing_delivery), 2)
            if trailing_delivery
            else None
        )
        latest_delivery_pct = latest.get("delivery_pct")
        delivery_vs_10d_avg = (
            round(float(latest_delivery_pct) / avg_delivery_pct_10d, 2)
            if isinstance(latest_delivery_pct, (int, float))
            and isinstance(avg_delivery_pct_10d, (int, float))
            and avg_delivery_pct_10d > 0
            else None
        )
        summary[symbol] = {
            "delivery_date": latest.get("date"),
            "delivery_pct": latest_delivery_pct,
            "delivery_qty": int(round(latest["delivery_qty"]))
            if isinstance(latest.get("delivery_qty"), (int, float))
            else None,
            "delivery_traded_qty": int(round(latest["traded_qty"]))
            if isinstance(latest.get("traded_qty"), (int, float))
            else None,
            "avg_delivery_pct_10d": avg_delivery_pct_10d,
            "delivery_vs_10d_avg": delivery_vs_10d_avg,
        }
    return summary


def _fetch_delivery_snapshot(
    symbols: list[str],
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fetch official NSE delivery % data and compare against the prior 10 sessions."""
    global _delivery_cache, _delivery_cache_ts

    if (
        not force_refresh
        and _delivery_cache is not None
        and (time.time() - _delivery_cache_ts) < _DELIVERY_CACHE_TTL_SECONDS
    ):
        return deepcopy(_delivery_cache)

    symbol_set = {symbol.strip().upper() for symbol in symbols if symbol}
    if not symbol_set:
        return {}

    sessions_to_collect = _DELIVERY_AVG_WINDOW + 1
    history_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbol_set}

    for trade_day in _candidate_business_days(max(sessions_to_collect * 2, 12)):
        rows = _fetch_delivery_archive_day(trade_day, symbol_set)
        if not rows:
            continue
        for symbol, payload in rows.items():
            history_by_symbol.setdefault(symbol, []).append(payload)
        collected_sessions = max((len(rows) for rows in history_by_symbol.values()), default=0)
        if collected_sessions >= sessions_to_collect:
            break

    summary = _summarize_delivery_history(history_by_symbol)
    if not summary and _delivery_cache is not None:
        return deepcopy(_delivery_cache)
    _delivery_cache = summary
    _delivery_cache_ts = time.time()
    return deepcopy(summary)


def _classify_institutional_cash(
    latest_fii: float | None,
    latest_dii: float | None,
    fii_5d: float | None,
) -> str:
    """Classify cash-market positioning from institutional flows."""
    if latest_fii is None and latest_dii is None:
        return "neutral"
    if latest_fii is not None and latest_fii <= -3000 and (fii_5d or 0) < 0:
        return "bearish"
    if latest_fii is not None and latest_fii >= 3000 and (fii_5d or 0) > 0:
        return "bullish"
    if latest_fii is not None and latest_dii is not None:
        if latest_fii < 0 and latest_dii > 0:
            return "bearish"
        if latest_fii > 0 and latest_dii < 0:
            return "bullish"
    return "neutral"


def _classify_institutional_derivatives(row: dict[str, Any]) -> str:
    """Read participant derivative positioning into a simple bias label."""
    bull_score = 0.0
    bear_score = 0.0
    fut_net = _safe_float(row.get("fii_idx_fut_net"))
    call_net = _safe_float(row.get("fii_idx_call_net"))
    put_net = _safe_float(row.get("fii_idx_put_net"))

    if fut_net is not None:
        if fut_net >= 75000:
            bull_score += 2
        elif fut_net <= -75000:
            bear_score += 2

    if call_net is not None:
        if call_net >= 100000:
            bull_score += 1
        elif call_net <= -100000:
            bear_score += 1

    if put_net is not None:
        if put_net >= 100000:
            bear_score += 1
        elif put_net <= -100000:
            bull_score += 1

    if bull_score - bear_score >= 1.5:
        return "bullish"
    if bear_score - bull_score >= 1.5:
        return "bearish"
    return "neutral"


def _resolve_institutional_headline_bias(
    cash_bias: str,
    derivatives_bias: str,
    sentiment_score: float | None,
) -> str:
    """Blend cash and derivatives cues into one headline bias."""
    if cash_bias == derivatives_bias and cash_bias != "neutral":
        return cash_bias
    if derivatives_bias != "neutral":
        return derivatives_bias
    if cash_bias != "neutral":
        return cash_bias
    if sentiment_score is not None:
        if sentiment_score >= 60:
            return "bullish"
        if sentiment_score <= 40:
            return "bearish"
    return "neutral"


def _fetch_institutional_flows(force_refresh: bool = False) -> dict[str, Any] | None:
    """Fetch and summarize FII/DII flow data from the external reference feed."""
    global _institutional_cache, _institutional_cache_ts

    if (
        not force_refresh
        and _institutional_cache is not None
        and (time.time() - _institutional_cache_ts) < _INSTITUTIONAL_CACHE_TTL_SECONDS
    ):
        return deepcopy(_institutional_cache)

    try:
        response = requests.get(
            _FII_DII_HISTORY_URL,
            timeout=8,
            headers={"User-Agent": "OpenAlgo-MarketPulse/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise ValueError("Unexpected FII/DII payload format")

        rows = [row for row in payload if isinstance(row, dict) and row.get("date")]
        rows.sort(
            key=lambda row: _parse_institutional_date(row.get("date")) or datetime.min,
            reverse=True,
        )
        latest = rows[0]
        recent_rows = rows[:10]
        five_day_rows = rows[:5]

        fii_5d = round(
            sum(_safe_float(row.get("fii_net")) or 0.0 for row in five_day_rows),
            2,
        )
        dii_5d = round(
            sum(_safe_float(row.get("dii_net")) or 0.0 for row in five_day_rows),
            2,
        )
        latest_fii = _safe_float(latest.get("fii_net"))
        latest_dii = _safe_float(latest.get("dii_net"))
        sentiment_score = _safe_float(latest.get("sentiment_score"))
        latest_dt = _parse_institutional_date(latest.get("date"))
        expected_dt = _previous_business_day()
        lag_business_days = _business_day_lag(
            latest_dt.date() if latest_dt else None,
            expected_dt,
        )
        is_stale = latest_dt is None or latest_dt.date() < expected_dt
        cash_bias = _classify_institutional_cash(latest_fii, latest_dii, fii_5d)
        derivatives_bias = _classify_institutional_derivatives(latest)
        headline_bias = _resolve_institutional_headline_bias(
            cash_bias,
            derivatives_bias,
            sentiment_score,
        )

        summary = {
            "source": "MrChartist/fii-dii-data",
            "source_url": "https://github.com/MrChartist/fii-dii-data",
            "freshness": {
                "is_stale": is_stale,
                "lag_business_days": lag_business_days,
                "latest_trading_date": latest_dt.date().isoformat() if latest_dt else None,
                "expected_min_date": expected_dt.isoformat(),
            },
            "latest": {
                "date": latest.get("date"),
                "updated_at": latest.get("_updated_at"),
                "fii_net": latest_fii,
                "dii_net": latest_dii,
                "sentiment_score": sentiment_score,
                "cash_bias": cash_bias,
                "derivatives_bias": derivatives_bias,
                "headline_bias": headline_bias,
                "fii_idx_fut_net": _safe_float(latest.get("fii_idx_fut_net")),
                "fii_idx_call_net": _safe_float(latest.get("fii_idx_call_net")),
                "fii_idx_put_net": _safe_float(latest.get("fii_idx_put_net")),
            },
            "five_day": {
                "fii_net": fii_5d,
                "dii_net": dii_5d,
                "divergence": round((dii_5d or 0.0) - (fii_5d or 0.0), 2),
                "fii_buy_days": sum(
                    1 for row in five_day_rows if (_safe_float(row.get("fii_net")) or 0) > 0
                ),
                "dii_buy_days": sum(
                    1 for row in five_day_rows if (_safe_float(row.get("dii_net")) or 0) > 0
                ),
            },
            "recent": [
                {
                    "date": row.get("date"),
                    "fii_net": _safe_float(row.get("fii_net")),
                    "dii_net": _safe_float(row.get("dii_net")),
                }
                for row in recent_rows
            ],
        }

        _institutional_cache = summary
        _institutional_cache_ts = time.time()
        return deepcopy(summary)
    except Exception as e:
        logger.warning("Institutional flow fetch failed: %s", e)
        if _institutional_cache is not None:
            return deepcopy(_institutional_cache)
    return None


def _get_market_pulse_api_key() -> str | None:
    """Return the configured OpenAlgo API key for Market Pulse."""
    api_key = os.getenv("APP_KEY")
    if not api_key:
        logger.warning("No APP_KEY configured for market pulse")
    return api_key


def _quote_ltp(quote: dict | None) -> float | None:
    if isinstance(quote, dict) and isinstance(quote.get("ltp"), (int, float)):
        return float(quote["ltp"])
    return None


def _quote_prev_close(quote: dict | None) -> float | None:
    if isinstance(quote, dict) and isinstance(quote.get("prev_close"), (int, float)):
        return float(quote["prev_close"])
    return None


def _quote_change_pct(quote: dict | None) -> float | None:
    if isinstance(quote, dict) and isinstance(quote.get("change_pct"), (int, float)):
        return float(quote["change_pct"])
    if isinstance(quote, dict):
        ltp = quote.get("ltp")
        prev_close = quote.get("prev_close")
        if isinstance(ltp, (int, float)) and isinstance(prev_close, (int, float)) and prev_close:
            return round(((float(ltp) - float(prev_close)) / float(prev_close)) * 100, 2)
    return None


def _with_computed_change(quote: dict | None) -> dict | None:
    """Normalize a quote dict with derived day-change fields when absent."""
    if not isinstance(quote, dict):
        return quote

    normalized = dict(quote)
    ltp = _quote_ltp(normalized)
    prev_close = _quote_prev_close(normalized)
    if (
        ltp is not None
        and prev_close is not None
        and prev_close > 0
    ):
        if not isinstance(normalized.get("change"), (int, float)):
            normalized["change"] = round(ltp - prev_close, 2)
        if not isinstance(normalized.get("change_pct"), (int, float)):
            normalized["change_pct"] = round(((ltp - prev_close) / prev_close) * 100, 2)
    return normalized


def _normalize_expiry_date(expiry: str | None) -> str | None:
    """Normalize expiry strings to DDMMMYY format expected by option services."""
    if not expiry:
        return None

    cleaned = expiry.strip().upper()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d%b%y", "%d%b%Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%d%b%y").upper()
        except ValueError:
            continue
    return None


def _get_nearest_option_expiry(
    underlying: str = "NIFTY",
    exchange: str = "NFO",
) -> str | None:
    """Fetch the nearest listed option expiry for an underlying."""
    try:
        from services.expiry_service import get_expiry_dates

        api_key = os.getenv("APP_KEY")
        if not api_key:
            logger.warning("No APP_KEY configured for market pulse expiry lookup")
            return None

        success, data, _ = get_expiry_dates(
            symbol=underlying,
            exchange=exchange,
            instrumenttype="options",
            api_key=api_key,
        )
        if not success or data.get("status") != "success":
            logger.warning("Expiry lookup failed for %s on %s", underlying, exchange)
            return None

        expiries = data.get("data", [])
        for expiry in expiries:
            normalized = _normalize_expiry_date(expiry)
            if normalized:
                return normalized
    except Exception as e:
        logger.warning("Expiry lookup failed for %s:%s - %s", underlying, exchange, e)
    return None


# ── Broker Data Fetching ────────────────────────────────────────

def _fetch_quote(symbol: str, exchange: str) -> dict | None:
    """Fetch a single quote via the existing quotes service."""
    try:
        from services.quotes_service import get_quotes

        api_key = _get_market_pulse_api_key()
        if not api_key:
            return None

        success, data, _ = get_quotes(symbol=symbol, exchange=exchange, api_key=api_key)
        if success and data.get("status") == "success":
            return data.get("data", {})
    except Exception as e:
        logger.warning("Quote fetch failed for %s:%s - %s", symbol, exchange, e)
    return None


def _fetch_quotes_map(symbols: list[dict[str, str]]) -> dict[tuple[str, str], dict]:
    """Fetch multiple quotes in one request and return a symbol/exchange keyed map."""
    try:
        from services.quotes_service import get_multiquotes

        api_key = _get_market_pulse_api_key()
        if not api_key:
            return {}

        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in symbols:
            key = (item["symbol"], item["exchange"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        success, data, _ = get_multiquotes(symbols=deduped, api_key=api_key)
        if not success or data.get("status") != "success":
            logger.warning("Multiquotes fetch failed for market pulse")
            return {}

        quotes_map: dict[tuple[str, str], dict] = {}
        for item in data.get("results", []):
            symbol = item.get("symbol")
            exchange = item.get("exchange")
            quote = item.get("data")
            if symbol and exchange and isinstance(quote, dict):
                quotes_map[(symbol, exchange)] = quote
        return quotes_map
    except Exception as e:
        logger.warning("Multiquotes fetch failed for market pulse - %s", e)
        return {}


def _refresh_intraday_market_fields(
    result: dict[str, Any],
    include_constituents: bool = False,
) -> None:
    """Refresh quote-driven fields without refetching historical data."""
    quote_requests = [
        {"symbol": info["symbol"], "exchange": info["exchange"]}
        for info in INDEX_SYMBOLS.values()
    ] + [
        {"symbol": info["symbol"], "exchange": info["exchange"]}
        for info in SECTOR_INDICES.values()
    ]

    constituents = _get_constituents() if include_constituents else []
    quote_requests.extend(
        {"symbol": item["symbol"], "exchange": item["exchange"]} for item in constituents
    )

    quotes_map = _fetch_quotes_map(quote_requests)

    ticker = dict(result.get("ticker", {}))
    for key, info in INDEX_SYMBOLS.items():
        quote = quotes_map.get((info["symbol"], info["exchange"]))
        if quote:
            ticker[key] = _with_computed_change(quote)
    result["ticker"] = ticker

    sectors = dict(result.get("sectors", {}))
    for key, info in SECTOR_INDICES.items():
        quote = quotes_map.get((info["symbol"], info["exchange"]))
        if quote:
            sectors[key] = _with_computed_change(quote)
    result["sectors"] = sectors

    if include_constituents:
        constituent_quotes = dict(result.get("constituent_quotes", {}))
        for item in constituents:
            quote = quotes_map.get((item["symbol"], item["exchange"]))
            if quote:
                constituent_quotes[item["symbol"]] = _with_computed_change(quote)
        result["constituent_quotes"] = constituent_quotes

    result["pcr"] = _fetch_option_chain_pcr()
    result["updated_at"] = time.time()


def _select_day_constituent_history_symbols(
    constituents: list[dict[str, Any]],
    quotes: dict[str, dict] | None,
    benchmark_change_pct: float | None,
    limit_per_side: int = _DAY_CONSTITUENT_HISTORY_LIMIT_PER_SIDE,
) -> list[dict[str, Any]]:
    """Pick a compact live shortlist for day-mode daily-history enrichment."""
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []

    for item in constituents:
        symbol = item.get("symbol")
        exchange = item.get("exchange")
        if not symbol or not exchange:
            continue

        quote = (quotes or {}).get(symbol) or {}
        live_change_pct = _quote_change_pct(quote)
        if live_change_pct is None:
            continue

        rs_vs_nifty = (
            float(live_change_pct) - float(benchmark_change_pct)
            if isinstance(benchmark_change_pct, (int, float))
            else float(live_change_pct)
        )
        row = {
            "symbol": symbol,
            "exchange": exchange,
            "sector": item.get("sector"),
            "rs_vs_nifty": rs_vs_nifty,
            "change_pct": float(live_change_pct),
        }
        if rs_vs_nifty >= 0:
            positive.append(row)
        else:
            negative.append(row)

    positive.sort(key=lambda current: (current["rs_vs_nifty"], current["change_pct"]), reverse=True)
    negative.sort(key=lambda current: (current["rs_vs_nifty"], current["change_pct"]))

    selected = positive[:limit_per_side] + negative[:limit_per_side]
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        symbol = item["symbol"]
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(item)
    return deduped


def _fetch_history(symbol: str, exchange: str, days: int = 200) -> pd.DataFrame | None:
    """Fetch historical OHLCV, preferring Historify/DuckDB when configured.

    Results are cached per (symbol, exchange) for _DAILY_HISTORY_CACHE_TTL_SECONDS
    (default 1 hour) so repeated market-pulse refresh cycles don't re-hit the
    broker API for data that cannot have changed.
    """
    cache_key = (symbol, exchange)
    cached_ts, cached_df = _daily_history_cache.get(cache_key, (0.0, None))
    if cached_df is not None and (time.time() - cached_ts) < _DAILY_HISTORY_CACHE_TTL_SECONDS:
        return cached_df

    try:
        from services.history_service import get_history

        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        source_key = (symbol, exchange, "D")
        preferred_source = _history_source_cache.get(source_key, _HISTORY_SOURCE_MODE)

        if preferred_source == "auto":
            source_candidates = ["db", "api"]
        elif preferred_source in {"db", "api"}:
            source_candidates = [preferred_source]
            if (
                preferred_source == "db"
                and _HISTORY_SOURCE_MODE == "auto"
                and source_key not in _history_source_cache
            ):
                source_candidates.append("api")
        else:
            source_candidates = ["api"]

        last_error: str | None = None
        for source in source_candidates:
            api_key = None
            if source == "api":
                api_key = _get_market_pulse_api_key()
                if not api_key:
                    last_error = "No APP_KEY configured for broker history fallback"
                    continue

            success, data, _ = get_history(
                symbol=symbol,
                exchange=exchange,
                interval="D",
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                api_key=api_key,
                source=source,
            )

            if success and data.get("status") == "success":
                candles = data.get("data", [])
                if candles:
                    df = pd.DataFrame(candles)
                    for col in ["open", "high", "low", "close", "volume"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                    if source == "db" and _daily_history_is_stale(df):
                        latest_day = _latest_history_trade_day(df)
                        last_error = (
                            f"Local daily history stale (latest {latest_day}); "
                            "falling back to broker API"
                        )
                        continue
                    _history_source_cache[source_key] = source
                    _daily_history_cache[cache_key] = (time.time(), df)
                    return df

            last_error = data.get("message") if isinstance(data, dict) else None
    except Exception as e:
        last_error = str(e)

    if last_error:
        logger.warning("History fetch failed for %s:%s - %s", symbol, exchange, last_error)
    return None


def _fetch_history_batch(
    jobs: list[tuple[str, str, str, int]],
) -> dict[str, pd.DataFrame | None]:
    """Fetch multiple daily-history requests concurrently."""
    results: dict[str, pd.DataFrame | None] = {}
    if not jobs:
        return results

    # Per-job timeout: each history fetch must complete within this many seconds.
    # Prevents a single slow/hung broker call from stalling the entire batch.
    _JOB_TIMEOUT = float(os.getenv("MARKET_PULSE_HISTORY_JOB_TIMEOUT", "20"))

    with ThreadPoolExecutor(max_workers=min(_HISTORY_FETCH_WORKERS, len(jobs))) as pool:
        future_map = {
            pool.submit(_fetch_history, symbol, exchange, days): key
            for key, symbol, exchange, days in jobs
        }
        for future in as_completed(future_map, timeout=_JOB_TIMEOUT * len(jobs)):
            key = future_map[future]
            try:
                results[key] = future.result(timeout=_JOB_TIMEOUT)
            except Exception as e:
                logger.warning("Parallel history fetch failed for %s - %s", key, e)
                results[key] = None

    return results


def _compute_option_max_pain_from_chain(chain: list[dict[str, Any]]) -> float | None:
    """Compute max pain directly from per-strike OI values."""
    valid_chain = [
        item
        for item in chain
        if isinstance(item.get("strike"), (int, float)) and item.get("strike", 0) > 0
    ]
    if not valid_chain:
        return None

    best_strike = None
    best_pain = None
    for candidate in valid_chain:
        candidate_strike = float(candidate["strike"])
        total_pain = 0.0
        for item in valid_chain:
            strike = float(item["strike"])
            ce_oi = float(item.get("ce_oi") or 0)
            pe_oi = float(item.get("pe_oi") or 0)
            if candidate_strike > strike and ce_oi > 0:
                total_pain += (candidate_strike - strike) * ce_oi
            if candidate_strike < strike and pe_oi > 0:
                total_pain += (strike - candidate_strike) * pe_oi
        if best_pain is None or total_pain < best_pain:
            best_pain = total_pain
            best_strike = candidate_strike

    return round(best_strike, 2) if best_strike is not None else None


def _summarize_oi_wall(
    chain: list[dict[str, Any]],
    *,
    side: str,
    spot_price: float | None,
) -> dict[str, Any] | None:
    """Return the strongest relevant OI wall for calls or puts around spot."""
    if side not in {"call", "put"}:
        return None

    filtered: list[dict[str, Any]] = []
    for item in chain:
        strike = item.get("strike")
        if not isinstance(strike, (int, float)):
            continue
        oi = float(item.get("ce_oi") or 0) if side == "call" else float(item.get("pe_oi") or 0)
        if oi <= 0:
            continue
        if isinstance(spot_price, (int, float)):
            if side == "call" and strike < spot_price:
                continue
            if side == "put" and strike > spot_price:
                continue
        filtered.append({"strike": float(strike), "oi": oi})

    if not filtered:
        return None

    top = max(filtered, key=lambda item: item["oi"])
    distance_pct = None
    if isinstance(spot_price, (int, float)) and spot_price:
        distance_pct = round(((top["strike"] - float(spot_price)) / float(spot_price)) * 100, 2)

    return {
        "strike": int(top["strike"]) if top["strike"].is_integer() else round(top["strike"], 2),
        "oi": int(top["oi"]),
        "distance_pct": distance_pct,
    }


def _fetch_single_options_context(underlying: str, api_key: str) -> dict[str, Any] | None:
    """Fetch max pain and OI-wall context for one index underlying."""
    try:
        from services.oi_tracker_service import get_oi_data

        expiry_date = _get_nearest_option_expiry(underlying, "NFO")
        if not expiry_date:
            return None

        success, response, _ = get_oi_data(
            underlying=underlying,
            exchange="NFO",
            expiry_date=expiry_date,
            api_key=api_key,
        )
        if not success or response.get("status") != "success":
            return None

        chain = response.get("chain", [])
        spot_price = response.get("spot_price")
        if isinstance(spot_price, (int, float)):
            spot_price = float(spot_price)

        return {
            "underlying": underlying,
            "expiry_date": expiry_date,
            "spot_price": spot_price,
            "futures_price": response.get("futures_price"),
            "atm_strike": response.get("atm_strike"),
            "pcr_oi": response.get("pcr_oi"),
            "pcr_volume": response.get("pcr_volume"),
            "max_pain": _compute_option_max_pain_from_chain(chain),
            "call_wall": _summarize_oi_wall(chain, side="call", spot_price=spot_price),
            "put_wall": _summarize_oi_wall(chain, side="put", spot_price=spot_price),
            "total_ce_oi": response.get("total_ce_oi"),
            "total_pe_oi": response.get("total_pe_oi"),
        }
    except Exception as e:
        logger.warning("Options context fetch failed for %s - %s", underlying, e)
        return None


def _fetch_options_context(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch cached options positioning context for NIFTY and BANKNIFTY."""
    global _options_context_cache, _options_context_cache_ts

    if (
        not force_refresh
        and _options_context_cache is not None
        and (time.time() - _options_context_cache_ts) < _OPTIONS_CONTEXT_CACHE_TTL_SECONDS
    ):
        return deepcopy(_options_context_cache)

    api_key = _get_market_pulse_api_key()
    if not api_key:
        return {}

    underlyings = ["NIFTY", "BANKNIFTY"]
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(underlyings)) as pool:
        future_map = {
            pool.submit(_fetch_single_options_context, underlying, api_key): underlying
            for underlying in underlyings
        }
        for future in as_completed(future_map):
            underlying = future_map[future]
            context = future.result()
            if context:
                results[underlying] = context

    _options_context_cache = results
    _options_context_cache_ts = time.time()
    return deepcopy(results)


def _fetch_intraday_history(
    symbol: str,
    exchange: str,
    interval: str = _INTRADAY_INTERVAL,
    days: int = _INTRADAY_LOOKBACK_DAYS,
    force_refresh: bool = False,
) -> pd.DataFrame | None:
    """Fetch cached intraday history, preferring DuckDB/Historify when possible."""
    cache_key = (symbol, exchange, interval)
    if not force_refresh:
        cached = _intraday_history_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _INTRADAY_CACHE_TTL_SECONDS:
            frame = cached[1]
            return frame.copy() if isinstance(frame, pd.DataFrame) else frame

    try:
        from services.history_service import get_history

        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        source_key = (symbol, exchange, interval)
        preferred_source = _history_source_cache.get(source_key, _HISTORY_SOURCE_MODE)

        if preferred_source == "auto":
            source_candidates = ["db", "api"]
        elif preferred_source in {"db", "api"}:
            source_candidates = [preferred_source]
            if (
                preferred_source == "db"
                and _HISTORY_SOURCE_MODE == "auto"
                and source_key not in _history_source_cache
            ):
                source_candidates.append("api")
        else:
            source_candidates = ["api"]

        last_error: str | None = None
        for source in source_candidates:
            api_key = None
            if source == "api":
                api_key = _get_market_pulse_api_key()
                if not api_key:
                    last_error = "No APP_KEY configured for broker intraday fallback"
                    continue

            success, data, _ = get_history(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                api_key=api_key,
                source=source,
            )

            if success and data.get("status") == "success":
                candles = data.get("data", [])
                if candles:
                    df = pd.DataFrame(candles)
                    for col in ["open", "high", "low", "close", "volume"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                    _history_source_cache[source_key] = source
                    _intraday_history_cache[cache_key] = (time.time(), df)
                    return df.copy()

            last_error = data.get("message") if isinstance(data, dict) else None
    except Exception as e:
        last_error = str(e)

    if last_error:
        logger.warning("Intraday history fetch failed for %s:%s %s - %s", symbol, exchange, interval, last_error)
    _intraday_history_cache[cache_key] = (time.time(), None)
    return None


def _build_intraday_trade_context(
    history: pd.DataFrame | None,
    current_price: float | None = None,
) -> dict[str, Any] | None:
    """Compute session VWAP and time-adjusted RVOL from intraday bars."""
    if history is None or "timestamp" not in history.columns or "close" not in history.columns:
        return None

    df = history.copy()
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        parsed = pd.to_datetime(ts, unit="s", errors="coerce")
    else:
        parsed = pd.to_datetime(ts, errors="coerce")
    try:
        parsed = parsed.dt.tz_localize(None)
    except (TypeError, AttributeError):
        try:
            parsed = parsed.dt.tz_convert(None)
        except (TypeError, AttributeError):
            pass
    df["timestamp"] = parsed
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return None

    df["session_date"] = df["timestamp"].dt.date
    current_session = df["session_date"].max()
    today = df[df["session_date"] == current_session].copy()
    if today.empty:
        return None

    for col in ["high", "low", "close", "volume"]:
        if col not in today.columns:
            return None
        today[col] = pd.to_numeric(today[col], errors="coerce")
    today = today.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    if today.empty:
        return None

    vol = today["volume"].fillna(0).clip(lower=0)
    cumulative_volume = float(vol.cumsum().iloc[-1]) if not vol.empty else 0.0
    if cumulative_volume <= 0:
        return None

    reference_price = float(current_price) if isinstance(current_price, (int, float)) else float(today["close"].iloc[-1])
    typical_price = (today["high"] + today["low"] + today["close"]) / 3
    vwap = float((typical_price * vol).cumsum().iloc[-1] / cumulative_volume)

    prior_sessions: list[float] = []
    session_dates = sorted(d for d in df["session_date"].dropna().unique() if d < current_session)
    bars_today = len(today)
    for session_date in session_dates[-5:]:
        session_df = df[df["session_date"] == session_date].copy().reset_index(drop=True)
        if session_df.empty or "volume" not in session_df.columns:
            continue
        session_vol = pd.to_numeric(session_df["volume"], errors="coerce").fillna(0).clip(lower=0)
        if session_vol.empty:
            continue
        idx = min(bars_today, len(session_vol)) - 1
        if idx >= 0:
            prior_sessions.append(float(session_vol.cumsum().iloc[idx]))

    avg_prior_cum = sum(prior_sessions) / len(prior_sessions) if prior_sessions else None
    rvol = round(cumulative_volume / avg_prior_cum, 2) if avg_prior_cum and avg_prior_cum > 0 else None
    vwap_distance_pct = round(((reference_price - vwap) / vwap) * 100, 2) if vwap else None

    return {
        "session_date": current_session.isoformat(),
        "bars": bars_today,
        "vwap": round(vwap, 2),
        "vwap_distance_pct": vwap_distance_pct,
        "above_vwap": bool(reference_price > vwap),
        "below_vwap": bool(reference_price < vwap),
        "session_volume": int(round(cumulative_volume)),
        "avg_cumulative_volume": int(round(avg_prior_cum)) if avg_prior_cum else None,
        "rvol": rvol,
    }


def fetch_intraday_trade_context(
    symbols: list[dict[str, Any]],
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Fetch intraday VWAP/RVOL context for a shortlist of symbols."""
    results: dict[str, dict[str, Any]] = {}
    if not symbols:
        return results

    with ThreadPoolExecutor(max_workers=min(_INTRADAY_CONTEXT_WORKERS, len(symbols))) as pool:
        future_map = {
            pool.submit(
                _fetch_intraday_history,
                item["symbol"],
                item["exchange"],
                _INTRADAY_INTERVAL,
                _INTRADAY_LOOKBACK_DAYS,
                force_refresh,
            ): item
            for item in symbols
            if item.get("symbol") and item.get("exchange")
        }
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                history = future.result()
                context = _build_intraday_trade_context(
                    history,
                    item.get("current_price"),
                )
                if context:
                    results[item.get("key") or item["symbol"]] = context
            except Exception as e:
                logger.warning("Intraday trade context failed for %s - %s", item.get("symbol"), e)

    return results


def _fetch_option_chain_pcr() -> float | None:
    """Compute Nifty Put/Call Ratio from option chain OI."""
    global _pcr_cache, _pcr_cache_ts

    if (
        _pcr_cache is not None
        and (time.time() - _pcr_cache_ts) < _PCR_CACHE_TTL_SECONDS
    ):
        return _pcr_cache

    try:
        from services.option_chain_service import get_option_chain

        api_key = _get_market_pulse_api_key()
        if not api_key:
            return None

        expiry_date = _get_nearest_option_expiry("NIFTY", "NFO")
        if not expiry_date:
            logger.warning("No NIFTY option expiry available for PCR fetch")
            return None

        success, data, _ = get_option_chain(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date=expiry_date,
            strike_count=10,
            api_key=api_key,
        )
        if success and data.get("status") == "success":
            chain = data.get("chain", [])
            total_put_oi = sum((row.get("pe") or {}).get("oi", 0) for row in chain)
            total_call_oi = sum((row.get("ce") or {}).get("oi", 0) for row in chain)
            if total_call_oi > 0:
                _pcr_cache = round(total_put_oi / total_call_oi, 3)
                _pcr_cache_ts = time.time()
                return _pcr_cache
    except Exception as e:
        logger.warning("PCR fetch failed: %s", e)
    return None


# ── Technical Indicators ────────────────────────────────────────

def compute_sma(series: pd.Series, period: int) -> float | None:
    """Compute simple moving average."""
    if len(series) < period:
        return None
    return round(series.tail(period).mean(), 2)


def compute_rsi(series: pd.Series, period: int = 14) -> float | None:
    """Compute RSI."""
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.tail(period).mean()
    avg_loss = loss.tail(period).mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_slope(series: pd.Series, period: int = 5) -> float | None:
    """Compute slope as percentage change over period."""
    if len(series) < period:
        return None
    return round((series.iloc[-1] - series.iloc[-period]) / series.iloc[-period] * 100, 4)


def compute_percentile(current: float, series: pd.Series) -> float | None:
    """Compute percentile rank of current value in historical series."""
    if len(series) < 20:
        return None
    below = (series < current).sum()
    return round(below / len(series) * 100, 1)


def compute_constituent_breadth_snapshot(
    constituent_data: dict[str, dict],
    constituent_quotes: dict[str, dict] | None = None,
    use_live_ltp: bool = False,
) -> dict[str, Any]:
    """Derive a consistent breadth snapshot from the Nifty 50 constituent basket."""
    advances = 0
    declines = 0
    unchanged = 0
    above_50d = 0
    eligible_50d = 0
    above_200d = 0
    eligible_200d = 0
    highs_52w = 0
    lows_52w = 0
    eligible_52w = 0
    epsilon = 1e-6
    total_symbols = len(constituent_data)

    for symbol, payload in constituent_data.items():
        hist = payload.get("history")
        quote = (constituent_quotes or {}).get(symbol)
        closes = None
        current_price = None
        previous_reference = None

        if hist is not None and "close" in hist.columns:
            closes = pd.to_numeric(hist["close"], errors="coerce").dropna().reset_index(drop=True)
            if closes.empty:
                closes = None
            else:
                current_price = float(closes.iloc[-1])
                previous_reference = float(closes.iloc[-2]) if len(closes) >= 2 else None

        if use_live_ltp:
            live_ltp = _quote_ltp(quote)
            if live_ltp is not None:
                current_price = live_ltp
                live_prev_close = _quote_prev_close(quote)
                if live_prev_close is not None:
                    previous_reference = live_prev_close

        if current_price is not None and previous_reference is not None:
            if current_price > previous_reference + epsilon:
                advances += 1
            elif current_price < previous_reference - epsilon:
                declines += 1
            else:
                unchanged += 1

        if closes is None:
            continue

        if len(closes) >= 50:
            eligible_50d += 1
            if current_price > float(closes.tail(50).mean()):
                above_50d += 1

        if len(closes) >= 200:
            eligible_200d += 1
            if current_price > float(closes.tail(200).mean()):
                above_200d += 1

        if len(closes) >= _ANNUAL_LOOKBACK_SESSIONS:
            eligible_52w += 1
            if use_live_ltp and _quote_ltp(quote) is not None:
                annual_window = closes.tail(_ANNUAL_LOOKBACK_SESSIONS)
            else:
                annual_window = closes.iloc[-_ANNUAL_LOOKBACK_SESSIONS:-1]
            if not annual_window.empty:
                annual_high = float(annual_window.max())
                annual_low = float(annual_window.min())
                if current_price >= annual_high - epsilon:
                    highs_52w += 1
                if current_price <= annual_low + epsilon:
                    lows_52w += 1

    ad_ratio = round(advances / max(declines, 1), 2) if (advances or declines) else None
    pct_above_50d = (
        round(above_50d / eligible_50d * 100, 1) if eligible_50d else None
    )
    pct_above_200d = (
        round(above_200d / eligible_200d * 100, 1) if eligible_200d else None
    )
    highs_lows_ratio = (
        round(highs_52w / max(lows_52w, 1), 2)
        if eligible_52w and (highs_52w or lows_52w)
        else None
    )

    if use_live_ltp and eligible_50d < total_symbols:
        pct_above_50d = None
        above_50d = None
        eligible_50d = None
    if use_live_ltp and eligible_200d < total_symbols:
        pct_above_200d = None
        above_200d = None
        eligible_200d = None
    if use_live_ltp and eligible_52w < total_symbols:
        highs_52w = None
        lows_52w = None
        highs_lows_ratio = None
        eligible_52w = None

    return {
        "scope": "Nifty 50",
        "advance_decline": {
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "ad_ratio": ad_ratio,
            "basis": "live quotes vs prev close" if use_live_ltp else "daily close vs prior close",
        },
        "moving_averages": {
            "pct_above_50d": pct_above_50d,
            "above_50d": above_50d,
            "eligible_50d": eligible_50d,
            "pct_above_200d": pct_above_200d,
            "above_200d": above_200d,
            "eligible_200d": eligible_200d,
        },
        "annual_extremes": {
            "highs_52w": highs_52w if eligible_52w else None,
            "lows_52w": lows_52w if eligible_52w else None,
            "ratio": highs_lows_ratio,
            "eligible_52w": eligible_52w,
        },
    }


# ── Main Aggregation ────────────────────────────────────────────

def fetch_market_data(mode: str = "swing", force_refresh: bool = False) -> dict[str, Any]:
    """Fetch and aggregate all market data. Returns cached if fresh.

    Args:
        mode: "swing" or "day"
        force_refresh: When True, bypass the shared market snapshot cache.

    Returns: dict with all data needed for scoring.
    """
    global _cache, _cache_ts

    if not force_refresh and _is_cache_valid():
        cached = dict(_cache)
        cached["mode"] = mode
        return cached

    if not force_refresh and _is_day_base_cache_reusable():
        cached = dict(_cache)
        _refresh_intraday_market_fields(cached, include_constituents=(mode == "day"))
        _cache.update(
            {
                "ticker": cached.get("ticker", {}),
                "sectors": cached.get("sectors", {}),
                "pcr": cached.get("pcr"),
                "updated_at": cached.get("updated_at"),
            }
        )
        if mode == "day":
            _cache["constituent_quotes"] = cached.get("constituent_quotes", {})
        cached["mode"] = mode
        return cached

    result: dict[str, Any] = {"errors": []}
    is_day_mode = mode == "day"
    nifty_history_days = (
        _DAY_NIFTY_HISTORY_CALENDAR_DAYS if is_day_mode else _NIFTY_HISTORY_CALENDAR_DAYS
    )
    sensex_history_days = (
        _DAY_SENSEX_HISTORY_CALENDAR_DAYS if is_day_mode else _SENSEX_HISTORY_CALENDAR_DAYS
    )
    banknifty_history_days = (
        _DAY_BANKNIFTY_HISTORY_CALENDAR_DAYS
        if is_day_mode
        else _BANKNIFTY_HISTORY_CALENDAR_DAYS
    )
    vix_history_days = _DAY_VIX_HISTORY_CALENDAR_DAYS if is_day_mode else _VIX_HISTORY_CALENDAR_DAYS
    sector_history_days = (
        _DAY_SECTOR_HISTORY_CALENDAR_DAYS if is_day_mode else _SECTOR_HISTORY_CALENDAR_DAYS
    )
    constituent_history_days = (
        _DAY_CONSTITUENT_HISTORY_CALENDAR_DAYS
        if is_day_mode
        else _CONSTITUENT_HISTORY_CALENDAR_DAYS
    )

    # 1. USDINR (skip - only available as dated futures in Zerodha)
    # USDINR only exists as USDINR20MAR26FUT, USDINR25MAR26FUT, etc.
    # Not fetching to avoid contract-rolling complexity

    constituents = _get_constituents()
    # Delivery is an end-of-day swing input; skip it on day-mode cold loads.
    delivery_snapshot = (
        {}
        if is_day_mode
        else _fetch_delivery_snapshot(
            [item["symbol"] for item in constituents],
            force_refresh=force_refresh,
        )
    )
    history_jobs = [
        ("nifty_history", "NIFTY", "NSE_INDEX", nifty_history_days),
        ("sensex_history", "SENSEX", "BSE_INDEX", sensex_history_days),
        ("banknifty_history", "BANKNIFTY", "NSE_INDEX", banknifty_history_days),
        ("vix_history", "INDIAVIX", "NSE_INDEX", vix_history_days),
    ]
    history_jobs.extend(
        (
            f"sector:{key}",
            info["symbol"],
            info["exchange"],
            sector_history_days,
        )
        for key, info in SECTOR_INDICES.items()
    )
    if not is_day_mode:
        history_jobs.extend(
            (
                f"constituent:{item['symbol']}",
                item["symbol"],
                item["exchange"],
                constituent_history_days,
            )
            for item in constituents
        )

    history_results = _fetch_history_batch(history_jobs)

    # 2. Historical data for Nifty/BankNifty/VIX (for MAs, RSI, slopes)
    nifty_hist = history_results.get("nifty_history")
    result["nifty_history"] = nifty_hist
    sensex_hist = history_results.get("sensex_history")
    result["sensex_history"] = sensex_hist
    banknifty_hist = history_results.get("banknifty_history")
    result["banknifty_history"] = banknifty_hist
    vix_hist = history_results.get("vix_history")
    result["vix_history"] = vix_hist

    # 5. USDINR history (skip if not available as standalone symbol)
    # Note: USDINR only exists as dated futures (USDINR20MAR26FUT, etc.) in Zerodha
    # Using dated futures would require contract rolling logic, so skip for now
    usdinr_hist = None
    result["usdinr_history"] = usdinr_hist

    # 6. Sector index histories (for MAs, to check above/below 20d)
    sector_histories = {}
    for key in SECTOR_INDICES:
        hist = history_results.get(f"sector:{key}")
        if hist is not None:
            sector_histories[key] = hist
    result["sector_histories"] = sector_histories

    # 7. Nifty 50 constituent data
    constituent_data = {
        item["symbol"]: {
            "sector": item["sector"],
            "exchange": item["exchange"],
            **delivery_snapshot.get(item["symbol"], {}),
        }
        for item in constituents
    }
    if is_day_mode:
        _refresh_intraday_market_fields(result, include_constituents=True)
        constituent_quotes = result.get("constituent_quotes", {})
        benchmark_change_pct = _quote_change_pct((result.get("ticker") or {}).get("NIFTY"))
        shortlisted_constituents = _select_day_constituent_history_symbols(
            constituents,
            constituent_quotes,
            benchmark_change_pct,
        )
        history_results.update(
            _fetch_history_batch(
                [
                    (
                        f"constituent:{item['symbol']}",
                        item["symbol"],
                        item["exchange"],
                        constituent_history_days,
                    )
                    for item in shortlisted_constituents
                ]
            )
        )
        for item in shortlisted_constituents:
            hist = history_results.get(f"constituent:{item['symbol']}")
            if hist is not None:
                constituent_data[item["symbol"]]["history"] = hist
    else:
        for c in constituents:
            hist = history_results.get(f"constituent:{c['symbol']}")
            if hist is not None:
                constituent_data[c["symbol"]]["history"] = hist
    result["constituent_data"] = constituent_data

    # 8. Events calendar
    result["events"] = _get_events()

    # 8b. Institutional flows (GitHub mirror of NSE/NSDL participant data)
    result["institutional_flows"] = _fetch_institutional_flows(force_refresh=force_refresh)
    result["options_context"] = _fetch_options_context(force_refresh=force_refresh)
    flow_freshness = (result.get("institutional_flows") or {}).get("freshness") or {}
    if flow_freshness.get("is_stale"):
        latest_flow_date = flow_freshness.get("latest_trading_date") or "unknown"
        lag_days = flow_freshness.get("lag_business_days", 0)
        latest_snapshot = (result.get("institutional_flows") or {}).get("latest") or {}
        fii_value = latest_snapshot.get("fii_net")
        dii_value = latest_snapshot.get("dii_net")
        fii_text = (
            f"FII {fii_value:+.2f} Cr"
            if isinstance(fii_value, (int, float))
            else "FII n/a"
        )
        dii_text = (
            f"DII {dii_value:+.2f} Cr"
            if isinstance(dii_value, (int, float))
            else "DII n/a"
        )
        result["errors"].append(
            "Institutional flow feed stale by "
            f"{lag_days} business day(s); showing latest available snapshot from "
            f"{latest_flow_date} ({fii_text}, {dii_text})"
        )

    # 9. Computed indicators for Nifty
    if nifty_hist is not None and "close" in nifty_hist.columns:
        closes = nifty_hist["close"]
        result["nifty_indicators"] = {
            "sma_20": compute_sma(closes, 20),
            "sma_50": compute_sma(closes, 50),
            "sma_200": compute_sma(closes, 200),
            "rsi_14": compute_rsi(closes, 14),
            "slope_50d": compute_slope(
                pd.Series([compute_sma(closes.head(len(closes) - i), 50) for i in range(5)][::-1]),
                5,
            ) if len(closes) >= 55 else None,
            "slope_200d": compute_slope(
                pd.Series([compute_sma(closes.head(len(closes) - i), 200) for i in range(5)][::-1]),
                5,
            ) if len(closes) >= 205 else None,
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
        }
    else:
        result["nifty_indicators"] = {}

    # 10. BankNifty indicators
    if banknifty_hist is not None and "close" in banknifty_hist.columns:
        closes = banknifty_hist["close"]
        result["banknifty_indicators"] = {
            "sma_50": compute_sma(closes, 50),
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
        }
    else:
        result["banknifty_indicators"] = {}

    # 11. VIX indicators
    if vix_hist is not None and "close" in vix_hist.columns:
        closes = vix_hist["close"]
        current_vix = closes.iloc[-1] if len(closes) > 0 else None
        result["vix_indicators"] = {
            "current": current_vix,
            "slope_5d": compute_slope(closes, 5),
            "percentile_1y": compute_percentile(current_vix, closes) if current_vix else None,
        }
    else:
        result["vix_indicators"] = {}

    # 12. USDINR indicators
    if usdinr_hist is not None and "close" in usdinr_hist.columns:
        closes = usdinr_hist["close"]
        result["usdinr_indicators"] = {
            "ltp": closes.iloc[-1] if len(closes) > 0 else None,
            "slope_5d": compute_slope(closes, 5),
            "slope_20d": compute_slope(closes, 20),
        }
    else:
        result["usdinr_indicators"] = {}

    if not is_day_mode:
        _refresh_intraday_market_fields(
            result,
            include_constituents=False,
        )
    _cache = result
    _cache_ts = time.time()

    response = dict(result)
    response["mode"] = mode
    return response
