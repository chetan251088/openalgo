"""
TOMIC Market Context Agent
==========================
Aggregates market analysis into a single MarketContext snapshot:
  - India VIX (primary IV proxy, replaces broken session IV rank)
  - PCR (Put-Call Ratio) → directional bias
  - NIFTY/BANKNIFTY trend (20-MA)
  - Support / Resistance from previous-day high/low
  - Max pain and OI walls (populated when available)

Thread-safe. Single writer pattern like AtomicRegimeState.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional

from tomic.config import MarketContextParams, TomicConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classify helpers (pure functions — easily testable)
# ---------------------------------------------------------------------------

def classify_vix_regime(vix: float, params: Optional[MarketContextParams] = None) -> str:
    """Classify India VIX into regime bucket."""
    if params is None:
        params = MarketContextParams()
    if vix < params.vix_too_low:
        return "TOO_LOW"
    if vix <= params.vix_normal_high:
        return "NORMAL"
    if vix <= params.vix_elevated_high:
        return "ELEVATED"
    if vix <= params.vix_extreme:
        return "HIGH"
    return "EXTREME"


def classify_pcr_bias(pcr: float, params: Optional[MarketContextParams] = None) -> str:
    """Classify PCR into directional bias."""
    if params is None:
        params = MarketContextParams()
    if pcr > params.pcr_bullish_above:
        return "BULLISH"
    if pcr < params.pcr_bearish_below:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """Snapshot of current market conditions."""
    vix: float = 0.0
    vix_regime: str = "UNKNOWN"        # TOO_LOW / NORMAL / ELEVATED / HIGH / EXTREME
    pcr: float = 1.0
    pcr_bias: str = "NEUTRAL"          # BULLISH / BEARISH / NEUTRAL
    nifty_ltp: float = 0.0
    banknifty_ltp: float = 0.0
    sensex_ltp: float = 0.0
    nifty_trend: str = "NEUTRAL"       # ABOVE_20MA / BELOW_20MA / NEUTRAL
    banknifty_trend: str = "NEUTRAL"
    sensex_trend: str = "NEUTRAL"
    prev_day_high: Dict[str, float] = field(default_factory=dict)
    prev_day_low: Dict[str, float] = field(default_factory=dict)
    max_pain: Dict[str, float] = field(default_factory=dict)
    oi_put_wall: Dict[str, float] = field(default_factory=dict)
    oi_call_wall: Dict[str, float] = field(default_factory=dict)
    timestamp_mono: float = 0.0


class AtomicMarketContext:
    """Thread-safe versioned market context. Single writer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ctx = MarketContext()

    def update(self, ctx: MarketContext) -> None:
        with self._lock:
            self._ctx = ctx

    def read(self) -> MarketContext:
        import copy
        with self._lock:
            return copy.copy(self._ctx)


# ---------------------------------------------------------------------------
# Market Context Agent
# ---------------------------------------------------------------------------

class MarketContextAgent:
    """
    Lightweight agent — not a full AgentBase subclass.
    Updated directly by MarketBridge on each tick.
    PCR / max pain fetched asynchronously by the runtime's 5-min timer.
    """

    _MA_PERIOD = 20

    def __init__(self, config: TomicConfig) -> None:
        self._config = config
        self._params: MarketContextParams = config.market_context
        self._atomic = AtomicMarketContext()
        self._lock = threading.Lock()

        # Rolling close buffers per underlying for MA computation
        self._closes: Dict[str, deque] = {}
        self._ltps: Dict[str, float] = {}
        self._vix: float = 0.0
        self._pcr: float = 1.0
        self._prev_high: Dict[str, float] = {}
        self._prev_low: Dict[str, float] = {}
        self._max_pain: Dict[str, float] = {}
        self._oi_put_wall: Dict[str, float] = {}
        self._oi_call_wall: Dict[str, float] = {}

    # -----------------------------------------------------------------------
    # Feed methods (called by MarketBridge on every tick)
    # -----------------------------------------------------------------------

    def feed_vix(self, vix: float) -> None:
        with self._lock:
            self._vix = vix
        self._publish()

    def feed_ltp(self, underlying: str, ltp: float) -> None:
        key = underlying.upper()
        with self._lock:
            self._ltps[key] = ltp
        self._publish()

    def feed_candle(self, underlying: str, close: float) -> None:
        """Feed a new close for MA computation."""
        key = underlying.upper()
        with self._lock:
            if key not in self._closes:
                self._closes[key] = deque(maxlen=self._MA_PERIOD + 5)
            self._closes[key].append(close)
        self._publish()

    def feed_pcr(self, pcr: float, instrument: str = "NIFTY") -> None:
        with self._lock:
            self._pcr = pcr
        self._publish()

    def feed_max_pain(self, underlying: str, max_pain: float) -> None:
        with self._lock:
            self._max_pain[underlying.upper()] = max_pain
        self._publish()

    def feed_oi_walls(self, underlying: str, put_wall: float, call_wall: float) -> None:
        with self._lock:
            self._oi_put_wall[underlying.upper()] = put_wall
            self._oi_call_wall[underlying.upper()] = call_wall
        self._publish()

    def feed_prev_day(self, underlying: str, high: float, low: float) -> None:
        key = underlying.upper()
        with self._lock:
            self._prev_high[key] = high
            self._prev_low[key] = low

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    def read_context(self) -> MarketContext:
        return self._atomic.read()

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _compute_trend(self, key: str) -> str:
        closes = list(self._closes.get(key, []))
        if len(closes) < self._MA_PERIOD:
            return "NEUTRAL"
        ma = sum(closes[-self._MA_PERIOD:]) / self._MA_PERIOD
        ltp = self._ltps.get(key, closes[-1])
        if ltp > ma * 1.001:
            return "ABOVE_20MA"
        if ltp < ma * 0.999:
            return "BELOW_20MA"
        return "NEUTRAL"

    def _publish(self) -> None:
        with self._lock:
            ctx = MarketContext(
                vix=self._vix,
                vix_regime=classify_vix_regime(self._vix, self._params),
                pcr=self._pcr,
                pcr_bias=classify_pcr_bias(self._pcr, self._params),
                nifty_ltp=self._ltps.get("NIFTY", 0.0),
                banknifty_ltp=self._ltps.get("BANKNIFTY", 0.0),
                sensex_ltp=self._ltps.get("SENSEX", 0.0),
                nifty_trend=self._compute_trend("NIFTY"),
                banknifty_trend=self._compute_trend("BANKNIFTY"),
                sensex_trend=self._compute_trend("SENSEX"),
                prev_day_high=dict(self._prev_high),
                prev_day_low=dict(self._prev_low),
                max_pain=dict(self._max_pain),
                oi_put_wall=dict(self._oi_put_wall),
                oi_call_wall=dict(self._oi_call_wall),
                timestamp_mono=time.monotonic(),
            )
        self._atomic.update(ctx)
