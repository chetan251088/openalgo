"""
Rotation Client - Calls the Sector Rotation Map API and detects quadrant transitions.
"""

import os
import time
import logging
from typing import Optional

import httpx

from .models import RotationSignal, SectorRotation, RRGQuadrant

logger = logging.getLogger(__name__)

SECTOR_TO_STOCKS = {
    "NIFTYIT": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"],
    "NIFTYAUTO": ["M&M", "MARUTI", "TATAMOTORS", "BAJAJ-AUTO", "HEROMOTOCO"],
    "NIFTYPHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP"],
    "NIFTYENERGY": ["RELIANCE", "NTPC", "POWERGRID", "ONGC", "ADANIGREEN"],
    "NIFTYFMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "GODREJCP"],
    "NIFTYMETAL": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "ADANIENT", "COALINDIA"],
    "NIFTYREALTY": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD"],
    "NIFTYPVTBANK": ["HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "INDUSINDBK"],
    "NIFTYPSUBANK": ["SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK"],
    "NIFTYMEDIA": ["ZEEL", "PVR", "SUNTV", "NETWORK18", "TV18BRDCST"],
    "NIFTYINFRA": ["LT", "RELIANCE", "NTPC", "POWERGRID", "ADANIPORTS"],
    "NIFTYCOMMODITIES": ["RELIANCE", "ONGC", "TATASTEEL", "COALINDIA", "HINDALCO"],
}

STOCK_TO_SECTOR: dict = {}
for _sector, _stocks in SECTOR_TO_STOCKS.items():
    for _stock in _stocks:
        if _stock not in STOCK_TO_SECTOR:
            STOCK_TO_SECTOR[_stock] = _sector


class RotationClient:
    """HTTP client for the Sector Rotation Map API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        cache_ttl: int = 300,
    ):
        self.base_url = (base_url or os.getenv("SECTOR_ROTATION_URL", "http://localhost:8000")).rstrip("/")
        self._cache: Optional[RotationSignal] = None
        self._cache_ts: float = 0.0
        self._cache_ttl = cache_ttl
        self._prev_quadrants: dict = {}

    def get_rotation(
        self,
        benchmark: str = "NIFTY",
        tail: int = 8,
    ) -> Optional[RotationSignal]:
        """Fetch RRG data and detect quadrant transitions."""
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    f"{self.base_url}/api/rrg",
                    params={"benchmark": benchmark, "tail": tail},
                )
                resp.raise_for_status()
                body = resp.json()

            sectors_data = body.get("sectors", {})
            sectors = []
            leading = []
            lagging = []
            improving = []
            weakening = []
            transitions = []

            for sym, info in sectors_data.items():
                if not info.get("isDefault", False):
                    continue

                current = info.get("current", {})
                quadrant_str = info.get("quadrant", "Lagging")

                try:
                    quadrant = RRGQuadrant(quadrant_str)
                except ValueError:
                    quadrant = RRGQuadrant.LAGGING

                sr = SectorRotation(
                    symbol=sym,
                    name=info.get("name", sym),
                    quadrant=quadrant,
                    rs_ratio=current.get("rs_ratio", 100.0),
                    rs_momentum=current.get("rs_momentum", 100.0),
                    date=current.get("date", ""),
                )
                sectors.append(sr)

                if quadrant == RRGQuadrant.LEADING:
                    leading.append(sym)
                elif quadrant == RRGQuadrant.LAGGING:
                    lagging.append(sym)
                elif quadrant == RRGQuadrant.IMPROVING:
                    improving.append(sym)
                elif quadrant == RRGQuadrant.WEAKENING:
                    weakening.append(sym)

                prev_q = self._prev_quadrants.get(sym)
                if prev_q and prev_q != quadrant_str:
                    transitions.append({
                        "symbol": sym,
                        "name": info.get("name", sym),
                        "from_quadrant": prev_q,
                        "to_quadrant": quadrant_str,
                    })

                self._prev_quadrants[sym] = quadrant_str

            signal = RotationSignal(
                sectors=sectors,
                transitions=transitions,
                leading_sectors=leading,
                lagging_sectors=lagging,
                improving_sectors=improving,
                weakening_sectors=weakening,
                benchmark=benchmark,
                timestamp=time.time(),
                stale=False,
            )

            self._cache = signal
            self._cache_ts = time.time()
            return signal

        except httpx.ConnectError:
            logger.warning("Sector Rotation service unreachable at %s", self.base_url)
            return self._mark_stale()
        except Exception as e:
            logger.error("Rotation client error: %s", e)
            return self._mark_stale()

    def get_stock_rotation(
        self,
        symbols: list,
        benchmark: str = "NIFTY",
        tail: int = 8,
    ) -> dict:
        """Fetch RRG data for individual stocks."""
        try:
            csv = ",".join(symbols)
            with httpx.Client(timeout=15) as client:
                resp = client.get(
                    f"{self.base_url}/api/rrg-stocks",
                    params={"symbols": csv, "benchmark": benchmark, "tail": tail},
                )
                resp.raise_for_status()
                body = resp.json()

            result = {}
            for sym, info in body.get("stocks", {}).items():
                current = info.get("current", {})
                try:
                    quadrant = RRGQuadrant(info.get("quadrant", "Lagging"))
                except ValueError:
                    quadrant = RRGQuadrant.LAGGING
                result[sym] = SectorRotation(
                    symbol=sym,
                    name=info.get("name", sym),
                    quadrant=quadrant,
                    rs_ratio=current.get("rs_ratio", 100.0),
                    rs_momentum=current.get("rs_momentum", 100.0),
                    date=current.get("date", ""),
                )
            return result

        except Exception as e:
            logger.error("Stock rotation fetch failed: %s", e)
            return {}

    def get_sector_for_stock(self, stock_symbol: str) -> Optional[str]:
        """Map a stock symbol to its sector index symbol."""
        return STOCK_TO_SECTOR.get(stock_symbol)

    def get_cached(self) -> Optional[RotationSignal]:
        if self._cache is None:
            return None
        age = time.time() - self._cache_ts
        if age > self._cache_ttl:
            self._cache.stale = True
        return self._cache

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/api/health")
                return resp.status_code == 200
        except Exception:
            return False

    def _mark_stale(self) -> Optional[RotationSignal]:
        if self._cache:
            self._cache.stale = True
        return self._cache
