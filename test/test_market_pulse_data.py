import os
import sys
import types

import pandas as pd

os.environ.setdefault("API_KEY_PEPPER", "0123456789abcdef0123456789abcdef")

import services.market_pulse_data as market_pulse_data

from services.market_pulse_data import (
    _build_intraday_trade_context,
    _compute_option_max_pain_from_chain,
    _fetch_history,
    _parse_nse_delivery_csv,
    _summarize_oi_wall,
    _summarize_delivery_history,
    _with_computed_change,
    compute_constituent_breadth_snapshot,
    fetch_market_data,
)


def _hist(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


class TestConstituentBreadthSnapshot:
    def test_200d_breadth_uses_eligible_denominator_only(self):
        snapshot = compute_constituent_breadth_snapshot(
            {
                "LEADER": {"history": _hist([100 + i for i in range(210)])},
                "MIDCAP": {"history": _hist([200 + i for i in range(120)])},
                "LAGGARD": {"history": _hist([320 - i for i in range(210)])},
            }
        )

        ma = snapshot["moving_averages"]
        ad = snapshot["advance_decline"]

        assert ma["eligible_50d"] == 3
        assert ma["above_50d"] == 2
        assert ma["pct_above_50d"] == 66.7

        assert ma["eligible_200d"] == 2
        assert ma["above_200d"] == 1
        assert ma["pct_above_200d"] == 50.0

        assert ad["advances"] == 2
        assert ad["declines"] == 1
        assert ad["ad_ratio"] == 2.0

    def test_52w_highs_cannot_exceed_names_above_200d_when_derived_consistently(self):
        snapshot = compute_constituent_breadth_snapshot(
            {
                "STRONG": {"history": _hist([100 + i for i in range(260)])},
                "WEAK": {"history": _hist([400 - i for i in range(260)])},
                "SIDEWAYS": {"history": _hist([200 + ((i % 7) - 3) for i in range(260)])},
            }
        )

        ma = snapshot["moving_averages"]
        annual = snapshot["annual_extremes"]

        assert annual["eligible_52w"] == 3
        assert annual["highs_52w"] <= ma["above_200d"]
        assert annual["lows_52w"] <= (annual["eligible_52w"] - ma["above_200d"])


class TestQuoteNormalization:
    def test_with_computed_change_derives_change_pct_from_ltp_and_prev_close(self):
        normalized = _with_computed_change({"ltp": 104.5, "prev_close": 100.0})

        assert normalized is not None
        assert normalized["change"] == 4.5
        assert normalized["change_pct"] == 4.5


class TestHistorySourceFallback:
    def test_fetch_history_prefers_db_in_auto_mode(self, monkeypatch):
        calls: list[str] = []

        def fake_get_history(**kwargs):
            calls.append(kwargs["source"])
            return True, {"status": "success", "data": [{"close": 100.0}]}, 200

        monkeypatch.setattr(market_pulse_data, "_HISTORY_SOURCE_MODE", "auto")
        monkeypatch.setattr(market_pulse_data, "_history_source_cache", {})
        monkeypatch.setitem(
            sys.modules,
            "services.history_service",
            types.SimpleNamespace(get_history=fake_get_history),
        )

        df = _fetch_history("NIFTY", "NSE_INDEX", days=5)

        assert df is not None
        assert calls == ["db"]

    def test_fetch_history_falls_back_to_api_when_db_has_no_data(self, monkeypatch):
        calls: list[str] = []

        def fake_get_history(**kwargs):
            calls.append(kwargs["source"])
            if kwargs["source"] == "db":
                return (
                    False,
                    {"status": "error", "message": "No data found in local database"},
                    404,
                )
            return True, {"status": "success", "data": [{"close": 100.0}]}, 200

        monkeypatch.setattr(market_pulse_data, "_HISTORY_SOURCE_MODE", "auto")
        monkeypatch.setattr(market_pulse_data, "_history_source_cache", {})
        monkeypatch.setattr(market_pulse_data, "_get_market_pulse_api_key", lambda: "test-key")
        monkeypatch.setitem(
            sys.modules,
            "services.history_service",
            types.SimpleNamespace(get_history=fake_get_history),
        )

        df = _fetch_history("BANKNIFTY", "NSE_INDEX", days=5)

        assert df is not None
        assert calls == ["db", "api"]

    def test_fetch_history_falls_back_to_api_when_db_snapshot_is_stale(self, monkeypatch):
        calls: list[str] = []

        stale_db_row = {
            "timestamp": 1700000000,
            "open": 25906.7,
            "high": 25906.7,
            "low": 25752.4,
            "close": 25807.2,
            "volume": 0,
            "oi": 0,
        }
        fresh_api_row = {
            "timestamp": 1774310400,
            "open": 22878.45,
            "high": 23057.3,
            "low": 22624.2,
            "close": 22958.4,
            "volume": 0,
            "oi": 0,
        }

        def fake_get_history(**kwargs):
            calls.append(kwargs["source"])
            if kwargs["source"] == "db":
                return True, {"status": "success", "data": [stale_db_row]}, 200
            return True, {"status": "success", "data": [fresh_api_row]}, 200

        monkeypatch.setattr(market_pulse_data, "_HISTORY_SOURCE_MODE", "auto")
        monkeypatch.setattr(market_pulse_data, "_history_source_cache", {})
        monkeypatch.setattr(market_pulse_data, "_get_market_pulse_api_key", lambda: "test-key")
        monkeypatch.setitem(
            sys.modules,
            "services.history_service",
            types.SimpleNamespace(get_history=fake_get_history),
        )

        df = _fetch_history("NIFTY", "NSE_INDEX", days=5)

        assert df is not None
        assert calls == ["db", "api"]
        assert float(df["close"].iloc[-1]) == 22958.4


class TestOptionsContextHelpers:
    def test_compute_option_max_pain_from_chain(self):
        chain = [
            {"strike": 22000, "ce_oi": 1000, "pe_oi": 1200},
            {"strike": 22200, "ce_oi": 1500, "pe_oi": 900},
            {"strike": 22400, "ce_oi": 900, "pe_oi": 1800},
        ]

        max_pain = _compute_option_max_pain_from_chain(chain)

        assert max_pain == 22200.0

    def test_summarize_oi_wall_prefers_relevant_side_of_spot(self):
        chain = [
            {"strike": 22800, "ce_oi": 4000, "pe_oi": 1000},
            {"strike": 23000, "ce_oi": 8000, "pe_oi": 1200},
            {"strike": 22600, "ce_oi": 900, "pe_oi": 7000},
            {"strike": 22400, "ce_oi": 600, "pe_oi": 9500},
        ]

        call_wall = _summarize_oi_wall(chain, side="call", spot_price=22912.4)
        put_wall = _summarize_oi_wall(chain, side="put", spot_price=22912.4)

        assert call_wall == {"strike": 23000, "oi": 8000, "distance_pct": 0.38}
        assert put_wall == {"strike": 22400, "oi": 9500, "distance_pct": -2.24}


class TestDeliveryArchiveHelpers:
    def test_parse_nse_delivery_csv_extracts_eq_rows(self):
        csv_text = """SYMBOL, SERIES, DATE1, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER
HDFCBANK, EQ, 24-Mar-2026, 61269986, 34473827, 56.27
HDFCBANK, BE, 24-Mar-2026, 1, 1, 100.00
RELIANCE, EQ, 24-Mar-2026, 18620893, 9406770, 50.52
"""

        parsed = _parse_nse_delivery_csv(csv_text, {"HDFCBANK", "RELIANCE"})

        assert parsed == {
            "HDFCBANK": {
                "symbol": "HDFCBANK",
                "date": "2026-03-24",
                "delivery_pct": 56.27,
                "delivery_qty": 34473827.0,
                "traded_qty": 61269986.0,
            },
            "RELIANCE": {
                "symbol": "RELIANCE",
                "date": "2026-03-24",
                "delivery_pct": 50.52,
                "delivery_qty": 9406770.0,
                "traded_qty": 18620893.0,
            },
        }

    def test_summarize_delivery_history_compares_latest_against_prior_10_sessions(self):
        history = {
            "HDFCBANK": [
                {"date": "2026-03-24", "delivery_pct": 56.27, "delivery_qty": 34473827.0, "traded_qty": 61269986.0},
                {"date": "2026-03-23", "delivery_pct": 51.00, "delivery_qty": 30000000.0, "traded_qty": 59000000.0},
                {"date": "2026-03-21", "delivery_pct": 49.00, "delivery_qty": 29500000.0, "traded_qty": 58000000.0},
                {"date": "2026-03-20", "delivery_pct": 50.00, "delivery_qty": 29700000.0, "traded_qty": 58100000.0},
            ]
        }

        summary = _summarize_delivery_history(history)

        assert summary["HDFCBANK"]["delivery_date"] == "2026-03-24"
        assert summary["HDFCBANK"]["delivery_pct"] == 56.27
        assert summary["HDFCBANK"]["avg_delivery_pct_10d"] == 50.0
        assert summary["HDFCBANK"]["delivery_vs_10d_avg"] == 1.13


class TestIntradayTradeContext:
    def test_build_intraday_trade_context_computes_vwap_and_time_adjusted_rvol(self):
        frame = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2026-03-23 09:20:00",
                        "2026-03-23 09:25:00",
                        "2026-03-23 09:30:00",
                        "2026-03-24 09:20:00",
                        "2026-03-24 09:25:00",
                        "2026-03-24 09:30:00",
                    ]
                ),
                "high": [100.0, 101.0, 102.0, 101.0, 103.0, 105.0],
                "low": [99.0, 100.0, 101.0, 100.0, 102.0, 104.0],
                "close": [99.5, 100.5, 101.5, 100.5, 102.5, 104.5],
                "volume": [1000, 1200, 1400, 1500, 1800, 2100],
            }
        )

        context = _build_intraday_trade_context(frame, current_price=105.0)

        assert context is not None
        assert context["session_date"] == "2026-03-24"
        assert context["bars"] == 3
        assert context["above_vwap"] is True
        assert context["below_vwap"] is False
        assert context["session_volume"] == 5400
        assert context["avg_cumulative_volume"] == 3600
        assert context["rvol"] == 1.5
        assert context["vwap"] == 102.72
        assert context["vwap_distance_pct"] == 2.22


class TestDayModePerformanceGuards:
    def test_fetch_market_data_includes_sensex_history(self, monkeypatch):
        captured_history_jobs = []

        def fake_fetch_history_batch(jobs):
            captured_history_jobs.extend(jobs)
            frame = pd.DataFrame(
                {
                    "close": [100.0 + i for i in range(30)],
                    "high": [100.5 + i for i in range(30)],
                    "low": [99.5 + i for i in range(30)],
                    "volume": [100000 + i * 1000 for i in range(30)],
                }
            )
            return {key: frame.copy() for key, *_ in jobs}

        def fake_refresh_intraday_market_fields(result, include_constituents=False):
            result["ticker"] = {
                "NIFTY": {"ltp": 22912.4, "prev_close": 22890.0, "change_pct": 0.1},
                "SENSEX": {"ltp": 75412.3, "prev_close": 75100.0, "change_pct": 0.42},
                "BANKNIFTY": {"ltp": 52363.0, "prev_close": 52100.0, "change_pct": 0.5},
                "INDIAVIX": {"ltp": 14.0, "prev_close": 15.0, "change_pct": -6.5},
            }
            result["sectors"] = {}
            result["pcr"] = 0.92
            result["updated_at"] = 1234567890.0

        monkeypatch.setattr(market_pulse_data, "_cache", {})
        monkeypatch.setattr(market_pulse_data, "_cache_ts", 0.0)
        monkeypatch.setattr(market_pulse_data, "_get_constituents", lambda: [])
        monkeypatch.setattr(market_pulse_data, "_fetch_delivery_snapshot", lambda symbols, force_refresh=False: {})
        monkeypatch.setattr(market_pulse_data, "_fetch_history_batch", fake_fetch_history_batch)
        monkeypatch.setattr(
            market_pulse_data,
            "_refresh_intraday_market_fields",
            fake_refresh_intraday_market_fields,
        )
        monkeypatch.setattr(market_pulse_data, "_fetch_institutional_flows", lambda force_refresh=False: {})
        monkeypatch.setattr(market_pulse_data, "_fetch_options_context", lambda force_refresh=False: {})
        monkeypatch.setattr(market_pulse_data, "_get_events", lambda: [])

        result = fetch_market_data(mode="swing", force_refresh=True)

        assert result["sensex_history"] is not None
        assert ("sensex_history", "SENSEX", "BSE_INDEX", market_pulse_data._SENSEX_HISTORY_CALENDAR_DAYS) in captured_history_jobs

    def test_fetch_market_data_skips_delivery_snapshot_in_day_mode(self, monkeypatch):
        calls = {"delivery": 0}

        def fake_fetch_delivery_snapshot(symbols, force_refresh=False):
            calls["delivery"] += 1
            return {
                "HDFCBANK": {
                    "delivery_pct": 56.27,
                }
            }

        def fake_fetch_history_batch(jobs):
            frame = pd.DataFrame(
                {
                    "close": [100.0 + i for i in range(30)],
                    "high": [100.5 + i for i in range(30)],
                    "low": [99.5 + i for i in range(30)],
                    "volume": [100000 + i * 1000 for i in range(30)],
                }
            )
            return {key: frame.copy() for key, *_ in jobs}

        def fake_refresh_intraday_market_fields(result, include_constituents=False):
            result["ticker"] = {
                "NIFTY": {"ltp": 22912.4, "prev_close": 22890.0, "change_pct": 1.2},
                "BANKNIFTY": {"ltp": 52363.0, "prev_close": 52100.0, "change_pct": 0.5},
                "INDIAVIX": {"ltp": 14.0, "prev_close": 15.0, "change_pct": -6.5},
            }
            result["sectors"] = {}
            result["pcr"] = 0.92
            result["updated_at"] = 1234567890.0
            if include_constituents:
                result["constituent_quotes"] = {
                    "HDFCBANK": {
                        "ltp": 1520.0,
                        "prev_close": 1500.0,
                        "change_pct": 1.33,
                        "volume": 150000,
                        "average_price": 1510.0,
                    }
                }

        monkeypatch.setattr(market_pulse_data, "_cache", {})
        monkeypatch.setattr(market_pulse_data, "_cache_ts", 0.0)
        monkeypatch.setattr(
            market_pulse_data,
            "_get_constituents",
            lambda: [{"symbol": "HDFCBANK", "exchange": "NSE", "sector": "BANK"}],
        )
        monkeypatch.setattr(market_pulse_data, "_fetch_delivery_snapshot", fake_fetch_delivery_snapshot)
        monkeypatch.setattr(market_pulse_data, "_fetch_history_batch", fake_fetch_history_batch)
        monkeypatch.setattr(
            market_pulse_data,
            "_refresh_intraday_market_fields",
            fake_refresh_intraday_market_fields,
        )
        monkeypatch.setattr(market_pulse_data, "_fetch_institutional_flows", lambda force_refresh=False: {})
        monkeypatch.setattr(market_pulse_data, "_fetch_options_context", lambda force_refresh=False: {})
        monkeypatch.setattr(market_pulse_data, "_get_events", lambda: [])

        result = fetch_market_data(mode="day", force_refresh=True)

        assert calls["delivery"] == 0
        assert "HDFCBANK" in result["constituent_data"]
