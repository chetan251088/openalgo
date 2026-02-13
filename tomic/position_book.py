"""
TOMIC PositionBook — Versioned Snapshot Model
==============================================
Single writer (Execution Agent), versioned reads for Risk/Journal agents.
Persisted to SQLite on every change. Reconciles with broker on startup.

Design per plan §8:
  - snapshot_version is a monotonic counter (never decrements)
  - read_snapshot() returns (version, positions_dict) — callers detect staleness
  - Execution Agent is the ONLY writer — no concurrent writes allowed
  - On restart: load from DB → reconcile with /api/v1/positionbook
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position data model
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """Single position entry."""
    instrument: str = ""
    exchange: str = "NSE"
    product: str = "NRML"
    direction: str = ""         # BUY (long) or SELL (short)
    quantity: int = 0
    avg_price: float = 0.0
    ltp: float = 0.0           # last traded price
    pnl: float = 0.0
    strategy_tag: str = ""     # e.g. TOMIC_VOL_CREDIT_SPREAD
    strategy_id: str = ""      # links to correlation chain
    # Greeks (for options positions)
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    # Metadata
    entry_time: str = ""
    hedge_pair_id: Optional[str] = None  # links short to its hedge leg


# ---------------------------------------------------------------------------
# Snapshot container
# ---------------------------------------------------------------------------

@dataclass
class PositionSnapshot:
    """Immutable snapshot returned to consumers."""
    version: int
    positions: Dict[str, Position]  # keyed by instrument+strategy_id
    timestamp_mono: float           # monotonic clock at snapshot time
    total_pnl: float = 0.0
    total_positions: int = 0


# ---------------------------------------------------------------------------
# PositionBook
# ---------------------------------------------------------------------------

class PositionBook:
    """
    Thread-safe, versioned position store.

    Write API (Execution Agent only):
        book.update_position(pos)
        book.remove_position(key)
        book.update_ltp(instrument, ltp)

    Read API (Risk Agent, Journaling Agent):
        snapshot = book.read_snapshot()
        snapshot.version   # monotonic version counter
        snapshot.positions # dict of Position objects (deep copy, safe to hold)

    Persistence:
        book.persist()     # flush to SQLite
        book.load()        # recover from SQLite
    """

    def __init__(self, db_path: str = "db/tomic_positions.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._positions: Dict[str, Position] = {}
        self._version: int = 0
        self._last_mono: float = time.monotonic()
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Create positions table if needed."""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    key         TEXT PRIMARY KEY,
                    data        TEXT NOT NULL,
                    updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
                );
                CREATE TABLE IF NOT EXISTS position_meta (
                    id      INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER DEFAULT 0
                );
                INSERT OR IGNORE INTO position_meta (id, version) VALUES (1, 0);
            """)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    # -----------------------------------------------------------------------
    # Position key
    # -----------------------------------------------------------------------

    @staticmethod
    def make_key(instrument: str, strategy_id: str) -> str:
        """Generate unique key for a position."""
        return f"{instrument}|{strategy_id}"

    @staticmethod
    def _pick_first(source: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
        """Return first non-empty value from key candidates."""
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                if value.strip() == "":
                    continue
                return value.strip()
            return value
        return default

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """Coerce broker numeric fields to int safely."""
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.replace(",", "").strip()
            if not text:
                return default
            try:
                return int(float(text))
            except ValueError:
                return default
        return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """Coerce broker numeric fields to float safely."""
        if value is None:
            return default
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.replace(",", "").strip()
            if not text:
                return default
            try:
                return float(text)
            except ValueError:
                return default
        return default

    # -----------------------------------------------------------------------
    # Write API (Execution Agent only)
    # -----------------------------------------------------------------------

    def update_position(self, pos: Position) -> int:
        """
        Add or update a position. Returns new snapshot version.
        Persists to DB immediately.
        """
        key = self.make_key(pos.instrument, pos.strategy_id)
        with self._lock:
            self._positions[key] = pos
            self._version += 1
            self._last_mono = time.monotonic()
            version = self._version
        self._persist_single(key, pos, version)
        logger.debug(
            "Position updated: %s qty=%d v=%d", key, pos.quantity, version
        )
        return version

    def remove_position(self, instrument: str, strategy_id: str) -> int:
        """Remove a closed position. Returns new snapshot version."""
        key = self.make_key(instrument, strategy_id)
        with self._lock:
            self._positions.pop(key, None)
            self._version += 1
            self._last_mono = time.monotonic()
            version = self._version
        self._delete_single(key, version)
        logger.info("Position removed: %s v=%d", key, version)
        return version

    def update_ltp(self, instrument: str, ltp: float) -> None:
        """
        Update last traded price for all positions in an instrument.
        Does NOT bump version (LTP is fast-path, non-critical).
        """
        with self._lock:
            for key, pos in self._positions.items():
                if pos.instrument == instrument:
                    pos.ltp = ltp
                    pos.pnl = (ltp - pos.avg_price) * pos.quantity
                    if pos.direction == "SELL":
                        pos.pnl = (pos.avg_price - ltp) * pos.quantity

    def update_greeks(
        self, instrument: str, strategy_id: str,
        delta: float, gamma: float, theta: float, vega: float,
    ) -> None:
        """Update Greeks for a specific position."""
        key = self.make_key(instrument, strategy_id)
        with self._lock:
            if key in self._positions:
                pos = self._positions[key]
                pos.delta = delta
                pos.gamma = gamma
                pos.theta = theta
                pos.vega = vega

    # -----------------------------------------------------------------------
    # Read API (Risk Agent, Journaling Agent)
    # -----------------------------------------------------------------------

    def read_snapshot(self) -> PositionSnapshot:
        """
        Return an immutable, deep-copied snapshot of current positions.
        Safe for callers to hold without affecting the book.
        """
        with self._lock:
            positions_copy = copy.deepcopy(self._positions)
            version = self._version
            mono = self._last_mono

        total_pnl = sum(p.pnl for p in positions_copy.values())

        return PositionSnapshot(
            version=version,
            positions=positions_copy,
            timestamp_mono=mono,
            total_pnl=total_pnl,
            total_positions=len(positions_copy),
        )

    @property
    def current_version(self) -> int:
        """Quick version check without copying positions."""
        with self._lock:
            return self._version

    # -----------------------------------------------------------------------
    # Convenience queries
    # -----------------------------------------------------------------------

    def count_positions(self) -> int:
        """Current open position count."""
        with self._lock:
            return len(self._positions)

    def count_by_underlying(self, instrument: str) -> int:
        """Count positions for a specific underlying."""
        with self._lock:
            return sum(
                1 for p in self._positions.values()
                if p.instrument == instrument
            )

    def get_sector_margin_pct(self, sector_instruments: List[str], total_margin: float) -> float:
        """Calculate what % of margin is consumed by a sector."""
        if total_margin <= 0:
            return 0.0
        with self._lock:
            sector_notional = sum(
                abs(p.avg_price * p.quantity)
                for p in self._positions.values()
                if p.instrument in sector_instruments
            )
        return sector_notional / total_margin

    def has_unhedged_short(self) -> List[str]:
        """
        Find short options without paired hedge legs.
        Returns list of unhedged position keys.
        """
        unhedged = []
        with self._lock:
            for key, pos in self._positions.items():
                if pos.direction == "SELL" and pos.hedge_pair_id:
                    # Check if hedge exists
                    if pos.hedge_pair_id not in self._positions:
                        unhedged.append(key)
        return unhedged

    def get_net_greeks(self) -> Dict[str, float]:
        """Portfolio-level net Greeks."""
        with self._lock:
            return {
                "delta": sum(p.delta * p.quantity for p in self._positions.values()),
                "gamma": sum(p.gamma * p.quantity for p in self._positions.values()),
                "theta": sum(p.theta * p.quantity for p in self._positions.values()),
                "vega": sum(p.vega * p.quantity for p in self._positions.values()),
            }

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def persist(self) -> None:
        """Flush all positions to SQLite."""
        with self._lock:
            positions_copy = dict(self._positions)
            version = self._version

        conn = self._get_conn()
        try:
            # Clear and rewrite (small table, simple approach)
            conn.execute("DELETE FROM positions")
            for key, pos in positions_copy.items():
                conn.execute(
                    "INSERT INTO positions (key, data) VALUES (?, ?)",
                    (key, json.dumps(asdict(pos))),
                )
            conn.execute(
                "UPDATE position_meta SET version = ? WHERE id = 1",
                (version,),
            )
            conn.commit()
            logger.debug("PositionBook persisted: %d positions, v=%d", len(positions_copy), version)
        finally:
            conn.close()

    def _persist_single(self, key: str, pos: Position, version: int) -> None:
        """Persist single position change (called on every update)."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO positions (key, data) VALUES (?, ?)",
                (key, json.dumps(asdict(pos))),
            )
            conn.execute(
                "UPDATE position_meta SET version = ? WHERE id = 1",
                (version,),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_single(self, key: str, version: int) -> None:
        """Delete single position from DB."""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM positions WHERE key = ?", (key,))
            conn.execute(
                "UPDATE position_meta SET version = ? WHERE id = 1",
                (version,),
            )
            conn.commit()
        finally:
            conn.close()

    def load(self) -> int:
        """
        Load positions from SQLite. Returns loaded version.
        Called on startup before reconciliation.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT key, data FROM positions").fetchall()
            meta = conn.execute("SELECT version FROM position_meta WHERE id = 1").fetchone()
            version = meta["version"] if meta else 0

            with self._lock:
                self._positions.clear()
                for row in rows:
                    data = json.loads(row["data"])
                    self._positions[row["key"]] = Position(**data)
                self._version = version
                self._last_mono = time.monotonic()

            logger.info("PositionBook loaded: %d positions, v=%d", len(rows), version)
            return version
        finally:
            conn.close()

    def reconcile(self, broker_positions: List[Dict[str, Any]]) -> List[str]:
        """
        Reconcile local PositionBook with broker positionbook.
        Returns list of discrepancy descriptions.
        Broker is treated as authority.
        """
        discrepancies = []

        # Build lookup from broker data
        broker_map = {}
        for bp in broker_positions:
            if not isinstance(bp, dict):
                continue

            instrument = str(
                self._pick_first(
                    bp,
                    ["symbol", "tradingsymbol", "tradingSymbol", "instrument", "trdSym"],
                    default="",
                )
                or ""
            ).strip().upper()
            if not instrument:
                continue

            quantity = self._to_int(
                self._pick_first(bp, ["quantity", "netqty", "netQty", "net_qty", "qty"], default=0),
                default=0,
            )

            # Broker positionbook includes closed rows (qty=0); skip them for open-position state.
            if quantity == 0:
                continue

            strategy_id = str(
                self._pick_first(
                    bp,
                    ["strategy_id", "strategy", "strategy_tag", "tag"],
                    default="BROKER_SYNC",
                )
                or "BROKER_SYNC"
            ).strip()

            key = self.make_key(instrument, strategy_id)
            broker_map[key] = {
                "instrument": instrument,
                "exchange": str(
                    self._pick_first(bp, ["exchange", "exchangeSegment", "exSeg"], default="NSE")
                    or "NSE"
                ).strip().upper(),
                "product": str(
                    self._pick_first(bp, ["product", "productType", "prod"], default="NRML")
                    or "NRML"
                ).strip().upper(),
                "quantity": quantity,
                "avg_price": self._to_float(
                    self._pick_first(
                        bp,
                        ["average_price", "avg_price", "avgprice", "avgPrc", "costPrice", "entry_price"],
                        default=0.0,
                    ),
                    default=0.0,
                ),
                "ltp": self._to_float(
                    self._pick_first(bp, ["ltp", "last_price", "mark_price"], default=0.0),
                    default=0.0,
                ),
                "pnl": self._to_float(
                    self._pick_first(
                        bp,
                        ["pnl", "unrealized_pnl", "unrealizedProfit", "mtm", "m2m"],
                        default=0.0,
                    ),
                    default=0.0,
                ),
                "strategy_tag": strategy_id,
            }

        with self._lock:
            local_keys = set(self._positions.keys())
            broker_keys = set(broker_map.keys())

            # Positions in local but not broker (possibly closed externally)
            for key in local_keys - broker_keys:
                discrepancies.append(f"LOCAL_ONLY: {key} — removing (broker closed)")
                del self._positions[key]

            # Positions in broker but not local (possibly opened externally)
            for key in broker_keys - local_keys:
                discrepancies.append(f"BROKER_ONLY: {key} — adding from broker")
                bp = broker_map[key]
                self._positions[key] = Position(
                    instrument=bp.get("instrument", ""),
                    exchange=bp.get("exchange", "NSE"),
                    product=bp.get("product", "NRML"),
                    quantity=int(bp.get("quantity", 0)),
                    avg_price=float(bp.get("avg_price", 0.0)),
                    ltp=float(bp.get("ltp", 0.0)),
                    pnl=float(bp.get("pnl", 0.0)),
                    direction="BUY" if int(bp.get("quantity", 0)) > 0 else "SELL",
                    strategy_id=str(bp.get("strategy_tag", "BROKER_SYNC")),
                    strategy_tag=str(bp.get("strategy_tag", "BROKER_SYNC")),
                )

            # Quantity mismatches
            for key in local_keys & broker_keys:
                local_qty = self._positions[key].quantity
                broker_qty = int(broker_map[key].get("quantity", 0))
                if local_qty != broker_qty:
                    discrepancies.append(
                        f"QTY_MISMATCH: {key} local={local_qty} broker={broker_qty} — using broker"
                    )
                    self._positions[key].quantity = broker_qty
                    self._positions[key].direction = "BUY" if broker_qty > 0 else "SELL"
                self._positions[key].avg_price = float(broker_map[key].get("avg_price", self._positions[key].avg_price))
                self._positions[key].ltp = float(broker_map[key].get("ltp", self._positions[key].ltp))
                self._positions[key].pnl = float(broker_map[key].get("pnl", self._positions[key].pnl))
                self._positions[key].exchange = str(broker_map[key].get("exchange", self._positions[key].exchange))
                self._positions[key].product = str(broker_map[key].get("product", self._positions[key].product))

            if discrepancies:
                self._version += 1
                self._last_mono = time.monotonic()

        if discrepancies:
            for d in discrepancies:
                logger.warning("RECONCILE: %s", d)
            self.persist()

        return discrepancies
