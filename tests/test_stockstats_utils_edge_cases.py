"""Targeted edge-case tests for uncovered code paths in stockstats_utils.py.

Existing tests cover ~85%. This file fills remaining gaps:
- _load_ohlcv_from_akshare: no_proxy + column mapping, exception catch
- load_ohlcv: A-share fallback chain (smartmoney_db -> akshare), except Exception
- load_ohlcv: CSV save after successful download
- StockstatsUtils.get_stock_stats: remaining uncovered lines
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows.stockstats_utils import (
    StockstatsUtils,
    _load_ohlcv_from_akshare,
    load_ohlcv,
)
from tradingagents.dataflows.symbol_utils import NoMarketDataError


class _TempDirMixin:
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self._tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
# _load_ohlcv_from_akshare: network call + column mapping (lines ~158-216)
#
# NOTE: _load_ohlcv_from_akshare imports akshare_common functions inline
# (from tradingagents.dataflows.akshare_common import ...), so patches
# must target the akshare_common module, not stockstats_utils.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class LoadOhlcvFromAkshareNetworkTests(unittest.TestCase):
    """Test _load_ohlcv_from_akshare code paths: no_proxy, col map, exception."""

    def test_successful_download_with_column_mapping(self):
        """AkShare returns data with Chinese column names -> mapped to English."""
        mock_df = pd.DataFrame({
            "日期": ["2026-01-02", "2026-01-03"],
            "开盘": [99.0, 100.0],
            "收盘": [100.0, 101.0],
            "最高": [102.0, 103.0],
            "最低": [98.0, 99.0],
            "成交量": [10000, 11000],
        })

        patcher_akshare_common_is_a_share = patch(
            "tradingagents.dataflows.akshare_common.is_a_share_ticker",
            return_value=True,
        )
        patcher_akshare_common_to_symbol = patch(
            "tradingagents.dataflows.akshare_common.to_akshare_symbol",
            return_value="000001",
        )
        patcher_akshare_common_no_proxy = patch(
            "tradingagents.dataflows.akshare_common.no_proxy",
        )
        patcher_akshare_common_retry = patch(
            "tradingagents.dataflows.akshare_common._akshare_retry",
            return_value=mock_df,
        )

        with patcher_akshare_common_is_a_share, \
             patcher_akshare_common_to_symbol, \
             patcher_akshare_common_no_proxy as mock_np, \
             patcher_akshare_common_retry:
            mock_np.return_value.__enter__.return_value = None
            result = _load_ohlcv_from_akshare("000001.SZ", "2026-01-01", "2026-01-10")

        self.assertIsNotNone(result)
        for col in ("Date", "Open", "Close", "High", "Low", "Volume"):
            self.assertIn(col, result.columns)

    def test_network_exception_caught(self):
        """AkShare network call raises -> caught by except Exception, returns None."""
        patcher_akshare_common_is_a_share = patch(
            "tradingagents.dataflows.akshare_common.is_a_share_ticker",
            return_value=True,
        )
        patcher_akshare_common_to_symbol = patch(
            "tradingagents.dataflows.akshare_common.to_akshare_symbol",
            return_value="000001",
        )
        patcher_akshare_common_no_proxy = patch(
            "tradingagents.dataflows.akshare_common.no_proxy",
        )
        patcher_akshare_common_retry = patch(
            "tradingagents.dataflows.akshare_common._akshare_retry",
            side_effect=ConnectionError("network down"),
        )

        with patcher_akshare_common_is_a_share, \
             patcher_akshare_common_to_symbol, \
             patcher_akshare_common_no_proxy, \
             patcher_akshare_common_retry:
            result = _load_ohlcv_from_akshare("000001.SZ", "2026-01-01", "2026-01-10")

        self.assertIsNone(result)

    def test_empty_dataframe_returns_none(self):
        """AkShare returns empty DataFrame -> returns None."""
        patcher_akshare_common_is_a_share = patch(
            "tradingagents.dataflows.akshare_common.is_a_share_ticker",
            return_value=True,
        )
        patcher_akshare_common_to_symbol = patch(
            "tradingagents.dataflows.akshare_common.to_akshare_symbol",
            return_value="000001",
        )
        patcher_akshare_common_no_proxy = patch(
            "tradingagents.dataflows.akshare_common.no_proxy",
        )
        patcher_akshare_common_retry = patch(
            "tradingagents.dataflows.akshare_common._akshare_retry",
            return_value=pd.DataFrame(),
        )

        with patcher_akshare_common_is_a_share, \
             patcher_akshare_common_to_symbol, \
             patcher_akshare_common_no_proxy, \
             patcher_akshare_common_retry:
            result = _load_ohlcv_from_akshare("000001.SZ", "2026-01-01", "2026-01-10")

        self.assertIsNone(result)

    def test_partial_column_mapping(self):
        """AkShare returns data with only some Chinese columns -> maps available ones."""
        mock_df = pd.DataFrame({
            "日期": ["2026-01-02"],
            "收盘": [100.0],
            "成交量": [10000],
        })

        patcher_akshare_common_is_a_share = patch(
            "tradingagents.dataflows.akshare_common.is_a_share_ticker",
            return_value=True,
        )
        patcher_akshare_common_to_symbol = patch(
            "tradingagents.dataflows.akshare_common.to_akshare_symbol",
            return_value="000001",
        )
        patcher_akshare_common_no_proxy = patch(
            "tradingagents.dataflows.akshare_common.no_proxy",
        )
        patcher_akshare_common_retry = patch(
            "tradingagents.dataflows.akshare_common._akshare_retry",
            return_value=mock_df,
        )

        with patcher_akshare_common_is_a_share, \
             patcher_akshare_common_to_symbol, \
             patcher_akshare_common_no_proxy, \
             patcher_akshare_common_retry:
            result = _load_ohlcv_from_akshare("000001.SZ", "2026-01-01", "2026-01-10")

        self.assertIsNotNone(result)
        self.assertIn("Date", result.columns)
        self.assertIn("Close", result.columns)
        self.assertNotIn("Open", result.columns)


# ---------------------------------------------------------------------------
# load_ohlcv: yfinance exception, A-share fallback, CSV save (lines ~219-307)
#
# load_ohlcv imports is_a_share_ticker inline from akshare_common, so patches
# must target akshare_common, not stockstats_utils.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class LoadOhlcvYfinanceExceptionTests(_TempDirMixin, unittest.TestCase):
    """load_ohlcv: yfinance raises non-rate-limit exception -> fallback."""

    def test_yfinance_exception_falls_to_smartmoney_db_for_a_share(self):
        """yfinance raises Exception -> A-share ticker falls to smartmoney_db."""
        smartmoney_df = pd.DataFrame({
            "Date": ["2026-01-02", "2026-01-03"],
            "Open": [99.0, 100.0],
            "High": [102.0, 103.0],
            "Low": [98.0, 99.0],
            "Close": [100.0, 101.0],
            "Volume": [10000, 11000],
        })

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="600519.SS"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=True), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   side_effect=ValueError("yfinance crashed")), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_smartmoney_db",
                   return_value=smartmoney_df):
            result = load_ohlcv("600519.SS", "2026-01-03", lookback_years=5)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_yfinance_exception_both_fallbacks_fail(self):
        """yfinance + smartmoney_db + akshare all fail -> NoMarketDataError."""
        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="600519.SS"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=True), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   side_effect=ValueError("yfinance crashed")), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_smartmoney_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_akshare",
                   return_value=None), self.assertRaises(NoMarketDataError):
            load_ohlcv("600519.SS", "2026-01-03", lookback_years=5)

    def test_non_a_share_yfinance_exception_raises(self):
        """yfinance raises Exception for non-A-share -> NoMarketDataError."""
        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="AAPL"), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_global_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   side_effect=ValueError("yfinance crashed")), self.assertRaises(NoMarketDataError):
            load_ohlcv("AAPL", "2026-01-03", lookback_years=5)

    def test_akshare_fallback_success(self):
        """yfinance fails -> smartmoney_db fails -> akshare succeeds."""
        akshare_df = pd.DataFrame({
            "Date": ["2026-01-02", "2026-01-03"],
            "Open": [99.0, 100.0],
            "High": [102.0, 103.0],
            "Low": [98.0, 99.0],
            "Close": [100.0, 101.0],
            "Volume": [10000, 11000],
        })

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="600519.SS"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=True), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   side_effect=YFRateLimitError()), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_smartmoney_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_akshare",
                   return_value=akshare_df):
            result = load_ohlcv("600519.SS", "2026-01-03", lookback_years=5)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_yfinance_success_saves_csv(self):
        """yfinance download succeeds -> data saved to CSV cache."""
        downloaded = pd.DataFrame({
            "Date": ["2026-01-02", "2026-01-03"],
            "Open": [99.0, 100.0],
            "High": [102.0, 103.0],
            "Low": [98.0, 99.0],
            "Close": [100.0, 101.0],
            "Volume": [10000, 11000],
        })

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="AAPL"), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_global_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   return_value=downloaded):
            result = load_ohlcv("AAPL", "2026-01-03", lookback_years=5)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        cached_files = list(self._tmp.glob("AAPL-*.csv"))
        self.assertEqual(len(cached_files), 1)
        cached = pd.read_csv(cached_files[0])
        self.assertEqual(len(cached), 2)
        self.assertIn("Close", cached.columns)

    def test_yfinance_returns_empty_after_download(self):
        """yfinance returns empty data after ensure_date_column -> NoMarketDataError."""
        empty_df = pd.DataFrame()

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="INVALID"), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   return_value=empty_df), \
             patch("tradingagents.dataflows.stockstats_utils._ensure_date_column",
                   return_value=empty_df), self.assertRaises(NoMarketDataError):
            load_ohlcv("INVALID", "2026-01-03", lookback_years=5)

    # ------------------------------------------------------------------ #
    # New tests for A-share fallback order & DISABLE_YFINANCE_FALLBACK    #
    # (Todo 1 — fail with current code, pass after fallback reorder)     #
    # ------------------------------------------------------------------ #

    def test_a_share_prefers_smartmoney_then_akshare_then_yfinance(self):
        """A-share: smartmoney_db returns data -> neither akshare nor yf_retry called.
        FAILS with current code (yfinance-first order); passes after reorder.
        """
        smartmoney_df = pd.DataFrame({
            "Date": ["2026-01-02", "2026-01-03"],
            "Open": [99.0, 100.0],
            "High": [102.0, 103.0],
            "Low": [98.0, 99.0],
            "Close": [100.0, 101.0],
            "Volume": [10000, 11000],
        })

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="000001.SZ"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=True), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry") as mock_yf, \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_smartmoney_db",
                   return_value=smartmoney_df), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_akshare") as mock_akshare:
            result = load_ohlcv("000001.SZ", "2026-01-03", lookback_years=5)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        # Current code calls yf_retry FIRST (before smartmoney) -> this assertion FAILS.
        mock_yf.assert_not_called()
        mock_akshare.assert_not_called()

    def test_disable_yfinance_fallback_skips_yfinance(self):
        """DISABLE_YFINANCE_FALLBACK=1 -> yfinance not called when smartmoney/akshare fail.
        FAILS with current code (env var not implemented); passes after impl.
        """
        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="600519.SS"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=True), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry") as mock_yf, \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_smartmoney_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_akshare",
                   return_value=None), \
             patch.dict(os.environ, {"DISABLE_YFINANCE_FALLBACK": "1"}):
            with self.assertRaises(NoMarketDataError):
                load_ohlcv("600519.SS", "2026-01-03", lookback_years=5)
            # Current code does NOT check env var -> yf_retry called -> FAILS.
            mock_yf.assert_not_called()

    def test_yfinance_fallback_failure_does_not_write_cache(self):
        """yfinance as last A-share fallback fails -> no cache CSV written.
        Regression: even when yfinance is the final resort and fails, the
        cache directory must NOT contain a *-YFin-data-*.csv file.
        """
        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="600519.SS"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=True), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   side_effect=YFRateLimitError()), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_smartmoney_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_akshare",
                   return_value=None), \
             self.assertRaises(NoMarketDataError):
            load_ohlcv("600519.SS", "2026-01-03", lookback_years=5)

        # Temp dir started empty — no CSV means no cache was written.
        cached_files = list(self._tmp.glob("*.csv"))
        self.assertEqual(len(cached_files), 0)


# ---------------------------------------------------------------------------
# StockstatsUtils.get_stock_stats: remaining uncovered paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class StockstatsUtilsGetStatsEdgeCases(unittest.TestCase):
    """Edge-case tests for get_stock_stats uncovered lines."""

    @patch("tradingagents.dataflows.stockstats_utils.load_ohlcv")
    @patch("tradingagents.dataflows.stockstats_utils.wrap")
    def test_indicator_computation_returns_numeric(self, mock_wrap, mock_load):
        """get_stock_stats computes indicator and returns a numeric value."""
        mock_load.return_value = pd.DataFrame({
            "Date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
            "Close": [100.0, 101.0],
        })
        wrapped = pd.DataFrame({
            "Date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
            "Close": [100.0, 101.0],
            "close_5_sma": [102.5, 103.0],
        })
        mock_wrap.return_value = wrapped.copy()

        result = StockstatsUtils.get_stock_stats("AAPL", "close_5_sma", "2026-01-03")
        self.assertEqual(result, 103.0)

    @patch("tradingagents.dataflows.stockstats_utils.load_ohlcv")
    @patch("tradingagents.dataflows.stockstats_utils.wrap")
    def test_non_existent_indicator_raises(self, mock_wrap, mock_load):
        """Non-existent indicator -> df[indicator] raises KeyError."""
        mock_load.return_value = pd.DataFrame({
            "Date": pd.to_datetime(["2026-01-02"]),
            "Close": [100.0],
        })
        wrapped = pd.DataFrame({
            "Date": pd.to_datetime(["2026-01-02"]),
            "Close": [100.0],
        })
        mock_wrap.return_value = wrapped

        with self.assertRaises(KeyError):
            StockstatsUtils.get_stock_stats("AAPL", "nonexistent_indicator", "2026-01-02")


# ---------------------------------------------------------------------------
# load_ohlcv: cache freshness / refresh behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class LoadOhlcvCacheFreshnessTests(_TempDirMixin, unittest.TestCase):
    """Cache is reused only when it covers the requested date; otherwise refresh."""

    def _write_cache(self, symbol: str, dates: list[str]) -> None:
        df = pd.DataFrame({
            "Date": dates,
            "Open": [100.0] * len(dates),
            "High": [101.0] * len(dates),
            "Low": [99.0] * len(dates),
            "Close": [100.5 + i for i in range(len(dates))],
            "Volume": [10000] * len(dates),
        })
        # File name matches load_ohlcv's naming convention.
        today = pd.Timestamp.today()
        start = (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
        end = (today + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        cache_file = self._tmp / f"{symbol}-YFin-data-{start}-{end}.csv"
        df.to_csv(cache_file, index=False)

    def test_refreshes_when_cache_lags_weekday(self):
        """Weekday curr_date with older cache -> re-download."""
        self._write_cache("AAPL", ["2026-01-02"])  # Friday
        fresh = pd.DataFrame({
            "Date": ["2026-01-02", "2026-01-05"],  # Monday
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [10000, 11000],
        })

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="AAPL"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=False), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_global_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   return_value=fresh) as mock_yf:
            result = load_ohlcv("AAPL", "2026-01-05", lookback_years=5)

        mock_yf.assert_called_once()
        self.assertEqual(len(result), 2)
        self.assertEqual(result["Close"].iloc[-1], 101.5)

    def test_uses_cache_when_it_covers_requested_date(self):
        """Cache already covers curr_date -> no re-download."""
        self._write_cache("AAPL", ["2026-01-02", "2026-01-05"])

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="AAPL"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=False), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry") as mock_yf:
            result = load_ohlcv("AAPL", "2026-01-05", lookback_years=5)

        mock_yf.assert_not_called()
        self.assertEqual(len(result), 2)

    def test_refresh_parameter_bypasses_cache(self):
        """refresh=True ignores cache and re-downloads."""
        self._write_cache("AAPL", ["2026-01-02", "2026-01-05"])
        fresh = pd.DataFrame({
            "Date": ["2026-01-02", "2026-01-05", "2026-01-06"],
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [10000, 11000, 12000],
        })

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="AAPL"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=False), \
             patch("tradingagents.dataflows.stockstats_utils._load_ohlcv_from_global_db",
                   return_value=None), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry",
                   return_value=fresh) as mock_yf:
            result = load_ohlcv("AAPL", "2026-01-06", lookback_years=5, refresh=True)

        mock_yf.assert_called_once()
        self.assertEqual(len(result), 3)

    def test_accepts_friday_cache_for_saturday(self):
        """Weekend curr_date -> accept last trading day without refreshing."""
        self._write_cache("AAPL", ["2026-01-02"])  # Friday

        with patch("tradingagents.dataflows.stockstats_utils.get_config",
                   return_value={"data_cache_dir": str(self._tmp)}), \
             patch("tradingagents.dataflows.stockstats_utils.normalize_symbol",
                   return_value="AAPL"), \
             patch("tradingagents.dataflows.akshare_common.is_a_share_ticker",
                   return_value=False), \
             patch("tradingagents.dataflows.stockstats_utils.yf_retry") as mock_yf:
            result = load_ohlcv("AAPL", "2026-01-03", lookback_years=5)  # Saturday

        mock_yf.assert_not_called()
        self.assertEqual(len(result), 1)
