"""
TOMIC Freshness Gates — Data Staleness Checks
===============================================
All timing uses monotonic clock (time.monotonic()) to avoid
wall-clock drift from NTP sync, DST, or manual changes.

Two categories of gates:
  1. Market data: underlying quote, option quote, depth, feed switch cooldown
  2. Analytics data: PCR, GEX, MaxPain, IV/Greeks, VIX

Gates are checked by Risk Agent and Execution Agent before every order.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from tomic.config import FreshnessThresholds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate results
# ---------------------------------------------------------------------------

class GateResult(str, Enum):
    PASS = "PASS"
    STALE_QUOTE = "STALE_QUOTE"
    STALE_OPTION = "STALE_OPTION"
    FEED_SWITCHING = "FEED_SWITCHING"
    NO_DEPTH = "NO_DEPTH"
    PCR_STALE = "PCR_STALE"
    GEX_STALE = "GEX_STALE"
    MAX_PAIN_STALE = "MAX_PAIN_STALE"
    IV_STALE = "IV_STALE"
    IV_HARD_BLOCK = "IV_HARD_BLOCK"
    VIX_STALE = "VIX_STALE"


@dataclass
class FreshnessReport:
    """Result of running all freshness gates for an order."""
    passed: bool
    blocking_gates: List[GateResult] = field(default_factory=list)
    warning_gates: List[GateResult] = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)

    def add_block(self, gate: GateResult, detail: str = "") -> None:
        self.passed = False
        self.blocking_gates.append(gate)
        if detail:
            self.details[gate.value] = detail

    def add_warning(self, gate: GateResult, detail: str = "") -> None:
        self.warning_gates.append(gate)
        if detail:
            self.details[gate.value] = detail


# ---------------------------------------------------------------------------
# Freshness Tracker
# ---------------------------------------------------------------------------

class FreshnessTracker:
    """
    Thread-safe tracker for data freshness using monotonic clock.

    Usage:
        tracker = FreshnessTracker(thresholds)

        # Data manager calls on every tick:
        tracker.update_quote("NIFTY")
        tracker.update_option_quote("NIFTY25FEB23000CE")
        tracker.update_depth("NIFTY")

        # On feed switch:
        tracker.record_feed_switch()

        # Analytics updates:
        tracker.update_pcr()
        tracker.update_gex()
        tracker.update_iv()

        # Before every order, Risk/Execution agents call:
        report = tracker.check_order_gates("NIFTY", needs_depth=True, is_credit_spread=True)
        if not report.passed:
            # Block the order
    """

    def __init__(self, thresholds: Optional[FreshnessThresholds] = None):
        self._thresholds = thresholds or FreshnessThresholds()
        self._lock = threading.Lock()

        # Market data timestamps (monotonic)
        self._quote_times: Dict[str, float] = {}
        self._option_quote_times: Dict[str, float] = {}
        self._depth_available: Set[str] = set()
        self._depth_times: Dict[str, float] = {}

        # Analytics timestamps (monotonic)
        self._pcr_time: float = 0.0
        self._gex_time: float = 0.0
        self._max_pain_time: float = 0.0
        self._iv_time: float = 0.0
        self._vix_time: float = 0.0

        # Feed switch tracking
        self._last_feed_switch: float = 0.0

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        token = str(symbol or "").strip().upper()
        if not token:
            return ""

        if ":" in token:
            left, right = token.split(":", 1)
            right = right.strip().upper()
            if right:
                token = right
            else:
                token = left.strip().upper()

        if "." in token:
            left, right = token.split(".", 1)
            right = right.strip().upper()
            if right in {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "NSE_INDEX", "BSE_INDEX"}:
                token = left.strip().upper()

        compact = token.replace(" ", "").replace("-", "").replace("_", "")
        if not compact:
            return ""

        alias_map = {
            "NIFTY": "NIFTY",
            "NIFTY50": "NIFTY",
            "NIFTYINDEX": "NIFTY",
            "BANKNIFTY": "BANKNIFTY",
            "NIFTYBANK": "BANKNIFTY",
            "BANKNIFTYINDEX": "BANKNIFTY",
            "FINNIFTY": "FINNIFTY",
            "MIDCPNIFTY": "MIDCPNIFTY",
            "INDIAVIX": "INDIAVIX",
            "INDIAVOLATILITYINDEX": "INDIAVIX",
            "VIX": "INDIAVIX",
            "SENSEX": "SENSEX",
            "SENSEXINDEX": "SENSEX",
            "BANKEX": "BANKEX",
        }
        mapped = alias_map.get(compact)
        if mapped:
            return mapped

        # Derivative symbols: extract base underlying if symbol contains expiry/option suffix.
        deriv_match = re.match(r"^([A-Z]+)\d{2}[A-Z]{3}\d{2}(?:\d+(?:CE|PE)|FUT)?$", compact)
        if deriv_match:
            return deriv_match.group(1)

        # Fallback for compact index aliases that include suffix words.
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

        return compact

    # -----------------------------------------------------------------------
    # Market data updates (called by ws_data_manager)
    # -----------------------------------------------------------------------

    def update_quote(self, symbol: str) -> None:
        """Record receipt of underlying quote tick."""
        key = self._normalize_symbol(symbol)
        if not key:
            return
        with self._lock:
            self._quote_times[key] = time.monotonic()

    def update_option_quote(self, symbol: str) -> None:
        """Record receipt of option quote tick."""
        key = self._normalize_symbol(symbol)
        if not key:
            return
        with self._lock:
            self._option_quote_times[key] = time.monotonic()

    def update_depth(self, symbol: str) -> None:
        """Record receipt of depth data."""
        key = self._normalize_symbol(symbol)
        if not key:
            return
        with self._lock:
            self._depth_available.add(key)
            self._depth_times[key] = time.monotonic()

    def remove_depth(self, symbol: str) -> None:
        """Mark depth as unavailable for a symbol."""
        key = self._normalize_symbol(symbol)
        if not key:
            return
        with self._lock:
            self._depth_available.discard(key)

    def record_feed_switch(self) -> None:
        """Record that feed has switched (primary ↔ fallback)."""
        with self._lock:
            self._last_feed_switch = time.monotonic()
        logger.warning("FEED_SWITCH recorded — cooldown %ss", self._thresholds.feed_switch_cooldown)

    # -----------------------------------------------------------------------
    # Analytics data updates
    # -----------------------------------------------------------------------

    def update_pcr(self) -> None:
        with self._lock:
            self._pcr_time = time.monotonic()

    def update_gex(self) -> None:
        with self._lock:
            self._gex_time = time.monotonic()

    def update_max_pain(self) -> None:
        with self._lock:
            self._max_pain_time = time.monotonic()

    def update_iv(self) -> None:
        with self._lock:
            self._iv_time = time.monotonic()

    def update_vix(self) -> None:
        with self._lock:
            self._vix_time = time.monotonic()

    # -----------------------------------------------------------------------
    # Gate checks
    # -----------------------------------------------------------------------

    def check_order_gates(
        self,
        underlying: str,
        option_symbol: str = "",
        needs_depth: bool = False,
        is_credit_spread: bool = False,
    ) -> FreshnessReport:
        """
        Run all freshness gates for a proposed order.
        Returns FreshnessReport with blocking and warning gates.
        """
        now = time.monotonic()
        report = FreshnessReport(passed=True)
        th = self._thresholds
        under_key = self._normalize_symbol(underlying)
        opt_key = self._normalize_symbol(option_symbol)

        with self._lock:
            # --- Market data gates (BLOCKING) ---

            # Underlying quote freshness
            last_quote = self._quote_times.get(under_key, 0.0)
            quote_age = now - last_quote if last_quote > 0 else float("inf")
            if quote_age > th.underlying_quote_max_age:
                report.add_block(
                    GateResult.STALE_QUOTE,
                    f"{under_key or underlying} quote age={quote_age:.1f}s > {th.underlying_quote_max_age}s",
                )

            # Option quote freshness
            if opt_key:
                last_opt = self._option_quote_times.get(opt_key, 0.0)
                opt_age = now - last_opt if last_opt > 0 else float("inf")
                if opt_age > th.option_quote_max_age:
                    report.add_block(
                        GateResult.STALE_OPTION,
                        f"{opt_key} age={opt_age:.1f}s > {th.option_quote_max_age}s",
                    )

            # Feed switch cooldown
            if self._last_feed_switch > 0:
                since_switch = now - self._last_feed_switch
                if since_switch < th.feed_switch_cooldown:
                    report.add_block(
                        GateResult.FEED_SWITCHING,
                        f"Feed switched {since_switch:.1f}s ago, cooldown={th.feed_switch_cooldown}s",
                    )

            # Depth availability
            if needs_depth and under_key not in self._depth_available:
                report.add_block(
                    GateResult.NO_DEPTH,
                    f"Depth data missing for {under_key or underlying}",
                )

            # --- Analytics gates (WARNING or BLOCKING) ---

            # PCR
            pcr_age = now - self._pcr_time if self._pcr_time > 0 else float("inf")
            if pcr_age > th.pcr_max_age:
                report.add_warning(
                    GateResult.PCR_STALE,
                    f"PCR age={pcr_age:.0f}s > {th.pcr_max_age}s — using last known",
                )

            # GEX
            gex_age = now - self._gex_time if self._gex_time > 0 else float("inf")
            if gex_age > th.gex_max_age:
                report.add_warning(
                    GateResult.GEX_STALE,
                    f"GEX age={gex_age:.0f}s > {th.gex_max_age}s — skipping GEX S/R",
                )

            # Max Pain
            mp_age = now - self._max_pain_time if self._max_pain_time > 0 else float("inf")
            if mp_age > th.max_pain_max_age:
                report.add_warning(
                    GateResult.MAX_PAIN_STALE,
                    f"MaxPain age={mp_age:.0f}s > {th.max_pain_max_age}s — skipping bias",
                )

            # IV / Greeks
            iv_age = now - self._iv_time if self._iv_time > 0 else float("inf")
            if iv_age > th.iv_greeks_hard_block and is_credit_spread:
                report.add_block(
                    GateResult.IV_HARD_BLOCK,
                    f"IV age={iv_age:.0f}s > {th.iv_greeks_hard_block}s — blocking credit spread",
                )
            elif iv_age > th.iv_greeks_max_age:
                report.add_warning(
                    GateResult.IV_STALE,
                    f"IV age={iv_age:.0f}s > {th.iv_greeks_max_age}s — using API fallback",
                )

            # VIX
            vix_age = now - self._vix_time if self._vix_time > 0 else float("inf")
            if vix_age > th.vix_max_age:
                report.add_warning(
                    GateResult.VIX_STALE,
                    f"VIX age={vix_age:.0f}s > {th.vix_max_age}s — using last known",
                )

        # Log
        if not report.passed:
            logger.warning(
                "FRESHNESS_BLOCK: %s gates=%s",
                under_key or underlying,
                [g.value for g in report.blocking_gates],
            )
        elif report.warning_gates:
            logger.info(
                "FRESHNESS_WARN: %s warnings=%s",
                under_key or underlying,
                [g.value for g in report.warning_gates],
            )

        return report

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    def get_all_ages(self) -> Dict[str, float]:
        """Return current age of all tracked data sources (for observability)."""
        now = time.monotonic()
        with self._lock:
            ages = {
                "pcr": now - self._pcr_time if self._pcr_time else -1,
                "gex": now - self._gex_time if self._gex_time else -1,
                "max_pain": now - self._max_pain_time if self._max_pain_time else -1,
                "iv": now - self._iv_time if self._iv_time else -1,
                "vix": now - self._vix_time if self._vix_time else -1,
            }
            for sym, t in self._quote_times.items():
                ages[f"quote:{sym}"] = now - t
            return ages
