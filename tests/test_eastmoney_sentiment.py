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

    @pytest.fixture(autouse=True)
    def _no_local_snapshot(self):
        """Force the online fallback path: real quant_core.db may hold a
        stock_hot_rank snapshot for the test tickers, which would otherwise
        short-circuit these akshare-mock tests."""
        with patch(
            "tradingagents.dataflows.eastmoney_sentiment._hot_rank_table_from_local",
            return_value=None,
        ):
            yield

    def test_non_a_share_returns_placeholder(self):
        """Non-A-share tickers should get a graceful placeholder."""
        result = fetch_eastmoney_hot_rank("AAPL")
        assert "non-a-share" in result.lower()
        assert "AAPL" in result

    def test_empty_ticker_returns_placeholder(self):
        """Empty string is not an A-share ticker."""
        result = fetch_eastmoney_hot_rank("")
        assert "non-A-share" in result.lower() or "unavailable" in result.lower()

    def test_local_snapshot_preferred_over_online_fetch(self):
        """When quant_core.db has a stock_hot_rank snapshot, the online
        emappdata fetch (stock_hot_rank_em) must not be called at all."""
        local_line = "整体人气排名: #5  |  贵州茅台(600519)  (快照日测试)"
        with (
            patch(
                "tradingagents.dataflows.eastmoney_sentiment._hot_rank_table_from_local",
                return_value=local_line,
            ) as mock_local,
            patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak,
        ):
            mock_ak.stock_hot_rank_detail_em.return_value = _FAKE_HOT_RANK_DETAIL_DF

            result = fetch_eastmoney_hot_rank("600519.SS")

        mock_local.assert_called_once_with("600519.SS", "600519")
        mock_ak.stock_hot_rank_em.assert_not_called()
        assert local_line in result

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
        """Not in the top-100 hot list → explicit negative signal (not a
        degraded placeholder, so the route status stays ok)."""
        empty_rank = pd.DataFrame([
            {"当前排名": 1, "代码": "SZ000001", "股票名称": "SomeOther",
             "最新价": 10.0, "涨跌额": 0.5, "涨跌幅": 5.0},
        ])
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_hot_rank_em.return_value = empty_rank
            mock_ak.stock_hot_rank_detail_em.return_value = _FAKE_HOT_RANK_DETAIL_DF

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "未进入 top-100" in result
        assert "阴性" in result
        assert "not found" not in result.lower()  # 不再触发路由层 partial 判定
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
        """Any exception should produce a graceful placeholder, not raise —
        and only when the hithink substitute also misses."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak, \
             patch(
                 "tradingagents.dataflows.eastmoney_sentiment._hithink_hot_rank_line",
                 return_value=None,
             ):
            mock_ak.stock_hot_rank_em.side_effect = RuntimeError("API timeout")
            mock_ak.stock_hot_rank_detail_em.side_effect = RuntimeError("API timeout")

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "unavailable" in result.lower()
        assert "RuntimeError" in result

    def test_hithink_substitute_fills_rank_line(self):
        """Eastmoney table down + ticker in hithink Top30 → hithink line
        replaces the placeholder (no 'unavailable' marker, no partial)."""
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak, \
             patch(
                 "tradingagents.dataflows.eastmoney_sentiment._hithink_hot_rank_line",
                 return_value="同花顺热榜 — 600519.SS (source: hithink hot-stock-list, 当日Top30)\n整体热度排名: #3",
             ):
            mock_ak.stock_hot_rank_em.side_effect = ConnectionError("refused")
            mock_ak.stock_hot_rank_detail_em.return_value = _FAKE_HOT_RANK_DETAIL_DF

            result = fetch_eastmoney_hot_rank("600519.SS")

        assert "同花顺热榜" in result
        assert "整体热度排名: #3" in result
        assert "hot-rank table unavailable" not in result
        assert "历史排名" in result  # detail still shows

    def test_hot_rank_uses_max_retries_3(self):
        """_akshare_retry should be called with max_retries=3 for hot-rank calls."""
        with patch(
            "tradingagents.dataflows.eastmoney_sentiment._akshare_retry"
        ) as mock_retry, patch(
            "tradingagents.dataflows.eastmoney_sentiment._hithink_hot_rank_line",
            return_value=None,
        ):
            mock_retry.side_effect = [None, _FAKE_HOT_RANK_DETAIL_DF]
            fetch_eastmoney_hot_rank("600519.SS")
            for call in mock_retry.call_args_list:
                assert call.kwargs.get("max_retries") == 3, (
                    f"Expected max_retries=3, got {call.kwargs.get('max_retries')}"
                )

    def test_hot_rank_detail_returns_latest_dates(self):
        """Detail DataFrame is sorted oldest-first by akshare; the function
        must return the MOST RECENT rows (tail), not the oldest (head).
        Regression test for: .head(limit) returning 2025-07 data in 2026-07 reports."""
        # Construct a detail DataFrame sorted oldest-first (as akshare returns it).
        # Oldest: 2025-07-16, newest: 2026-07-16.
        detail_df = pd.DataFrame([
            {"时间": "2025-07-16", "排名": 100, "证券代码": "SZ002179",
             "新晋粉丝": 0.1, "铁杆粉丝": 0.9},
            {"时间": "2025-07-17", "排名": 95, "证券代码": "SZ002179",
             "新晋粉丝": 0.2, "铁杆粉丝": 0.8},
            {"时间": "2026-07-15", "排名": 20, "证券代码": "SZ002179",
             "新晋粉丝": 0.7, "铁杆粉丝": 0.3},
            {"时间": "2026-07-16", "排名": 15, "证券代码": "SZ002179",
             "新晋粉丝": 0.8, "铁杆粉丝": 0.2},
        ])
        with patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak:
            mock_ak.stock_hot_rank_em.return_value = pd.DataFrame()  # rank table empty
            mock_ak.stock_hot_rank_detail_em.return_value = detail_df

            # limit=1 → should return only the newest row (2026-07-16)
            result = fetch_eastmoney_hot_rank("002179.SZ", limit=1)

        # The newest date MUST appear; the oldest MUST NOT.
        assert "2026-07-16" in result, (
            f"Expected newest date 2026-07-16 in result, got:\n{result}"
        )
        assert "2025-07-16" not in result, (
            f"Oldest date 2025-07-16 should not appear when limit=1, got:\n{result}"
        )


# ===================================================================
# _hot_rank_table_from_local helper tests
# ===================================================================


class TestHotRankTableFromLocal:
    """Unit tests for the local-snapshot helper (smartmoney_vendor mocked)."""

    _VENDOR = "tradingagents.dataflows.smartmoney_vendor"
    _P_SNAPSHOT = _VENDOR + ".get_stock_hot_rank_snapshot_date"
    _P_GET = _VENDOR + ".get_stock_hot_rank"

    def test_no_snapshot_returns_none(self):
        from tradingagents.dataflows.eastmoney_sentiment import (
            _hot_rank_table_from_local,
        )

        with patch(self._P_SNAPSHOT, return_value=None) as mock_snap:
            assert _hot_rank_table_from_local("600519.SS", "600519") is None
        mock_snap.assert_called_once_with()

    def test_symbol_in_snapshot_returns_rank_line(self):
        from tradingagents.dataflows.eastmoney_sentiment import (
            _hot_rank_table_from_local,
        )
        from tradingagents.dataflows.errors import NoMarketDataError

        with (
            patch(self._P_SNAPSHOT, return_value="2026-09-23"),
            patch(
                self._P_GET,
                side_effect=NoMarketDataError(
                    "stock_hot_rank", detail="not in top-100"
                ),
            ),
        ):
            line = _hot_rank_table_from_local("600519.SS", "600519")
        assert line is not None
        assert "未进入 top-100" in line
        assert "2026-09-23" in line

    def test_symbol_not_in_snapshot_is_negative_signal(self):
        from tradingagents.dataflows.eastmoney_sentiment import (
            _hot_rank_table_from_local,
        )

        row = {
            "trade_date": "2026-09-23", "code": "600519", "name": "贵州茅台",
            "rank": 5, "rank_change": -1.0, "prev_rank": 4,
            "close_price": 1500.0, "change_pct": 0.5,
        }
        with (
            patch(self._P_SNAPSHOT, return_value="2026-09-23"),
            patch(self._P_GET, return_value=row),
        ):
            line = _hot_rank_table_from_local("600519.SS", "600519")
        assert line is not None
        assert "#5" in line
        assert "贵州茅台" in line
        assert "quant_core.db" in line


# ===================================================================
# Guba sentiment tests
# ===================================================================


class TestFetchEastmoneyGubaSentiment:
    """Tests for fetch_eastmoney_guba_sentiment()."""

    @pytest.fixture(autouse=True)
    def _no_local_snapshot(self):
        """Force the online fallback path: real quant_core.db may hold a
        stock_comment snapshot for the test tickers, which would otherwise
        short-circuit these akshare-mock tests."""
        with patch(
            "tradingagents.dataflows.eastmoney_sentiment._stock_comment_from_local",
            return_value=None,
        ):
            yield

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

    def test_guba_uses_max_retries_3(self):
        """_akshare_retry should be called with max_retries=3 for all guba sentiment calls."""
        with patch(
            "tradingagents.dataflows.eastmoney_sentiment._akshare_retry"
        ) as mock_retry:
            mock_retry.return_value = pd.DataFrame()
            fetch_eastmoney_guba_sentiment("600519.SS")
            assert len(mock_retry.call_args_list) >= 3
            for call in mock_retry.call_args_list:
                assert call.kwargs.get("max_retries") == 3, (
                    f"Expected max_retries=3, got {call.kwargs.get('max_retries')}"
                )

    def test_local_snapshot_preferred_over_online_fetch(self):
        """When quant_core.db has a stock_comment snapshot, the online
        whole-table fetch (stock_comment_em) must not be called at all."""
        local_block = "综合评分 — 贵州茅台(600519)\n  综合得分: 68.19/100 (快照)"
        with (
            patch(
                "tradingagents.dataflows.eastmoney_sentiment._stock_comment_from_local",
                return_value=local_block,
            ) as mock_local,
            patch("tradingagents.dataflows.eastmoney_sentiment.ak") as mock_ak,
        ):
            mock_ak.stock_comment_detail_scrd_focus_em.return_value = _FAKE_FOCUS_DF
            mock_ak.stock_comment_detail_scrd_desire_em.return_value = _FAKE_DESIRE_DF

            result = fetch_eastmoney_guba_sentiment("600519.SS")

        mock_local.assert_called_once_with("600519")
        mock_ak.stock_comment_em.assert_not_called()
        assert local_block in result

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

    def test_out_of_bounds_index_caught_by_except(self):
        """IndexError from row.iloc[idx] when idx >= len(row) should be caught and return N/A."""
        row = pd.Series(["a"])  # Only 1 element
        cols = ["col1", "col2"]  # "col2" is at index 1, but row has only 1 element → IndexError
        result = _safe_col(row, cols, "col2")
        assert result == "N/A"

    def test_invalid_row_type_caught_by_except(self):
        """When row doesn't support .iloc (e.g. a bare list), AttributeError is caught."""
        row = ["a", "b"]  # bare list, no .iloc
        cols = ["col1", "col2"]
        result = _safe_col(row, cols, "col2", fallback_idx=1)
        assert result == "N/A"

    def test_elif_exception_caught_via_fallback(self):
        """Exception in the elif branch (fallback_idx) should also be caught."""
        row = ["a", "b"]  # bare list, no .iloc
        cols = ["col1"]  # Only one column; "nonexistent" not in cols
        # name not in cols → enters elif with fallback_idx → row.iloc fails
        result = _safe_col(row, cols, "nonexistent", fallback_idx=1)
        assert result == "N/A"
