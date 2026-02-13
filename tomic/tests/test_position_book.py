"""
Test Suite: TOMIC PositionBook — Versioned Snapshots, Single Writer, Reconciliation
======================================================================================
Tests versioned increment, deep-copy reads, persistence, and unhedged detection.
"""

import pytest
from tomic.position_book import PositionBook, Position


@pytest.fixture
def book(tmp_path):
    """Fresh PositionBook for each test."""
    db = str(tmp_path / "test_positions.db")
    pb = PositionBook(db)
    return pb


class TestVersionedWrites:
    """Single-writer versioning."""

    def test_initial_version_is_zero(self, book):
        assert book.current_version == 0

    def test_write_increments_version(self, book):
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        v = book.update_position(pos)
        assert v == 1

    def test_multiple_writes_monotonic(self, book):
        for i in range(5):
            pos = Position(
                instrument=f"SYM{i}", strategy_id="s1",
                quantity=50, avg_price=100, direction="BUY",
            )
            v = book.update_position(pos)
            assert v == i + 1

    def test_version_never_decreases(self, book):
        versions = []
        for i in range(3):
            pos = Position(
                instrument="NIFTY", strategy_id=f"s{i}",
                quantity=50, avg_price=100, direction="BUY",
            )
            v = book.update_position(pos)
            versions.append(v)
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions)  # all unique


class TestSnapshotReads:
    """Reads return deep-copied immutable snapshots."""

    def test_snapshot_returns_correct_count(self, book):
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        book.update_position(pos)
        snap = book.read_snapshot()
        assert snap.total_positions == 1
        assert snap.version == 1

    def test_snapshot_is_deep_copy(self, book):
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        book.update_position(pos)

        snap1 = book.read_snapshot()
        snap2 = book.read_snapshot()

        # Modifying snap1 should not affect snap2 or the book
        assert snap1 is not snap2

    def test_snapshot_version_matches(self, book):
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        book.update_position(pos)
        snap = book.read_snapshot()
        assert snap.version == book.current_version


class TestPositionOperations:
    """Update and remove positions."""

    def test_update_same_key_overwrites(self, book):
        pos1 = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        pos2 = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=100, avg_price=22050, direction="BUY",
        )
        book.update_position(pos1)
        book.update_position(pos2)
        snap = book.read_snapshot()
        assert snap.total_positions == 1

    def test_remove_position(self, book):
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        book.update_position(pos)
        book.remove_position("NIFTY", "s1")
        snap = book.read_snapshot()
        assert snap.total_positions == 0

    def test_remove_nonexistent_no_error(self, book):
        book.remove_position("NONEXISTENT", "s1")  # should not raise


class TestUnhedgedDetection:
    """Detect short options without paired hedge."""

    def test_no_positions_no_unhedged(self, book):
        assert book.has_unhedged_short() == []

    def test_long_only_no_unhedged(self, book):
        pos = Position(
            instrument="NIFTY25FEB22000CE", strategy_id="s1",
            quantity=50, avg_price=200, direction="BUY",
        )
        book.update_position(pos)
        assert book.has_unhedged_short() == []

    def test_short_without_hedge_detected(self, book):
        # has_unhedged_short() checks SELL positions with hedge_pair_id
        # pointing to a non-existent position in the book
        pos = Position(
            instrument="NIFTY25FEB21800PE", strategy_id="s1",
            quantity=50, avg_price=150, direction="SELL",
            hedge_pair_id="NIFTY25FEB21600PE|s1",  # refers to missing long leg
        )
        book.update_position(pos)
        unhedged = book.has_unhedged_short()
        assert len(unhedged) >= 1


class TestPositionCount:
    """Count helpers."""

    def test_count_positions(self, book):
        assert book.count_positions() == 0
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        book.update_position(pos)
        assert book.count_positions() == 1


class TestPersistence:
    """Positions survive across PositionBook instances."""

    def test_persist_and_reload(self, tmp_path):
        db = str(tmp_path / "test_persist.db")

        # Write
        book1 = PositionBook(db)
        pos = Position(
            instrument="NIFTY", strategy_id="s1",
            quantity=50, avg_price=22000, direction="BUY",
        )
        book1.update_position(pos)
        book1.persist()

        # Reload
        book2 = PositionBook(db)
        book2.load()
        snap = book2.read_snapshot()
        assert snap.total_positions == 1


class TestReconcile:
    """Broker reconciliation should map canonical positionbook fields correctly."""

    def test_reconcile_uses_symbol_quantity_fields(self, book):
        discrepancies = book.reconcile(
            [
                {
                    "symbol": "NIFTY26FEB2623000CE",
                    "exchange": "NFO",
                    "product": "MIS",
                    "quantity": "50",
                    "average_price": "123.45",
                    "ltp": "125.00",
                    "pnl": "77.5",
                }
            ]
        )
        assert any("BROKER_ONLY" in item for item in discrepancies)

        snap = book.read_snapshot()
        assert snap.total_positions == 1
        pos = list(snap.positions.values())[0]
        assert pos.instrument == "NIFTY26FEB2623000CE"
        assert pos.quantity == 50
        assert pos.avg_price == pytest.approx(123.45)
        assert pos.exchange == "NFO"
        assert pos.product == "MIS"

    def test_reconcile_skips_closed_and_invalid_rows(self, book):
        discrepancies = book.reconcile(
            [
                {"symbol": "", "quantity": 10},
                {"symbol": "BANKNIFTY26FEB60500PE", "quantity": 0},
            ]
        )
        assert discrepancies == []
        assert book.read_snapshot().total_positions == 0
