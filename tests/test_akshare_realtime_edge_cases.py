"""Edge-case tests for uncovered code paths in akshare_realtime.py.

Existing tests in test_akshare_realtime.py cover the happy path, non-A-share,
and XQ-less fallback. This file fills remaining gaps: ``_with_retry`` retry
behavior, both sources failing gracefully, alternate Xueqiu field names, and
already-populated company_name not being overwritten.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows.akshare_realtime import _with_retry, fetch_realtime_snapshot

# ---------------------------------------------------------------------------
# _with_retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWithRetry:
    def test_succeeds_on_first_attempt(self):
        fn = MagicMock(return_value=42)
        assert _with_retry(fn, attempts=3) == 42
        fn.assert_called_once()

    def test_retries_and_succeeds(self):
        fn = MagicMock(side_effect=[ConnectionError("try1"), ConnectionError("try2"), "ok"])
        assert _with_retry(fn, attempts=3) == "ok"
        assert fn.call_count == 3

    def test_exhausts_retries_returns_none(self):
        fn = MagicMock(side_effect=ConnectionError("always fails"))
        result = _with_retry(fn, attempts=2)
        assert result is None
        assert fn.call_count == 2


# ---------------------------------------------------------------------------
# fetch_realtime_snapshot: failure paths and edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRealtimeSnapshotEdgeCases:
    def test_both_sources_fail_returns_none(self):
        """When both Xueqiu and Eastmoney fail → returns None."""
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak:
            mock_ak.stock_individual_spot_xq.side_effect = ConnectionError("XQ timeout")
            mock_ak.stock_individual_info_em.side_effect = ConnectionError("EM timeout")
            snap = fetch_realtime_snapshot("600519.SS", xq_token="dummy")
        assert snap is None

    def test_xueqiu_returns_empty_df(self):
        """Xueqiu returns empty DataFrame → falls through to Eastmoney."""
        info_df = pd.DataFrame({
            "item": ["股票简称", "总市值"],
            "value": ["贵州茅台", 2_108_000_000_000.0],
        })
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak:
            mock_ak.stock_individual_spot_xq.return_value = pd.DataFrame()
            mock_ak.stock_individual_info_em.return_value = info_df
            snap = fetch_realtime_snapshot("600519.SS", xq_token="dummy")
        assert snap is not None
        assert snap["company_name"] == "贵州茅台"
        assert snap["sources"] == ["eastmoney_info"]

    def test_xueqiu_with_alternate_field_names(self):
        """Xueqiu uses '最新' instead of '现价' → still parsed."""
        spot_df = pd.DataFrame({
            "item": ["最新", "总市值", "市盈率(TTM)", "市净率"],
            "value": [100.5, 500_000_000_000.0, 20.0, 5.0],
        })
        info_df = pd.DataFrame({
            "item": ["股票简称", "总市值"],
            "value": ["五粮液", 500_000_000_000.0],
        })
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak:
            mock_ak.stock_individual_spot_xq.return_value = spot_df
            mock_ak.stock_individual_info_em.return_value = info_df
            snap = fetch_realtime_snapshot("000858.SZ", xq_token="dummy")
        assert snap["price"] == pytest.approx(100.5)
        assert snap["pe_ttm"] == pytest.approx(20.0)
        assert snap["pb"] == pytest.approx(5.0)

    def test_company_name_already_set_not_overwritten(self):
        """Company name from XQ is not overwritten by Eastmoney."""
        spot_df = pd.DataFrame({
            "item": ["名称", "现价"],
            "value": ["贵州茅台", 1680.5],
        })
        info_df = pd.DataFrame({
            "item": ["股票简称", "总市值"],
            "value": ["", 2_108_000_000_000.0],
        })
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak:
            mock_ak.stock_individual_spot_xq.return_value = spot_df
            mock_ak.stock_individual_info_em.return_value = info_df
            snap = fetch_realtime_snapshot("600519.SS", xq_token="dummy")
        assert snap["company_name"] == "贵州茅台"

    def test_xq_token_reads_from_env(self):
        """XUEQIU_TOKEN from env var is used when xq_token=None."""
        spot_df = pd.DataFrame({
            "item": ["名称", "现价"],
            "value": ["茅台", 1700.0],
        })
        info_df = pd.DataFrame({
            "item": ["股票简称"],
            "value": ["茅台"],
        })
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak, \
             patch.dict("os.environ", {"XUEQIU_TOKEN": "env_token"}, clear=True):
            mock_ak.stock_individual_spot_xq.return_value = spot_df
            mock_ak.stock_individual_info_em.return_value = info_df
            # Pass xq_token=None so it reads from env
            snap = fetch_realtime_snapshot("600519.SS", xq_token=None)
        assert snap is not None
        mock_ak.stock_individual_spot_xq.assert_called_once()

    def test_no_token_no_env_falls_to_eastmoney_only(self):
        """
        When no XQ token and no env var, skip Xueqiu entirely and use Eastmoney only.
        Covers partial branch 75->93 (if token: False).
        """
        info_df = pd.DataFrame({
            "item": ["股票简称", "总市值"],
            "value": ["贵州茅台", 2_108_000_000_000.0],
        })
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak, \
             patch.dict("os.environ", {}, clear=True):
            mock_ak.stock_individual_info_em.return_value = info_df
            snap = fetch_realtime_snapshot("600519.SS", xq_token=None)
        assert snap is not None
        assert snap["company_name"] == "贵州茅台"
        assert snap["market_cap_yi"] == pytest.approx(21080.0, rel=1e-3)
        assert snap["sources"] == ["eastmoney_info"]
        mock_ak.stock_individual_spot_xq.assert_not_called()
