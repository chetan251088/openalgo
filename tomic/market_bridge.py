"""
TOMIC Market Bridge
===================
Bridges live WebSocket ticks into signal-agent data structures:
  - RegimeAgent OHLCV candles + VIX
  - SniperAgent OHLCV candles + benchmark closes
  - VolatilityAgent underlying closes + IV snapshots
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from tomic.agents.regime_agent import RegimeAgent
from tomic.agents.sniper_agent import SniperAgent
from tomic.agents.volatility_agent import VolatilityAgent
from tomic.config import TomicConfig
from tomic.freshness import FreshnessTracker
from tomic.greeks_engine import GreeksEngine
from tomic.ws_data_manager import WSDataManager

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_ltp(payload: Dict[str, Any]) -> float:
    for key in ("ltp", "last_price", "close", "lp", "price"):
        value = _safe_float(payload.get(key), 0.0)
        if value > 0:
            return value
    return 0.0


def _extract_volume(payload: Dict[str, Any]) -> float:
    for key in ("volume", "vtt", "vol", "volume_trade_for_the_day"):
        value = _safe_float(payload.get(key), 0.0)
        if value >= 0:
            return value
    return 0.0


def _extract_iv(payload: Dict[str, Any]) -> Optional[float]:
    for key in ("iv", "implied_volatility", "impliedVolatility"):
        raw = _safe_float(payload.get(key), -1.0)
        if raw > 0:
            # Broker payloads vary: some send IV as 0.18, others as 18.
            return raw / 100.0 if raw > 3 else raw
    return None


def _extract_ts(payload: Dict[str, Any], fallback_wall: float) -> float:
    ts = payload.get("timestamp")
    if ts is None:
        return fallback_wall
    value = _safe_float(ts, 0.0)
    if value <= 0:
        return fallback_wall
    # milliseconds -> seconds
    if value > 1e12:
        return value / 1000.0
    return value


@dataclass
class SymbolSpec:
    exchange: str
    symbol: str

    def to_subscription_dict(self) -> Dict[str, str]:
        return {"exchange": self.exchange, "symbol": self.symbol}

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


@dataclass
class OptionContract:
    underlying: str
    option_type: str
    strike: float
    expiry: datetime


class MinuteCandleBuilder:
    """Aggregates ticks into minute candles."""

    def __init__(self) -> None:
        self._bucket: Optional[int] = None
        self._open = 0.0
        self._high = 0.0
        self._low = 0.0
        self._close = 0.0
        self._volume = 0.0
        self._last_cum_volume = 0.0

    def update(self, price: float, cum_volume: float, ts_seconds: float) -> Optional[Tuple[float, float, float, float, float]]:
        if price <= 0:
            return None

        bucket = int(ts_seconds // 60)
        volume_delta = max(0.0, cum_volume - self._last_cum_volume)
        self._last_cum_volume = max(self._last_cum_volume, cum_volume)

        if self._bucket is None:
            self._bucket = bucket
            self._open = self._high = self._low = self._close = price
            self._volume = volume_delta
            return None

        if bucket == self._bucket:
            self._close = price
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._volume += volume_delta
            return None

        finished = (self._open, self._high, self._low, self._close, self._volume)
        self._bucket = bucket
        self._open = self._high = self._low = self._close = price
        self._volume = volume_delta
        return finished


class OptionIvTracker:
    """Tracks rolling IV stats by underlying."""

    def __init__(self, max_points: int = 512) -> None:
        self.ce_iv: Optional[float] = None
        self.pe_iv: Optional[float] = None
        self.iv_hist: deque[float] = deque(maxlen=max_points)
        self.last_update_wall: float = 0.0

    def update(self, option_type: str, iv: float, wall_time: float) -> None:
        if option_type == "CE":
            self.ce_iv = iv
        elif option_type == "PE":
            self.pe_iv = iv
        else:
            return

        self.iv_hist.append(iv)
        self.last_update_wall = wall_time

    def snapshot(self) -> Optional[Tuple[float, float, float, float, float]]:
        values = [v for v in (self.ce_iv, self.pe_iv) if v is not None]
        if not values:
            return None
        current = sum(values) / len(values)
        iv_low = min(self.iv_hist) if self.iv_hist else current
        iv_high = max(self.iv_hist) if self.iv_hist else current
        put_iv = self.pe_iv if self.pe_iv is not None else current
        call_iv = self.ce_iv if self.ce_iv is not None else current
        return current, iv_high, iv_low, put_iv, call_iv


class TomicMarketBridge:
    """
    Live data bridge between WS ticks and TOMIC signal agents.

    Notes:
      - Uses minute candles from ticks for regime/sniper ingestion.
      - Uses option IV from tick payload when available.
    """

    _OPTION_RE = re.compile(r"^(?P<prefix>[A-Z]+).*?(?P<strike>\d+)(?P<otype>CE|PE)$")
    _NON_OPTION_UNDERLYINGS = {"INDIAVIX", "VIX"}

    def __init__(
        self,
        config: TomicConfig,
        ws_data_manager: WSDataManager,
        freshness_tracker: FreshnessTracker,
        regime_agent: RegimeAgent,
        sniper_agent: SniperAgent,
        volatility_agent: VolatilityAgent,
    ) -> None:
        self._config = config
        self._ws = ws_data_manager
        self._freshness = freshness_tracker
        self._regime = regime_agent
        self._sniper = sniper_agent
        self._vol = volatility_agent

        self._lock = threading.RLock()
        self._running = False

        self._underlyings = self._parse_symbol_specs(
            os.getenv("TOMIC_FEED_UNDERLYINGS", "").strip(),
            default_specs=self._default_underlyings(),
        )
        default_sniper_specs = [
            spec for spec in self._underlyings
            if spec.symbol.upper() not in self._NON_OPTION_UNDERLYINGS
        ]
        self._sniper_symbols = self._parse_symbol_specs(
            os.getenv("TOMIC_SNIPER_SYMBOLS", "").strip(),
            default_specs=default_sniper_specs,
        )
        raw_option_symbols = os.getenv("TOMIC_FEED_OPTION_SYMBOLS", "").strip()
        self._option_symbols = self._parse_symbol_specs(raw_option_symbols, default_specs=[])
        self._auto_option_mode = len(self._option_symbols) == 0
        self._auto_option_symbols: Dict[str, List[SymbolSpec]] = {}
        self._auto_option_expiry_by_underlying: Dict[str, str] = {}
        self._auto_option_expiries_by_underlying: Dict[str, List[str]] = {}
        self._auto_option_last_refresh_wall: Dict[str, float] = {}
        self._auto_option_last_ltp: Dict[str, float] = {}
        self._auto_option_recenter_points: Dict[str, float] = {}
        self._auto_option_refresh_inflight: Set[str] = set()
        self._auto_option_expiry_cache: Dict[Tuple[str, str], Tuple[List[str], float]] = {}
        self._auto_option_refresh_sec = max(
            15.0,
            _safe_float(os.getenv("TOMIC_OPTION_AUTO_REFRESH_SEC"), 90.0),
        )
        self._auto_option_strike_span = max(
            1,
            int(_safe_float(os.getenv("TOMIC_OPTION_AUTO_STRIKE_SPAN"), 2.0)),
        )
        self._auto_option_recenter_steps = max(
            0.5,
            _safe_float(os.getenv("TOMIC_OPTION_AUTO_RECENTER_STEPS"), 1.0),
        )
        self._auto_option_expiry_count = max(
            1,
            int(_safe_float(os.getenv("TOMIC_OPTION_AUTO_EXPIRY_COUNT"), 2.0)),
        )
        self._auto_option_strike_offsets = self._parse_strike_offsets(
            os.getenv("TOMIC_OPTION_AUTO_STRIKE_OFFSETS", "-1,0,1").strip(),
            default_span=self._auto_option_strike_span,
        )
        self._auto_option_expiry_cache_sec = max(
            60.0,
            _safe_float(os.getenv("TOMIC_OPTION_EXPIRY_CACHE_SEC"), 600.0),
        )
        self._vix_symbol = self._parse_symbol_spec(
            os.getenv("TOMIC_FEED_VIX_SYMBOL", "NSE_INDEX:INDIAVIX")
        )

        self._underlying_keys = {spec.key for spec in self._underlyings}
        self._sniper_keys = {spec.key for spec in self._sniper_symbols}
        self._benchmark_symbol = self._resolve_benchmark_symbol()
        self._benchmark_key = self._benchmark_symbol.key if self._benchmark_symbol else ""
        self._vix_key = self._vix_symbol.key if self._vix_symbol else ""

        self._underlying_candles: Dict[str, MinuteCandleBuilder] = {
            spec.key: MinuteCandleBuilder() for spec in self._underlyings
        }
        self._sniper_candles: Dict[str, MinuteCandleBuilder] = {
            spec.key: MinuteCandleBuilder() for spec in self._sniper_symbols
        }
        self._option_iv_trackers: Dict[str, OptionIvTracker] = {}
        self._option_contract_cache: Dict[str, OptionContract] = {}
        self._synthetic_iv_cache: Dict[str, Tuple[float, float]] = {}
        self._iv_fallback_enabled = str(
            os.getenv("TOMIC_OPTION_IV_FALLBACK_ENABLED", "true")
        ).strip().lower() not in {"0", "false", "no", "off"}
        self._iv_fallback_cache_s = max(
            0.25,
            _safe_float(os.getenv("TOMIC_OPTION_IV_FALLBACK_CACHE_S"), 1.0),
        )
        self._iv_engine = GreeksEngine(
            risk_free_rate=max(0.0, _safe_float(os.getenv("TOMIC_OPTION_IV_RF"), 0.06)),
            dividend_yield=max(0.0, _safe_float(os.getenv("TOMIC_OPTION_IV_DIVIDEND_YIELD"), 0.0)),
        )

        self._stats: Dict[str, Any] = {
            "ticks_total": 0,
            "underlying_ticks": 0,
            "option_ticks": 0,
            "candles_built_regime": 0,
            "candles_built_sniper": 0,
            "vol_iv_updates": 0,
            "last_tick_wall": 0.0,
            "last_error": "",
            "subscriptions": 0,
            "option_symbol_mode": "auto" if self._auto_option_mode else "manual",
            "auto_option_refreshes": 0,
            "auto_option_subscriptions": 0,
            "auto_option_last_error": "",
            "option_iv_fallback_enabled": bool(self._iv_fallback_enabled),
            "option_iv_from_feed": 0,
            "option_iv_from_synthetic": 0,
            "option_iv_missing": 0,
        }

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def start(self) -> None:
        with self._lock:
            if self._running:
                return

            self._running = True
            self._ws.set_tick_callback(self._on_tick)
            subscriptions = self._build_subscriptions()
            if subscriptions:
                self._ws.subscribe([s.to_subscription_dict() for s in subscriptions], mode="QUOTE")
                self._stats["subscriptions"] = len(subscriptions)
            logger.info("TOMIC market bridge started with %d subscriptions", len(subscriptions))

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._ws.set_tick_callback(None)
            logger.info("TOMIC market bridge stopped")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            age = -1.0
            if self._stats["last_tick_wall"] > 0:
                age = max(0.0, time.time() - self._stats["last_tick_wall"])
            return {
                **self._stats,
                "last_tick_age_s": round(age, 2) if age >= 0 else -1.0,
                "underlyings": [s.key for s in self._underlyings],
                "sniper_symbols": [s.key for s in self._sniper_symbols],
                "option_symbols": [s.key for s in self._all_option_symbols()],
                "benchmark": self._benchmark_key,
                "option_expiry": dict(self._auto_option_expiry_by_underlying),
                "option_expiries": dict(self._auto_option_expiries_by_underlying),
                "auto_option_refresh_inflight": sorted(self._auto_option_refresh_inflight),
                "auto_option_refresh_sec": self._auto_option_refresh_sec,
                "auto_option_strike_span": self._auto_option_strike_span,
                "auto_option_expiry_count": self._auto_option_expiry_count,
                "auto_option_strike_offsets": list(self._auto_option_strike_offsets),
            }

    def _on_tick(self, tick: Dict[str, Any]) -> None:
        with self._lock:
            if not self._running:
                return

            try:
                symbol = self._normalize_symbol(str(tick.get("symbol", "")))
                exchange = str(tick.get("exchange", "")).strip().upper()
                if not symbol or not exchange:
                    return

                payload = tick.get("data", {}) if isinstance(tick.get("data"), dict) else tick
                ltp = _extract_ltp(payload)
                if ltp <= 0:
                    return

                wall_time = _safe_float(tick.get("_recv_wall"), time.time())
                ts = _extract_ts(payload, wall_time)
                volume = _extract_volume(payload)
                symbol_key = f"{exchange}:{symbol}"

                self._stats["ticks_total"] += 1
                self._stats["last_tick_wall"] = wall_time

                # Keep freshness tracker warm.
                self._freshness.update_quote(symbol)
                if symbol.endswith("CE") or symbol.endswith("PE"):
                    self._freshness.update_option_quote(symbol)

                if symbol_key in self._underlying_keys:
                    self._stats["underlying_ticks"] += 1
                    self._maybe_refresh_auto_option_symbols(
                        underlying=symbol,
                        exchange=exchange,
                        ltp=ltp,
                        wall_time=wall_time,
                    )
                    self._handle_underlying_tick(symbol_key, symbol, exchange, ltp, volume, ts)

                if symbol_key in self._sniper_keys:
                    self._handle_sniper_tick(symbol_key, symbol, ltp, volume, ts)

                if symbol_key == self._vix_key:
                    self._regime.feed_vix(ltp)
                    self._freshness.update_vix()

                if symbol.endswith("CE") or symbol.endswith("PE"):
                    self._stats["option_ticks"] += 1
                    self._handle_option_tick(symbol, exchange, payload, wall_time)

            except Exception as exc:
                self._stats["last_error"] = str(exc)
                logger.debug("TOMIC market bridge tick error: %s", exc)

    def _handle_underlying_tick(
        self,
        symbol_key: str,
        symbol: str,
        exchange: str,
        ltp: float,
        volume: float,
        ts: float,
    ) -> None:
        builder = self._underlying_candles.get(symbol_key)
        if builder is None:
            return

        candle = builder.update(ltp, volume, ts)
        if candle is None:
            return

        open_, high, low, close, vol = candle
        # Regime must be benchmark-driven (single underlying), not a mixed stream.
        if symbol_key == self._benchmark_key:
            self._regime.feed_candle(high=high, low=low, close=close, volume=vol)
            self._stats["candles_built_regime"] += 1
            self._sniper.feed_benchmark(close)
        self._vol.feed_price(symbol, close)

    def _handle_sniper_tick(self, symbol_key: str, symbol: str, ltp: float, volume: float, ts: float) -> None:
        builder = self._sniper_candles.get(symbol_key)
        if builder is None:
            return

        candle = builder.update(ltp, volume, ts)
        if candle is None:
            return

        open_, high, low, close, vol = candle
        self._sniper.feed_candle(
            instrument=symbol,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=vol,
        )
        self._stats["candles_built_sniper"] += 1

    def _handle_option_tick(self, symbol: str, exchange: str, payload: Dict[str, Any], wall_time: float) -> None:
        parsed = self._parse_option_symbol(symbol, exchange)
        if parsed is None:
            return

        underlying, option_type = parsed
        iv, source = self._resolve_option_iv(
            symbol=symbol,
            exchange=exchange,
            payload=payload,
            wall_time=wall_time,
            underlying_hint=underlying,
            option_type_hint=option_type,
        )
        if iv is None:
            self._stats["option_iv_missing"] += 1
            return

        if source == "synthetic":
            self._stats["option_iv_from_synthetic"] += 1
        else:
            self._stats["option_iv_from_feed"] += 1

        tracker = self._option_iv_trackers.setdefault(underlying, OptionIvTracker())
        tracker.update(option_type=option_type, iv=iv, wall_time=wall_time)
        snapshot = tracker.snapshot()
        if snapshot is None:
            return

        current_iv, iv_high, iv_low, put_iv, call_iv = snapshot
        self._vol.feed_iv_data(
            underlying=underlying,
            current_iv=current_iv,
            iv_52w_high=iv_high,
            iv_52w_low=iv_low,
            put_iv_25d=put_iv,
            call_iv_25d=call_iv,
            front_iv=current_iv,
            back_iv=current_iv * 1.02,
        )
        self._freshness.update_iv()
        self._stats["vol_iv_updates"] += 1

    def _resolve_option_iv(
        self,
        symbol: str,
        exchange: str,
        payload: Dict[str, Any],
        wall_time: float,
        underlying_hint: str,
        option_type_hint: str,
    ) -> Tuple[Optional[float], str]:
        direct_iv = _extract_iv(payload)
        if direct_iv is not None and direct_iv > 0:
            return direct_iv, "feed"
        if not self._iv_fallback_enabled:
            return None, "missing"

        symbol_key = f"{exchange}:{symbol}".upper()
        cached = self._synthetic_iv_cache.get(symbol_key)
        if cached and (wall_time - cached[1]) <= self._iv_fallback_cache_s:
            return cached[0], "synthetic"

        option_price = _extract_ltp(payload)
        if option_price <= 0:
            return None, "missing"

        contract = self._resolve_option_contract(
            symbol=symbol,
            exchange=exchange,
            underlying_hint=underlying_hint,
            option_type_hint=option_type_hint,
        )
        if contract is None:
            return None, "missing"

        spot = self._resolve_underlying_ltp(contract.underlying, exchange)
        if spot <= 0:
            return None, "missing"

        now_dt = datetime.fromtimestamp(float(wall_time or time.time()))
        days_to_expiry = max(0.0, (contract.expiry - now_dt).total_seconds() / 86400.0)
        if days_to_expiry <= 0:
            return None, "missing"

        flag = "c" if contract.option_type == "CE" else "p"
        try:
            greeks = self._iv_engine.compute(
                spot=spot,
                strike=float(contract.strike),
                expiry_days=days_to_expiry,
                option_price=option_price,
                option_type=flag,
            )
            iv = float(greeks.iv or 0.0)
        except Exception:
            return None, "missing"

        if iv <= 0 or iv > 5:
            return None, "missing"

        self._synthetic_iv_cache[symbol_key] = (iv, float(wall_time or time.time()))
        return iv, "synthetic"

    @staticmethod
    def _resolve_expiry_time_for_exchange(exchange: str) -> Tuple[int, int]:
        ex = str(exchange or "").strip().upper()
        if ex == "MCX":
            return 23, 30
        if ex == "CDS":
            return 12, 30
        return 15, 30

    def _resolve_option_contract(
        self,
        symbol: str,
        exchange: str,
        underlying_hint: str,
        option_type_hint: str,
    ) -> Optional[OptionContract]:
        key = f"{exchange}:{symbol}".upper()
        cached = self._option_contract_cache.get(key)
        if cached is not None:
            return cached

        contract = self._resolve_option_contract_from_symbol(
            symbol=symbol,
            exchange=exchange,
            underlying_hint=underlying_hint,
            option_type_hint=option_type_hint,
        )
        if contract is None:
            contract = self._resolve_option_contract_from_db(
                symbol=symbol,
                exchange=exchange,
                underlying_hint=underlying_hint,
                option_type_hint=option_type_hint,
            )
        if contract is not None:
            self._option_contract_cache[key] = contract
        return contract

    def _resolve_option_contract_from_symbol(
        self,
        symbol: str,
        exchange: str,
        underlying_hint: str,
        option_type_hint: str,
    ) -> Optional[OptionContract]:
        try:
            from services.option_greeks_service import parse_option_symbol

            base, expiry_dt, strike, opt_type = parse_option_symbol(symbol, exchange)
            underlying = str(base or underlying_hint or "").strip().upper()
            option_type = str(opt_type or option_type_hint or "").strip().upper()
            strike_f = float(strike or 0.0)
            if not underlying or option_type not in {"CE", "PE"} or strike_f <= 0:
                return None
            return OptionContract(
                underlying=underlying,
                option_type=option_type,
                strike=strike_f,
                expiry=expiry_dt,
            )
        except Exception:
            return None

    def _resolve_option_contract_from_db(
        self,
        symbol: str,
        exchange: str,
        underlying_hint: str,
        option_type_hint: str,
    ) -> Optional[OptionContract]:
        try:
            from database.symbol import SymToken, db_session

            row = (
                db_session.query(SymToken.name, SymToken.expiry, SymToken.strike)
                .filter(
                    SymToken.symbol == str(symbol or "").strip().upper(),
                    SymToken.exchange == str(exchange or "").strip().upper(),
                )
                .first()
            )
            if not row:
                return None

            underlying = str(row[0] or underlying_hint or "").strip().upper()
            raw_expiry = str(row[1] or "").strip().upper()
            strike = float(row[2] or 0.0)
            option_type = str(option_type_hint or "").strip().upper()
            if option_type not in {"CE", "PE"}:
                upper_symbol = str(symbol or "").strip().upper()
                if upper_symbol.endswith("CE"):
                    option_type = "CE"
                elif upper_symbol.endswith("PE"):
                    option_type = "PE"

            parsed_expiry = self._parse_expiry(raw_expiry)
            if parsed_expiry is None:
                return None
            hour, minute = self._resolve_expiry_time_for_exchange(exchange)
            expiry_dt = parsed_expiry.replace(hour=hour, minute=minute, second=0, microsecond=0)

            if not underlying or option_type not in {"CE", "PE"} or strike <= 0:
                return None
            return OptionContract(
                underlying=underlying,
                option_type=option_type,
                strike=strike,
                expiry=expiry_dt,
            )
        except Exception:
            return None

    @staticmethod
    def _resolve_underlying_exchange(underlying: str, option_exchange: str) -> str:
        token = str(underlying or "").strip().upper()
        if token in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "INDIAVIX"}:
            return "NSE_INDEX"
        if token in {"SENSEX", "BANKEX"}:
            return "BSE_INDEX"
        opt_ex = str(option_exchange or "").strip().upper()
        if opt_ex == "BFO":
            return "BSE"
        if opt_ex == "NFO":
            return "NSE"
        return ""

    def _resolve_underlying_ltp(self, underlying: str, option_exchange: str) -> float:
        normalized = self._base_underlying(underlying)
        if not normalized:
            return 0.0

        exchange = self._resolve_underlying_exchange(normalized, option_exchange)
        ltp = float(self._ws.get_last_price(symbol=normalized, exchange=exchange, max_age_s=30.0) or 0.0)
        if ltp > 0:
            return ltp

        # Fallback to recent underlying ticks cached by the bridge.
        ltp = float(self._auto_option_last_ltp.get(normalized, 0.0) or 0.0)
        if ltp > 0:
            return ltp
        return 0.0

    def _build_subscriptions(self) -> List[SymbolSpec]:
        all_specs: Dict[str, SymbolSpec] = {}
        for spec in [*self._underlyings, *self._sniper_symbols, *self._all_option_symbols()]:
            all_specs[spec.key] = spec
        if self._vix_symbol is not None:
            all_specs[self._vix_symbol.key] = self._vix_symbol
        return list(all_specs.values())

    def _all_option_symbols(self) -> List[SymbolSpec]:
        all_specs: Dict[str, SymbolSpec] = {}
        for spec in self._option_symbols:
            all_specs[spec.key] = spec
        for specs in self._auto_option_symbols.values():
            for spec in specs:
                all_specs[spec.key] = spec
        return list(all_specs.values())

    def _maybe_refresh_auto_option_symbols(
        self,
        underlying: str,
        exchange: str,
        ltp: float,
        wall_time: float,
    ) -> None:
        if not self._auto_option_mode or ltp <= 0:
            return

        base = str(underlying or "").strip().upper()
        if not base or base in self._NON_OPTION_UNDERLYINGS or "VIX" in base:
            return

        if base in self._auto_option_refresh_inflight:
            return

        existing = self._auto_option_symbols.get(base, [])
        last_refresh = self._auto_option_last_refresh_wall.get(base, 0.0)
        last_ltp = self._auto_option_last_ltp.get(base, 0.0)
        recenter_points = self._auto_option_recenter_points.get(base, 0.0)

        initial_seed = len(existing) == 0
        time_due = (wall_time - last_refresh) >= self._auto_option_refresh_sec
        move_due = (
            recenter_points > 0
            and last_ltp > 0
            and abs(ltp - last_ltp) >= recenter_points
        )
        if not (initial_seed or time_due or move_due):
            return

        self._auto_option_refresh_inflight.add(base)
        thread = threading.Thread(
            target=self._refresh_auto_option_symbols_worker,
            args=(base, exchange.upper(), ltp, wall_time),
            daemon=True,
            name=f"tomic-opt-refresh-{base}",
        )
        thread.start()

    def _refresh_auto_option_symbols_worker(
        self,
        underlying: str,
        exchange: str,
        ltp: float,
        wall_time: float,
    ) -> None:
        discovered: List[SymbolSpec] = []
        expiries: List[str] = []
        recenter_points = 0.0
        err_msg = ""
        try:
            discovered, expiries, recenter_points = self._discover_option_symbols_for_underlying(
                underlying=underlying,
                exchange=exchange,
                ltp=ltp,
            )
            if isinstance(expiries, str):
                expiries = [expiries] if expiries else []
        except Exception as exc:
            err_msg = str(exc)
            logger.debug(
                "TOMIC auto option discovery failed for %s: %s",
                underlying,
                exc,
            )

        with self._lock:
            self._auto_option_refresh_inflight.discard(underlying)
            self._auto_option_last_refresh_wall[underlying] = wall_time
            if ltp > 0:
                self._auto_option_last_ltp[underlying] = ltp
            if recenter_points > 0:
                self._auto_option_recenter_points[underlying] = recenter_points
            if expiries:
                self._auto_option_expiries_by_underlying[underlying] = list(expiries)
                self._auto_option_expiry_by_underlying[underlying] = expiries[0]

            if err_msg:
                self._stats["auto_option_last_error"] = err_msg
                return

            if not discovered:
                self._stats["auto_option_last_error"] = (
                    f"no option symbols discovered for {underlying}"
                )
                return

            current = self._auto_option_symbols.get(underlying, [])
            current_keys = {spec.key for spec in current}
            next_keys = {spec.key for spec in discovered}

            to_remove = [spec for spec in current if spec.key not in next_keys]
            to_add = [spec for spec in discovered if spec.key not in current_keys]
            self._auto_option_symbols[underlying] = discovered

            if to_remove:
                self._ws.remove_symbols([spec.to_subscription_dict() for spec in to_remove])
            if to_add:
                self._ws.add_symbols([spec.to_subscription_dict() for spec in to_add])

            self._stats["subscriptions"] = len(self._build_subscriptions())
            self._stats["auto_option_subscriptions"] = sum(
                len(specs) for specs in self._auto_option_symbols.values()
            )
            self._stats["auto_option_refreshes"] += 1
            self._stats["auto_option_last_error"] = ""

    def _discover_option_symbols_for_underlying(
        self,
        underlying: str,
        exchange: str,
        ltp: float,
    ) -> Tuple[List[SymbolSpec], List[str], float]:
        from services.option_chain_service import get_option_symbols_for_chain
        from services.option_symbol_service import (
            find_atm_strike_from_actual,
            get_available_strikes,
            get_option_exchange,
        )

        base_underlying = self._base_underlying(underlying)
        if not base_underlying:
            return [], [], 0.0
        options_exchange = get_option_exchange(exchange)
        expiries = self._resolve_front_expiries(
            underlying=base_underlying,
            options_exchange=options_exchange,
        )
        if not expiries:
            return [], [], 0.0

        discovered: Dict[str, SymbolSpec] = {}
        recenter_points = 0.0
        for expiry in expiries:
            strikes = get_available_strikes(base_underlying, expiry, "CE", options_exchange)
            if not strikes:
                strikes = get_available_strikes(base_underlying, expiry, "PE", options_exchange)
            if not strikes:
                continue

            atm = find_atm_strike_from_actual(ltp, strikes)
            if atm is None:
                continue

            try:
                atm_index = strikes.index(atm)
            except ValueError:
                continue

            selected_strikes = self._select_interested_strikes(strikes, atm_index)
            if not selected_strikes:
                selected_strikes = [atm]

            strikes_with_labels = [
                {"strike": strike, "ce_label": "", "pe_label": ""}
                for strike in selected_strikes
            ]
            chain = get_option_symbols_for_chain(
                base_symbol=base_underlying,
                expiry_date=expiry,
                strikes_with_labels=strikes_with_labels,
                exchange=options_exchange,
            )

            for row in chain:
                ce_symbol = str((row.get("ce") or {}).get("symbol") or "").strip().upper()
                pe_symbol = str((row.get("pe") or {}).get("symbol") or "").strip().upper()
                if ce_symbol:
                    spec = SymbolSpec(exchange=options_exchange, symbol=ce_symbol)
                    discovered[spec.key] = spec
                if pe_symbol:
                    spec = SymbolSpec(exchange=options_exchange, symbol=pe_symbol)
                    discovered[spec.key] = spec

            if recenter_points <= 0:
                strike_step = self._infer_strike_step(strikes)
                if strike_step > 0:
                    recenter_points = max(strike_step, strike_step * self._auto_option_recenter_steps)

        return list(discovered.values()), expiries, recenter_points

    def _resolve_front_expiries(self, underlying: str, options_exchange: str) -> List[str]:
        from database.symbol import get_distinct_expiries

        cache_key = (underlying.upper(), options_exchange.upper())
        now = time.time()
        cached = self._auto_option_expiry_cache.get(cache_key)
        if cached and (now - cached[1]) <= self._auto_option_expiry_cache_sec:
            return list(cached[0])

        raw_expiries = get_distinct_expiries(exchange=options_exchange, underlying=underlying)
        fronts = self._select_front_expiries(raw_expiries, max_count=self._auto_option_expiry_count)
        if fronts:
            self._auto_option_expiry_cache[cache_key] = (list(fronts), now)
        return fronts

    @classmethod
    def _select_front_expiries(cls, expiries: List[str], max_count: int = 2) -> List[str]:
        parsed_dates: List[datetime] = []
        for raw in expiries:
            parsed = cls._parse_expiry(raw)
            if parsed is not None:
                parsed_dates.append(parsed)

        if not parsed_dates:
            return []

        unique_sorted = sorted(set(parsed_dates))
        today = datetime.now().date()
        future = [exp for exp in unique_sorted if exp.date() >= today]
        source = future if future else unique_sorted
        count = max(1, int(max_count or 1))
        if future:
            selected = source[:count]
        else:
            selected = source[-count:]
        return [exp.strftime("%d%b%y").upper() for exp in selected]

    @classmethod
    def _select_front_expiry(cls, expiries: List[str]) -> str:
        fronts = cls._select_front_expiries(expiries, max_count=1)
        return fronts[0] if fronts else ""

    @staticmethod
    def _parse_expiry(raw: str) -> Optional[datetime]:
        token = str(raw or "").strip()
        if not token:
            return None
        upper = token.upper()
        for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d%b%y", "%d%b%Y"):
            try:
                return datetime.strptime(upper, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _infer_strike_step(strikes: List[float]) -> float:
        if len(strikes) < 2:
            return 0.0
        diffs = [
            abs(float(strikes[idx]) - float(strikes[idx - 1]))
            for idx in range(1, len(strikes))
            if float(strikes[idx]) != float(strikes[idx - 1])
        ]
        return min(diffs) if diffs else 0.0

    def _select_interested_strikes(self, strikes: List[float], atm_index: int) -> List[float]:
        selected_indices: Set[int] = set()
        for offset in self._auto_option_strike_offsets:
            idx = atm_index + offset
            if 0 <= idx < len(strikes):
                selected_indices.add(idx)

        if not selected_indices:
            left = max(0, atm_index - self._auto_option_strike_span)
            right = min(len(strikes), atm_index + self._auto_option_strike_span + 1)
            selected_indices.update(range(left, right))

        ordered = sorted(selected_indices)
        return [strikes[idx] for idx in ordered]

    def _resolve_benchmark_symbol(self) -> Optional[SymbolSpec]:
        always = [str(v).upper() for v in self._config.universe.always_included]
        for pref in always:
            for spec in self._underlyings:
                if spec.symbol.upper() == pref:
                    return spec
        return self._underlyings[0] if self._underlyings else None

    def _default_underlyings(self) -> List[SymbolSpec]:
        defaults: List[SymbolSpec] = []
        for raw in self._config.universe.always_included:
            symbol = str(raw).upper()
            if symbol == "SENSEX":
                defaults.append(SymbolSpec(exchange="BSE_INDEX", symbol=symbol))
            else:
                defaults.append(SymbolSpec(exchange="NSE_INDEX", symbol=symbol))
        if not any(s.symbol == "INDIAVIX" for s in defaults):
            defaults.append(SymbolSpec(exchange="NSE_INDEX", symbol="INDIAVIX"))
        return defaults

    @classmethod
    def _parse_symbol_spec(cls, raw: str) -> Optional[SymbolSpec]:
        token = (raw or "").strip()
        if not token:
            return None

        if ":" in token:
            exchange, symbol = token.split(":", 1)
            exchange = exchange.strip().upper()
            symbol = symbol.strip().upper()
            if exchange and symbol:
                return SymbolSpec(exchange=exchange, symbol=symbol)

        symbol = token.upper()
        if symbol in {"SENSEX", "BANKEX"}:
            return SymbolSpec(exchange="BSE_INDEX", symbol=symbol)
        if symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX"}:
            return SymbolSpec(exchange="NSE_INDEX", symbol=symbol)
        return SymbolSpec(exchange="NSE", symbol=symbol)

    @staticmethod
    def _parse_strike_offsets(raw: str, default_span: int) -> List[int]:
        if not raw:
            return list(range(-default_span, default_span + 1))

        parsed: Set[int] = set()
        for token in raw.split(","):
            t = str(token or "").strip()
            if not t:
                continue
            try:
                parsed.add(int(t))
            except ValueError:
                continue

        if not parsed:
            return list(range(-default_span, default_span + 1))
        return sorted(parsed)

    @classmethod
    def _parse_symbol_specs(cls, raw: str, default_specs: List[SymbolSpec]) -> List[SymbolSpec]:
        if not raw:
            return list(default_specs)

        specs: List[SymbolSpec] = []
        for token in raw.split(","):
            spec = cls._parse_symbol_spec(token)
            if spec is not None:
                specs.append(spec)
        return specs or list(default_specs)

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        token = str(raw or "").strip().upper()
        if not token:
            return ""

        if ":" in token:
            _, right = token.split(":", 1)
            token = right.strip().upper() or token

        if "." in token:
            left, right = token.split(".", 1)
            if right.strip().upper() in {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "NSE_INDEX", "BSE_INDEX"}:
                token = left.strip().upper()

        return token

    @staticmethod
    def _base_underlying(symbol: str) -> str:
        token = TomicMarketBridge._normalize_symbol(symbol)
        if not token:
            return ""
        compact = token.replace(" ", "").replace("-", "").replace("_", "")
        if not compact:
            return ""
        if compact.startswith("BANKNIFTY"):
            return "BANKNIFTY"
        if compact.startswith("FINNIFTY"):
            return "FINNIFTY"
        if compact.startswith("MIDCPNIFTY"):
            return "MIDCPNIFTY"
        if compact.startswith("NIFTY"):
            return "NIFTY"
        if compact.startswith("SENSEX"):
            return "SENSEX"
        if compact.startswith("BANKEX"):
            return "BANKEX"
        if "VIX" in compact:
            return "INDIAVIX"
        match = re.match(r"^([A-Z]+)\d{2}[A-Z]{3}\d{2}(?:\d+(?:CE|PE)|FUT)?$", compact)
        return match.group(1) if match else compact

    def _parse_option_symbol(self, symbol: str, exchange: str) -> Optional[Tuple[str, str]]:
        match = self._OPTION_RE.match(symbol.upper())
        if not match:
            return None

        option_type = match.group("otype")
        prefix = match.group("prefix")

        # Heuristics for common index-option prefixes.
        if prefix.startswith("BANKNIFTY"):
            underlying = "BANKNIFTY"
        elif prefix.startswith("FINNIFTY"):
            underlying = "FINNIFTY"
        elif prefix.startswith("MIDCPNIFTY"):
            underlying = "MIDCPNIFTY"
        elif prefix.startswith("NIFTY"):
            underlying = "NIFTY"
        elif prefix.startswith("SENSEX") or exchange == "BFO":
            underlying = "SENSEX"
        elif prefix == "B" and exchange == "BFO":
            underlying = "SENSEX"
        else:
            # Fall back to the leading alpha prefix.
            underlying = prefix

        return underlying, option_type
