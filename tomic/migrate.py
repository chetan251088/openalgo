"""
TOMIC Schema Migration Runner
===============================
Forward-only numbered SQL migrations for all TOMIC SQLite databases.

Usage:
    from tomic.migrate import run_migrations
    run_migrations("db/tomic_commands.db")
    run_migrations("db/tomic_positions.db")
    run_migrations("db/tomic_journal.db")
    run_migrations("db/tomic_audit.db")
    run_migrations("db/tomic_metrics.db")

Migration files live in tomic/migrations/ as numbered .sql files:
    001_create_commands.sql
    002_create_positions.sql
    003_create_journal.sql
    004_create_audit.sql
    005_create_metrics.sql

Each migration runs in a transaction. On failure, the migration is
rolled back and the process exits with an error.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create schema_version table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            filename    TEXT NOT NULL,
            applied_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
        )
    """)
    conn.commit()


def _get_applied_versions(conn: sqlite3.Connection) -> set:
    """Get set of already-applied migration version numbers."""
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {row[0] for row in rows}


def _get_migration_files() -> List[Path]:
    """Get sorted list of migration SQL files."""
    if not MIGRATIONS_DIR.exists():
        logger.warning("Migrations directory not found: %s", MIGRATIONS_DIR)
        return []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


def _extract_version(filepath: Path) -> int:
    """Extract version number from filename like '001_create_commands.sql'."""
    name = filepath.stem  # e.g. "001_create_commands"
    parts = name.split("_", 1)
    try:
        return int(parts[0])
    except (ValueError, IndexError):
        raise ValueError(f"Invalid migration filename: {filepath.name}. Expected format: NNN_description.sql")


def run_migrations(db_path: str, target_db: Optional[str] = None) -> int:
    """
    Apply pending migrations to a database.

    Args:
        db_path: Path to SQLite database file
        target_db: If set, only apply migrations whose filename contains this string
                   (e.g., "commands" for tomic_commands.db)

    Returns:
        Number of migrations applied
    """
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        _ensure_schema_version_table(conn)
        applied = _get_applied_versions(conn)
        migration_files = _get_migration_files()

        applied_count = 0
        for filepath in migration_files:
            version = _extract_version(filepath)

            # Skip if already applied
            if version in applied:
                continue

            # Filter by target_db if specified
            if target_db and target_db not in filepath.stem:
                continue

            # Read and execute migration
            sql = filepath.read_text(encoding="utf-8")
            logger.info(
                "Applying migration %03d (%s) to %s",
                version, filepath.name, db_file.name,
            )

            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_version (version, filename) VALUES (?, ?)",
                    (version, filepath.name),
                )
                conn.commit()
                applied_count += 1
                logger.info("Migration %03d applied successfully", version)
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(
                    "Migration %03d FAILED: %s — rolling back",
                    version, str(e),
                )
                raise RuntimeError(
                    f"Migration {filepath.name} failed: {e}"
                ) from e

        if applied_count > 0:
            logger.info(
                "Applied %d migration(s) to %s", applied_count, db_file.name
            )
        else:
            logger.debug("No pending migrations for %s", db_file.name)

        return applied_count

    finally:
        conn.close()


def run_all_tomic_migrations() -> Dict[str, int]:
    """
    Run migrations for all TOMIC databases.
    Returns dict of {db_name: migrations_applied}.
    """
    databases = {
        "db/tomic_commands.db": None,   # all migrations apply
        "db/tomic_positions.db": None,
        "db/tomic_journal.db": None,
        "db/tomic_audit.db": None,
        "db/tomic_metrics.db": None,
    }

    results = {}
    for db_path in databases:
        try:
            count = run_migrations(db_path)
            results[db_path] = count
        except RuntimeError as e:
            logger.error("Failed to migrate %s: %s", db_path, e)
            results[db_path] = -1

    return results


# Allow dict return type hint
from typing import Dict
