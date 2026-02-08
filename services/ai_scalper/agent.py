from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from concurrent.futures import ThreadPoolExecutor

import websocket

from services.option_chain_service import get_option_chain
from utils.logging import get_logger

from .advisor import build_advisor
from .config import AgentConfig, AdvisorConfig, ExecutionConfig, PlaybookConfig, RiskConfig
from .execution_engine import ExecutionEngine
from .feature_cache import FeatureCache
from .log_store import get_auto_trade_log_store
from .learning import LearningConfig, LearningOrchestrator
from .log_store import get_auto_trade_log_store
from .playbooks import PlaybookManager, is_expiry_day
from .risk_engine import RiskEngine

logger = get_logger(__name__)


@dataclass
class AvgSession:
    end_ts: float
    next_ts: float
    lots_added: int


class CandleTracker:
    def __init__(self, period: int = 9) -> None:
        self.period = period
        self.multiplier = 2 / (period + 1)
        self.current: Optional[Dict[str, float]] = None
        self.last_close: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.ema: Optional[float] = None

    def update(self, price: float, ts: float) -> None:
        candle_time = int(ts // 60) * 60
        if self.current is None or self.current.get("time") != candle_time:
            if self.current is not None:
                self.prev_close = self.last_close
                self.last_close = self.current.get("close")
                if self.last_close is not None:
                    self.ema = (
                        self.last_close
                        if self.ema is None
                        else (self.last_close - self.ema) * self.multiplier + self.ema
                    )
            self.current = {"time": candle_time, "open": price, "high": price, "low": price, "close": price}
            return
        self.current["high"] = max(self.current.get("high", price), price)
        self.current["low"] = min(self.current.get("low", price), price)
        self.current["close"] = price


def _ema_last(candles: list[Dict[str, float]], period: int) -> Optional[float]:
    if len(candles) < period:
        return None
    ema = sum(c["close"] for c in candles[:period]) / period
    multiplier = 2 / (period + 1)
    for candle in candles[period:]:
        ema = (candle["close"] - ema) * multiplier + ema
    return ema


def _vwap_last(candles: list[Dict[str, float]]) -> Optional[float]:
    if not candles:
        return None
    cum_tp = 0.0
    count = 0
    current_day = None
    vwap = None
    for candle in candles:
        day = datetime.fromtimestamp(candle["time"]).date()
        if day != current_day:
            cum_tp = 0.0
            count = 0
            current_day = day
        tp = (candle["high"] + candle["low"] + candle["close"]) / 3
        cum_tp += tp
        count += 1
        vwap = cum_tp / count
    return vwap


def _supertrend_last(candles: list[Dict[str, float]], atr_period: int = 10, mult: float = 3.0) -> Optional[float]:
    if len(candles) < atr_period + 1:
        return None
    tr: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr: list[float] = [0.0 for _ in candles]
    atr[atr_period - 1] = sum(tr[:atr_period]) / atr_period
    for i in range(atr_period, len(candles)):
        atr[i] = (atr[i - 1] * (atr_period - 1) + tr[i]) / atr_period
    prev_upper = None
    prev_lower = None
    trend = 1
    st_value = None
    for i in range(atr_period - 1, len(candles)):
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        basic_upper = hl2 + mult * atr[i]
        basic_lower = hl2 - mult * atr[i]
        if i == atr_period - 1:
            upper = basic_upper
            lower = basic_lower
            trend = 1 if candles[i]["close"] > upper else -1
        else:
            assert prev_upper is not None and prev_lower is not None
            upper = basic_upper if (basic_upper < prev_upper or candles[i - 1]["close"] > prev_upper) else prev_upper
            lower = basic_lower if (basic_lower > prev_lower or candles[i - 1]["close"] < prev_lower) else prev_lower
            if trend == 1 and candles[i]["close"] < lower:
                trend = -1
            elif trend == -1 and candles[i]["close"] > upper:
                trend = 1
        prev_upper = upper
        prev_lower = lower
        st_value = lower if trend == 1 else upper
    return st_value


def _rsi_last(candles: list[Dict[str, float]], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = candles[i]["close"] - candles[i - 1]["close"]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
    for i in range(period + 1, len(candles)):
        change = candles[i]["close"] - candles[i - 1]["close"]
        gain = max(change, 0)
        loss = max(-change, 0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
    return rsi


def _adx_last(candles: list[Dict[str, float]], period: int = 14) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(candles) < period + 2:
        return None, None, None
    tr: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(candles)):
        high_diff = candles[i]["high"] - candles[i - 1]["high"]
        low_diff = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0.0)
        minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0.0)
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    tr14 = sum(tr[:period])
    plus14 = sum(plus_dm[:period])
    minus14 = sum(minus_dm[:period])
    if tr14 == 0:
        return None, None, None
    plus_di = 100 * (plus14 / tr14)
    minus_di = 100 * (minus14 / tr14)
    dx = 0.0 if (plus_di + minus_di) == 0 else 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    dx_list = [dx]
    adx = None
    for i in range(period, len(tr)):
        tr14 = tr14 - (tr14 / period) + tr[i]
        plus14 = plus14 - (plus14 / period) + plus_dm[i]
        minus14 = minus14 - (minus14 / period) + minus_dm[i]
        if tr14 == 0:
            continue
        plus_di = 100 * (plus14 / tr14)
        minus_di = 100 * (minus14 / tr14)
        dx = 0.0 if (plus_di + minus_di) == 0 else 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        dx_list.append(dx)
        if len(dx_list) == period:
            adx = sum(dx_list) / period
        elif len(dx_list) > period and adx is not None:
            adx = ((adx * (period - 1)) + dx) / period
    return adx, plus_di, minus_di


class AutoScalperAgent(threading.Thread):
    def __init__(
        self,
        agent_config: AgentConfig,
        risk_config: RiskConfig,
        playbook_config: PlaybookConfig,
        advisor_config: AdvisorConfig,
        learning_config: LearningConfig,
        execution_config: ExecutionConfig,
        api_key: str,
        ws_url: str,
    ) -> None:
        super().__init__(daemon=True)
        self.agent_config = agent_config
        self.risk_config = risk_config
        self.playbook_config = playbook_config
        self.advisor_config = advisor_config
        self.execution_config = execution_config
        self.api_key = api_key
        self.ws_url = ws_url

        self.feature_cache = FeatureCache()
        self.risk_engine = RiskEngine(risk_config)
        self.playbook_manager = PlaybookManager(playbook_config)
        self.execution = ExecutionEngine(execution_config, api_key, agent_config.paper_mode, agent_config.assist_only)
        self.learning = LearningOrchestrator(learning_config, agent_config.tick_size)
        self.active_trade_id: Optional[str] = None

        self.advisor = build_advisor(advisor_config)
        self.advisor_lock = threading.Lock()
        self.last_advisor_ts = 0.0
        self.pending_advice: Optional[dict] = None
        self.advisor_thread_pool: ThreadPoolExecutor | None = None
        self.advisor_future = None

        self.stop_event = threading.Event()
        self.ws_app: websocket.WebSocketApp | None = None
        self.ws_lock = threading.Lock()

        self.last_any_tick_ts: float = 0.0
        self.flip_cooldown_until: float = 0.0
        self.avg_session: Optional[AvgSession] = None
        self.tp_price: Optional[float] = None
        self.sl_price: Optional[float] = None
        self.trailing_anchor: Optional[float] = None
        self.sl_trailing: bool = False
        self.last_signal: str = ""
        self.last_roll_underlying: Optional[float] = None
        self.last_underlying: Optional[float] = None
        self.last_underlying_tick: Optional[float] = None
        self.underlying_momentum_dir: Optional[str] = None
        self.underlying_momentum_count: int = 0
        self.last_exit_price: Dict[str, Optional[float]] = {"CE": None, "PE": None}
        self.candles: Dict[str, CandleTracker] = {"CE": CandleTracker(), "PE": CandleTracker()}
        self.index_candles: list[Dict[str, float]] = []
        self.index_pending: Optional[Dict[str, float]] = None
        self.index_indicators: Dict[str, Optional[float]] = {
            "ema9": None,
            "ema21": None,
            "vwap": None,
            "supertrend": None,
            "rsi": None,
            "adx": None,
            "di_plus": None,
            "di_minus": None,
        }
        self.index_indicator_ts: float = 0.0

        self.status_lock = threading.Lock()
        self.status_snapshot: Dict[str, Any] = {
            "running": False,
            "enabled": False,
            "playbook": "baseline",
            "last_signal": "--",
        }

    def run(self) -> None:
        self._start_ws()
        monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor.start()
        while not self.stop_event.is_set():
            time.sleep(0.2)
        self._cleanup()

    def stop(self, reason: str = "", close_positions: bool = True) -> None:
        if close_positions and self.risk_engine.position:
            self._exit_position(reason or "Auto stopped")
        self.stop_event.set()
        self._set_status(enabled=False, running=False, last_signal=reason or "Stopped")
        with self.ws_lock:
            if self.ws_app:
                try:
                    self.ws_app.close()
                except Exception:
                    pass

    def _start_ws(self) -> None:
        def on_open(ws):
            payload = {"action": "authenticate", "api_key": self.api_key}
            ws.send(json_dumps(payload))

        def on_message(ws, message):
            try:
                data = json_loads(message)
                self._handle_ws_message(data)
            except Exception as exc:
                logger.debug("WS message error: %s", exc)

        def on_error(ws, error):
            logger.warning("WS error: %s", error)

        def on_close(ws, code, msg):
            logger.info("WS closed: %s %s", code, msg)

        self.ws_app = websocket.WebSocketApp(
            self.ws_url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close
        )
        ws_thread = threading.Thread(target=self.ws_app.run_forever, daemon=True)
        ws_thread.start()
        self._set_status(running=True, enabled=True, last_signal="Connecting")

    def _cleanup(self) -> None:
        self._set_status(enabled=False, running=False)
        if self.advisor_thread_pool:
            try:
                self.advisor_thread_pool.shutdown(wait=False)
            except Exception:
                pass
        try:
            self.learning.stop()
        except Exception:
            pass

    def _set_status(self, **updates: Any) -> None:
        with self.status_lock:
            self.status_snapshot.update(updates)

    def get_status(self) -> Dict[str, Any]:
        with self.status_lock:
            snapshot = dict(self.status_snapshot)
        pos = self.risk_engine.position
        snapshot["position"] = (
            {
                "side": pos.side,
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
            }
            if pos
            else None
        )
        snapshot["pnl"] = {
            "daily": self.risk_engine.status.daily_pnl,
            "realized": self.risk_engine.status.realized_pnl,
            "open": self.risk_engine.status.open_pnl,
        }
        snapshot["learning"] = self.learning.status()
        return snapshot

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            time.sleep(1)
            if not self.risk_config.stop_on_stale:
                continue
            if not self.last_any_tick_ts:
                continue
            age = time.time() - self.last_any_tick_ts
            if age > self.risk_config.stale_timeout_s:
                if self.risk_engine.position:
                    self._exit_position("Data stall")
                self.stop("Data stall")

    def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        if data.get("type") == "auth" and data.get("status") == "success":
            self._subscribe_symbols()
            self._set_status(last_signal="Authenticated")
            return
        if data.get("type") == "subscribe" and data.get("status") in ("success", "partial"):
            return
        if data.get("type") == "market_data":
            self._handle_market_data(data)

    def _subscribe_symbols(self) -> None:
        if not self.ws_app:
            return
        symbols = []
        if self.agent_config.ce_symbol:
            symbols.append({"symbol": self.agent_config.ce_symbol, "exchange": self.execution_config.exchange})
        if self.agent_config.pe_symbol and self.agent_config.pe_symbol != self.agent_config.ce_symbol:
            symbols.append({"symbol": self.agent_config.pe_symbol, "exchange": self.execution_config.exchange})
        if symbols:
            self.ws_app.send(
                json_dumps({"action": "subscribe", "symbols": symbols, "mode": "LTP"})
            )
        if self.agent_config.underlying:
            underlying_exch = self._get_underlying_exchange()
            self.ws_app.send(
                json_dumps(
                    {
                        "action": "subscribe",
                        "symbols": [{"symbol": self.agent_config.underlying, "exchange": underlying_exch}],
                        "mode": "LTP",
                    }
                )
            )
        if self.agent_config.enable_depth:
            self._subscribe_depth(self.agent_config.ce_symbol)
            if self.agent_config.pe_symbol and self.agent_config.pe_symbol != self.agent_config.ce_symbol:
                self._subscribe_depth(self.agent_config.pe_symbol)

    def _subscribe_depth(self, symbol: str) -> None:
        if not self.ws_app or not symbol:
            return
        self.ws_app.send(
            json_dumps(
                {
                    "action": "subscribe",
                    "symbols": [{"symbol": symbol, "exchange": self.execution_config.exchange}],
                    "mode": "Depth",
                    "depth": self.agent_config.depth_level,
                }
            )
        )

    def _unsubscribe_depth(self, symbol: str) -> None:
        if not self.ws_app or not symbol:
            return
        self.ws_app.send(
            json_dumps(
                {
                    "action": "unsubscribe",
                    "symbols": [{"symbol": symbol, "exchange": self.execution_config.exchange}],
                    "mode": "Depth",
                }
            )
        )

    def _handle_market_data(self, data: Dict[str, Any]) -> None:
        symbol = data.get("symbol")
        payload = data.get("data") or data
        ltp = payload.get("ltp") or payload.get("data", {}).get("ltp")
        if ltp is None and isinstance(payload.get("depth"), dict):
            ltp = payload.get("depth", {}).get("ltp")
        ts = payload.get("timestamp") or time.time()
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = time.time()
        if ts > 1e12:
            ts = ts / 1000.0
        if symbol == self.agent_config.underlying:
            if ltp is not None:
                ltp = float(ltp)
                self.last_underlying = ltp
                self._update_underlying_momentum(ltp)
                self._update_index_candle(ltp, ts)
                self._maybe_roll_strikes(ltp)
            return

        side = self._side_for_symbol(symbol)
        if not side:
            return

        if ltp is not None:
            ltp = float(ltp)
            self.last_any_tick_ts = time.time()
            self.feature_cache.update_tick(side, ltp, ts)
            self.candles[side].update(ltp, ts)
            self._update_playbook()
            self._evaluate(side, ltp)

        depth = payload.get("depth") or payload.get("data", {}).get("depth")
        if depth and self.agent_config.enable_depth:
            bid, ask, bid_qty, ask_qty = self._extract_top_depth(depth)
            self.feature_cache.update_depth(side, bid, ask, bid_qty, ask_qty)

    def _update_underlying_momentum(self, price: float) -> None:
        last = self.last_underlying_tick
        if last is None:
            self.last_underlying_tick = price
            return
        direction = None
        if price > last:
            direction = "up"
        elif price < last:
            direction = "down"
        if direction:
            if self.underlying_momentum_dir == direction:
                self.underlying_momentum_count += 1
            else:
                self.underlying_momentum_dir = direction
                self.underlying_momentum_count = 1
        self.last_underlying_tick = price

    def _update_index_candle(self, price: float, ts: float) -> None:
        candle_time = int(ts // 60) * 60
        if self.index_pending is None or self.index_pending.get("time") != candle_time:
            if self.index_pending is not None:
                self.index_candles.append(self.index_pending)
                if len(self.index_candles) > 240:
                    self.index_candles.pop(0)
            self.index_pending = {
                "time": candle_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        else:
            self.index_pending["high"] = max(self.index_pending.get("high", price), price)
            self.index_pending["low"] = min(self.index_pending.get("low", price), price)
            self.index_pending["close"] = price
        self._update_index_indicators()

    def _update_index_indicators(self) -> None:
        now = time.time()
        if now - self.index_indicator_ts < 0.25:
            return
        self.index_indicator_ts = now
        candles = list(self.index_candles)
        if self.index_pending is not None:
            candles.append(self.index_pending)
        if len(candles) < 2:
            return
        self.index_indicators["ema9"] = _ema_last(candles, 9)
        self.index_indicators["ema21"] = _ema_last(candles, 21)
        self.index_indicators["vwap"] = _vwap_last(candles)
        self.index_indicators["supertrend"] = _supertrend_last(candles, 10, 3.0)
        self.index_indicators["rsi"] = _rsi_last(candles, 14)
        adx, di_plus, di_minus = _adx_last(candles, 14)
        self.index_indicators["adx"] = adx
        self.index_indicators["di_plus"] = di_plus
        self.index_indicators["di_minus"] = di_minus

    def _index_bias_snapshot(self) -> Dict[str, Any]:
        mode = (self.agent_config.index_bias_mode or "OFF").upper()
        snapshot = {"mode": mode, "score": 0, "bias": None, "ready": False, "signals": []}
        if mode == "OFF":
            return snapshot
        min_score = max(1, int(self.agent_config.index_bias_min_score or 1))
        price = self.last_underlying
        score = 0
        ready = 0
        signals: list[str] = []

        def add_signal(label: str, direction: int) -> None:
            nonlocal score
            if direction > 0:
                score += 1
                signals.append(f"{label}↑")
            elif direction < 0:
                score -= 1
                signals.append(f"{label}↓")
            else:
                signals.append(f"{label}·")

        if self.agent_config.index_ema_enabled:
            ema9 = self.index_indicators.get("ema9")
            ema21 = self.index_indicators.get("ema21")
            if ema9 is not None and ema21 is not None:
                ready += 1
                if ema9 > ema21:
                    add_signal("EMA", 1)
                elif ema9 < ema21:
                    add_signal("EMA", -1)
                else:
                    add_signal("EMA", 0)
            else:
                signals.append("EMA?")
        if self.agent_config.index_vwap_enabled:
            vwap = self.index_indicators.get("vwap")
            buffer_pts = float(self.agent_config.index_vwap_buffer or 0.0)
            if price is not None and vwap is not None:
                ready += 1
                if price > vwap + buffer_pts:
                    add_signal("VWAP", 1)
                elif price < vwap - buffer_pts:
                    add_signal("VWAP", -1)
                else:
                    add_signal("VWAP", 0)
            else:
                signals.append("VWAP?")
        if self.agent_config.index_rsi_enabled:
            rsi = self.index_indicators.get("rsi")
            bull = float(self.agent_config.index_rsi_bull or 55.0)
            bear = float(self.agent_config.index_rsi_bear or 45.0)
            if rsi is not None:
                ready += 1
                if rsi >= bull:
                    add_signal("RSI", 1)
                elif rsi <= bear:
                    add_signal("RSI", -1)
                else:
                    add_signal("RSI", 0)
            else:
                signals.append("RSI?")
        if self.agent_config.index_adx_enabled:
            adx = self.index_indicators.get("adx")
            di_plus = self.index_indicators.get("di_plus")
            di_minus = self.index_indicators.get("di_minus")
            adx_min = float(self.agent_config.index_adx_min or 18.0)
            if adx is not None and di_plus is not None and di_minus is not None:
                ready += 1
                if adx >= adx_min:
                    if di_plus > di_minus:
                        add_signal("ADX", 1)
                    elif di_minus > di_plus:
                        add_signal("ADX", -1)
                    else:
                        add_signal("ADX", 0)
                else:
                    signals.append(f"ADX<{int(adx_min)}")
            else:
                signals.append("ADX?")
        if self.agent_config.index_supertrend_enabled:
            st = self.index_indicators.get("supertrend")
            if price is not None and st is not None:
                ready += 1
                if price > st:
                    add_signal("ST", 1)
                elif price < st:
                    add_signal("ST", -1)
                else:
                    add_signal("ST", 0)
            else:
                signals.append("ST?")

        snapshot["score"] = score
        snapshot["ready"] = ready > 0
        if score >= min_score:
            snapshot["bias"] = "BULL"
        elif score <= -min_score:
            snapshot["bias"] = "BEAR"
        snapshot["signals"] = signals
        return snapshot

    def _index_bias_ok(self, side: str) -> tuple[bool, str]:
        snapshot = self._index_bias_snapshot()
        mode = snapshot["mode"]
        if mode == "OFF":
            return True, "Index bias off"
        bias = snapshot["bias"]
        if not bias:
            if mode == "STRONG":
                return False, "Index bias neutral"
            return True, "Index bias neutral"
        bull = bias == "BULL"
        wants_bull = side == "CE"
        if mode == "STRONG":
            ok = bull == wants_bull
        else:
            ok = not ((bull and side == "PE") or ((not bull) and side == "CE"))
        return ok, f"Index bias {'bull' if bull else 'bear'}"

    def _required_momentum_ticks(self) -> int:
        ticks = self.playbook_manager.current.config.momentum_ticks
        if self.agent_config.candle_confirm_enabled:
            ticks = max(ticks, self.agent_config.candle_confirm_ticks)
        return ticks

    def _underlying_ok(self, side: str) -> bool:
        if not self.agent_config.underlying_direction_filter:
            return True
        if not self.agent_config.underlying:
            return True
        if not self.underlying_momentum_dir:
            return False
        if self.underlying_momentum_count < self.agent_config.underlying_momentum_ticks:
            return False
        if side == "CE":
            return self.underlying_momentum_dir == "up"
        return self.underlying_momentum_dir == "down"

    def _candle_confirm_ok(self, side: str) -> bool:
        if not self.agent_config.candle_confirm_enabled:
            return True
        tracker = self.candles.get(side)
        if not tracker or tracker.last_close is None or tracker.prev_close is None:
            return False
        mode = (self.agent_config.candle_confirm_mode or "EMA9").upper()
        if mode == "EMA9":
            if tracker.ema is not None:
                return tracker.last_close > tracker.ema
            return tracker.last_close > tracker.prev_close
        return tracker.last_close > tracker.prev_close

    def _relative_strength_ok(self, side: str) -> bool:
        if not self.agent_config.relative_strength_enabled:
            return True
        other = "PE" if side == "CE" else "CE"
        dir_side, count_side = self.feature_cache.get_momentum(side)
        dir_other, count_other = self.feature_cache.get_momentum(other)
        strength_side = count_side if dir_side == "up" else 0
        strength_other = count_other if dir_other == "up" else 0
        return strength_side >= strength_other + self.agent_config.relative_strength_diff

    def _min_hold_met(self) -> bool:
        position = self.risk_engine.position
        if not position:
            return True
        min_hold = self.risk_config.min_hold_before_flip_s
        if not min_hold:
            return True
        return (time.time() - position.entry_ts) >= min_hold

    def _evaluate(self, side: str, ltp: float) -> None:
        if self.flip_cooldown_until and time.time() < self.flip_cooldown_until:
            return

        if self.risk_engine.position:
            self._manage_position(side, ltp)
            return

        signal_side = side
        trade_side = self._trade_side(signal_side)

        if not self._side_allowed(signal_side):
            return

        if not self._underlying_ok(signal_side):
            self._set_status(last_signal="Underlying filter")
            return

        if not self._candle_confirm_ok(signal_side):
            self._set_status(last_signal="Candle confirm")
            return

        if not self._momentum_ready(signal_side):
            return

        if not self._relative_strength_ok(signal_side):
            self._set_status(last_signal="Rel strength")
            return

        ok, reason = self._index_bias_ok(trade_side)
        if not ok:
            self._set_status(last_signal=reason)
            return

        if not self._spread_ok(signal_side):
            self._set_status(last_signal="Spread wide")
            return

        if not self._imbalance_ok(signal_side):
            self._set_status(last_signal="Imbalance weak")
            return

        trade_ltp = ltp
        if trade_side != signal_side:
            trade_ltp = self.feature_cache.sides[trade_side].last_price or ltp
        if trade_ltp is None:
            self._set_status(last_signal="No trade price")
            return
        if not self._min_move_ok(trade_side, trade_ltp):
            self._set_status(last_signal="Min move")
            return

        can_enter, reason = self.risk_engine.can_enter()
        if not can_enter:
            self._set_status(last_signal=reason)
            return

        # Capture all conditions that matched for this entry (for logs and analysis)
        entry_conditions: Dict[str, Any] = {
            "checks_passed": [
                "side_allowed",
                "underlying_ok",
                "candle_confirm_ok",
                "momentum_ready",
                "relative_strength_ok",
                "index_bias_ok",
                "spread_ok",
                "imbalance_ok",
                "min_move_ok",
                "can_enter",
            ],
            "signal_side": signal_side,
            "trade_side": trade_side,
            "trade_ltp": trade_ltp,
            "reverse_trades": self.agent_config.reverse_trades,
        }
        reason = "Momentum"
        if self.agent_config.reverse_trades:
            reason = "Momentum (Reversed)"
        self._enter_position(
            trade_side, trade_ltp, lots=self.agent_config.entry_lots, reason=reason, entry_conditions=entry_conditions
        )

    def _manage_position(self, tick_side: str, ltp: float) -> None:
        position = self.risk_engine.position
        if not position:
            return
        # Only act on ticks from the position side
        if position.side != tick_side:
            # consider flip if other side momentum is strong
            if self._momentum_ready(tick_side):
                if not self._min_hold_met():
                    return
                self._exit_position("Flip")
                self.flip_cooldown_until = time.time() + self.risk_config.flip_cooldown_s
            return

        self.risk_engine.update_open_pnl(ltp)
        self._set_status(
            daily_pnl=self.risk_engine.status.daily_pnl,
            open_pnl=self.risk_engine.status.open_pnl,
        )

        self._maybe_breakeven(ltp)
        self._maybe_profit_lock(ltp)

        if self.risk_engine.check_daily_loss(ltp):
            self._exit_position("Daily max loss")
            self.stop("Daily max loss hit")
            return

        if self.risk_engine.should_exit_per_trade(ltp):
            self._exit_position("Per-trade max loss")
            return

        if self._tp_hit(ltp):
            self._exit_position("TP hit")
            return

        if self._sl_hit(ltp):
            self._exit_position("Trail SL" if self.sl_trailing else "SL hit")
            return

        self._maybe_trail(ltp)

        momentum_ok = self._momentum_ready(position.side)
        should_exit, tighten = self.risk_engine.evaluate_time_guard(ltp, momentum_ok)
        if should_exit:
            self._exit_position("Time exit")
            return
        if tighten:
            self._tighten_sl(ltp)

        self._maybe_average(ltp)

    def _learning_params(self) -> Dict[str, Any]:
        cfg = self.playbook_manager.current.config
        return {
            "momentum_ticks": cfg.momentum_ticks,
            "tp_points": cfg.tp_points,
            "sl_points": cfg.sl_points,
            "trail_distance": cfg.trail_distance,
            "trail_step": cfg.trail_step,
        }

    def _learning_features(self, side: str) -> Dict[str, Any]:
        direction, count = self.feature_cache.get_momentum(side)
        return {
            "volatility": self.feature_cache.get_volatility(side),
            "spread": self.feature_cache.get_spread(side),
            "imbalance_ratio": self.feature_cache.get_imbalance_ratio(side),
            "momentum_dir": direction,
            "momentum_count": count,
            "underlying_dir": self.underlying_momentum_dir,
            "underlying_count": self.underlying_momentum_count,
            "candle_confirm": self._candle_confirm_ok(side),
            "relative_strength_ok": self._relative_strength_ok(side),
        }

    def _send_telegram_alert(self, message: str) -> None:
        if not self.agent_config.telegram_alerts_enabled:
            return
        if not self.api_key:
            return
        try:
            from services.telegram_alert_service import telegram_alert_service
            from database.auth_db import get_username_by_apikey
            from database.telegram_db import get_telegram_user_by_username

            username = get_username_by_apikey(self.api_key)
            if not username:
                return
            telegram_user = get_telegram_user_by_username(username)
            if not telegram_user or not telegram_user.get("notifications_enabled"):
                return
            telegram_alert_service.send_alert_sync(
                telegram_user["telegram_id"], message
            )
        except Exception as exc:
            logger.debug("Telegram alert failed: %s", exc)

    def _enter_position(
        self,
        side: str,
        ltp: float,
        lots: int,
        reason: str,
        entry_conditions: Optional[Dict[str, Any]] = None,
    ) -> None:
        symbol = self._symbol_for_side(side)
        if not symbol:
            return
        new_position = self.risk_engine.position is None
        qty = self.agent_config.lot_size * lots
        max_qty = self._max_qty()
        current_qty = abs(self.risk_engine.position.quantity) if self.risk_engine.position else 0
        if current_qty + qty > max_qty:
            self._set_status(last_signal=f"Max qty {max_qty}")
            return
        result = self.execution.place_market_order("BUY", symbol, qty, reason=reason)
        if result.response.get("status") == "skipped":
            self._set_status(last_signal=f"Suggest {side} @ {ltp:.2f}")
            return
        if result.ok or result.response.get("status") == "success":
            entry_price = ltp
            if self.risk_engine.position:
                # averaging - update VWAP
                pos = self.risk_engine.position
                total_qty = pos.quantity + qty
                pos.entry_price = (pos.entry_price * pos.quantity + entry_price * qty) / total_qty
                pos.quantity = total_qty
                if not new_position:
                    cfg = self.playbook_manager.current.config
                    self._log_trade_event(
                        event_type="ENTRY",
                        side=side,
                        symbol=symbol,
                        qty=qty,
                        price=entry_price,
                        reason="Average",
                        tp_points=self._effective_tp_points(),
                        sl_points=cfg.sl_points,
                        trade_id=self.active_trade_id,
                        entry_conditions=entry_conditions or {"trigger": "average"},
                    )
            else:
                self.risk_engine.record_entry(side, symbol, qty, entry_price)
                cfg = self.playbook_manager.current.config
                tp_points = self._effective_tp_points()
                self.tp_price = entry_price + tp_points
                self.sl_price = entry_price - cfg.sl_points
                self.trailing_anchor = entry_price
                self.sl_trailing = False
                if self.agent_config.avg_enabled:
                    self.avg_session = AvgSession(
                        end_ts=time.time() + self.agent_config.avg_window_s,
                        next_ts=time.time() + self.agent_config.avg_interval_s,
                        lots_added=lots,
                    )
                else:
                    self.avg_session = None
                if new_position:
                    self.active_trade_id = self.learning.record_entry(
                        {
                            "side": side,
                            "symbol": symbol,
                            "quantity": qty,
                            "entry_price": entry_price,
                            "playbook": self.playbook_manager.current.name,
                            "mode": "paper" if self.agent_config.paper_mode else "live",
                            "params": self._learning_params(),
                            "features": self._learning_features(side),
                        }
                    )
                    self._log_trade_event(
                        event_type="ENTRY",
                        side=side,
                        symbol=symbol,
                        qty=qty,
                        price=entry_price,
                        reason=reason,
                        tp_points=tp_points,
                        sl_points=cfg.sl_points,
                        trade_id=self.active_trade_id,
                        entry_conditions=entry_conditions,
                    )
                    if self.agent_config.telegram_alerts_entry:
                        self._send_telegram_alert(
                            f"AI Scalper Entry {side}\nSymbol: {symbol}\nQty: {qty}\nPrice: {entry_price:.2f}\nPlaybook: {self.playbook_manager.current.name}"
                        )
            self.last_signal = f"Entry {side}"
            self._set_status(last_signal=self.last_signal)
        else:
            self._set_status(last_signal=f"Entry failed: {result.response.get('message')}")

    def _reason_to_trigger(self, reason: str) -> str:
        """Map exit reason to a canonical trigger for logging/analysis."""
        r = (reason or "").strip().lower()
        if "trail" in r or "trailing" in r:
            return "trail_sl"
        if "tp" in r or "target" in r:
            return "tp_hit"
        if "sl" in r and "trail" not in r:
            return "sl_hit"
        if "manual" in r or "close" in r:
            return "manual_close"
        if "time" in r:
            return "time_exit"
        if "daily" in r or "max loss" in r:
            return "daily_max_loss"
        if "per-trade" in r or "per trade" in r:
            return "per_trade_max_loss"
        if "flip" in r:
            return "flip"
        if "average" in r:
            return "average"
        return "other"

    def _exit_position(self, reason: str) -> None:
        position = self.risk_engine.position
        if not position:
            return
        qty = position.quantity
        ltp = self.feature_cache.sides[position.side].last_price
        result = self.execution.place_market_order("SELL", position.symbol, position.quantity, reason=reason)
        if result.response.get("status") == "skipped":
            self._set_status(last_signal=f"Suggest exit {reason}")
            return
        if result.ok or result.response.get("status") == "success":
            hold_ms = None
            if position.entry_ts:
                hold_ms = max(0.0, (time.time() - position.entry_ts) * 1000.0)
            pnl = self.risk_engine.record_exit(ltp, reason)
            self.tp_price = None
            self.sl_price = None
            self.trailing_anchor = None
            self.avg_session = None
            self.sl_trailing = False
            self.last_exit_price[position.side] = ltp
            self.last_signal = f"Exit {reason} {pnl:+.0f}"
            self._set_status(last_signal=self.last_signal, daily_pnl=self.risk_engine.status.daily_pnl)
            if self.agent_config.telegram_alerts_exit:
                self._send_telegram_alert(
                    f"AI Scalper Exit {position.side}\nSymbol: {position.symbol}\nQty: {position.quantity}\nPrice: {ltp:.2f}\nPnL: {pnl:.0f}\nReason: {reason}"
                )
            if self.active_trade_id:
                self.learning.record_exit(
                    self.active_trade_id,
                    {
                        "exit_price": ltp,
                        "pnl": pnl,
                        "reason": reason,
                        "quantity": qty,
                    },
                )
                exit_conditions: Dict[str, Any] = {
                    "trigger": self._reason_to_trigger(reason),
                    "reason": reason,
                    "exit_ltp": ltp,
                }
                self._log_trade_event(
                    event_type="EXIT",
                    side=position.side,
                    symbol=position.symbol,
                    qty=qty,
                    price=ltp,
                    reason=reason,
                    pnl=pnl,
                    trade_id=self.active_trade_id,
                    hold_ms=hold_ms,
                    exit_conditions=exit_conditions,
                )
                try:
                    from services.ai_scalper.model_tuner import get_model_tuning_service

                    get_model_tuning_service().notify_trade_exit()
                except Exception:
                    pass
                self.active_trade_id = None
                tuned = self.learning.maybe_tune(self.playbook_manager.base.__dict__)
                if tuned:
                    self.playbook_manager.apply_adjustments(tuned.params)
                    self._set_status(last_signal=f"Learning {tuned.arm_id}")
                    if self.agent_config.telegram_alerts_tune:
                        self._send_telegram_alert(
                            f"AI Scalper Tune {tuned.arm_id}\nParams: {tuned.params}"
                        )

    def _maybe_average(self, ltp: float) -> None:
        if not self.agent_config.avg_enabled:
            return
        if self.agent_config.avg_only_profit and self.risk_engine.status.open_pnl <= 0:
            return
        session = self.avg_session
        if not session:
            return
        if time.time() > session.end_ts:
            return
        if time.time() < session.next_ts:
            return
        if session.lots_added >= self.agent_config.max_lots_per_strike:
            return
        if not self._momentum_ready(self.risk_engine.position.side):
            return
        if not self._underlying_ok(self.risk_engine.position.side):
            return
        if not self._candle_confirm_ok(self.risk_engine.position.side):
            return
        ok, _ = self._index_bias_ok(self.risk_engine.position.side)
        if not ok:
            return
        session.next_ts = time.time() + self.agent_config.avg_interval_s
        session.lots_added += self.agent_config.scale_lots
        self._enter_position(
            self.risk_engine.position.side,
            ltp,
            self.agent_config.scale_lots,
            "Average",
            entry_conditions={"trigger": "average", "reason": "Average"},
        )

    def _log_trade_event(
        self,
        event_type: str,
        side: str,
        symbol: str,
        qty: int,
        price: float,
        reason: str | None = None,
        pnl: float | None = None,
        tp_points: float | None = None,
        sl_points: float | None = None,
        trade_id: str | None = None,
        hold_ms: float | None = None,
        entry_conditions: Optional[Dict[str, Any]] = None,
        exit_conditions: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            store = get_auto_trade_log_store()
            payload = {
                "eventId": str(uuid.uuid4()),
                "tradeId": trade_id,
                "ts": datetime.utcnow().isoformat() + "Z",
                "type": event_type,
                "source": "server",
                "mode": "PAPER" if self.agent_config.paper_mode else "LIVE",
                "side": side,
                "symbol": symbol,
                "action": "BUY" if event_type == "ENTRY" else "SELL",
                "qty": int(qty),
                "price": float(price) if price is not None else None,
                "tpPoints": float(tp_points) if tp_points is not None else None,
                "slPoints": float(sl_points) if sl_points is not None else None,
                "pnl": float(pnl) if pnl is not None else None,
                "reason": reason,
                "holdMs": float(hold_ms) if hold_ms is not None else None,
                "underlying": self.agent_config.underlying,
                "exchange": self.agent_config.underlying_exchange,
                "playbook": self.playbook_manager.current.name if self.playbook_manager else None,
            }
            if event_type == "ENTRY" and entry_conditions is not None:
                payload["matched_conditions"] = entry_conditions
            if event_type == "EXIT" and exit_conditions is not None:
                payload["matched_conditions"] = exit_conditions
            store.enqueue(payload)
        except Exception:
            pass

    def _tp_hit(self, ltp: float) -> bool:
        cfg = self.playbook_manager.current.config
        if cfg.trailing_enabled and cfg.trailing_override_tp:
            return False
        if self.tp_price is None:
            return False
        return ltp >= self.tp_price

    def _sl_hit(self, ltp: float) -> bool:
        if self.sl_price is None:
            return False
        return ltp <= self.sl_price

    def _maybe_trail(self, ltp: float) -> None:
        cfg = self.playbook_manager.current.config
        if not cfg.trailing_enabled:
            return
        if self.trailing_anchor is None:
            self.trailing_anchor = ltp
        if ltp - self.trailing_anchor < cfg.trail_distance:
            return
        desired = round_to_tick(ltp - cfg.trail_distance, self.agent_config.tick_size)
        if self.sl_price is None:
            self.sl_price = desired
        elif desired > self.sl_price + cfg.trail_step:
            self.sl_price = desired
        if self.risk_engine.position and desired >= self.risk_engine.position.entry_price:
            self.sl_trailing = True

    def _maybe_profit_lock(self, ltp: float) -> None:
        position = self.risk_engine.position
        if not position:
            return
        lock_rs = max(0.0, float(self.agent_config.profit_lock_rs or 0.0))
        if lock_rs > 0:
            qty = abs(position.quantity)
            if qty <= 0:
                return
            pts = lock_rs / qty
        else:
            pts = max(0.0, float(self.agent_config.profit_lock_pts or 0.0))
        if pts <= 0:
            return
        entry = position.entry_price
        tick_size = self.agent_config.tick_size
        if position.quantity >= 0:
            if ltp - entry < pts:
                return
            desired = round_to_tick(entry + pts, tick_size)
            if self.sl_price is None or desired > self.sl_price:
                self.sl_price = desired
                if desired >= entry:
                    self.sl_trailing = True
        else:
            if entry - ltp < pts:
                return
            desired = round_to_tick(entry - pts, tick_size)
            if self.sl_price is None or desired < self.sl_price:
                self.sl_price = desired
                if desired <= entry:
                    self.sl_trailing = True

    def _tighten_sl(self, ltp: float) -> None:
        tighten = self.risk_config.time_exit_tighten_pts
        desired = round_to_tick(ltp - tighten, self.agent_config.tick_size)
        if self.sl_price is None or desired > self.sl_price:
            self.sl_price = desired
        if self.risk_engine.position and desired >= self.risk_engine.position.entry_price:
            self.sl_trailing = True

    def _momentum_ready(self, side: str) -> bool:
        direction, count = self.feature_cache.get_momentum(side)
        required = self._required_momentum_ticks()
        return direction == "up" and count >= required

    def _side_allowed(self, side: str) -> bool:
        mode = self.agent_config.trade_mode
        if mode == "AUTO":
            return True
        return mode == side

    def _trade_side(self, signal_side: str) -> str:
        if self.agent_config.reverse_trades:
            return "PE" if signal_side == "CE" else "CE"
        return signal_side

    def _effective_tp_points(self) -> float:
        cfg = self.playbook_manager.current.config
        if self.agent_config.rr_guard_enabled and cfg.tp_points < cfg.sl_points:
            return cfg.sl_points
        return cfg.tp_points

    def _max_qty(self) -> int:
        if self.agent_config.max_qty and self.agent_config.max_qty > 0:
            return int(self.agent_config.max_qty)
        return int(max(1, self.agent_config.max_lots_per_strike * self.agent_config.lot_size))

    def _min_move_ok(self, side: str, ltp: float) -> bool:
        min_move = self.agent_config.min_move_pts
        if not min_move or min_move <= 0:
            return True
        last_exit = self.last_exit_price.get(side)
        if last_exit is None:
            return True
        return abs(ltp - last_exit) >= min_move

    def _maybe_breakeven(self, ltp: float) -> None:
        if not self.agent_config.breakeven_enabled:
            return
        position = self.risk_engine.position
        if not position:
            return
        if self.risk_engine.status.open_pnl <= 0:
            return
        delay_s = max(0.0, float(self.agent_config.breakeven_delay_s or 0.0))
        if delay_s:
            age = time.time() - position.entry_ts
            if age < delay_s:
                return
        buffer_pts = max(0.0, float(self.agent_config.breakeven_buffer_pts or 0.0))
        entry = position.entry_price
        if position.quantity >= 0:
            desired = round_to_tick(entry - buffer_pts, self.agent_config.tick_size)
            if self.sl_price is None or desired > self.sl_price:
                self.sl_price = desired
                if desired >= entry:
                    self.sl_trailing = True
        else:
            desired = round_to_tick(entry + buffer_pts, self.agent_config.tick_size)
            if self.sl_price is None or desired < self.sl_price:
                self.sl_price = desired
                if desired <= entry:
                    self.sl_trailing = True

    def _spread_ok(self, side: str) -> bool:
        if not self.agent_config.enable_spread_filter:
            return True
        spread = self.feature_cache.get_spread(side)
        if spread is None:
            return True
        base = self.risk_config.spread_max_sensex if "SENSEX" in self.agent_config.underlying.upper() else self.risk_config.spread_max_nifty
        if is_expiry_day(self.agent_config.expiry):
            now = datetime.now()
            if now.hour >= 14:
                base *= 1.5
        return spread <= base

    def _imbalance_ok(self, side: str) -> bool:
        if not self.agent_config.enable_imbalance:
            return True
        ratio = self.feature_cache.get_imbalance_ratio(side)
        if ratio is None:
            return False
        return ratio >= self.agent_config.imbalance_ratio

    def _side_for_symbol(self, symbol: str) -> Optional[str]:
        if symbol == self.agent_config.ce_symbol:
            return "CE"
        if symbol == self.agent_config.pe_symbol:
            return "PE"
        return None

    def _symbol_for_side(self, side: str) -> Optional[str]:
        return self.agent_config.ce_symbol if side == "CE" else self.agent_config.pe_symbol

    def _update_playbook(self) -> None:
        vol = max(self.feature_cache.get_volatility("CE"), self.feature_cache.get_volatility("PE"))
        playbook = self.playbook_manager.update(vol, self.agent_config.expiry)
        self._set_status(playbook=playbook.name)
        self._maybe_request_advice(vol)

    def _maybe_request_advice(self, volatility: float) -> None:
        if not self.advisor_config.enabled or self.advisor_config.provider == "none":
            return
        now = time.time()
        if self.advisor_future is None and now - self.last_advisor_ts >= self.advisor_config.interval_s:
            if self.advisor_thread_pool is None:
                self.advisor_thread_pool = ThreadPoolExecutor(max_workers=1)
            context = {
                "volatility": volatility,
                "playbook": self.playbook_manager.current.name,
                "config": self.playbook_manager.current.config.__dict__,
                "risk": {
                    "daily_pnl": self.risk_engine.status.daily_pnl,
                    "realized": self.risk_engine.status.realized_pnl,
                    "open": self.risk_engine.status.open_pnl,
                    "trades": self.risk_engine.status.trades_today,
                },
                "position": {
                    "side": self.risk_engine.position.side if self.risk_engine.position else None,
                    "symbol": self.risk_engine.position.symbol if self.risk_engine.position else None,
                    "qty": self.risk_engine.position.quantity if self.risk_engine.position else 0,
                },
                "underlying": self.last_underlying,
            }
            self.advisor_future = self.advisor_thread_pool.submit(self.advisor.get_update, context)
            self.last_advisor_ts = now

        if self.advisor_future and self.advisor_future.done():
            try:
                update = self.advisor_future.result()
                if update and update.changes:
                    if self.advisor_config.auto_apply:
                        self.playbook_manager.apply_adjustments(update.changes)
                        self._set_status(last_signal=f"Advisor applied: {update.notes or 'update'}")
                    else:
                        self.pending_advice = update.changes
                        self._set_status(last_signal="Advisor update pending")
            except Exception as exc:
                logger.debug("Advisor update failed: %s", exc)
            finally:
                self.advisor_future = None

    def _extract_top_depth(self, depth: Dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        bids = depth.get("buy") or depth.get("bids") or []
        asks = depth.get("sell") or depth.get("asks") or []
        best_bid = _extract_price_qty(bids[0]) if bids else (None, None)
        best_ask = _extract_price_qty(asks[0]) if asks else (None, None)
        return best_bid[0], best_ask[0], best_bid[1], best_ask[1]

    def _maybe_roll_strikes(self, underlying_ltp: float) -> None:
        if not self.agent_config.auto_roll:
            return
        if not self.agent_config.underlying or not self.api_key:
            return
        if self.last_roll_underlying is None:
            self.last_roll_underlying = underlying_ltp
            return
        step = self.agent_config.auto_roll_sensex if "SENSEX" in self.agent_config.underlying.upper() else self.agent_config.auto_roll_nifty
        if abs(underlying_ltp - self.last_roll_underlying) < step:
            return
        self.last_roll_underlying = underlying_ltp
        self._refresh_symbols()

    def _refresh_symbols(self) -> None:
        offset = parse_strike_offset(self.agent_config.strike_offset)
        if is_expiry_day(self.agent_config.expiry):
            now = datetime.now()
            if self.agent_config.expiry_otm and now.hour >= 14:
                offset = max(offset, 3)

        success, response, status = get_option_chain(
            underlying=self.agent_config.underlying,
            exchange=self._get_underlying_exchange(),
            expiry_date=self.agent_config.expiry,
            strike_count=max(6, offset + 2),
            api_key=self.api_key,
        )
        if not success:
            logger.warning("Auto-roll option chain failed: %s", response)
            return
        chain = response.get("chain") or []
        if not chain:
            return
        strikes = [item.get("strike") for item in chain if item.get("strike") is not None]
        strikes = sorted(set(strikes))
        if not strikes:
            return
        atm = response.get("atm_strike") or strikes[len(strikes) // 2]
        step = strikes[1] - strikes[0] if len(strikes) > 1 else 50
        target_ce = atm + offset * step
        target_pe = atm - offset * step
        ce_entry = _find_chain_entry(chain, target_ce)
        pe_entry = _find_chain_entry(chain, target_pe)
        if not ce_entry or not pe_entry:
            return
        new_ce = ce_entry.get("ce", {}).get("symbol") or self.agent_config.ce_symbol
        new_pe = pe_entry.get("pe", {}).get("symbol") or self.agent_config.pe_symbol
        if new_ce == self.agent_config.ce_symbol and new_pe == self.agent_config.pe_symbol:
            return
        old_ce = self.agent_config.ce_symbol
        old_pe = self.agent_config.pe_symbol
        self.agent_config.ce_symbol = new_ce
        self.agent_config.pe_symbol = new_pe
        self.agent_config.lot_size = ce_entry.get("ce", {}).get("lotsize") or self.agent_config.lot_size
        self.agent_config.tick_size = ce_entry.get("ce", {}).get("tick_size") or self.agent_config.tick_size
        self._resubscribe_ltp(old_ce, old_pe, new_ce, new_pe)
        if self.agent_config.enable_depth:
            self._unsubscribe_depth(old_ce)
            self._unsubscribe_depth(old_pe)
            self._subscribe_depth(new_ce)
            self._subscribe_depth(new_pe)
        self._set_status(last_signal=f"Auto-roll {new_ce}/{new_pe}")

    def _resubscribe_ltp(self, old_ce: str, old_pe: str, new_ce: str, new_pe: str) -> None:
        if not self.ws_app:
            return
        unsub = []
        for sym in (old_ce, old_pe):
            if sym:
                unsub.append({"symbol": sym, "exchange": self.execution_config.exchange})
        if unsub:
            self.ws_app.send(json_dumps({"action": "unsubscribe", "symbols": unsub, "mode": "LTP"}))
        subs = []
        for sym in (new_ce, new_pe):
            if sym:
                subs.append({"symbol": sym, "exchange": self.execution_config.exchange})
        if subs:
            self.ws_app.send(json_dumps({"action": "subscribe", "symbols": subs, "mode": "LTP"}))

    def _get_underlying_exchange(self) -> str:
        if self.agent_config.underlying_exchange:
            return self.agent_config.underlying_exchange
        # Guess exchange based on underlying
        if "SENSEX" in self.agent_config.underlying.upper():
            return "BSE_INDEX"
        return "NSE_INDEX"


def parse_strike_offset(offset: str) -> int:
    if not offset:
        return 0
    text = offset.upper().replace("ATM", "")
    text = text.replace("+", "").replace("-", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


def _find_chain_entry(chain: list[dict], strike: float) -> Optional[dict]:
    if not chain:
        return None
    # choose closest strike
    best = None
    best_diff = None
    for item in chain:
        s = item.get("strike")
        if s is None:
            continue
        diff = abs(s - strike)
        if best is None or diff < best_diff:
            best = item
            best_diff = diff
    return best


def _extract_price_qty(entry: Any) -> tuple[Optional[float], Optional[float]]:
    if isinstance(entry, dict):
        price = entry.get("price") or entry.get("p") or entry.get("rate")
        qty = entry.get("quantity") or entry.get("qty") or entry.get("q")
        return _to_float(price), _to_float(qty)
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return _to_float(entry[0]), _to_float(entry[1])
    return None, None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def json_dumps(obj: Dict[str, Any]) -> str:
    import json

    return json.dumps(obj)


def json_loads(raw: str) -> Dict[str, Any]:
    import json

    return json.loads(raw)


def round_to_tick(price: float, tick: float) -> float:
    if not tick:
        return price
    return round(price / tick) * tick
