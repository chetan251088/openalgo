"""
Market Pulse — Trade Journal & Signal Tracking (Phase 7).

Auto-logs HIGH-conviction signals plus their subsequent outcomes.
Uses DuckDB for lightweight, serverless persistence.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = os.getenv(
    "MARKET_PULSE_JOURNAL_DB",
    os.path.join(os.path.dirname(__file__), "..", "db", "market_pulse_journal.duckdb"),
)

_db_initialized = False


def _get_db():
    """Get DuckDB connection, creating schema if needed."""
    global _db_initialized
    import duckdb

    db_dir = os.path.dirname(os.path.abspath(_DB_PATH))
    os.makedirs(db_dir, exist_ok=True)

    conn = duckdb.connect(_DB_PATH)

    if not _db_initialized:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                signal_id VARCHAR PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                symbol VARCHAR NOT NULL,
                signal_type VARCHAR,     -- BUY, SELL, HOLD, AVOID
                conviction VARCHAR,      -- HIGH, MED, LOW
                entry DOUBLE,
                stop_loss DOUBLE,
                target DOUBLE,
                risk_reward DOUBLE,
                ltp DOUBLE,
                sector VARCHAR,
                reason TEXT,
                regime VARCHAR,
                quality_score INTEGER,
                execution_score INTEGER,
                mode VARCHAR,
                market_context TEXT       -- JSON blob of full context
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                outcome_id VARCHAR PRIMARY KEY,
                signal_id VARCHAR NOT NULL,
                outcome_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                exit_price DOUBLE,
                pnl_pct DOUBLE,
                hit_target BOOLEAN,
                hit_sl BOOLEAN,
                bars_held INTEGER,
                notes TEXT,
                FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
            )
        """)
        _db_initialized = True

    return conn


def log_signal(
    symbol: str,
    signal_type: str,
    conviction: str,
    entry: float | None = None,
    stop_loss: float | None = None,
    target: float | None = None,
    risk_reward: float | None = None,
    ltp: float | None = None,
    sector: str | None = None,
    reason: str | None = None,
    regime: str | None = None,
    quality_score: int | None = None,
    execution_score: int | None = None,
    mode: str = "swing",
    market_context: dict | None = None,
) -> str:
    """Log a signal to the journal. Returns signal_id."""
    signal_id = str(uuid.uuid4())[:12]

    try:
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO signals (
                signal_id, timestamp, symbol, signal_type, conviction,
                entry, stop_loss, target, risk_reward, ltp,
                sector, reason, regime, quality_score, execution_score,
                mode, market_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                signal_id,
                datetime.now(),
                symbol,
                signal_type,
                conviction,
                entry,
                stop_loss,
                target,
                risk_reward,
                ltp,
                sector,
                reason,
                regime,
                quality_score,
                execution_score,
                mode,
                str(market_context) if market_context else None,
            ],
        )
        conn.close()
        logger.info("Signal logged: %s %s %s @ %s", signal_id, conviction, signal_type, symbol)
        return signal_id
    except Exception as e:
        logger.exception("Failed to log signal: %s", e)
        return signal_id


def log_outcome(
    signal_id: str,
    exit_price: float | None = None,
    pnl_pct: float | None = None,
    hit_target: bool = False,
    hit_sl: bool = False,
    bars_held: int = 0,
    notes: str | None = None,
) -> bool:
    """Log an outcome for a signal."""
    try:
        conn = _get_db()
        outcome_id = str(uuid.uuid4())[:12]
        conn.execute(
            """
            INSERT INTO outcomes (
                outcome_id, signal_id, outcome_date,
                exit_price, pnl_pct, hit_target, hit_sl, bars_held, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                outcome_id,
                signal_id,
                datetime.now(),
                exit_price,
                pnl_pct,
                hit_target,
                hit_sl,
                bars_held,
                notes,
            ],
        )
        conn.close()
        return True
    except Exception as e:
        logger.exception("Failed to log outcome: %s", e)
        return False


def auto_log_ideas(
    equity_ideas: list[dict[str, Any]],
    regime: str = "unknown",
    quality_score: int = 50,
    execution_score: int = 50,
    mode: str = "swing",
) -> int:
    """Auto-log HIGH conviction ideas from the screener.
    Returns count of new signals logged.
    """
    logged = 0
    for idea in equity_ideas:
        if idea.get("conviction") != "HIGH":
            continue
        if idea.get("signal") not in ("BUY", "SELL"):
            continue

        # Dedup: check if we already logged this symbol today
        try:
            conn = _get_db()
            existing = conn.execute(
                """
                SELECT COUNT(*) FROM signals
                WHERE symbol = ? AND signal_type = ?
                AND CAST(timestamp AS DATE) = CURRENT_DATE
                """,
                [idea["symbol"], idea["signal"]],
            ).fetchone()
            conn.close()

            if existing and existing[0] > 0:
                continue  # Already logged today
        except Exception:
            pass

        log_signal(
            symbol=idea["symbol"],
            signal_type=idea["signal"],
            conviction=idea["conviction"],
            entry=idea.get("entry"),
            stop_loss=idea.get("stop_loss"),
            target=idea.get("target"),
            risk_reward=idea.get("risk_reward"),
            ltp=idea.get("ltp"),
            sector=idea.get("sector"),
            reason=idea.get("reason"),
            regime=regime,
            quality_score=quality_score,
            execution_score=execution_score,
            mode=mode,
        )
        logged += 1

    return logged


def get_signal_history(
    days: int = 30,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get recent signal history with outcomes."""
    try:
        conn = _get_db()
        rows = conn.execute(
            """
            SELECT
                s.signal_id, s.timestamp, s.symbol, s.signal_type,
                s.conviction, s.entry, s.stop_loss, s.target,
                s.risk_reward, s.ltp, s.sector, s.reason,
                s.regime, s.quality_score, s.mode,
                o.exit_price, o.pnl_pct, o.hit_target, o.hit_sl, o.bars_held
            FROM signals s
            LEFT JOIN outcomes o ON s.signal_id = o.signal_id
            WHERE s.timestamp >= CURRENT_DATE - (? * INTERVAL '1 DAY')
            ORDER BY s.timestamp DESC
            LIMIT ?
            """,
            [days, limit],
        ).fetchall()
        conn.close()

        columns = [
            "signal_id", "timestamp", "symbol", "signal_type",
            "conviction", "entry", "stop_loss", "target",
            "risk_reward", "ltp", "sector", "reason",
            "regime", "quality_score", "mode",
            "exit_price", "pnl_pct", "hit_target", "hit_sl", "bars_held",
        ]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.exception("Failed to get signal history: %s", e)
        return []


def get_win_rate_stats(days: int = 30) -> dict[str, Any]:
    """Compute win-rate statistics by regime, sector, etc."""
    try:
        conn = _get_db()

        # Overall stats
        overall = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(o.outcome_id) as with_outcome,
                SUM(CASE WHEN o.hit_target THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN o.hit_sl THEN 1 ELSE 0 END) as losses,
                AVG(o.pnl_pct) as avg_pnl
            FROM signals s
            LEFT JOIN outcomes o ON s.signal_id = o.signal_id
            WHERE s.timestamp >= CURRENT_DATE - (? * INTERVAL '1 DAY')
            AND s.conviction = 'HIGH'
            """,
            [days],
        ).fetchone()

        # By regime
        by_regime = conn.execute(
            """
            SELECT
                s.regime,
                COUNT(*) as total,
                SUM(CASE WHEN o.hit_target THEN 1 ELSE 0 END) as wins,
                AVG(o.pnl_pct) as avg_pnl
            FROM signals s
            LEFT JOIN outcomes o ON s.signal_id = o.signal_id
            WHERE s.timestamp >= CURRENT_DATE - (? * INTERVAL '1 DAY')
            AND s.conviction = 'HIGH'
            GROUP BY s.regime
            """,
            [days],
        ).fetchall()

        # By sector
        by_sector = conn.execute(
            """
            SELECT
                s.sector,
                COUNT(*) as total,
                SUM(CASE WHEN o.hit_target THEN 1 ELSE 0 END) as wins,
                AVG(o.pnl_pct) as avg_pnl
            FROM signals s
            LEFT JOIN outcomes o ON s.signal_id = o.signal_id
            WHERE s.timestamp >= CURRENT_DATE - (? * INTERVAL '1 DAY')
            AND s.conviction = 'HIGH'
            GROUP BY s.sector
            """,
            [days],
        ).fetchall()

        conn.close()

        total = overall[0] if overall else 0
        with_outcome = overall[1] if overall else 0
        wins = overall[2] if overall else 0
        losses = overall[3] if overall else 0

        return {
            "period_days": days,
            "total_signals": total,
            "with_outcome": with_outcome,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / max(with_outcome, 1)) * 100, 1),
            "avg_pnl": round(overall[4], 2) if overall and overall[4] else None,
            "by_regime": [
                {
                    "regime": r[0],
                    "total": r[1],
                    "wins": r[2],
                    "win_rate": round((r[2] / max(r[1], 1)) * 100, 1),
                    "avg_pnl": round(r[3], 2) if r[3] else None,
                }
                for r in by_regime
            ],
            "by_sector": [
                {
                    "sector": s[0],
                    "total": s[1],
                    "wins": s[2],
                    "win_rate": round((s[2] / max(s[1], 1)) * 100, 1),
                    "avg_pnl": round(s[3], 2) if s[3] else None,
                }
                for s in by_sector
            ],
        }
    except Exception as e:
        logger.exception("Failed to compute win rate stats: %s", e)
        return {"error": str(e)}
