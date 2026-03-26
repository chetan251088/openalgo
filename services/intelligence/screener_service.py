"""
Screener Service - Manages fundamental data from Screener.in.

Architecture: The heavy Playwright scraping runs in an EXTERNAL sidecar script
(scripts/fetch_fundamentals.py) that writes to a JSON file. This service reads
that file. This keeps the browser process out of the execution app.

Fallback: If the JSON file doesn't exist, optionally fetches via openscreener
in-process (but this is discouraged in production).
"""

import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import FundamentalProfile, FundamentalSignal

logger = logging.getLogger(__name__)

FUNDAMENTALS_CACHE_FILE = os.getenv(
    "FUNDAMENTALS_CACHE_FILE",
    os.path.join(os.path.dirname(__file__), "..", "..", "db", "fundamentals_cache.json"),
)


class ScreenerService:
    """Reads fundamental data from a pre-fetched cache file (sidecar pattern).
    
    The cache file is written by an external script (scripts/fetch_fundamentals.py)
    that runs Playwright outside the trading process. This avoids browser overhead,
    DOM-drift breakage, and memory bloat inside the execution app.
    """

    def __init__(self):
        self._cache: dict = {}
        self._cache_date: str = ""
        self._signal: Optional[FundamentalSignal] = None
        self._cache_file = Path(FUNDAMENTALS_CACHE_FILE)

        self._roce_min = float(os.getenv("FUNDAMENTAL_ROCE_MIN", "12"))
        self._debt_equity_max = float(os.getenv("FUNDAMENTAL_DEBT_EQUITY_MAX", "1.5"))
        self._promoter_min = float(os.getenv("FUNDAMENTAL_PROMOTER_MIN", "30"))
        self._fii_sell_max = float(os.getenv("FUNDAMENTAL_FII_SELL_MAX", "3"))

    def fetch_fundamentals(self, symbols: list | None) -> dict:
        """Load fundamentals from the sidecar cache file.
        Falls back to in-process openscreener if file is missing.
        """
        symbols = [str(symbol).upper() for symbol in (symbols or []) if symbol]
        today = datetime.now().strftime("%Y-%m-%d")
        if self._cache_date == today and self._cache:
            if not symbols:
                return dict(self._cache)
            missing = [s for s in symbols if s not in self._cache]
            if not missing:
                return {s: self._cache[s] for s in symbols if s in self._cache}
            symbols = missing

        # Try loading from sidecar cache file first
        loaded_result = self._load_from_cache_file(symbols)
        if loaded_result:
            loaded, file_date = loaded_result
            logger.info("Loaded %d fundamental profiles from cache file", len(loaded))
            for sym, profile in loaded.items():
                profile = self.apply_gate(profile)
                loaded[sym] = profile
            self._cache.update(loaded)
            self._cache_date = file_date or today
            self._rebuild_signal()
            return dict(self._cache) if not symbols else loaded

        # Fallback: in-process fetch (discouraged in production)
        logger.warning(
            "Fundamentals cache file not found at %s. "
            "Run scripts/fetch_fundamentals.py to populate it. "
            "Falling back to in-process fetch.",
            self._cache_file,
        )
        return self._fetch_in_process(symbols)

    def _load_from_cache_file(self, symbols: list) -> Optional[tuple[dict, str]]:
        """Load from the JSON cache file written by the sidecar script."""
        if not self._cache_file.exists():
            return None

        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
            file_date = data.get("date", "")
            today = datetime.now().strftime("%Y-%m-%d")
            all_profiles = data.get("profiles", {})

            if file_date != today:
                logger.warning("Cache file is from %s, not today (%s). Data may be stale.", file_date, today)

            profiles = {}
            requested_symbols = symbols or sorted(all_profiles.keys())
            for sym in requested_symbols:
                sym_data = all_profiles.get(sym)
                if sym_data:
                    profiles[sym] = FundamentalProfile(
                        symbol=sym,
                        roce=sym_data.get("roce"),
                        pe=sym_data.get("pe"),
                        debt_equity=sym_data.get("debt_equity"),
                        promoter_holding=sym_data.get("promoter_holding"),
                        fii_change_qoq=sym_data.get("fii_change_qoq"),
                        market_cap=sym_data.get("market_cap"),
                        quarterly_profit_growth=sym_data.get("quarterly_profit_growth"),
                        cleared=True,
                    )
                else:
                    profiles[sym] = FundamentalProfile(symbol=sym, cleared=True, block_reason="not_in_cache")

            return (profiles, file_date) if profiles else None
        except Exception as e:
            logger.error("Failed to read fundamentals cache: %s", e)
            return None

    def _fetch_in_process(self, symbols: list) -> dict:
        """In-process fallback using openscreener. Discouraged in production."""
        if not symbols:
            return dict(self._cache)

        profiles = {}
        try:
            from openscreener import Stock

            batch_size = 10
            for i in range(0, len(symbols), batch_size):
                batch_symbols = symbols[i : i + batch_size]
                try:
                    batch = Stock.batch(batch_symbols)
                    data = batch.fetch(["summary", "ratios", "shareholding", "quarterly_results"])
                    for sym in batch_symbols:
                        sym_data = data.get(sym, {})
                        profile = self._parse_profile(sym, sym_data)
                        profile = self.apply_gate(profile)
                        profiles[sym] = profile
                except Exception as e:
                    logger.warning("Batch fetch failed for %s: %s", batch_symbols, e)
                    for sym in batch_symbols:
                        profiles[sym] = FundamentalProfile(symbol=sym, cleared=True, block_reason="fetch_failed")

        except ImportError:
            logger.warning("openscreener not installed. All symbols cleared by default.")
            for sym in symbols:
                profiles[sym] = FundamentalProfile(symbol=sym, cleared=True, block_reason="openscreener_not_installed")

        self._cache.update(profiles)
        self._cache_date = datetime.now().strftime("%Y-%m-%d")
        self._rebuild_signal()
        return profiles

    # Sector-specific thresholds: banks/NBFCs get relaxed D/E, IT gets higher ROCE floor
    SECTOR_THRESHOLDS = {
        "NIFTYPVTBANK": {"roce_min": 10, "debt_equity_max": 8.0, "promoter_min": 20, "fii_sell_max": 5},
        "NIFTYPSUBANK": {"roce_min": 8, "debt_equity_max": 10.0, "promoter_min": 50, "fii_sell_max": 5},
        "NIFTYIT": {"roce_min": 18, "debt_equity_max": 0.5, "promoter_min": 30, "fii_sell_max": 3},
        "NIFTYMETAL": {"roce_min": 8, "debt_equity_max": 2.0, "promoter_min": 30, "fii_sell_max": 4},
        "NIFTYREALTY": {"roce_min": 8, "debt_equity_max": 2.0, "promoter_min": 40, "fii_sell_max": 3},
    }

    def _get_thresholds(self, symbol: str) -> dict:
        """Get sector-appropriate thresholds for a symbol."""
        from .rotation_client import STOCK_TO_SECTOR
        sector = STOCK_TO_SECTOR.get(symbol, "")
        if sector in self.SECTOR_THRESHOLDS:
            th = self.SECTOR_THRESHOLDS[sector]
            return {
                "roce_min": th["roce_min"],
                "debt_equity_max": th["debt_equity_max"],
                "promoter_min": th["promoter_min"],
                "fii_sell_max": th["fii_sell_max"],
            }
        return {
            "roce_min": self._roce_min,
            "debt_equity_max": self._debt_equity_max,
            "promoter_min": self._promoter_min,
            "fii_sell_max": self._fii_sell_max,
        }

    def apply_gate(self, profile: FundamentalProfile) -> FundamentalProfile:
        """Apply sector-specific fundamental pass/fail rules."""
        th = self._get_thresholds(profile.symbol)
        reasons = []

        if profile.roce is not None and profile.roce < th["roce_min"]:
            reasons.append(f"ROCE {profile.roce:.1f}% < {th['roce_min']}%")

        if profile.debt_equity is not None and profile.debt_equity > th["debt_equity_max"]:
            reasons.append(f"D/E {profile.debt_equity:.2f} > {th['debt_equity_max']}")

        if profile.promoter_holding is not None and profile.promoter_holding < th["promoter_min"]:
            reasons.append(f"Promoter {profile.promoter_holding:.1f}% < {th['promoter_min']}%")

        if profile.fii_change_qoq is not None and profile.fii_change_qoq < -th["fii_sell_max"]:
            reasons.append(f"FII selling {abs(profile.fii_change_qoq):.1f}% > {th['fii_sell_max']}%")

        if reasons:
            profile.cleared = False
            profile.block_reason = "; ".join(reasons)
        else:
            profile.cleared = True
            profile.block_reason = ""

        return profile

    def get_cached(self) -> Optional[FundamentalSignal]:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._signal and self._cache_date == today:
            return self._signal
        if self._signal:
            self._signal.stale = True
        return self._signal

    def is_symbol_cleared(self, symbol: str) -> bool:
        """Quick check if a symbol passes the fundamental gate.
        Index symbols (NIFTY, BANKNIFTY, SENSEX) always pass.
        """
        index_symbols = {"NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX"}
        if symbol.upper() in index_symbols:
            return True

        profile = self._cache.get(symbol)
        if profile is None:
            return True  # unknown symbols pass by default
        return profile.cleared

    def _parse_profile(self, symbol: str, data: dict) -> FundamentalProfile:
        """Parse openscreener response into FundamentalProfile."""
        summary = data.get("summary", {})
        ratios = data.get("ratios", {})
        shareholding = data.get("shareholding", [])
        quarterly = data.get("quarterly_results", [])

        summary_ratios = summary.get("ratios", {}) if isinstance(summary, dict) else {}

        profile = FundamentalProfile(symbol=symbol)

        profile.roce = _safe_float(ratios.get("roce_percent"))
        profile.pe = _safe_float(ratios.get("pe") or summary_ratios.get("pe"))
        profile.debt_equity = _safe_float(ratios.get("debt_to_equity"))
        profile.market_cap = _safe_float(summary_ratios.get("market_cap"))

        if shareholding and len(shareholding) >= 1:
            latest = shareholding[0] if isinstance(shareholding[0], dict) else {}
            profile.promoter_holding = _safe_float(latest.get("promoter"))

            if len(shareholding) >= 2:
                prev = shareholding[1] if isinstance(shareholding[1], dict) else {}
                curr_fii = _safe_float(latest.get("fii"))
                prev_fii = _safe_float(prev.get("fii"))
                if curr_fii is not None and prev_fii is not None:
                    profile.fii_change_qoq = curr_fii - prev_fii

        if quarterly and len(quarterly) >= 2:
            curr_q = quarterly[0] if isinstance(quarterly[0], dict) else {}
            prev_q = quarterly[1] if isinstance(quarterly[1], dict) else {}
            curr_profit = _safe_float(curr_q.get("net_profit"))
            prev_profit = _safe_float(prev_q.get("net_profit"))
            if curr_profit is not None and prev_profit is not None and prev_profit != 0:
                profile.quarterly_profit_growth = ((curr_profit - prev_profit) / abs(prev_profit)) * 100

        return profile

    def _rebuild_signal(self) -> None:
        cleared = set()
        blocked = {}
        for sym, profile in self._cache.items():
            if profile.cleared:
                cleared.add(sym)
            else:
                blocked[sym] = profile.block_reason

        self._signal = FundamentalSignal(
            profiles=dict(self._cache),
            cleared_symbols=cleared,
            blocked_symbols=blocked,
            timestamp=time.time(),
            stale=False,
        )


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
