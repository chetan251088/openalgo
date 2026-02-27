# test/test_tomic_circuit_breakers.py
import pytest
from tomic.circuit_breakers import CircuitBreakerEngine
from tomic.config import CircuitBreakerThresholds


@pytest.fixture
def engine():
    th = CircuitBreakerThresholds()
    return CircuitBreakerEngine(thresholds=th, capital=1_000_000)


def test_status_summary_has_breakers_key(engine):
    summary = engine.get_status_summary()
    assert "breakers" in summary
    assert "capital" in summary


def test_status_summary_each_breaker_has_tripped(engine):
    summary = engine.get_status_summary()
    for name in ["DAILY_MAX_LOSS", "ORDER_RATE", "GROSS_NOTIONAL",
                 "PER_UNDERLYING", "UNHEDGED_EXPOSURE"]:
        assert name in summary["breakers"], f"Missing breaker: {name}"
        assert "tripped" in summary["breakers"][name]


def test_order_rate_current_updates(engine):
    engine.record_order()
    engine.record_order()
    summary = engine.get_status_summary()
    assert summary["breakers"]["ORDER_RATE"]["current"] == 2


def test_not_tripped_when_no_orders(engine):
    summary = engine.get_status_summary()
    assert summary["breakers"]["ORDER_RATE"]["tripped"] is False
