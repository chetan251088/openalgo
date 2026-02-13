"""
TOMIC Regime Agent — Market Phase Classification Engine
=========================================================
Elder's Triple Screen + Minivervini's Trend Template.
Single writer for AtomicRegimeState.

Indicators:
  - Ichimoku Cloud (9, 26, 52) — trend direction & strength
  - Impulse System (13-EMA, MACD 12/26/9) — permission filter
  - Congestion / Blowoff detectors (BBW, ATR, volume)
  - VIX overlay — position sizing flags

Output:
  - RegimeUpdateEvent via ZeroMQ telemetry
  - AtomicRegimeState versioned snapshot (for Risk Agent)

Timing:
  - Rescores on every tick (~1s), but publishes only on state change
  - Uses WS data for intraday; config for indicator lookbacks
"""

from __future__ import annotations

import logging
import math
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

from collections import deque

from tomic.agent_base import AgentBase
from tomic.config import (
    TomicConfig,
    RegimePhase,
    VIXRules,
)
from tomic.events import (
    AlertLevel,
    EventType,
    RegimeUpdateEvent,
)
from tomic.event_bus import EventPublisher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regime State (versioned, single-writer — Regime Agent only)
# ---------------------------------------------------------------------------

@dataclass
class RegimeSnapshot:
    """Immutable snapshot returned to consumers (Risk Agent)."""
    version: int
    phase: RegimePhase
    score: int              # -20 to +20
    vix: float
    vix_flags: List[str]    # e.g. ["HALF_SIZE", "DEFINED_RISK_ONLY"]
    ichimoku_signal: str    # BULLISH / BEARISH / NEUTRAL
    impulse_color: str      # GREEN / RED / BLUE
    congestion: bool
    blowoff: bool
    timestamp_mono: float


class AtomicRegimeState:
    """
    Thread-safe, versioned regime state.
    Single writer: Regime Agent. Read by Risk Agent via read_snapshot().
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._version: int = 0
        self._phase: RegimePhase = RegimePhase.CONGESTION
        self._score: int = 0
        self._vix: float = 0.0
        self._vix_flags: List[str] = []
        self._ichimoku_signal: str = "NEUTRAL"
        self._impulse_color: str = "BLUE"
        self._congestion: bool = False
        self._blowoff: bool = False
        self._timestamp_mono: float = 0.0

    def update(
        self,
        phase: RegimePhase,
        score: int,
        vix: float,
        vix_flags: List[str],
        ichimoku_signal: str,
        impulse_color: str,
        congestion: bool,
        blowoff: bool,
    ) -> int:
        """Write new state. Returns new version number."""
        with self._lock:
            self._version += 1
            self._phase = phase
            self._score = score
            self._vix = vix
            self._vix_flags = list(vix_flags)
            self._ichimoku_signal = ichimoku_signal
            self._impulse_color = impulse_color
            self._congestion = congestion
            self._blowoff = blowoff
            self._timestamp_mono = time.monotonic()
            return self._version

    def read_snapshot(self) -> RegimeSnapshot:
        """Return deep-copied immutable snapshot."""
        with self._lock:
            return RegimeSnapshot(
                version=self._version,
                phase=self._phase,
                score=self._score,
                vix=self._vix,
                vix_flags=list(self._vix_flags),
                ichimoku_signal=self._ichimoku_signal,
                impulse_color=self._impulse_color,
                congestion=self._congestion,
                blowoff=self._blowoff,
                timestamp_mono=self._timestamp_mono,
            )

    @property
    def current_version(self) -> int:
        with self._lock:
            return self._version


# ---------------------------------------------------------------------------
# Indicator Calculators (pure functions, no side effects)
# ---------------------------------------------------------------------------

def compute_ichimoku(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
) -> Dict[str, Any]:
    """
    Compute Ichimoku Cloud components.
    Returns signal: BULLISH (price above cloud), BEARISH (below), NEUTRAL (inside).
    """
    if len(highs) < senkou_b_period:
        return {"signal": "NEUTRAL", "tenkan": 0, "kijun": 0, "senkou_a": 0, "senkou_b": 0}

    # Tenkan-sen (conversion line): (highest high + lowest low) / 2 over tenkan_period
    tenkan = (max(highs[-tenkan_period:]) + min(lows[-tenkan_period:])) / 2

    # Kijun-sen (base line): (highest high + lowest low) / 2 over kijun_period
    kijun = (max(highs[-kijun_period:]) + min(lows[-kijun_period:])) / 2

    # Senkou Span A: (Tenkan + Kijun) / 2 (plotted 26 periods ahead)
    senkou_a = (tenkan + kijun) / 2

    # Senkou Span B: (highest high + lowest low) / 2 over senkou_b_period (plotted 26 ahead)
    senkou_b = (max(highs[-senkou_b_period:]) + min(lows[-senkou_b_period:])) / 2

    # Current price relative to cloud
    price = closes[-1]
    cloud_top = max(senkou_a, senkou_b)
    cloud_bottom = min(senkou_a, senkou_b)

    if price > cloud_top:
        signal = "BULLISH"
    elif price < cloud_bottom:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "signal": signal,
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "price": price,
    }


def compute_impulse_system(
    closes: List[float],
    ema_period: int = 13,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
) -> Dict[str, Any]:
    """
    Elder's Impulse System.
    GREEN = EMA rising + MACD-H rising → buy/hold
    RED   = EMA falling + MACD-H falling → sell/short
    BLUE  = mixed → neutral (no new positions)
    """
    if len(closes) < macd_slow + macd_signal:
        return {"color": "BLUE", "ema": 0, "macd_histogram": 0}

    # EMA calculation
    ema_values = _compute_ema(closes, ema_period)
    ema_current = ema_values[-1]
    ema_prev = ema_values[-2] if len(ema_values) > 1 else ema_current
    ema_rising = ema_current > ema_prev

    # MACD
    fast_ema = _compute_ema(closes, macd_fast)
    slow_ema = _compute_ema(closes, macd_slow)
    macd_line = [f - s for f, s in zip(fast_ema[-len(slow_ema):], slow_ema)]
    signal_line = _compute_ema(macd_line, macd_signal)
    histogram = [m - s for m, s in zip(macd_line[-len(signal_line):], signal_line)]

    if len(histogram) < 2:
        return {"color": "BLUE", "ema": ema_current, "macd_histogram": 0}

    hist_current = histogram[-1]
    hist_prev = histogram[-2]
    hist_rising = hist_current > hist_prev

    # Impulse color
    if ema_rising and hist_rising:
        color = "GREEN"
    elif not ema_rising and not hist_rising:
        color = "RED"
    else:
        color = "BLUE"

    return {
        "color": color,
        "ema": ema_current,
        "macd_histogram": hist_current,
        "ema_rising": ema_rising,
        "hist_rising": hist_rising,
    }


def detect_congestion(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    bbw_period: int = 20,
    min_candles: int = 5,
) -> bool:
    """
    Detect congestion (range-bound market).
    Uses Bollinger Band Width squeeze + overlapping candle bodies.
    """
    if len(closes) < bbw_period:
        return False

    # Bollinger Band Width
    recent = closes[-bbw_period:]
    sma = sum(recent) / len(recent)
    variance = sum((x - sma) ** 2 for x in recent) / len(recent)
    std = math.sqrt(variance) if variance > 0 else 0.001

    upper = sma + 2 * std
    lower = sma - 2 * std
    bbw = (upper - lower) / sma if sma > 0 else 0

    # Low BBW indicates squeeze
    if bbw > 0.04:  # not squeezed
        return False

    # Check overlapping candle bodies in recent bars
    if len(highs) < min_candles:
        return False

    recent_highs = highs[-min_candles:]
    recent_lows = lows[-min_candles:]

    # All candles overlap if max(lows) < min(highs)
    overlap_zone = min(recent_highs) - max(recent_lows)
    return overlap_zone > 0


def detect_blowoff(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    volumes: List[float],
    atr_multiple: float = 3.0,
    volume_multiple: float = 2.0,
    ma_period: int = 20,
    atr_period: int = 14,
    volume_avg_period: int = 50,
) -> bool:
    """
    Detect blowoff top/bottom.
    Price > N×ATR from 20-MA AND volume > N× 50-day avg.
    """
    if len(closes) < max(ma_period, atr_period, volume_avg_period):
        return False

    # 20-period MA
    ma = sum(closes[-ma_period:]) / ma_period

    # ATR
    atr = _compute_atr(highs, lows, closes, atr_period)

    # Distance from MA
    price = closes[-1]
    distance = abs(price - ma)

    # Volume average
    avg_vol = sum(volumes[-volume_avg_period:]) / volume_avg_period
    current_vol = volumes[-1]

    return distance > (atr_multiple * atr) and current_vol > (volume_multiple * avg_vol)


def compute_vix_flags(vix: float, rules: VIXRules) -> List[str]:
    """
    Determine VIX-based position sizing flags.
    Returns list of active flags.
    """
    flags = []

    if vix < rules.stop_selling_below:
        flags.append("PREMIUMS_TOO_LOW")

    if vix > rules.half_size_above:
        flags.append("HALF_SIZE")

    if vix > rules.defined_risk_only_above:
        flags.append("DEFINED_RISK_ONLY")

    if vix > rules.halt_short_vega_above:
        flags.append("HALT_SHORT_VEGA")

    return flags


def compute_regime_score(
    ichimoku_signal: str,
    impulse_color: str,
    congestion: bool,
    blowoff: bool,
    vix: float,
    vix_rules: VIXRules,
    pcr: Optional[float] = None,
    pcr_threshold: float = 1.2,
    pcr_bonus: int = 5,
    score_min: int = -20,
    score_max: int = 20,
) -> int:
    """
    Compute composite regime score from -20 to +20.
    Higher = more bullish.
    """
    score = 0

    # Ichimoku: ±8 points
    if ichimoku_signal == "BULLISH":
        score += 8
    elif ichimoku_signal == "BEARISH":
        score -= 8

    # Impulse: ±6 points
    if impulse_color == "GREEN":
        score += 6
    elif impulse_color == "RED":
        score -= 6

    # Congestion penalty: -4
    if congestion:
        score -= 4

    # Blowoff: move toward extreme
    if blowoff:
        if score > 0:
            score += 4  # blowoff top → more extreme bullish (contrarian: warning)
        else:
            score -= 4  # blowoff bottom

    # PCR bonus (bullish confirmation)
    if pcr is not None and pcr > pcr_threshold and ichimoku_signal == "BULLISH":
        score += pcr_bonus

    # VIX cap: if VIX < 12, cap score at ±score_cap
    if vix < vix_rules.stop_selling_below:
        cap = vix_rules.score_cap_below_12
        score = max(-cap, min(cap, score))

    # Clamp
    return max(score_min, min(score_max, score))


def score_to_phase(score: int, congestion: bool, blowoff: bool) -> RegimePhase:
    """Convert numeric score to RegimePhase enum."""
    if blowoff:
        return RegimePhase.BLOWOFF
    if congestion:
        return RegimePhase.CONGESTION
    if score >= 5:
        return RegimePhase.BULLISH
    elif score <= -5:
        return RegimePhase.BEARISH
    else:
        return RegimePhase.CONGESTION


# ---------------------------------------------------------------------------
# Helper: EMA, ATR
# ---------------------------------------------------------------------------

def _compute_ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average."""
    if not values or period <= 0:
        return []
    multiplier = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * multiplier + ema[-1] * (1 - multiplier))
    return ema


def _compute_atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """Average True Range."""
    if len(highs) < period + 1:
        return 0.0

    trs = []
    for i in range(-period, 0):
        h = highs[i]
        l = lows[i]
        c_prev = closes[i - 1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


# ---------------------------------------------------------------------------
# Regime Agent
# ---------------------------------------------------------------------------

class RegimeAgent(AgentBase):
    """
    Market phase classification agent.

    Subscribes to OHLCV data (via WS data manager), computes regime indicators,
    and publishes RegimeUpdateEvent when the regime state changes.

    Tick interval: 1 second (intraday rescore).
    """

    def __init__(
        self,
        config: TomicConfig,
        publisher: EventPublisher,
        regime_state: Optional[AtomicRegimeState] = None,
    ):
        super().__init__(name="regime_agent", config=config, publisher=publisher)

        self._regime_state = regime_state or AtomicRegimeState()
        self._last_published_version: int = 0

        # OHLCV buffers (populated externally via feed_candle())
        self._lock_data = threading.Lock()
        self._highs: Deque[float] = deque(maxlen=200)
        self._lows: Deque[float] = deque(maxlen=200)
        self._closes: Deque[float] = deque(maxlen=200)
        self._volumes: Deque[float] = deque(maxlen=200)
        self._vix: float = 0.0
        self._pcr: Optional[float] = None

    @property
    def regime_state(self) -> AtomicRegimeState:
        return self._regime_state

    # -------------------------------------------------------------------
    # Data ingestion (called externally by WS data manager)
    # -------------------------------------------------------------------

    def feed_candle(
        self, high: float, low: float, close: float, volume: float
    ) -> None:
        """Feed a new OHLCV candle to the indicator engine."""
        with self._lock_data:
            self._highs.append(high)
            self._lows.append(low)
            self._closes.append(close)
            self._volumes.append(volume)

    def feed_vix(self, vix: float) -> None:
        """Update current VIX value."""
        with self._lock_data:
            self._vix = vix

    def feed_pcr(self, pcr: float) -> None:
        """Update Put-Call Ratio (optional, analytics-dependent)."""
        with self._lock_data:
            self._pcr = pcr

    # -------------------------------------------------------------------
    # Agent lifecycle
    # -------------------------------------------------------------------

    def _setup(self) -> None:
        self.logger.info("Regime Agent initialized, awaiting OHLCV data")

    @staticmethod
    def _state_changed(
        current: RegimeSnapshot,
        *,
        phase: RegimePhase,
        score: int,
        vix: float,
        vix_flags: List[str],
        ichimoku_signal: str,
        impulse_color: str,
        congestion: bool,
        blowoff: bool,
    ) -> bool:
        if current.version <= 0:
            return True
        if current.phase != phase:
            return True
        if int(current.score) != int(score):
            return True
        if not math.isclose(float(current.vix), float(vix), rel_tol=0.0, abs_tol=1e-4):
            return True
        if list(current.vix_flags) != list(vix_flags):
            return True
        if str(current.ichimoku_signal) != str(ichimoku_signal):
            return True
        if str(current.impulse_color) != str(impulse_color):
            return True
        if bool(current.congestion) != bool(congestion):
            return True
        if bool(current.blowoff) != bool(blowoff):
            return True
        return False

    def _tick(self) -> None:
        """Rescore regime on every tick."""
        with self._lock_data:
            highs = list(self._highs)
            lows = list(self._lows)
            closes = list(self._closes)
            volumes = list(self._volumes)
            vix = self._vix
            pcr = self._pcr

        if len(closes) < self.config.regime.ichimoku.senkou_b:
            return  # not enough data

        # Ichimoku
        ichi = compute_ichimoku(
            highs, lows, closes,
            tenkan_period=self.config.regime.ichimoku.tenkan,
            kijun_period=self.config.regime.ichimoku.kijun,
            senkou_b_period=self.config.regime.ichimoku.senkou_b,
        )

        # Impulse System
        impulse = compute_impulse_system(
            closes,
            ema_period=self.config.regime.impulse.ema_period,
            macd_fast=self.config.regime.impulse.macd_fast,
            macd_slow=self.config.regime.impulse.macd_slow,
            macd_signal=self.config.regime.impulse.macd_signal,
        )

        # Congestion
        congestion = detect_congestion(
            highs, lows, closes,
            bbw_period=self.config.regime.bbw_period,
            min_candles=self.config.regime.congestion_min_candles,
        )

        # Blowoff
        blowoff = detect_blowoff(
            closes, highs, lows, volumes,
            atr_multiple=self.config.regime.blowoff_atr_multiple,
            volume_multiple=self.config.regime.blowoff_volume_multiple,
        )

        # VIX flags
        vix_flags = compute_vix_flags(vix, self.config.vix)

        # Composite score
        score = compute_regime_score(
            ichimoku_signal=ichi["signal"],
            impulse_color=impulse["color"],
            congestion=congestion,
            blowoff=blowoff,
            vix=vix,
            vix_rules=self.config.vix,
            pcr=pcr,
            pcr_threshold=self.config.regime.pcr_bullish_threshold,
            pcr_bonus=self.config.regime.pcr_bonus,
            score_min=self.config.regime.score_min,
            score_max=self.config.regime.score_max,
        )

        # Phase
        phase = score_to_phase(score, congestion, blowoff)

        current = self._regime_state.read_snapshot()
        if not self._state_changed(
            current,
            phase=phase,
            score=score,
            vix=vix,
            vix_flags=vix_flags,
            ichimoku_signal=ichi["signal"],
            impulse_color=impulse["color"],
            congestion=congestion,
            blowoff=blowoff,
        ):
            return

        # Write atomic state
        new_version = self._regime_state.update(
            phase=phase,
            score=score,
            vix=vix,
            vix_flags=vix_flags,
            ichimoku_signal=ichi["signal"],
            impulse_color=impulse["color"],
            congestion=congestion,
            blowoff=blowoff,
        )

        # Publish only on state change
        if new_version != self._last_published_version:
            event = RegimeUpdateEvent(
                phase=phase.value,
                score=score,
                vix=vix,
                vix_flags=vix_flags,
            )
            self._publish_event(event)
            self._last_published_version = new_version

            self.logger.info(
                "Regime v%d: %s score=%d vix=%.1f ichi=%s impulse=%s cong=%s blow=%s",
                new_version, phase.value, score, vix,
                ichi["signal"], impulse["color"], congestion, blowoff,
            )

    def _teardown(self) -> None:
        self.logger.info("Regime Agent stopped at version %d", self._regime_state.current_version)

    def _get_tick_interval(self) -> float:
        return 1.0  # 1-second rescore
