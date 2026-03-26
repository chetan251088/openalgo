import pandas as pd

from blueprints.market_pulse import (
    _resolve_live_underlying_ltp,
    _resolve_previous_day_levels,
)


class TestLiveUnderlyingResolution:
    def test_resolve_live_underlying_ltp_prefers_ticker_over_stale_fallback(self):
        resolved = _resolve_live_underlying_ltp(
            {
                "NIFTY": {
                    "ltp": 22912.4,
                }
            },
            "NIFTY",
            25450.0,
        )

        assert resolved == 22912.4

    def test_resolve_live_underlying_ltp_uses_fallback_when_ticker_missing(self):
        resolved = _resolve_live_underlying_ltp({}, "NIFTY", 25450.0)

        assert resolved == 25450.0

    def test_resolve_previous_day_levels_prefers_session_matching_live_prev_close(self):
        history = pd.DataFrame(
            {
                "high": [22810.0, 22940.0],
                "low": [22590.0, 22680.0],
                "close": [22650.0, 22890.0],
            }
        )

        levels = _resolve_previous_day_levels(
            history,
            {
                "ltp": 22912.4,
                "prev_close": 22890.0,
            },
        )

        assert levels == {
            "pdh": 22940.0,
            "pdl": 22680.0,
            "pdc": 22890.0,
            "current": 22912.4,
            "state": "inside_prior_range",
            "gap_pct": 0.1,
        }

    def test_resolve_previous_day_levels_ignores_unsorted_stale_index_row(self):
        history = pd.DataFrame(
            {
                "timestamp": [1774224000, 1770854400, 1774310400],
                "high": [22851.7, 25906.7, 23057.3],
                "low": [22471.25, 25752.4, 22624.2],
                "close": [22512.65, 25807.2, 22958.4],
            }
        )

        levels = _resolve_previous_day_levels(
            history,
            {
                "ltp": 22912.4,
                "prev_close": 22512.65,
            },
        )

        assert levels == {
            "pdh": 22851.7,
            "pdl": 22471.25,
            "pdc": 22512.65,
            "current": 22912.4,
            "state": "above_pdh",
            "gap_pct": 1.78,
        }

    def test_resolve_previous_day_levels_returns_none_when_history_is_obviously_stale(self):
        history = pd.DataFrame(
            {
                "timestamp": [1770768000, 1770854400, 1770940800],
                "high": [26009.4, 25906.7, 25630.35],
                "low": [25899.8, 25752.4, 25444.3],
                "close": [25953.85, 25807.2, 25471.1],
            }
        )

        levels = _resolve_previous_day_levels(
            history,
            {
                "ltp": 22912.4,
                "prev_close": 22512.65,
            },
        )

        assert levels is None
