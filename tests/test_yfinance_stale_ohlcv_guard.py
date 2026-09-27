"""Stale OHLCV guard (#1021): a vendor returning a year-old partial frame must
be rejected, not fed into the report as if it were current.

The guard raises NoMarketDataError with a stale-specific detail, so the router's
existing try-next-vendor + single-sentinel handling applies and the sentinel
surfaces the reason.
"""
import copy
import unittest
from unittest import mock

import pandas as pd
import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.dataflows.y_finance as y_finance
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.stockstats_utils import _assert_ohlcv_not_stale
from tradingagents.dataflows.symbol_utils import NoMarketDataError


def _frame(date):
    return pd.DataFrame(
        {
            "Date": [pd.Timestamp(date)],
            "Open": [330.0],
            "High": [332.0],
            "Low": [328.0],
            "Close": [330.58],
            "Volume": [1_000_000],
        }
    )


@pytest.mark.unit
class StaleGuardUnitTests(unittest.TestCase):
    def test_current_day_is_accepted(self):
        # Same-day bar — the expectation for a pipeline refreshed daily after
        # close (budget 1: even one missing session means a skipped day).
        _assert_ohlcv_not_stale(_frame("2026-06-11"), "2026-06-11", "CB")

    def test_year_old_row_is_rejected_with_detail(self):
        with self.assertRaises(NoMarketDataError) as ctx:
            _assert_ohlcv_not_stale(_frame("2025-06-11"), "2026-06-11", "CB", "CB")
        msg = str(ctx.exception)
        self.assertIn("2025-06-11", msg)
        self.assertIn("2026-06-11", msg)
        self.assertIn("stale", msg)

    def test_empty_frame_is_left_to_caller(self):
        # Empty is a no-data condition handled elsewhere, not a staleness one.
        _assert_ohlcv_not_stale(
            pd.DataFrame(columns=["Date", "Close"]), "2026-06-11", "X"
        )

    def test_holiday_gap_adds_no_sessions_and_is_accepted(self):
        # National Day holiday (Oct 1-7): no trading sessions fall between
        # the last pre-holiday session and a mid-holiday anchor, so session
        # lag stays 0 no matter how many calendar days pass.
        _assert_ohlcv_not_stale(_frame("2026-09-30"), "2026-10-05", "X")

    def test_stall_case_trips_guard_within_a_week(self):
        # Pipeline stalled after Wed Jun 3; a request on Wed Jun 10 has
        # missed 5 sessions (>= 1) and must be rejected even though only
        # 7 calendar days passed.
        with self.assertRaises(NoMarketDataError):
            _assert_ohlcv_not_stale(_frame("2026-06-03"), "2026-06-10", "X")

    def test_unparseable_curr_date_passes_through(self):
        _assert_ohlcv_not_stale(_frame("2026-06-10"), "bad-date", "CB")

    def test_all_nan_dates_passes_through(self):
        df = pd.DataFrame({
            "Date": [pd.NaT, pd.NaT],
            "Close": [100.0, 101.0],
        })
        _assert_ohlcv_not_stale(df, "2026-06-11", "CB")

    def test_none_data_passes_through(self):
        _assert_ohlcv_not_stale(None, "2026-06-11", "CB")


@pytest.mark.unit
class CoerceOhlcvDatesTests(unittest.TestCase):
    def test_with_date_column(self):
        df = pd.DataFrame({"Date": ["2026-06-10", "2026-06-11"], "Close": [100.0, 101.0]})
        from tradingagents.dataflows.stockstats_utils import _coerce_ohlcv_dates
        result = _coerce_ohlcv_dates(df)
        self.assertEqual(len(result), 2)

    def test_with_datetime_index(self):
        df = pd.DataFrame(
            {"Close": [100.0, 101.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-06-10"), pd.Timestamp("2026-06-11")]),
        )
        from tradingagents.dataflows.stockstats_utils import _coerce_ohlcv_dates
        result = _coerce_ohlcv_dates(df)
        self.assertEqual(len(result), 2)

    def test_fallback_reset_index_finds_date_column(self):
        df = pd.DataFrame({"Close": [100.0, 101.0], "Date": ["2026-06-10", "2026-06-11"]})
        df.index = pd.RangeIndex(start=10, stop=12)
        from tradingagents.dataflows.stockstats_utils import _coerce_ohlcv_dates
        result = _coerce_ohlcv_dates(df)
        self.assertEqual(len(result), 2)

    def test_fallback_reset_index_uses_index_column(self):
        df = pd.DataFrame({"Close": [100.0, 101.0]})
        from tradingagents.dataflows.stockstats_utils import _coerce_ohlcv_dates
        result = _coerce_ohlcv_dates(df)
        self.assertEqual(len(result), 2)


@pytest.mark.unit
class StaleGuardPropagationTests(unittest.TestCase):
    def test_get_yfin_data_online_raises_on_stale_frame(self):
        stale = pd.DataFrame(
            {
                "Open": [280.0], "High": [286.0], "Low": [278.0],
                "Close": [284.45], "Volume": [1_000_000],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2025-06-11")], name="Date"),
        )

        class DummyTicker:
            def __init__(self, symbol):
                pass

            def history(self, start, end):
                return stale

        with mock.patch.object(y_finance.yf, "Ticker", DummyTicker), \
                self.assertRaises(NoMarketDataError):
            y_finance.get_YFin_data_online("CB", "2026-06-01", "2026-06-11")


@pytest.mark.unit
class StaleGuardRoutingTests(unittest.TestCase):
    def setUp(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def tearDown(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def test_router_sentinel_surfaces_stale_reason(self):
        set_config({"data_vendors": {"core_stock_apis": "yfinance"}})

        def _stale(symbol, *a, **k):
            raise NoMarketDataError(
                symbol, symbol, "latest row is 2025-06-11, 365 days before ... (stale)"
            )

        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_stock_data": {"yfinance": _stale}},
            clear=False,
        ):
            out = interface.route_to_vendor(
                "get_stock_data", "CB", "2026-06-01", "2026-06-11"
            )
        self.assertIn("NO_DATA_AVAILABLE", out)
        self.assertIn("stale", out)  # the typed detail is surfaced to the agent


if __name__ == "__main__":
    unittest.main()
