"""P1-1/2/7/10/12 regression tests.

These tests encode the new behaviors before the implementation changes:
- vendors raise typed exceptions instead of returning prose strings,
- the router falls back across the configured chain,
- A-share vendor order respects explicit user configuration,
- sector/global-news routing is correct,
- report-quality validation recognises both no-data sentinels,
- route_to_vendor is decomposed into helpers.
"""
from __future__ import annotations

import copy
import unittest
from unittest import mock

import pandas as pd
import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorRateLimitError
from tradingagents.graph.analyst_execution import validate_report_quality


def _reset_config():
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.mark.unit
class TestVendorExceptionDrivenFallback:
    """a. yfinance rate-limit must trigger fallback to alpha_vantage."""

    def setup_method(self):
        _reset_config()

    def teardown_method(self):
        _reset_config()

    def test_yfinance_rate_limit_falls_back_to_alpha_vantage(self):
        set_config({"data_vendors": {"core_stock_apis": "yfinance,alpha_vantage"}})

        def yf_rate_limit(*a, **k):
            raise VendorRateLimitError("Yahoo Finance throttled")

        av = mock.Mock(return_value="ALPHA_VANTAGE_DATA")
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_stock_data": {"yfinance": yf_rate_limit, "alpha_vantage": av}},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "AAPL", "2026-01-01", "2026-01-10"
            )
        assert result == "ALPHA_VANTAGE_DATA"
        av.assert_called_once()


@pytest.mark.unit
class TestAkshareRaisesOnNoData:
    """b. akshare functions raise NoMarketDataError instead of prose strings."""

    def test_get_stock_data_empty_raises(self):
        from tradingagents.dataflows import akshare_vendor

        with mock.patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            with pytest.raises(NoMarketDataError, match="600519.SS"):
                akshare_vendor.get_stock_data("600519.SS", "2026-05-10", "2026-05-14")

    def test_get_fundamentals_empty_raises(self):
        from tradingagents.dataflows import akshare_vendor

        with mock.patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
            mock_ak.stock_yjbb_em.return_value = pd.DataFrame()
            with pytest.raises(NoMarketDataError, match="600519.SS"):
                akshare_vendor.get_fundamentals("600519.SS", "2026-05-14")


@pytest.mark.unit
class TestSectorFundFlowRouting:
    """c. get_sector_fund_flow('白酒') succeeds through akshare."""

    def setup_method(self):
        _reset_config()

    def teardown_method(self):
        _reset_config()

    def test_sector_fund_flow_baijiu_via_akshare(self):
        set_config({"data_vendors": {"technical_indicators": "akshare"}})
        fake = mock.Mock(return_value="## 白酒 Sector Fund Flow ...")
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_sector_fund_flow": {"akshare": fake}},
            clear=False,
        ):
            result = interface.route_to_vendor("get_sector_fund_flow", "白酒")
        assert "Sector Fund Flow" in result or "板块资金流" in result
        fake.assert_called_once_with("白酒")


@pytest.mark.unit
class TestGlobalNewsNoAkshare:
    """d. get_global_news must not route through akshare."""

    def setup_method(self):
        _reset_config()

    def teardown_method(self):
        _reset_config()

    def test_global_news_uses_yfinance_not_akshare(self):
        fake_yf = mock.Mock(return_value="## Global Market News ...")
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_global_news": {"yfinance": fake_yf}},
            clear=False,
        ):
            result = interface.route_to_vendor("get_global_news", "2026-05-14")
        assert "Global" in result or "global news" in result.lower()
        assert "akshare" not in result.lower()
        fake_yf.assert_called_once()

    def test_global_news_yfinance_failure_no_akshare_fallback(self):
        set_config({"data_vendors": {"news_data": "yfinance"}})

        def yf_raises(*a, **k):
            raise ConnectionError("yahoo down")

        with (
            mock.patch.dict(
                interface.VENDOR_METHODS,
                {"get_global_news": {"yfinance": yf_raises}},
                clear=False,
            ),
            pytest.raises(ConnectionError, match="yahoo down"),
        ):
            interface.route_to_vendor("get_global_news", "2026-05-14")


@pytest.mark.unit
class TestAshareExplicitOrderRespected:
    """e. Explicit 'akshare,smartmoney_db' must not be reordered."""

    def setup_method(self):
        _reset_config()

    def teardown_method(self):
        _reset_config()

    def test_explicit_akshare_smartmoney_order_unchanged(self):
        with mock.patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="akshare,smartmoney_db",
        ):
            fake_ak = mock.Mock(return_value="AKSHARE_DATA")
            fake_sm = mock.Mock(return_value="SMARTMONEY_DATA")
            with mock.patch.dict(
                interface.VENDOR_METHODS,
                {
                    "get_fundamentals": {
                        "akshare": fake_ak,
                        "smartmoney_db": fake_sm,
                        "yfinance": mock.Mock(return_value="YF_DATA"),
                    }
                },
                clear=False,
            ):
                result = interface.route_to_vendor(
                    "get_fundamentals", "600519.SS", "2026-05-14"
                )
        assert result == "AKSHARE_DATA"
        fake_ak.assert_called_once()
        fake_sm.assert_not_called()


@pytest.mark.unit
class TestDataUnavailableSentinel:
    """f. validate_report_quality treats both sentinels as unreliable."""

    def test_no_data_available_is_unreliable(self):
        text = "Some words here. NO_DATA_AVAILABLE for this symbol. " * 5
        assert validate_report_quality("market_report", text) == "no_data"

    def test_data_unavailable_is_unreliable(self):
        text = (
            "The macro indicator returned DATA_UNAVAILABLE: not available. " * 5
        )
        assert validate_report_quality("news_report", text) == "no_data"


@pytest.mark.unit
class TestRouteToVendorHelpers(unittest.TestCase):
    """g. route_to_vendor is split into testable helpers."""

    def test_build_vendor_chain_default_enables_ashare_local_first(self):
        # default config for A-share ticker should put smartmoney_db first.
        chain = interface._build_vendor_chain(
            "get_fundamentals", "default", "600519.SS"
        )
        assert chain[0] == "smartmoney_db"
        assert chain[1] == "akshare"

    def test_build_vendor_chain_explicit_respects_order(self):
        chain = interface._build_vendor_chain(
            "get_fundamentals", "akshare,smartmoney_db", "600519.SS"
        )
        assert chain == ["akshare", "smartmoney_db"]

    def test_should_skip_ashare_filter_for_sector_and_global(self):
        assert interface._should_skip_ashare_filter("technical_indicators", "get_sector_fund_flow") is True
        assert interface._should_skip_ashare_filter("news_data", "get_global_news") is True
        assert interface._should_skip_ashare_filter("news_data", "get_news") is False

    def test_format_no_data_sentinel_contains_reason_and_chain(self):
        err = NoMarketDataError("600519.SS", "600519.SS", "empty dataframe")
        sentinel = interface._format_no_data_sentinel(
            err, ["smartmoney_db", "akshare", "yfinance"], "get_fundamentals"
        )
        assert "NO_DATA_AVAILABLE" in sentinel
        assert "600519.SS" in sentinel
        assert "empty dataframe" in sentinel
        assert "smartmoney_db" in sentinel

    def test_format_optional_unavailable_contains_category(self):
        sentinel = interface._format_optional_unavailable(
            "macro_data", ValueError("boom"), "get_macro_indicators"
        )
        assert "DATA_UNAVAILABLE" in sentinel
        assert "macro_data" in sentinel
        assert "boom" in sentinel
