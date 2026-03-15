"""
MarketContextAgent — Aggregates VIX, PCR, trend, support/resistance,
MiroFish bias, and sector rotation into a unified AtomicMarketContext.
Fed by MarketBridge on every tick + periodic intelligence refreshes.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List

from .config import MarketContextParams

logger = logging.getLogger(__name__)


@dataclass
class AtomicMarketContext:
    """Thread-safe snapshot of current market conditions."""
    # VIX data
    vix: float = 0.0
    vix_regime: str = "UNKNOWN"  # TOO_LOW, NORMAL, ELEVATED, HIGH, EXTREME

    # PCR data
    pcr: float = 0.0
    pcr_bias: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL

    # Trend data
    nifty_ltp: float = 0.0
    nifty_trend: str = "UNKNOWN"  # ABOVE_20MA, BELOW_20MA, AT_20MA
    nifty_20ma: float = 0.0

    # Support/Resistance
    max_pain: float = 0.0
    oi_support: float = 0.0
    oi_resistance: float = 0.0

    # Intelligence integration
    mirofish_bias: Optional[str] = None
    mirofish_confidence: float = 0.0
    leading_sectors: List[str] = field(default_factory=list)
    lagging_sectors: List[str] = field(default_factory=list)
    rotation_transitions: List[dict] = field(default_factory=list)

    # Metadata
    timestamp: float = 0.0
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "vix": self.vix,
            "vix_regime": self.vix_regime,
            "pcr": self.pcr,
            "pcr_bias": self.pcr_bias,
            "nifty_ltp": self.nifty_ltp,
            "nifty_trend": self.nifty_trend,
            "nifty_20ma": self.nifty_20ma,
            "max_pain": self.max_pain,
            "oi_support": self.oi_support,
            "oi_resistance": self.oi_resistance,
            "mirofish_bias": self.mirofish_bias,
            "mirofish_confidence": self.mirofish_confidence,
            "leading_sectors": self.leading_sectors,
            "lagging_sectors": self.lagging_sectors,
            "rotation_transitions": self.rotation_transitions,
            "timestamp": self.timestamp,
            "stale": self.stale,
        }


class MarketContextAgent:
    """Maintains a live AtomicMarketContext updated from ticks and intelligence."""

    def __init__(self, config: Optional[MarketContextParams] = None, intelligence_service=None):
        self.config = config or MarketContextParams()
        self.intelligence = intelligence_service
        self._context = AtomicMarketContext()
        self._lock = threading.Lock()
        self._price_buffer: list = []  # recent NIFTY prices for MA calculation
        self._ma_period = self.config.trend_ma_period

    def get_context(self) -> AtomicMarketContext:
        """Return a snapshot of current market context."""
        with self._lock:
            ctx = AtomicMarketContext(**{
                k: getattr(self._context, k) for k in self._context.__dataclass_fields__
            })

            # Enrich with intelligence data if available
            if self.intelligence:
                intel = self.intelligence.get_intelligence()
                if intel and intel.mirofish:
                    ctx.mirofish_bias = intel.mirofish.bias.value if hasattr(intel.mirofish.bias, 'value') else intel.mirofish.bias
                    ctx.mirofish_confidence = intel.mirofish.confidence
                if intel and intel.rotation:
                    ctx.leading_sectors = intel.rotation.leading_sectors
                    ctx.lagging_sectors = intel.rotation.lagging_sectors
                    ctx.rotation_transitions = intel.rotation.transitions

            ctx.timestamp = time.time()
            return ctx

    def feed_vix(self, vix: float) -> None:
        """Update VIX value and compute regime."""
        with self._lock:
            self._context.vix = vix
            if vix < self.config.vix_too_low:
                self._context.vix_regime = "TOO_LOW"
            elif vix < self.config.vix_normal_high:
                self._context.vix_regime = "NORMAL"
            elif vix < self.config.vix_elevated_high:
                self._context.vix_regime = "ELEVATED"
            elif vix < self.config.vix_extreme:
                self._context.vix_regime = "HIGH"
            else:
                self._context.vix_regime = "EXTREME"

    def feed_ltp(self, symbol: str, ltp: float) -> None:
        """Update LTP for underlying. Compute 20-MA trend for NIFTY."""
        if symbol == "NIFTY":
            with self._lock:
                self._context.nifty_ltp = ltp
                self._price_buffer.append(ltp)
                if len(self._price_buffer) > self._ma_period * 10:
                    self._price_buffer = self._price_buffer[-self._ma_period * 10:]

                if len(self._price_buffer) >= self._ma_period:
                    ma = sum(self._price_buffer[-self._ma_period:]) / self._ma_period
                    self._context.nifty_20ma = ma
                    margin = 0.001 * ltp  # 0.1% band
                    if ltp > ma + margin:
                        self._context.nifty_trend = "ABOVE_20MA"
                    elif ltp < ma - margin:
                        self._context.nifty_trend = "BELOW_20MA"
                    else:
                        self._context.nifty_trend = "AT_20MA"

    def feed_pcr(self, pcr: float) -> None:
        """Update PCR value and bias."""
        with self._lock:
            self._context.pcr = pcr
            if pcr > self.config.pcr_bullish_above:
                self._context.pcr_bias = "BULLISH"
            elif pcr < self.config.pcr_bearish_below:
                self._context.pcr_bias = "BEARISH"
            else:
                self._context.pcr_bias = "NEUTRAL"

    def feed_oi_levels(self, max_pain: float, support: float, resistance: float) -> None:
        """Update OI-derived levels."""
        with self._lock:
            self._context.max_pain = max_pain
            self._context.oi_support = support
            self._context.oi_resistance = resistance

    def reset(self) -> None:
        """Reset context to defaults."""
        with self._lock:
            self._context = AtomicMarketContext()
            self._price_buffer.clear()
