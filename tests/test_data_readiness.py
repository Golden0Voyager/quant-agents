"""Tests for the data readiness pre-check module.

Covers all functions and branches to achieve 100% branch coverage.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from rich.console import Console

pytestmark = pytest.mark.unit

from tradingagents.agents.utils.data_readiness import (
    ANALYST_DATA_REQUIREMENTS,
    ReadinessItem,
    ReadinessReport,
    _check_index_daily,
    _check_limit_up_down,
    check_batch_readiness,
    check_data_readiness,
    display_readiness_report,
)

# ===========================================================================
# Dataclass basics
# ===========================================================================


class TestReadinessItemAndReport:
    """ReadinessItem and ReadinessReport dataclass basics."""

    def test_readiness_item_creation(self):
        item = ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market")
        assert item.label == "日K行情"
        assert item.category == "cacheable"
        assert item.status == "cached"

    def test_readiness_report_defaults(self):
        report = ReadinessReport()
        assert report.items == []
        assert report.all_ready is True
        assert report.warning_count == 0

    def test_readiness_report_append(self):
        item = ReadinessItem("测试", "realtime", "available", "ok", "market")
        report = ReadinessReport(items=[item], all_ready=True, warning_count=0)
        assert len(report.items) == 1
        assert report.items[0] is item


# ===========================================================================
# _check_ohlcv additional branches
# ===========================================================================


class TestCheckOhlcvBranches:
    """Additional branches of _check_ohlcv not yet tested."""

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_empty_dataframe_returns_unavailable(self, mock_load):
        mock_load.return_value = pd.DataFrame()
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
        assert ohlcv_item.status == "unavailable"

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_generic_exception_returns_unavailable(self, mock_load):
        mock_load.side_effect = ValueError("unexpected error")
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
        assert ohlcv_item.status == "unavailable"
        assert "ValueError" in ohlcv_item.details

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_stale_with_days_old_suffix(self, mock_load):
        mock_df = pd.DataFrame({
            "Date": ["2026-06-28", "2026-06-30"],
            "Close": [10.0, 10.5],
        })
        mock_load.return_value = mock_df
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
        assert ohlcv_item.status == "preloaded"
        assert "日前" in ohlcv_item.details


# ===========================================================================
# _check_smartmoney_table
# ===========================================================================


class TestCheckSmartmoneyTable:
    """Test _check_smartmoney_table via check_data_readiness which triggers it for A-share fund_flow."""

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_non_ashare_returns_none(
        self, mock_load, mock_is_a_share
    ):
        """Non-A-share tickers do not query smartmoney_db at all."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = False

        # fund_flow is only checked for A-shares; for non-A, it's realtime "available"
        report = check_data_readiness("AAPL", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert len(ff_items) == 1
        # For non-A-share: _check_smartmoney_table returns None, then falls through
        # to "无缓存，分析时实时获取"
        assert ff_items[0].status == "available"
        assert "实时获取" in ff_items[0].details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_with_cache_hit(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """A-share with data in smartmoney_db returns cached status."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [15],
            "latest": ["2026-07-03"],
        })

        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert len(ff_items) == 1
        assert ff_items[0].status == "cached"
        assert "15 条记录" in ff_items[0].details
        assert "2026-07-03" in ff_items[0].details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_cache_stale_flags_warning(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """Cache older than the stale budget flags 'stale' (yellow panel light)."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [45],
            "latest": ["2026-06-18"],
        })

        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert len(ff_items) == 1
        assert ff_items[0].status == "stale"
        assert "滞后" in ff_items[0].details
        assert "45 条记录" in ff_items[0].details
        # stale is a warning, not a blocker
        assert report.all_ready is True
        assert report.warning_count == 1

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_fin_statements_behind_expected_period_flags_warning(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """quarterly report older than the expected period (deadline+grace)
        flags 'stale' — judged by quarter, not by trading sessions."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-09-15"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [8],
            "latest": ["2026-03-31"],
        })

        report = check_data_readiness("000001.SZ", "2026-09-15", ["fundamentals"])
        fin_items = [i for i in report.items if i.label == "财务报表"]
        assert len(fin_items) == 1
        assert fin_items[0].status == "stale"
        assert "应披露期" in fin_items[0].details
        assert report.all_ready is True

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_fin_statements_current_period_stays_cached(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        mock_load.return_value = pd.DataFrame({"Date": ["2026-06-19"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [8],
            "latest": ["2026-03-31"],
        })

        report = check_data_readiness("000001.SZ", "2026-06-19", ["fundamentals"])
        fin_items = [i for i in report.items if i.label == "财务报表"]
        assert len(fin_items) == 1
        assert fin_items[0].status == "cached"

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_cache_hit_none_latest(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """Cache hit with null latest date — no date suffix appended."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [3],
            "latest": [None],
        })

        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert ff_items[0].status == "cached"
        # When latest is None, latest_str="N/A", but the guard
        # `if latest_str != "N/A"` skips the suffix entirely
        assert ff_items[0].details == "3 条记录"

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_cache_empty_result(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """A-share with zero cnt in smartmoney_db falls through to 'available'."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [0],
            "latest": [None],
        })

        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert ff_items[0].status == "available"

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_cache_exception(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """Exception in smartmoney_db query falls through gracefully."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.side_effect = RuntimeError("db unavailable")

        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert ff_items[0].status == "available"
        assert "实时获取" in ff_items[0].details


# ===========================================================================
# _check_fund_flow and _check_fin_statements
# ===========================================================================


class TestCheckFundFlow:
    """Test _check_fund_flow branches."""

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_non_ashare_fund_flow_falls_through(
        self, mock_load, mock_is_a_share
    ):
        """Non-A-share: fund_flow shows '实时获取'."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = False

        report = check_data_readiness("AAPL", "2026-07-03", ["market"])
        ff_items = [i for i in report.items if i.label == "资金流向"]
        assert len(ff_items) == 1
        assert ff_items[0].status == "available"
        assert "实时获取" in ff_items[0].details


class TestCheckFinStatements:
    """Test _check_fin_statements branches."""

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_non_ashare_fin_statements_falls_through(
        self, mock_load, mock_is_a_share
    ):
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = False

        report = check_data_readiness("AAPL", "2026-07-03", ["fundamentals"])
        fs_items = [i for i in report.items if i.label == "财务报表"]
        assert len(fs_items) == 1
        assert fs_items[0].status == "available"
        assert "实时获取" in fs_items[0].details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_fin_statements_cached(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [5],
            "latest": ["2026-06-30"],
        })

        report = check_data_readiness("000001.SZ", "2026-07-03", ["fundamentals"])
        fs_items = [i for i in report.items if i.label == "财务报表"]
        assert fs_items[0].status == "cached"
        assert "5 条记录" in fs_items[0].details


# ===========================================================================
# _check_limit_up_down
# ===========================================================================


class TestCheckLimitUpDown:
    """Test _check_limit_up_down branches."""

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    def test_non_ashare_limit_up_down_falls_through(
        self, mock_is_a_share
    ):
        """Non-A-share: limit_up_down shows '实时获取'."""
        mock_is_a_share.return_value = False

        item = _check_limit_up_down("AAPL", "2026-07-03", "market")
        assert item.status == "available"
        assert "非A股标的" in item.details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    def test_ashare_limit_up_down_cached(
        self, mock_df, mock_is_a_share
    ):
        """A-share with data in limit_up_down returns cached status."""
        mock_is_a_share.return_value = True
        mock_df.return_value = pd.DataFrame({"cnt": [85]})

        item = _check_limit_up_down("000001.SZ", "2026-07-03", "market")
        assert item.status == "cached"
        assert "85 条记录" in item.details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    def test_ashare_limit_up_down_empty_result(
        self, mock_df, mock_is_a_share
    ):
        """A-share with zero cnt falls through to 'available'."""
        mock_is_a_share.return_value = True
        mock_df.return_value = pd.DataFrame({"cnt": [0]})

        item = _check_limit_up_down("000001.SZ", "2026-07-03", "market")
        assert item.status == "available"
        assert "实时获取" in item.details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    def test_ashare_limit_up_down_exception(
        self, mock_df, mock_is_a_share
    ):
        """Exception in smartmoney_db query falls through gracefully."""
        mock_is_a_share.return_value = True
        mock_df.side_effect = RuntimeError("db unavailable")

        item = _check_limit_up_down("000001.SZ", "2026-07-03", "market")
        assert item.status == "available"
        assert "实时获取" in item.details


# ===========================================================================
# _check_index_daily
# ===========================================================================


class TestCheckIndexDaily:
    """Test _check_index_daily branches."""

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    def test_non_ashare_index_daily_falls_through(
        self, mock_is_a_share
    ):
        """Non-A-share: index_daily shows '实时获取'."""
        mock_is_a_share.return_value = False

        item = _check_index_daily("AAPL", "2026-07-03", "market")
        assert item.status == "available"
        assert "非A股标的" in item.details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    def test_ashare_index_daily_cached(
        self, mock_df, mock_is_a_share
    ):
        """A-share with data in index_daily returns cached status."""
        mock_is_a_share.return_value = True
        mock_df.return_value = pd.DataFrame({
            "cnt": [20],
            "latest": ["2026-07-02"],
        })

        item = _check_index_daily("000001.SZ", "2026-07-03", "market")
        assert item.status == "cached"
        assert "20 条记录" in item.details
        assert "2026-07-02" in item.details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    def test_ashare_index_daily_empty_result(
        self, mock_df, mock_is_a_share
    ):
        """A-share with zero cnt falls through to 'available'."""
        mock_is_a_share.return_value = True
        mock_df.return_value = pd.DataFrame({
            "cnt": [0],
            "latest": [None],
        })

        item = _check_index_daily("000001.SZ", "2026-07-03", "market")
        assert item.status == "available"
        assert "实时获取" in item.details

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    def test_ashare_index_daily_exception(
        self, mock_df, mock_is_a_share
    ):
        """Exception in smartmoney_db query falls through gracefully."""
        mock_is_a_share.return_value = True
        mock_df.side_effect = RuntimeError("db unavailable")

        item = _check_index_daily("000001.SZ", "2026-07-03", "market")
        assert item.status == "available"
        assert "实时获取" in item.details


# ===========================================================================
# _check_derived
# ===========================================================================


class TestCheckDerived:
    """Derived data (indicators from OHLCV)."""

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_indicators_available_when_ohlcv_ok(self, mock_load):
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ind_items = [i for i in report.items if i.label == "技术指标"]
        assert len(ind_items) == 1
        assert ind_items[0].status == "available"
        assert "即时计算" in ind_items[0].details

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_indicators_unavailable_when_ohlcv_fails(self, mock_load):
        mock_load.side_effect = RuntimeError("no data")
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ind_items = [i for i in report.items if i.label == "技术指标"]
        assert len(ind_items) == 1
        assert ind_items[0].status == "unavailable"
        assert "基础行情不可用" in ind_items[0].details


# ===========================================================================
# _check_northbound branch (line 150)
# ===========================================================================


class TestCheckNorthbound:
    """Test _check_northbound early-return branch (line 150)."""

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_ashare_northbound_cached(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """Line 150: A-share northbound with cache hit returns cached status.
        Triggered via governance analyst which includes northbound."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({
            "cnt": [8],
            "latest": ["2026-07-02"],
        })

        report = check_data_readiness("000001.SZ", "2026-07-03", ["governance"])
        nb_items = [i for i in report.items if i.label == "北向资金"]
        assert len(nb_items) == 1
        assert nb_items[0].status == "cached"
        assert "8 条记录" in nb_items[0].details


# ===========================================================================
# check_batch_readiness — resolve_ticker exception (lines 357-358)
# ===========================================================================


class TestCheckBatchReadinessResolveTicker:
    """Test check_batch_readiness resolve_ticker exception fallback."""

    @patch("tradingagents.agents.utils.data_readiness.resolve_ticker")
    @patch("tradingagents.agents.utils.data_readiness.check_data_readiness")
    def test_resolve_ticker_exception_falls_back_to_original_ticker(
        self, mock_check, mock_resolve
    ):
        """Lines 357-358: resolve_ticker raises -> fallback to original ticker."""
        mock_resolve.side_effect = ValueError("resolve failed")
        mock_check.return_value.items = []
        from tradingagents.agents.utils.data_readiness import ReadinessItem
        mock_check.return_value.items = [
            ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market"),
        ]

        ready, total = check_batch_readiness(
            ["000001.SZ"], "2026-07-03", ["market"]
        )
        assert ready == 1
        assert total == 1
        # check_data_readiness should be called with the original ticker
        mock_check.assert_called_once_with("000001.SZ", "2026-07-03", ["market"])


# ===========================================================================
# check_data_readiness — integration tests
# ===========================================================================


class TestCheckDataReadinessIntegration:
    """Integration-level tests for check_data_readiness."""

    def test_empty_selected_analysts(self):
        """No analysts selected → empty report."""
        report = check_data_readiness("AAPL", "2026-07-03", [])
        assert report.items == []
        assert report.all_ready is True
        assert report.warning_count == 0

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_governance_analyst_all_realtime(self, mock_load):
        """Governance analyst data sources are all realtime."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        report = check_data_readiness("AAPL", "2026-07-03", ["governance"])
        # governance has: dragon_tiger, margin_trading, shareholders, pledge, northbound, inst_intel
        expected_labels = {"龙虎榜", "融资融券", "股东户数", "股权质押", "北向资金", "机构综合情报"}
        actual_labels = {i.label for i in report.items}
        assert actual_labels == expected_labels
        for item in report.items:
            assert item.status == "available"
            assert item.category in ("realtime", "cacheable")

    @patch("tradingagents.agents.utils.data_readiness.is_a_share_ticker")
    @patch("tradingagents.dataflows.smartmoney_vendor._to_smartmoney_symbol")
    @patch("tradingagents.dataflows.smartmoney_vendor._df_from_sql")
    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_governance_ashare_uses_local_tables(
        self, mock_load, mock_df, mock_to_sym, mock_is_a_share
    ):
        """A-share governance items are served from local tables when present."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        mock_is_a_share.return_value = True
        mock_to_sym.return_value = "000001"
        mock_df.return_value = pd.DataFrame({"cnt": [5], "latest": ["2026-07-02"]})

        report = check_data_readiness("000001.SZ", "2026-07-03", ["governance"])
        by_label = {i.label: i for i in report.items}
        # Items with local tables are now cacheable-first
        for label in ("龙虎榜", "融资融券", "股东户数", "北向资金"):
            assert by_label[label].category == "cacheable", label
            assert by_label[label].status == "cached", label
        # Pledge has no local table — stays realtime
        assert by_label["股权质押"].category == "realtime"

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_industry_analyst_all_realtime(self, mock_load):
        """Industry analyst data sources are all realtime."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        report = check_data_readiness("AAPL", "2026-07-03", ["industry"])
        expected_labels = {"行业估值", "宏观数据", "概念题材"}
        actual_labels = {i.label for i in report.items}
        assert actual_labels == expected_labels

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_social_analyst_all_realtime(self, mock_load):
        """Social analyst has stocktwits + reddit."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        report = check_data_readiness("AAPL", "2026-07-03", ["social"])
        expected_labels = {"StockTwits", "Reddit"}
        actual_labels = {i.label for i in report.items}
        assert actual_labels == expected_labels

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_unknown_analyst_key_produces_no_items(self, mock_load):
        """Unknown analyst key not in ANALYST_DATA_REQUIREMENTS → no items."""
        report = check_data_readiness("AAPL", "2026-07-03", ["nonexistent"])
        assert report.items == []

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_multiple_analysts_deduplicates_keys(self, mock_load):
        """Multiple analysts sharing same data keys → only one item per key."""
        mock_load.return_value = pd.DataFrame({"Date": ["2026-07-03"], "Close": [10.0]})
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market", "market"])
        # "market" analyst listed twice but deduplicated:
        # ohlcv, indicators, chip_distribution, fund_flow, limit_up_down, index_daily (6 items)
        assert len(report.items) == 6

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_warning_count_unavailable_item(self, mock_load):
        """Unavailable items increment warning_count and set all_ready=False."""
        mock_load.return_value = None  # load_ohlcv returns None → empty branch
        report = check_data_readiness("INVALID", "2026-07-03", ["market"])
        # ohlcv → None/empty → unavailable → derived indicators also unavailable
        assert report.all_ready is False
        assert report.warning_count >= 1


# ===========================================================================
# display_readiness_report
# ===========================================================================


class TestDisplayReadinessReport:
    """Test Rich rendering of readiness report."""

    def test_display_with_cacheable_only(self):
        """Only cacheable items show the cacheable table."""
        console = Console(width=100, force_terminal=True)
        item = ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market")
        report = ReadinessReport(items=[item], all_ready=True)
        display_readiness_report(console, report)

    def test_display_with_realtime_only(self):
        """Only realtime items show the realtime table."""
        console = Console(width=100, force_terminal=True)
        item = ReadinessItem("StockTwits", "realtime", "available", "分析时实时获取", "social")
        report = ReadinessReport(items=[item], all_ready=True)
        display_readiness_report(console, report)

    def test_display_with_both_tables(self):
        """Both cacheable and realtime tables shown."""
        console = Console(width=100, force_terminal=True)
        items = [
            ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market"),
            ReadinessItem("StockTwits", "realtime", "available", "分析时实时获取", "social"),
        ]
        report = ReadinessReport(items=items, all_ready=True)
        display_readiness_report(console, report)

    def test_display_with_warnings(self):
        """Warning count displayed when items are unavailable."""
        console = Console(width=100, force_terminal=True)
        items = [
            ReadinessItem("日K行情", "cacheable", "unavailable", "无可用数据", "market"),
            ReadinessItem("技术指标", "cacheable", "unavailable", "基础行情不可用", "market"),
        ]
        report = ReadinessReport(items=items, all_ready=False, warning_count=2)
        display_readiness_report(console, report)

    def test_display_empty_report(self):
        """Empty report displays panel but no tables."""
        console = Console(width=100, force_terminal=True)
        report = ReadinessReport(items=[], all_ready=True)
        display_readiness_report(console, report)

    def test_display_preloaded_status(self):
        """Preloaded items render with ✅ emoji."""
        console = Console(width=100, force_terminal=True)
        item = ReadinessItem("日K行情", "cacheable", "preloaded", "最新 2026-06-30", "market")
        report = ReadinessReport(items=[item], all_ready=True)
        display_readiness_report(console, report)

    def test_display_skipped_status(self):
        """Skipped items render with ⏭️ emoji."""
        console = Console(width=100, force_terminal=True)
        item = ReadinessItem("某数据", "cacheable", "skipped", "已跳过", "market")
        report = ReadinessReport(items=[item], all_ready=True)
        display_readiness_report(console, report)

    def test_display_stale_status(self):
        """Stale items render with ⚠️ emoji and a yellow summary line."""
        console = Console(width=100, force_terminal=True)
        item = ReadinessItem(
            "资金流向", "cacheable", "stale",
            "45 条记录，最新 2026-06-18 ⚠️ 滞后 15 天（超 7 天阈值）", "market",
        )
        report = ReadinessReport(items=[item], all_ready=True, warning_count=1)
        with console.capture() as capture:
            display_readiness_report(console, report)
        out = capture.get()
        assert "⚠️" in out
        assert "线上刷新" in out

    def test_display_unknown_status(self):
        """Unknown status defaults to ❓ emoji."""
        console = Console(width=100, force_terminal=True)
        item = ReadinessItem("某数据", "cacheable", "unknown_status", "?", "market")
        report = ReadinessReport(items=[item], all_ready=True)
        display_readiness_report(console, report)

    def test_display_captures_print_calls(self):
        """Verify console.print is called the expected number of times."""
        console = Console(width=100, force_terminal=True)
        console.print = MagicMock()  # type: ignore[method-assign]

        items = [
            ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market"),
            ReadinessItem("StockTwits", "realtime", "available", "分析时实时获取", "social"),
        ]
        report = ReadinessReport(items=items, all_ready=True)
        display_readiness_report(console, report)

        # Called: Panel, cacheable Table, realtime Table, success message
        assert console.print.call_count >= 4

    def test_display_warning_message_when_not_ready(self):
        """Warning message shown when items are unavailable."""
        console = Console(width=100, force_terminal=True)
        console.print = MagicMock()  # type: ignore[method-assign]

        item = ReadinessItem("日K行情", "cacheable", "unavailable", "无可用数据", "market")
        report = ReadinessReport(items=[item], all_ready=False, warning_count=1)
        display_readiness_report(console, report)

        # Check that a print call contains the warning text
        call_args = [str(c[0][0]) for c in console.print.call_args_list if c[0]]
        warning_texts = [s for s in call_args if "⚠" in s or "1 项数据" in s or "不可用" in s]
        assert len(warning_texts) >= 1


# ===========================================================================
# check_batch_readiness
# ===========================================================================


class TestCheckBatchReadiness:
    """Test batch readiness pre-loader."""

    @pytest.fixture(autouse=True)
    def _isolate_ticker_resolution(self):
        with patch(
            "tradingagents.agents.utils.data_readiness.resolve_ticker",
            side_effect=lambda ticker: {"ticker": ticker},
        ):
            yield

    @patch("tradingagents.agents.utils.data_readiness.check_data_readiness")
    def test_all_tickers_ready(self, mock_check):
        """All tickers have OHLCV data."""
        mock_check.return_value.items = []
        mock_check.return_value.all_ready = True

        # Create an ohlcv item that is not unavailable
        from tradingagents.agents.utils.data_readiness import ReadinessItem
        mock_check.return_value.items = [
            ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market"),
        ]

        ready, total = check_batch_readiness(["000001.SZ", "000002.SZ"], "2026-07-03", ["market"])
        assert ready == 2
        assert total == 2

    @patch("tradingagents.agents.utils.data_readiness.check_data_readiness")
    def test_some_tickers_not_ready(self, mock_check):
        """Some tickers have OHLCV unavailable."""
        from tradingagents.agents.utils.data_readiness import ReadinessItem

        def side_effect(ticker, *_args, **_kwargs):
            if ticker == "000001.SZ":
                return type("R", (), {"items": [
                    ReadinessItem("日K行情", "cacheable", "cached", "最新 2026-07-03", "market"),
                ]})()
            else:
                return type("R", (), {"items": [
                    ReadinessItem("日K行情", "cacheable", "unavailable", "无数据", "market"),
                ]})()

        mock_check.side_effect = side_effect
        ready, total = check_batch_readiness(
            ["000001.SZ", "000002.SZ"], "2026-07-03", ["market"]
        )
        assert ready == 1
        assert total == 2

    @patch("tradingagents.agents.utils.data_readiness.check_data_readiness")
    def test_exception_handling(self, mock_check):
        """Exception in check_data_readiness is caught, ticker not counted as ready."""
        mock_check.side_effect = RuntimeError("timeout")

        ready, total = check_batch_readiness(["000001.SZ"], "2026-07-03", ["market"])
        assert ready == 0
        assert total == 1

    @patch("tradingagents.agents.utils.data_readiness.check_data_readiness")
    def test_empty_tickers_list(self, mock_check):
        """Empty tickers list returns (0, 0)."""
        ready, total = check_batch_readiness([], "2026-07-03", ["market"])
        assert ready == 0
        assert total == 0
        mock_check.assert_not_called()


# ===========================================================================
# Existing tests (preserved and updated)
# ===========================================================================


class TestAnalystMapping:
    """Original tests for analyst mapping structure — preserved."""

    def test_analyst_mapping_has_all_analysts(self):
        expected_keys = {"market", "social", "news", "fundamentals", "governance", "industry"}
        assert expected_keys.issubset(ANALYST_DATA_REQUIREMENTS.keys())

    def test_analyst_data_keys_are_unique(self):
        seen: dict[str, str] = {}
        duplicates: list[tuple[str, str, str]] = []
        for analyst, reqs in ANALYST_DATA_REQUIREMENTS.items():
            for req in reqs:
                if req["key"] in seen:
                    duplicates.append((req["key"], seen[req["key"]], analyst))
                seen[req["key"]] = analyst
        assert not duplicates, f"Duplicate keys found: {duplicates}"

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_check_market_analyst_ohlcv_cached(self, mock_load_ohlcv):
        mock_df = pd.DataFrame({
            "Date": ["2026-07-01", "2026-07-02", "2026-07-03"],
            "Close": [10.0, 10.5, 11.0],
        })
        mock_load_ohlcv.return_value = mock_df
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        assert report.all_ready is True
        ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
        assert ohlcv_item.status == "cached"
        assert "2026-07-03" in ohlcv_item.details

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_check_market_analyst_ohlcv_stale(self, mock_load_ohlcv):
        mock_df = pd.DataFrame({
            "Date": ["2026-06-28", "2026-06-30"],
            "Close": [10.0, 10.5],
        })
        mock_load_ohlcv.return_value = mock_df
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market"])
        ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
        assert ohlcv_item.status == "preloaded"

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_check_market_analyst_ohlcv_unavailable(self, mock_load_ohlcv):
        from tradingagents.dataflows.errors import NoMarketDataError
        mock_load_ohlcv.side_effect = NoMarketDataError("TEST", "test", "no data")
        report = check_data_readiness("INVALID", "2026-07-03", ["market"])
        ohlcv_item = [i for i in report.items if i.label == "日K行情"][0]
        assert ohlcv_item.status == "unavailable"

    def test_realtime_data_always_available(self):
        report = check_data_readiness("AAPL", "2026-07-03", ["news", "social"])
        for item in report.items:
            assert item.category == "realtime"
            assert item.status == "available"

    @patch("tradingagents.agents.utils.data_readiness.load_ohlcv")
    def test_readiness_items_count_matches_unique_keys(self, mock_load_ohlcv):
        mock_load_ohlcv.return_value = pd.DataFrame({
            "Date": ["2026-07-03"],
            "Close": [11.0],
        })
        report = check_data_readiness("000001.SZ", "2026-07-03", ["market", "social", "news"])
        all_keys: set[str] = set()
        for analyst in ["market", "social", "news"]:
            for req in ANALYST_DATA_REQUIREMENTS.get(analyst, []):
                all_keys.add(req["key"])
        assert len(report.items) == len(all_keys)
