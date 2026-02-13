"""
TOMIC Journaling Agent — Trade Recording & Performance Metrics
================================================================
Reads DONE commands from the command table.
Records full context: regime state, sizing chain, execution quality.
Idempotency-safe: skips already-journaled event_ids.
Calculates rolling performance metrics.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from tomic.agent_base import AgentBase
from tomic.command_store import CommandStore
from tomic.config import TomicConfig
from tomic.event_bus import EventPublisher
from tomic.position_book import PositionBook

logger = logging.getLogger(__name__)


class JournalingAgent(AgentBase):
    """
    Records trade data and calculates performance metrics.

    Idempotency: checks event_id uniqueness before inserting.
    Stores full sizing chain for post-trade analysis.
    """

    def __init__(
        self,
        config: TomicConfig,
        publisher: EventPublisher,
        command_store: CommandStore,
        position_book: PositionBook,
        journal_db_path: str = "db/tomic_journal.db",
    ):
        super().__init__("journaling", config, publisher)
        self._command_store = command_store
        self._position_book = position_book
        self._db_path = Path(journal_db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_scan_time: str = "2000-01-01T00:00:00"
        self._journaled_ids: set = set()

    def _get_tick_interval(self) -> float:
        return 5.0  # scan every 5 seconds

    def _setup(self) -> None:
        """Initialize journal database."""
        self._init_db()
        self._load_journaled_ids()
        self.logger.info("Journaling Agent ready, %d existing entries", len(self._journaled_ids))

    def _tick(self) -> None:
        """Scan for new DONE commands and journal them."""
        done_cmds = self._command_store.get_done_since(self._last_scan_time)
        if not done_cmds:
            return

        new_count = 0
        for cmd in done_cmds:
            if cmd.event_id in self._journaled_ids:
                continue  # idempotency: already journaled

            self._journal_trade(cmd)
            self._journaled_ids.add(cmd.event_id)
            new_count += 1

        if new_count > 0:
            # Update scan watermark
            self._last_scan_time = done_cmds[-1].processed_at or self._last_scan_time
            self.logger.info("Journaled %d new trade(s)", new_count)

            # Recalculate rolling metrics
            self._update_performance_metrics()

    def _teardown(self) -> None:
        """Final metric update on shutdown."""
        self._update_performance_metrics()
        self.logger.info("Journaling Agent stopped")

    # -----------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Ensure journal tables exist (via migrate or inline)."""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id        TEXT UNIQUE NOT NULL,
                    correlation_id  TEXT NOT NULL,
                    strategy_id     TEXT NOT NULL,
                    strategy_tag    TEXT NOT NULL,
                    instrument      TEXT NOT NULL,
                    exchange        TEXT DEFAULT 'NSE',
                    direction       TEXT NOT NULL,
                    quantity        INTEGER NOT NULL,
                    entry_price     REAL,
                    entry_reason    TEXT,
                    entry_reason_meta TEXT,
                    exit_price      REAL,
                    pnl             REAL DEFAULT 0,
                    hold_duration_s INTEGER DEFAULT 0,
                    exit_reason     TEXT,
                    regime_phase    TEXT,
                    regime_score    INTEGER,
                    vix_at_entry    REAL,
                    iv_rank         REAL,
                    pcr_at_entry    REAL,
                    max_pain_dist   REAL,
                    zone_freshness  TEXT,
                    sizing_chain    TEXT,
                    kelly_value     REAL,
                    sector_heat_pct REAL,
                    slippage_ticks  REAL DEFAULT 0,
                    latency_ms      REAL DEFAULT 0,
                    fill_rate       REAL DEFAULT 1.0,
                    entry_time      TEXT,
                    exit_time       TEXT,
                    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_journal_strategy ON journal_entries(strategy_tag);
                CREATE INDEX IF NOT EXISTS idx_journal_instrument ON journal_entries(instrument);
                CREATE INDEX IF NOT EXISTS idx_journal_created ON journal_entries(created_at);

                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id                  INTEGER PRIMARY KEY CHECK (id = 1),
                    rolling_expectancy  REAL DEFAULT 0,
                    rolling_win_rate    REAL DEFAULT 0,
                    rolling_avg_win     REAL DEFAULT 0,
                    rolling_avg_loss    REAL DEFAULT 0,
                    total_trades        INTEGER DEFAULT 0,
                    sharpe_ratio        REAL DEFAULT 0,
                    max_drawdown_pct    REAL DEFAULT 0,
                    last_updated        TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
                );
                INSERT OR IGNORE INTO performance_metrics (id) VALUES (1);
            """)
            self._ensure_column(conn, "journal_entries", "entry_reason", "TEXT")
            self._ensure_column(conn, "journal_entries", "entry_reason_meta", "TEXT")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
        """Add column if missing to keep existing DBs forward-compatible."""
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]).lower() for row in rows}
        if column.lower() in existing:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _load_journaled_ids(self) -> None:
        """Load already-journaled event IDs for idempotency check."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT event_id FROM journal_entries").fetchall()
            self._journaled_ids = {row["event_id"] for row in rows}
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Trade journaling
    # -----------------------------------------------------------------------

    def _journal_trade(self, cmd) -> None:
        """Record a single trade to the journal."""
        payload = cmd.payload
        regime = payload.get("regime_snapshot", {})
        sizing = payload.get("sizing_chain", [])
        entry_reason = str(payload.get("entry_reason", "") or payload.get("reason", "") or "").strip()
        entry_reason_meta = payload.get("entry_reason_meta", {})

        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO journal_entries (
                    event_id, correlation_id, strategy_id, strategy_tag,
                    instrument, exchange, direction, quantity,
                    entry_price, entry_reason, entry_reason_meta, regime_phase, regime_score, vix_at_entry,
                    sizing_chain, slippage_ticks, latency_ms,
                    entry_time
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    cmd.event_id,
                    cmd.correlation_id,
                    payload.get("strategy_id", ""),
                    payload.get("strategy_tag", ""),
                    payload.get("instrument", ""),
                    payload.get("exchange", "NSE"),
                    payload.get("direction", ""),
                    int(payload.get("quantity", 0)),
                    float(payload.get("entry_price", 0)),
                    entry_reason,
                    json.dumps(entry_reason_meta or {}),
                    regime.get("phase", ""),
                    int(regime.get("score", 0)),
                    float(regime.get("vix", 0)),
                    json.dumps(sizing),
                    float(payload.get("slippage_ticks", 0)),
                    float(payload.get("latency_ms", 0)),
                    cmd.processed_at or datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            self.logger.debug("Journal entry already exists: %s", cmd.event_id)
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Performance metrics
    # -----------------------------------------------------------------------

    def _update_performance_metrics(self) -> None:
        """Recalculate rolling performance metrics."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT pnl FROM journal_entries WHERE pnl IS NOT NULL"
            ).fetchall()

            if not rows:
                return

            pnls = [row["pnl"] for row in rows]
            total = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            win_rate = len(wins) / total if total > 0 else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0

            # Expectancy: avg_win * win_rate + avg_loss * (1 - win_rate)
            expectancy = avg_win * win_rate + avg_loss * (1 - win_rate)

            # Simple Sharpe (daily PnLs)
            import statistics
            if len(pnls) > 1:
                mean_pnl = statistics.mean(pnls)
                std_pnl = statistics.stdev(pnls)
                sharpe = (mean_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0
            else:
                sharpe = 0

            # Max drawdown
            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            for p in pnls:
                cumulative += p
                peak = max(peak, cumulative)
                dd = (peak - cumulative) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

            conn.execute(
                """
                UPDATE performance_metrics SET
                    rolling_expectancy = ?,
                    rolling_win_rate = ?,
                    rolling_avg_win = ?,
                    rolling_avg_loss = ?,
                    total_trades = ?,
                    sharpe_ratio = ?,
                    max_drawdown_pct = ?,
                    last_updated = strftime('%Y-%m-%dT%H:%M:%f', 'now')
                WHERE id = 1
                """,
                (expectancy, win_rate, avg_win, avg_loss, total, sharpe, max_dd),
            )
            conn.commit()
            self.logger.debug(
                "Metrics updated: %d trades, win_rate=%.1f%%, exp=%.2f",
                total, win_rate * 100, expectancy,
            )
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Queries (for API)
    # -----------------------------------------------------------------------

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent journal entries for dashboard."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM journal_entries ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            results: List[Dict] = []
            for row in rows:
                item = dict(row)
                reason = str(item.get("entry_reason", "") or "").strip()
                if not reason:
                    reason = str(item.get("exit_reason", "") or "").strip()
                item["reason"] = reason
                raw_meta = item.get("entry_reason_meta")
                if isinstance(raw_meta, str) and raw_meta.strip():
                    try:
                        item["entry_reason_meta"] = json.loads(raw_meta)
                    except json.JSONDecodeError:
                        # Keep raw string when legacy/broken payload exists.
                        item["entry_reason_meta"] = raw_meta
                results.append(item)
            return results
        finally:
            conn.close()

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get rolling performance metrics."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM performance_metrics WHERE id = 1"
            ).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def get_strategy_breakdown(self) -> List[Dict]:
        """P&L breakdown by strategy type."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT
                    strategy_tag,
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    AVG(slippage_ticks) as avg_slippage
                FROM journal_entries
                GROUP BY strategy_tag
                ORDER BY total_pnl DESC
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
