"""Unit tests for tradingagents/dataflows/eastmoney_sentiment.py."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows.eastmoney_sentiment import (
    _safe_col,
    fetch_eastmoney_guba_sentiment,
    fetch_eastmoney_hot_rank,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fake data fixtures
# ---------------------------------------------------------------------------

_FAKE_HOT_RANK_DF = pd.DataFrame([
    {"当前排名": 61, "代码": "SH600519", "股票名称": "贵州茅台",
     "最新价": 1495.50, "涨跌额": -12.30, "涨跌幅": -0.82},
])

_FAKE_HOT_RANK_DETAIL_DF = pd.DataFrame([
    {"时间": "2026-07-09", "排名": 61, "证券代码": "SH600519",
     "新晋粉丝": 0.3158, "铁杆粉丝": 0.6842},
    {"时间": "2026-07-08", "排名": 53, "证券代码": "SH600519",
     "新晋粉丝": 0.3114, "铁杆粉丝": 0.6886},
])

_FAKE_COMMENT_DF = pd.DataFrame([
    {"序号": 1, "代码": "000001", "名称": "平安银行",
     "最新价": 10.49, "涨跌幅": -1.04, "换手率": 0.39, "市盈率": 3.50,
     "主力成本": 10.51, "机构参与度": 0.38, "综合得分": 65.33,
     "上升": -271, "目前排名": 1466, "关注指数": 87.2, "交易日": "2026-07-09"},
    {"序号": 2, "代码": "600519", "名称": "贵州茅台",
     "最新价": 1495.50, "涨跌幅": -0.82, "换手率": 0.25, "市盈率": 25.5,
     "主力成本": 1500.00, "机构参与度": 0.65, "综合得分": 72.50,
     "上升": 120, "目前排名": 61, "关注指数": 94.4, "交易日": "2026-07-09"},
])

_FAKE_FOCUS_DF = pd.DataFrame([
    {"交易日": "2026-07-03", "用户关注指数": 94.0},
    {"交易日": "2026-07-04", "用户关注指数": 94.4},
    {"交易日": "2026-07-07", "用户关注指数": 94.0},
    {"交易日": "2026-07-08", "用户关注指数": 94.4},
    {"交易日": "2026-07-09", "用户关注指数": 94.4},
])

_FAKE_DESIRE_DF = pd.DataFrame([
    {"交易日期": "2026-07-03", "股票代码": "600519",
     "参与意愿": 41.46, "5日平均参与意愿": 47.92, "参与意愿变化": -6.87,
     "5日平均变化": 1},
    {"交易日期": "2026-07-09", "股票代码": "600519",
     "参与意愿": 44.52, "5日平均参与意愿": 47.41, "参与意愿变化": -9.62,
     "5日平均变化": 1},
])


# ===================================================================
# Hot rank tests
# ===================================================================


class TestFetchEastmoneyHotRank:
    """Tests for fetch_eastmoney_hot_rank()."""

    def test_non_a_share_returns_placeholder(self):
        """Non-A-share tickers should get a graceful placeholder."""
        result = fetch_eastmoney_hot_rank("AAPL")
        assert "non-a-share" in result.lower()
        assert "AAPL" in result

    def test_empty_ticker_returns_placeholder(self):
        """Empty string is not an A-share ticker."""
        result = fetch_eastmoney_hot_rank("")
        assert "non-A-share" in result.lower() or "unavailable" in result.lower()

    def test_happy_path(self):
        """Both rank table and detail should appear in the result."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_hot_rank_em.return_value = _FAKE_HOT_RANK_DF
            mock_ak.stock_hot_rank_detail_em.return_value = _FAKE_HOT_RANK_DETAIL_DF

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "贵州茅台" in result or "SH600519" in result
        assert "61" in result  # rank
        assert "0.3158" in result or "0.68" in result  # fan metrics
        assert "人气排名" in result
        # Verify both akshare functions were called
        mock_ak.stock_hot_rank_em.assert_called_once()
        mock_ak.stock_hot_rank_detail_em.assert_called_once()

    def test_rank_table_empty_falls_back_gracefully(self):
        """When the symbol is not in the top-100 hot rank list, show a message."""
        empty_rank = pd.DataFrame([
            {"当前排名": 1, "代码": "SZ000001", "股票名称": "SomeOther",
             "最新价": 10.0, "涨跌额": 0.5, "涨跌幅": 5.0},
        ])
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_hot_rank_em.return_value = empty_rank
            mock_ak.stock_hot_rank_detail_em.return_value = _FAKE_HOT_RANK_DETAIL_DF

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "not found" in result.lower() or "top-100" in result.lower()
        assert "历史排名" in result  # detail still shows

    def test_detail_empty_still_shows_rank(self):
        """When the detail fetch fails, the rank table should still appear."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_hot_rank_em.return_value = _FAKE_HOT_RANK_DF
            mock_ak.stock_hot_rank_detail_em.return_value = pd.DataFrame()

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "贵州茅台" in result
        assert "历史排名" in result

    def test_exception_returns_placeholder(self):
        """Any exception should produce a graceful placeholder, not raise."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_hot_rank_em.side_effect = RuntimeError("API timeout")
            mock_ak.stock_hot_rank_detail_em.side_effect = RuntimeError("API timeout")

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "unavailable" in result.lower()
        assert "RuntimeError" in result


# ===================================================================
# Guba sentiment tests
# ===================================================================


class TestFetchEastmoneyGubaSentiment:
    """Tests for fetch_eastmoney_guba_sentiment()."""

    def test_non_a_share_returns_placeholder(self):
        """Non-A-share tickers should get a graceful placeholder."""
        result = fetch_eastmoney_guba_sentiment("AAPL")
        assert "non-A-share" in result.lower() or "unavailable" in result.lower()
        assert "AAPL" in result

    def test_happy_path(self):
        """All three Guba data sources should appear in the result."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_comment_em.return_value = _FAKE_COMMENT_DF
            mock_ak.stock_comment_detail_scrd_focus_em.return_value = _FAKE_FOCUS_DF
            mock_ak.stock_comment_detail_scrd_desire_em.return_value = _FAKE_DESIRE_DF

            result = fetch_eastmoney_guba_sentiment("600519.SS")

        assert "贵州茅台" in result
        assert "72.50" in result  # 综合得分
        assert "94.4" in result  # 用户关注指数
        assert "44.52" in result  # 参与意愿
        assert "股吧情绪" in result
        assert "综合评分" in result
        # Verify all three API calls
        mock_ak.stock_comment_em.assert_called_once()
        mock_ak.stock_comment_detail_scrd_focus_em.assert_called_once()
        mock_ak.stock_comment_detail_scrd_desire_em.assert_called_once()

    def test_comment_table_empty(self):
        """When the symbol is not in the 千股千评 dataset, show a placeholder."""
        empty_comment = pd.DataFrame([
            {"序号": 1, "代码": "000001", "名称": "OtherStock",
             "最新价": 10.0, "涨跌幅": 0.0, "换手率": 0.0, "市盈率": 0.0,
             "主力成本": 0.0, "机构参与度": 0.0, "综合得分": 0.0,
             "上升": 0, "目前排名": 0, "关注指数": 0.0, "交易日": "2026-07-09"},
        ])
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_comment_em.return_value = empty_comment
            mock_ak.stock_comment_detail_scrd_focus_em.return_value = _FAKE_FOCUS_DF
            mock_ak.stock_comment_detail_scrd_desire_em.return_value = _FAKE_DESIRE_DF

            result = fetch_eastmoney_guba_sentiment("600519.SS")

        assert "not found" in result.lower() or "千股千评" in result
        # Focus and desire data should still be present
        assert "用户关注指数" in result
        assert "参与意愿" in result

    def test_all_sources_exception(self):
        """When all sources raise, the output should contain placeholders."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_comment_em.side_effect = RuntimeError("timeout")
            mock_ak.stock_comment_detail_scrd_focus_em.side_effect = RuntimeError("timeout")
            mock_ak.stock_comment_detail_scrd_desire_em.side_effect = RuntimeError("timeout")

            result = fetch_eastmoney_guba_sentiment("600519.SS")

        assert "unavailable" in result.lower() or "RuntimeError" in result
        # Should still return the structure with placeholders
        assert "股吧情绪" in result

    def test_bj_stock_works(self):
        """北交所 stocks should also work."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_comment_em.return_value = _FAKE_COMMENT_DF
            mock_ak.stock_comment_detail_scrd_focus_em.return_value = _FAKE_FOCUS_DF
            mock_ak.stock_comment_detail_scrd_desire_em.return_value = _FAKE_DESIRE_DF

            result = fetch_eastmoney_guba_sentiment("830799.BJ")

        assert "股吧情绪" in result


# ===================================================================
# _safe_col helper tests
# ===================================================================


class TestSafeCol:
    """Tests for the internal _safe_col helper."""

    def test_returns_value_by_name(self):
        row = pd.Series({"名称": "贵州茅台", "综合得分": 72.50})
        result = _safe_col(row, ["名称", "综合得分"], "名称")
        assert result == "贵州茅台"

    def test_returns_value_by_fallback_index(self):
        row = pd.Series(["date", "rank", "code"])
        result = _safe_col(row, ["时间", "排名", "代码"], "排名", fallback_idx=1)
        assert result == "rank"

    def test_returns_na_for_missing(self):
        row = pd.Series({"a": 1})
        result = _safe_col(row, ["a"], "nonexistent")
        assert result == "N/A"

    def test_handles_nan(self):
        row = pd.Series({"val": float("nan")})
        result = _safe_col(row, ["val"], "val")
        assert result == "N/A"

    def test_handles_none(self):
        row = pd.Series({"val": None})
        result = _safe_col(row, ["val"], "val")
        assert result == "N/A"
