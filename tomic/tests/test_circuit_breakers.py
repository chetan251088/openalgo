"""
Test Suite: TOMIC Circuit Breakers — All 5 System-Level Hard Stops
====================================================================
Tests daily max loss, order rate, gross notional, per-underlying cap,
and unhedged exposure breakers.
"""

import time
import pytest
from tomic.circuit_breakers import CircuitBreakerEngine, BreakerType


@pytest.fixture
def cb():
    """Circuit breaker engine with 1M capital."""
    return CircuitBreakerEngine(capital=1_000_000)


class TestDailyMaxLoss:
    """Breaker 1: Daily P&L < -6% of capital → kill switch."""

    def test_within_threshold_passes(self, cb):
        status = cb.check_all(daily_pnl=-50_000)  # -5%
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.DAILY_MAX_LOSS not in trips

    def test_over_threshold_trips(self, cb):
        status = cb.check_all(daily_pnl=-70_000)  # -7%
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.DAILY_MAX_LOSS in trips

    def test_positive_pnl_passes(self, cb):
        status = cb.check_all(daily_pnl=50_000)
        assert status.all_clear

    def test_kill_switch_flag(self, cb):
        status = cb.check_all(daily_pnl=-70_000)
        for r in status.tripped_breakers:
            if r.breaker == BreakerType.DAILY_MAX_LOSS:
                assert r.kill_switch is True


class TestOrderRate:
    """Breaker 2: > 30 orders/minute → throttle."""

    def test_below_rate_passes(self, cb):
        for _ in range(20):
            cb.record_order()
        status = cb.check_all(daily_pnl=0)
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.ORDER_RATE not in trips

    def test_above_rate_trips(self, cb):
        for _ in range(35):
            cb.record_order()
        status = cb.check_all(daily_pnl=0)
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.ORDER_RATE in trips


class TestGrossNotional:
    """Breaker 3: Gross notional > 5× capital → reject."""

    def test_within_limit_passes(self, cb):
        status = cb.check_all(daily_pnl=0, gross_notional=4_000_000)
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.GROSS_NOTIONAL not in trips

    def test_over_limit_trips(self, cb):
        status = cb.check_all(daily_pnl=0, gross_notional=6_000_000)
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.GROSS_NOTIONAL in trips


class TestUnderlyingConcentration:
    """Breaker 4: Single underlying > 30% used margin → reject."""

    def test_within_cap_passes(self, cb):
        status = cb.check_all(
            daily_pnl=0,
            underlying="NIFTY",
            underlying_margin_pct=0.20,
        )
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.PER_UNDERLYING not in trips

    def test_over_cap_trips(self, cb):
        status = cb.check_all(
            daily_pnl=0,
            underlying="NIFTY",
            underlying_margin_pct=0.40,
        )
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.PER_UNDERLYING in trips


class TestUnhedgedExposure:
    """Breaker 5: Any short unhedged > 5s → force close."""

    def test_no_unhedged_passes(self, cb):
        status = cb.check_all(daily_pnl=0, unhedged_keys=[])
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.UNHEDGED_EXPOSURE not in trips

    def test_fresh_unhedged_not_tripped(self, cb):
        """Newly detected unhedged key should NOT trip immediately (< 5s)."""
        status = cb.check_all(daily_pnl=0, unhedged_keys=["NIFTY|s1"])
        trips = [b.breaker for b in status.tripped_breakers]
        # First check starts the timer; it shouldn't trip yet
        assert BreakerType.UNHEDGED_EXPOSURE not in trips


class TestAllClear:
    """Combined check returns all_clear when nothing trips."""

    def test_clean_state_all_clear(self, cb):
        status = cb.check_all(daily_pnl=0)
        assert status.all_clear
        assert len(status.tripped_breakers) == 0

    def test_multiple_breakers_combine(self, cb):
        for _ in range(35):
            cb.record_order()

        status = cb.check_all(
            daily_pnl=-80_000,
            gross_notional=6_000_000,
        )
        assert not status.all_clear
        trips = [b.breaker for b in status.tripped_breakers]
        assert BreakerType.DAILY_MAX_LOSS in trips
        assert BreakerType.ORDER_RATE in trips
        assert BreakerType.GROSS_NOTIONAL in trips


class TestStatusSummary:
    """Diagnostic summary for observability."""

    def test_summary_is_dict(self, cb):
        summary = cb.get_status_summary()
        assert isinstance(summary, dict)

    def test_summary_has_capital(self, cb):
        summary = cb.get_status_summary()
        assert "capital" in summary
        assert summary["capital"] == 1_000_000

    def test_summary_has_order_count(self, cb):
        cb.record_order()
        summary = cb.get_status_summary()
        assert summary["orders_last_minute"] >= 1
