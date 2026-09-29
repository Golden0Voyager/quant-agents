import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.runtime_context import (
    RuntimeDataContext,
    use_runtime_data_context,
)
from tradingagents.dataflows.symbol_utils import NoMarketDataError
from tradingagents.market_context import AnalysisDates


@pytest.mark.unit
class TestRouteToVendor:
    def test_a_share_prefers_akshare(self):
        """A-share tickers (.SS/.SZ/.BJ) route to smartmoney_db first, then akshare."""
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(
            side_effect=NoMarketDataError("600519.SS", "600519.SS", "Not in local DB")
        )
        fake_ak = MagicMock(return_value="AKSHARE_RESULT")
        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak, "yfinance": fake_yf},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "600519.SS", "2026-05-14"
            )
        assert result == "AKSHARE_RESULT"
        fake_sm.assert_called_once_with("600519.SS", "2026-05-14")
        fake_ak.assert_called_once_with("600519.SS", "2026-05-14")
        fake_yf.assert_not_called()

    def test_us_share_keeps_yfinance(self):
        """Non-A-share ticker skips A-share-only vendors (akshare, smartmoney_db)."""
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.akshare_common import AShareSymbolError

        fake_ak = MagicMock(side_effect=AShareSymbolError("Not an A-share ticker"))
        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"akshare": fake_ak, "yfinance": fake_yf},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "AAPL", "2026-05-14"
            )
        assert result == "YFINANCE_RESULT"
        fake_ak.assert_not_called()
        fake_yf.assert_called_once_with("AAPL", "2026-05-14")

    def test_route_to_vendor_with_source_reports_selected_vendor(self):
        from tradingagents.dataflows import interface

        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"yfinance": fake_yf},
            clear=True,
        ), patch.object(interface, "get_vendor", return_value="yfinance"):
            result = interface.route_to_vendor_with_source(
                "get_fundamentals", "AAPL", "2026-05-14"
            )

        assert result.data == "YFINANCE_RESULT"
        assert result.vendor == "yfinance"

    def test_a_share_falls_back_to_yfinance_on_rate_limit(self):
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.alpha_vantage_common import (
            AlphaVantageRateLimitError,
        )

        def ak_raises(*a, **kw):
            raise AlphaVantageRateLimitError("simulated akshare unavailability")

        fake_sm = MagicMock(
            side_effect=NoMarketDataError("600519.SS", "600519.SS", "Not in local DB")
        )
        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"smartmoney_db": fake_sm, "akshare": ak_raises, "yfinance": fake_yf},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "600519.SS", "2026-05-14"
            )
        assert result == "YFINANCE_RESULT"

    def test_a_share_falls_back_to_yfinance_on_connection_error(self):
        """Any exception from a vendor should trigger fallback, not just rate limits."""
        from tradingagents.dataflows import interface

        def ak_raises(*a, **kw):
            raise ConnectionError("simulated network failure")

        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_indicators"],
            {"akshare": ak_raises, "yfinance": fake_yf},
            clear=True,
        ):
            result = interface.route_to_vendor(
                "get_indicators", "600519.SS", "rsi", "2026-05-14", 30
            )
        assert result == "YFINANCE_RESULT"
        fake_yf.assert_called_once_with("600519.SS", "rsi", "2026-05-14", 30)

    def test_disable_yfinance_fallback_skips_yfinance_for_ashare(self):
        """With DISABLE_YFINANCE_FALLBACK=1, A-share ticker skips yfinance entirely."""
        import os

        from tradingagents.dataflows import interface

        fake_sm = MagicMock(
            side_effect=NoMarketDataError("000001.SZ", "000001.SZ", "Not in local DB")
        )
        fake_ak = MagicMock(
            side_effect=NoMarketDataError("000001.SZ", "000001.SZ", "No akshare data")
        )
        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak, "yfinance": fake_yf},
            clear=False,
        ), patch.dict(os.environ, {"DISABLE_YFINANCE_FALLBACK": "1"}):
            result = interface.route_to_vendor(
                "get_fundamentals", "000001.SZ", "2026-05-14"
            )
        assert "NO_DATA_AVAILABLE" in result
        assert "000001.SZ" in result
        fake_sm.assert_called_once_with("000001.SZ", "2026-05-14")
        fake_ak.assert_called_once_with("000001.SZ", "2026-05-14")
        fake_yf.assert_not_called()

    def test_all_vendors_fail_raises_first_error(self):
        """When every vendor raises, route_to_vendor should raise the first error."""
        from tradingagents.dataflows import interface

        def ak_raises(*a, **kw):
            raise ConnectionError("akshare down")

        def yf_raises(*a, **kw):
            raise TimeoutError("yfinance down")

        def av_raises(*a, **kw):
            raise RuntimeError("alpha_vantage down")

        with patch.dict(
            interface.VENDOR_METHODS["get_indicators"],
            {"akshare": ak_raises, "yfinance": yf_raises, "alpha_vantage": av_raises},
            clear=True,
        ), pytest.raises(ConnectionError, match="akshare down"):
            interface.route_to_vendor(
                "get_indicators", "600519.SS", "rsi", "2026-05-14", 30
            )

    def test_exhausted_rate_limit_preserves_typed_error(self):
        """An exhausted rate-limited chain must not become a generic RuntimeError."""
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.errors import VendorRateLimitError

        limited = MagicMock(
            side_effect=VendorRateLimitError("Yahoo Finance rate-limited for NVDA")
        )
        with patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"yfinance": limited},
            clear=True,
        ), patch.object(interface, "get_vendor", return_value="yfinance"), pytest.raises(
            VendorRateLimitError, match="rate-limited"
        ):
            interface.route_to_vendor(
                "get_news", "NVDA", "2026-01-08", "2026-01-15"
            )

    def test_get_indicators_intercepts_get_fund_flow(self):
        """get_indicators tool should intercept get_fund_flow and return a clear redirection message."""
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators

        result = get_indicators.invoke({
            "symbol": "300454.SZ",
            "indicator": "get_fund_flow",
            "curr_date": "2026-05-14",
            "look_back_days": 30
        })
        assert "standalone tool" in result
        assert "get_fund_flow" in result


@pytest.mark.unit
class TestGetCategoryForMethod:
    def test_unknown_method_raises(self):
        """get_category_for_method should raise ValueError for unrecognised method names."""
        from tradingagents.dataflows import interface

        with pytest.raises(ValueError, match="not found in any category"):
            interface.get_category_for_method("nonexistent_method")

    def test_all_vendor_methods_have_category(self):
        """Every method in VENDOR_METHODS must belong to a TOOLS_CATEGORIES category."""
        from tradingagents.dataflows import interface

        all_categorized = {
            tool
            for cat in interface.TOOLS_CATEGORIES.values()
            for tool in cat["tools"]
        }
        missing = [m for m in interface.VENDOR_METHODS if m not in all_categorized]
        assert not missing, (
            f"Methods without category: {missing}. "
            f"Add them to TOOLS_CATEGORIES in interface.py"
        )

    def test_newly_added_tools_map_correctly(self):
        """Verify the 5 previously-missing tools now have correct categories."""
        from tradingagents.dataflows import interface

        assert interface.get_category_for_method("get_company_announcements") == "governance_risk"
        assert interface.get_category_for_method("get_margin_trading") == "governance_risk"
        assert interface.get_category_for_method("get_dragon_tiger") == "governance_risk"
        assert interface.get_category_for_method("get_block_trade") == "governance_risk"
        assert interface.get_category_for_method("get_shareholder_count") == "fundamental_data"


@pytest.mark.unit
class TestGetVendor:
    def test_tool_level_config_takes_precedence(self):
        """Tool-level config (tool_vendors) overrides category-level."""
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "tool_vendors": {"get_fundamentals": "alpha_vantage"},
                "data_vendors": {"fundamental_data": "yfinance"},
            }
            result = interface.get_vendor("fundamental_data", "get_fundamentals")
        assert result == "alpha_vantage"

    def test_category_level_fallback_when_no_tool_config(self):
        """Without tool-level config, falls back to category-level data_vendors."""
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "tool_vendors": {},
                "data_vendors": {"fundamental_data": "smartmoney_db"},
            }
            result = interface.get_vendor("fundamental_data", "get_fundamentals")
        assert result == "smartmoney_db"


@pytest.mark.unit
class TestRouteToVendorEdgeCases:
    def test_unsupported_method_raises(self):
        """route_to_vendor should raise ValueError for methods not in VENDOR_METHODS.

        Uses clear=True on top-level VENDOR_METHODS so the key is truly absent,
        while get_category_for_method (which searches TOOLS_CATEGORIES) still
        succeeds. This fires the ValueError at line 331.
        """
        from tradingagents.dataflows import interface

        with patch.dict(interface.VENDOR_METHODS, {}, clear=True), \
             pytest.raises(ValueError, match="not supported"):
            interface.route_to_vendor("get_pledge_ratio", "600519.SS")

    def test_a_share_without_smartmoney_db_in_config(self):
        """
        A-share ticker with 'default' config (no smartmoney_db) routes to akshare.
        Covers the else branch of the smartmoney_db check.
        """
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(
            side_effect=NoMarketDataError("600519.SS", "600519.SS", "Not in local DB")
        )
        fake_ak = MagicMock(return_value="AKSHARE_RESULT")
        # get_pledge_ratio belongs to governance_risk which has NO entry in
        # default data_vendors → get_vendor returns "default" → primary_vendors = ["default"]
        # → smartmoney_db NOT in primary_vendors → else branch (line 348)
        with patch.dict(
            interface.VENDOR_METHODS["get_pledge_ratio"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor("get_pledge_ratio", "600519.SS")
        assert result == "AKSHARE_RESULT"
        fake_ak.assert_called_once_with("600519.SS")

    def test_configured_vendor_not_available(self):
        """
        When configured vendor is not in VENDOR_METHODS, raise ValueError.
        Covers lines 361-364.

        Uses clear=True on VENDOR_METHODS to remove akshare, preventing the
        A-share routing code from adding it to the vendor chain.
        """
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg, \
             patch.dict(interface.VENDOR_METHODS["get_fundamentals"], {}, clear=True):
            mock_cfg.return_value = {
                "data_vendors": {"fundamental_data": "nonexistent_vendor"},
                "tool_vendors": {},
            }
            with pytest.raises(ValueError, match="not available"):
                interface.route_to_vendor("get_fundamentals", "600519.SS")

    def test_default_config_uses_all_available_vendors(self):
        """
        When no explicit vendor is configured ("default"), all available
        vendors are tried. Covers lines 365-366 (else branch).
        """
        from tradingagents.dataflows import interface

        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg, \
             patch.dict(interface.VENDOR_METHODS["get_fundamentals"], {"yfinance": fake_yf}, clear=True):
            mock_cfg.return_value = {
                "data_vendors": {},
                "tool_vendors": {},
            }
            result = interface.route_to_vendor("get_fundamentals", "AAPL")
        assert result == "YFINANCE_RESULT"
        fake_yf.assert_called_once_with("AAPL")

    def test_registered_method_returns_not_applicable_before_vendor_filtering(self):
        """
        A policy-excluded market returns the stronger not-applicable sentinel.
        """
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "data_vendors": {"governance_risk": "akshare"},
                "tool_vendors": {},
            }
            result = interface.route_to_vendor("get_pledge_ratio", "AAPL")
        assert "DATA_NOT_APPLICABLE" in result
        assert "XNYS" in result

    def test_vendor_not_configured_falls_back_to_next_vendor(self):
        """
        VendorNotConfiguredError triggers fallback to the next vendor.
        Covers lines 420-423.
        """
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.errors import VendorNotConfiguredError

        not_configured = MagicMock(
            side_effect=VendorNotConfiguredError("not configured")
        )
        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"smartmoney_db": not_configured, "akshare": not_configured, "yfinance": fake_yf},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "600519.SS", "2026-05-14"
            )
        assert result == "YFINANCE_RESULT"
        fake_yf.assert_called_once_with("600519.SS", "2026-05-14")

    def test_no_data_with_first_error_logs_and_returns_sentinel(self):
        """
        When a generic error precedes NoMarketDataError, logs a warning and
        returns NO_DATA_AVAILABLE sentinel. Covers lines 451-465.

        All three vendors must raise (first_error + NoMarketDataError), so the
        loop exhausts without a successful return.
        """
        from tradingagents.dataflows import interface

        def first_raises(*a, **kw):
            raise ConnectionError("primary vendor network error")

        def nodata(*a, **kw):
            raise NoMarketDataError("600519.SS", "600519.SS", "Not in local DB")

        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"smartmoney_db": first_raises, "akshare": nodata, "yfinance": nodata},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "600519.SS", "2026-05-14"
            )
        assert "NO_DATA_AVAILABLE" in result
        assert "600519.SS" in result

    def test_optional_category_returns_sentinel_on_all_fail(self):
        """
        Optional categories (macro_data, prediction_markets) return a
        graceful DATA_UNAVAILABLE sentinel instead of raising.
        Covers lines 479-480.
        """
        from tradingagents.dataflows import interface

        def fred_raises(*a, **kw):
            raise ConnectionError("fred service down")

        with patch.dict(
            interface.VENDOR_METHODS["get_macro_indicators"],
            {"fred": fred_raises},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_macro_indicators", "AAPL", curr_date="2026-05-14"
            )
        # akshare is tried first (macro_data bypasses A-share filtering) and
        # raises NoMarketDataError for "AAPL" (not a valid China macro indicator).
        # Since NoMarketDataError takes precedence over ConnectionError in the
        # error handler, the sentinel is NO_DATA_AVAILABLE, not DATA_UNAVAILABLE.
        assert "NO_DATA_AVAILABLE" in result

    def test_empty_registered_vendor_chain_returns_unavailable(self):
        """
        A registered method with no policy-allowed source degrades explicitly.
        """
        from tradingagents.dataflows import interface

        with patch.dict(interface.VENDOR_METHODS, {"get_pledge_ratio": {}}, clear=False):
            result = interface.route_to_vendor_with_source(
                "get_pledge_ratio", "600519.SS"
            )

        assert result.diagnostic is not None
        assert result.diagnostic.status == "unavailable"
        assert "DATA_UNAVAILABLE" in result.data


# ===========================================================================
# route_to_vendor NO_DATA_AVAILABLE sentinel behavior.
# Merged from tests/test_no_data_handling.py::TestRouteToVendorSentinel.
# ===========================================================================


@pytest.mark.unit
class TestRouteToVendorSentinel(unittest.TestCase):
    def test_no_data_from_all_vendors_returns_sentinel(self):
        from tradingagents.dataflows import interface

        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, "GC=F", "no rows")

        patched = {"yfinance": raises_no_data, "alpha_vantage": raises_no_data}
        with patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "XAUUSD+", "2026-01-01", "2026-01-10"
            )
        self.assertIn("NO_DATA_AVAILABLE", result)
        self.assertIn("XAUUSD+", result)
        self.assertIn("GC=F", result)
        self.assertIn("Do not estimate", result)

    def test_unconfigured_fallback_does_not_mask_no_data(self):
        # When the primary vendor reports no data and the fallback is simply
        # unavailable (e.g. missing API key -> raises), the no-data sentinel
        # must win rather than the fallback's incidental error crashing out.
        from tradingagents.dataflows import interface

        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, symbol, "no rows")

        def raises_unavailable(symbol, *a, **k):
            raise ValueError("ALPHA_VANTAGE_API_KEY environment variable is not set.")

        patched = {"yfinance": raises_no_data, "alpha_vantage": raises_unavailable}
        with patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "FAKE", "2026-01-01", "2026-01-10"
            )
        self.assertIn("NO_DATA_AVAILABLE", result)

    def test_margin_trading_empty_degrades_to_sentinel(self):
        """Regression (2026-09-28 batch, 605299.SS): smartmoney had no local
        margin rows and the online fallback errored too. Before the fix
        smartmoney raised a bare RuntimeError, which escaped the router
        (governance_risk is not optional) and aborted the whole ticker run.
        The empty result must degrade to a NO_DATA_AVAILABLE sentinel."""
        from tradingagents.dataflows import interface

        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, symbol, "no margin rows")

        def raises_generic(symbol, *a, **k):
            raise RuntimeError("akshare online fetch failed")

        patched = {"smartmoney_db": raises_no_data, "akshare": raises_generic}
        with patch.dict(
            interface.VENDOR_METHODS, {"get_margin_trading": patched}, clear=False
        ):
            result = interface.route_to_vendor("get_margin_trading", "605299.SS")
        self.assertIn("NO_DATA_AVAILABLE", result)

    def test_hk_ticker_falls_through_to_akshare_hk(self):
        """Regression (2026-09-28 batch, 1810.HK): local archive has no HK
        rows and yfinance's cookie/crumb handshake is unreachable from this
        network, so *.HK tickers must survive to the akshare_hk tier."""
        from tradingagents.dataflows import interface

        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, symbol, "no rows")

        def serves_hk(symbol, start_date, end_date):
            return "HK daily bars served"

        patched = {
            "quant_db_global": raises_no_data,
            "yfinance": raises_no_data,
            "akshare_hk": serves_hk,
        }
        with patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "1810.HK", "2026-09-20", "2026-09-28"
            )
        self.assertEqual(result, "HK daily bars served")


# ===========================================================================
# Regression: every tool bound by analysts must route through a category.
# ===========================================================================


@pytest.mark.unit
class TestAnalystBoundToolsHaveCategories:
    """Every tool imported and used by an analyst must have a TOOLS_CATEGORIES entry."""

    def test_governance_analyst_tools_all_categorized(self):
        from tradingagents.agents.utils.agent_utils import (
            get_company_announcements,
            get_dragon_tiger,
            get_insider_transactions,
            get_institutional_holdings,
            get_margin_trading,
            get_news,
            get_northbound_hold,
            get_pledge_ratio,
            get_restricted_release,
        )
        from tradingagents.dataflows import interface

        for tool_fn in [
            get_company_announcements,
            get_insider_transactions,
            get_news,
            get_restricted_release,
            get_institutional_holdings,
            get_northbound_hold,
            get_margin_trading,
            get_pledge_ratio,
            get_dragon_tiger,
        ]:
            cat = interface.get_category_for_method(tool_fn.name)
            assert cat is not None

    def test_fundamentals_analyst_tools_all_categorized(self):
        from tradingagents.agents.utils.agent_utils import (
            get_balance_sheet,
            get_cashflow,
            get_dividend_history,
            get_earnings_estimates,
            get_fundamentals,
            get_income_statement,
            get_shareholder_count,
        )
        from tradingagents.dataflows import interface

        for tool_fn in [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            get_earnings_estimates,
            get_shareholder_count,
            get_dividend_history,
        ]:
            cat = interface.get_category_for_method(tool_fn.name)
            assert cat is not None

    def test_market_analyst_tools_all_categorized(self):
        from tradingagents.agents.utils.agent_utils import (
            get_fund_flow,
            get_index_daily,
            get_indicators,
            get_sector_fund_flow,
            get_stock_data,
        )
        from tradingagents.dataflows import interface

        for tool_fn in [
            get_stock_data,
            get_index_daily,
            get_indicators,
            get_fund_flow,
            get_sector_fund_flow,
        ]:
            cat = interface.get_category_for_method(tool_fn.name)
            assert cat is not None


# ===========================================================================
# get_research_reports routing with smartmoney_db fallback
# ===========================================================================


@pytest.mark.unit
class TestGetResearchReportsRouting:
    """get_research_reports smartmoney_db fallback routing tests."""

    def test_smartmoney_raises_akshare_succeeds(self):
        """When smartmoney_db has no data (raises), akshare data is returned."""
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(
            side_effect=RuntimeError("No research_report table in quant_core.db")
        )
        fake_ak = MagicMock(return_value="AKSHARE_RESEARCH_RESULT")
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert result == "AKSHARE_RESEARCH_RESULT"
        fake_sm.assert_called_once_with("600519.SS")
        fake_ak.assert_called_once_with("600519.SS")

    def test_smartmoney_returns_data(self):
        """When smartmoney_db has data, it is returned directly."""
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(return_value="SMARTMONEY_RESEARCH_RESULT")
        fake_ak = MagicMock(return_value="AKSHARE_RESEARCH_RESULT")
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert result == "SMARTMONEY_RESEARCH_RESULT"
        fake_sm.assert_called_once_with("600519.SS")
        fake_ak.assert_not_called()

    def test_both_vendors_fail_returns_no_data(self):
        """When both smartmoney_db and akshare raise, returns DATA_UNAVAILABLE."""
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(
            side_effect=RuntimeError("No research_report table in quant_core.db")
        )
        fake_ak = MagicMock(
            side_effect=RuntimeError("akshare API unreachable")
        )
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert "DATA_UNAVAILABLE" in result
        fake_sm.assert_called_once_with("600519.SS")
        fake_ak.assert_called_once_with("600519.SS")


# ===========================================================================
# _is_stale_research_data helper function
# ===========================================================================


@pytest.mark.unit
class TestIsStaleResearchData:
    """Test _is_stale_research_data edge cases."""

    def test_no_parseable_dates_returns_false(self):
        """When no '日期:' pattern is found, return False."""
        from tradingagents.dataflows.interface import _is_stale_research_data

        assert _is_stale_research_data("some research text without dates") is False

    def test_invalid_date_format_continues(self):
        """Invalid date strings in the pattern trigger except ValueError: continue."""
        from tradingagents.dataflows.interface import _is_stale_research_data

        # One valid, one invalid date -> valid is collected, invalid is skipped
        result = (
            "日期: 2026-07-01\n"
            "日期: not-a-date\n"
            "日期: 2026-07-21\n"
        )
        assert _is_stale_research_data(result) is False  # latest=2026-07-21 is < 90 days

    def test_stale_data_returns_true(self):
        """Dates older than 90 days from today return True."""
        from tradingagents.dataflows.interface import _is_stale_research_data

        result = "日期: 2025-01-01\n日期: 2025-06-15\n"
        assert _is_stale_research_data(result) is True

    def test_fresh_data_returns_false(self):
        """Dates within 90 days return False."""
        from tradingagents.dataflows.interface import _is_stale_research_data

        result = "日期: 2026-07-01\n日期: 2026-07-15\n"
        assert _is_stale_research_data(result) is False


# ===========================================================================
# _is_failure_sentinel helper function
# ===========================================================================


@pytest.mark.unit
class TestIsFailureSentinel:
    """Test _is_failure_sentinel edge cases."""

    def test_detects_error_prefix(self):
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("Error: something failed") is True

    def test_detects_no_data_found(self):
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("No data found") is True

    def test_detects_data_unavailable(self):
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("DATA_UNAVAILABLE: service down") is True

    def test_detects_no_data_available(self):
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("NO_DATA_AVAILABLE: no rows") is True

    def test_leading_whitespace_still_detected(self):
        """Leading whitespace is stripped before matching."""
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("  Error: failed") is True

    def test_normal_data_not_failure(self):
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("Here is the data you requested") is False

    def test_empty_string_not_failure(self):
        from tradingagents.dataflows.interface import _is_failure_sentinel

        assert _is_failure_sentinel("") is False


# ===========================================================================
# Additional get_vendor edge cases (method=None branch)
# ===========================================================================


@pytest.mark.unit
class TestGetVendorNoMethod:
    """Test get_vendor when method is None (branch 355->361)."""

    def test_no_method_falls_to_category_level(self):
        """get_vendor without method (method=None) falls back to category-level config."""
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "tool_vendors": {"get_fundamentals": "alpha_vantage"},
                "data_vendors": {"fundamental_data": "smartmoney_db"},
            }
            # No method argument -> skips tool-level check, goes to category-level
            result = interface.get_vendor("fundamental_data")
        assert result == "smartmoney_db"

    def test_no_method_with_missing_category_returns_default(self):
        """get_vendor without method and missing category returns 'default'."""
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "tool_vendors": {},
                "data_vendors": {},
            }
            result = interface.get_vendor("nonexistent_category")
        assert result == "default"


# ===========================================================================
# route_to_vendor: failure sentinel fallthrough
# ===========================================================================


@pytest.mark.unit
class TestRouteToVendorFailureSentinel:
    """When a vendor returns a failure sentinel string, the router falls through."""

    def test_failure_sentinel_falls_through_to_next_vendor(self):
        """Failure sentinel from first vendor triggers fallthrough to second vendor."""
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(return_value="Error: no data in smartmoney_db")
        fake_ak = MagicMock(return_value="AKSHARE_RESULT")
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert result == "AKSHARE_RESULT"
        fake_sm.assert_called_once_with("600519.SS")
        fake_ak.assert_called_once_with("600519.SS")

    def test_all_vendors_return_failure_sentinel_returns_no_data(self):
        """When all vendors return failure sentinels, return NO_DATA_AVAILABLE."""
        from tradingagents.dataflows import interface

        fake_sm = MagicMock(return_value="NO_DATA_AVAILABLE: not found")
        fake_ak = MagicMock(return_value="NO_DATA_AVAILABLE: not found either")
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert "NO_DATA_AVAILABLE" in result
        assert "600519.SS" in result


# ===========================================================================
# route_to_vendor: stale research data fallthrough
# ===========================================================================


@pytest.mark.unit
class TestRouteToVendorStaleResearch:
    """Stale smartmoney_db research reports fall through to akshare."""

    def test_stale_research_falls_through_to_akshare(self):
        """When smartmoney_db returns research with only old dates, fall through to akshare."""
        from tradingagents.dataflows import interface

        # Data with dates older than 90 days
        stale_data = "日期: 2025-01-15\n日期: 2025-06-20\nSome old research"
        fake_sm = MagicMock(return_value=stale_data)
        fake_ak = MagicMock(return_value="AKSHARE_FRESH_RESEARCH")
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert result == "AKSHARE_FRESH_RESEARCH"
        fake_sm.assert_called_once_with("600519.SS")
        fake_ak.assert_called_once_with("600519.SS")

    def test_fresh_research_from_smartmoney_used_directly(self):
        """Fresh research from smartmoney_db is returned without fallthrough."""
        from tradingagents.dataflows import interface

        fresh_data = "日期: 2026-07-15\nSome recent research"
        fake_sm = MagicMock(return_value=fresh_data)
        fake_ak = MagicMock(return_value="AKSHARE_RESEARCH")
        with patch(
            "tradingagents.dataflows.interface.get_vendor",
            return_value="smartmoney_db,akshare",
        ), patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": fake_sm, "akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_research_reports", "600519.SS"
            )
        assert result == fresh_data
        fake_sm.assert_called_once_with("600519.SS")
        fake_ak.assert_not_called()


# ===========================================================================
# Additional route_to_vendor edge cases
# ===========================================================================


@pytest.mark.unit
class TestRouteToVendorAdditionalEdgeCases:

    def test_ashare_without_smartmoney_db_in_chain_promotes_akshare(self):
        """
        A-share ticker with method that has no smartmoney_db in VENDOR_METHODS
        still routes to akshare. Covers line 438 else branch.
        """
        from tradingagents.dataflows import interface

        fake_ak = MagicMock(return_value="AKSHARE_DIVIDEND")
        with patch.dict(
            interface.VENDOR_METHODS["get_dividend_history"],
            {"akshare": fake_ak},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_dividend_history", "600519.SS"
            )
        assert result == "AKSHARE_DIVIDEND"
        fake_ak.assert_called_once_with("600519.SS")

    def test_prediction_markets_returns_data_unavailable(self):
        """
        get_prediction_markets is not in VENDOR_METHODS but is categorized
        under prediction_markets (which is in OPTIONAL_CATEGORIES).
        Covers lines 508-514 (optional method with no vendor) and line 494
        (_format_optional_unavailable with first_error=None).
        """
        from tradingagents.dataflows import interface

        result = interface.route_to_vendor("get_prediction_markets", "AAPL")
        assert "DATA_UNAVAILABLE" in result
        assert "get_prediction_markets" in result
        assert "no available data source" in result

    def test_method_not_in_vendor_methods_non_optional_raises(self):
        """
        A method that IS categorized but NOT in VENDOR_METHODS, and whose
        category is NOT optional, raises ValueError.
        Covers lines 508-513 (raise ValueError branch).
        """
        from tradingagents.dataflows import interface

        with patch.dict(
            interface.TOOLS_CATEGORIES,
            {
                "governance_risk": {
                    "description": "governance",
                    "tools": [
                        "get_pledge_ratio",
                        "get_company_announcements",
                        "get_margin_trading",
                        "get_dragon_tiger",
                        "get_block_trade",
                        "nonexistent_governance_method",
                    ],
                }
            },
        ), pytest.raises(ValueError, match="not supported"):
            interface.route_to_vendor(
                "nonexistent_governance_method", "AAPL"
            )

    def test_multiple_rate_limits_first_error_stored(self):
        """
        When two vendors both raise VendorRateLimitError, the first error
        is stored and the second skips the 'if first_error is None' branch.
        Covers partial branch 598->600.
        """
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.errors import VendorRateLimitError

        def first_limited(*a, **kw):
            raise VendorRateLimitError("first vendor rate-limited")

        def second_limited(*a, **kw):
            raise VendorRateLimitError("second vendor rate-limited")

        with patch.dict(
            interface.VENDOR_METHODS["get_indicators"],
            {"akshare": first_limited, "yfinance": second_limited},
            clear=True,
        ), patch.object(interface, "get_vendor", return_value="akshare,yfinance"), \
            pytest.raises(VendorRateLimitError, match="first vendor rate-limited"):
            interface.route_to_vendor(
                "get_indicators", "600519.SS", "rsi", "2026-05-14", 30
            )


# ===========================================================================
# Additional route_to_vendor_with_source edge cases
# ===========================================================================


@pytest.mark.unit
class TestRouteToVendorWithSourceEdgeCases:
    def test_unsupported_method_via_with_source(self):
        """route_to_vendor_with_source raises ValueError for unsupported methods."""
        from tradingagents.dataflows import interface

        with pytest.raises(ValueError, match="not found in any category"):
            interface.route_to_vendor_with_source("nonexistent_method", "AAPL")


def _runtime_context(
    ticker: str = "600519.SS",
    market: str = "XSHG",
) -> RuntimeDataContext:
    return RuntimeDataContext(
        ticker=ticker,
        market=market,
        dates=AnalysisDates(
            analysis_date="2026-08-16",
            market_as_of_date="2026-08-14",
            evidence_window_end="2026-08-16",
        ),
        policy_version="v1",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_vendor"),
    [
        ("primary_success", "ok", "smartmoney_db"),
        ("fallback_success", "ok_fallback", "yfinance"),
        ("covered_empty_event_window", "valid_empty", "smartmoney_db"),
        ("unsupported_hk_tool", "not_applicable", None),
        ("partial_evidence", "partial", "smartmoney_db"),
        ("stale_snapshot", "stale", "smartmoney_db"),
        ("clean_no_data", "no_data", None),
        ("unconfigured_source", "unavailable", None),
        ("provider_exception", "failed", None),
    ],
)
def test_vendor_outcome_status_matrix(scenario, expected_status, expected_vendor):
    from tradingagents.dataflows import interface
    from tradingagents.dataflows.errors import VendorNotConfiguredError

    method = "get_news"
    args = ("600519.SS", "2026-08-01", "2026-08-16")
    vendor_config = "smartmoney_db"

    if scenario == "primary_success":
        vendors = {"smartmoney_db": MagicMock(return_value="primary data")}
    elif scenario == "fallback_success":
        vendors = {
            "smartmoney_db": MagicMock(
                side_effect=NoMarketDataError("600519.SS", detail="not archived")
            ),
            "yfinance": MagicMock(return_value="fallback data"),
        }
        vendor_config = "smartmoney_db,yfinance"
    elif scenario == "covered_empty_event_window":
        method = "get_company_announcements"
        vendors = {
            "smartmoney_db": MagicMock(
                return_value=interface.VendorPayload(
                    data="",
                    status="valid_empty",
                    as_of="2026-08-15",
                    reason="complete event window contained no announcements",
                )
            )
        }
    elif scenario == "unsupported_hk_tool":
        method = "get_company_announcements"
        args = ("0700.HK", "2026-08-01", "2026-08-16")
        vendors = {"smartmoney_db": MagicMock(return_value="must not run")}
    elif scenario == "partial_evidence":
        vendors = {
            "smartmoney_db": MagicMock(
                return_value=interface.VendorPayload(
                    data="partial data", status="partial", reason="one page missing"
                )
            )
        }
    elif scenario == "stale_snapshot":
        vendors = {
            "smartmoney_db": MagicMock(
                return_value=interface.VendorPayload(
                    data="stale data", status="stale", as_of="2026-08-01"
                )
            )
        }
    elif scenario == "clean_no_data":
        vendors = {
            "smartmoney_db": MagicMock(
                side_effect=NoMarketDataError("600519.SS", detail="no rows")
            )
        }
    else:
        method = "get_research_reports"
        args = ("600519.SS",)
        error = (
            VendorNotConfiguredError("missing API key")
            if scenario == "unconfigured_source"
            else ConnectionError("provider offline")
        )
        vendors = {"smartmoney_db": MagicMock(side_effect=error)}

    with patch.object(interface, "get_vendor", return_value=vendor_config), patch.dict(
        interface.VENDOR_METHODS[method], vendors, clear=True
    ):
        result = interface.route_to_vendor_with_source(method, *args)

    assert result.diagnostic is not None
    assert result.diagnostic.status == expected_status
    assert result.diagnostic.selected_vendor == expected_vendor
    if scenario == "covered_empty_event_window":
        assert result.data == ""
        assert result.diagnostic.as_of == "2026-08-15"
        assert result.diagnostic.reason == "complete event window contained no announcements"


@pytest.mark.unit
def test_legacy_empty_string_is_ok_not_valid_empty():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="")
    with patch.object(interface, "get_vendor", return_value="smartmoney_db"), patch.dict(
        interface.VENDOR_METHODS["get_news"], {"smartmoney_db": fake_vendor}, clear=True
    ):
        result = interface.route_to_vendor_with_source(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )

    assert result.data == ""
    assert result.diagnostic is not None
    assert result.diagnostic.status == "ok"


@pytest.mark.unit
def test_company_announcements_hk_is_not_applicable_without_vendor_calls():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="must not run")
    build_chain = MagicMock(side_effect=AssertionError("chain must not be built"))
    with patch.dict(
        interface.VENDOR_METHODS["get_company_announcements"],
        {"smartmoney_db": fake_vendor},
        clear=True,
    ), patch.object(interface, "_build_vendor_chain", build_chain):
        result = interface.route_to_vendor_with_source(
            "get_company_announcements", "0700.HK", "2026-08-01", "2026-08-16"
        )

    assert result.diagnostic is not None
    assert result.diagnostic.status == "not_applicable"
    assert result.diagnostic.attempted_vendors == ()
    fake_vendor.assert_not_called()
    build_chain.assert_not_called()


@pytest.mark.unit
def test_runtime_context_does_not_override_requested_ticker_market():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="must not run")
    build_chain = MagicMock(side_effect=AssertionError("chain must not be built"))
    with use_runtime_data_context(_runtime_context()), patch.dict(
        interface.VENDOR_METHODS["get_company_announcements"],
        {"cninfo": fake_vendor},
        clear=True,
    ), patch.object(interface, "_build_vendor_chain", build_chain):
        result = interface.route_to_vendor_with_source(
            "get_company_announcements", "0700.HK", "2026-08-01", "2026-08-16"
        )

    assert result.diagnostic is not None
    assert result.diagnostic.status == "not_applicable"
    assert result.diagnostic.attempted_vendors == ()
    fake_vendor.assert_not_called()
    build_chain.assert_not_called()


@pytest.mark.unit
def test_market_session_date_is_replaced_from_runtime_context():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="breadth")
    with use_runtime_data_context(_runtime_context()), patch.object(
        interface, "get_vendor", return_value="smartmoney_db"
    ), patch.dict(
        interface.VENDOR_METHODS["get_limit_up_down"],
        {"smartmoney_db": fake_vendor},
        clear=True,
    ):
        result = interface.route_to_vendor_with_source(
            "get_limit_up_down", "2026-08-16"
        )

    assert result.data == "breadth"
    assert result.diagnostic is not None
    assert result.diagnostic.as_of == "2026-08-14"
    fake_vendor.assert_called_once_with("2026-08-14")


@pytest.mark.unit
def test_date_only_direct_call_without_context_preserves_legacy_date():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="breadth")
    with patch.object(interface, "get_vendor", return_value="smartmoney_db"), patch.dict(
        interface.VENDOR_METHODS["get_limit_up_down"],
        {"smartmoney_db": fake_vendor},
        clear=True,
    ):
        result = interface.route_to_vendor("get_limit_up_down", "2026-08-16")

    assert result == "breadth"
    fake_vendor.assert_called_once_with("2026-08-16")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("original_end", "expected_end"),
    [("2026-08-20", "2026-08-16"), ("2026-08-10", "2026-08-10")],
)
def test_calendar_window_preserves_start_and_only_caps_end(original_end, expected_end):
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="news")
    with use_runtime_data_context(_runtime_context()), patch.object(
        interface, "get_vendor", return_value="smartmoney_db"
    ), patch.dict(
        interface.VENDOR_METHODS["get_news"], {"smartmoney_db": fake_vendor}, clear=True
    ):
        result = interface.route_to_vendor(
            "get_news", "600519.SS", "2026-07-01", original_end
        )

    assert result == "news"
    fake_vendor.assert_called_once_with("600519.SS", "2026-07-01", expected_end)


@pytest.mark.unit
def test_latest_snapshot_does_not_invent_date_argument():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(return_value="holdings")
    with use_runtime_data_context(_runtime_context()), patch.object(
        interface, "get_vendor", return_value="smartmoney_db"
    ), patch.dict(
        interface.VENDOR_METHODS["get_northbound_hold"],
        {"smartmoney_db": fake_vendor},
        clear=True,
    ):
        result = interface.route_to_vendor("get_northbound_hold", "600519.SS")

    assert result == "holdings"
    fake_vendor.assert_called_once_with("600519.SS")


@pytest.mark.unit
def test_registered_policy_filters_disallowed_vendors_from_chain():
    from tradingagents.dataflows import interface

    disallowed = MagicMock(return_value="wrong source")
    allowed = MagicMock(return_value="news")
    with use_runtime_data_context(_runtime_context()), patch.object(
        interface, "get_vendor", return_value="fred,yfinance"
    ), patch.dict(
        interface.VENDOR_METHODS["get_news"],
        {"fred": disallowed, "yfinance": allowed},
        clear=True,
    ):
        result = interface.route_to_vendor_with_source(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )

    assert result.vendor == "yfinance"
    disallowed.assert_not_called()
    allowed.assert_called_once()


@pytest.mark.unit
def test_unregistered_method_explicitly_uses_legacy_policy():
    from tradingagents.dataflows import data_policy, interface

    fake_vendor = MagicMock(return_value="fundamentals")
    with patch.object(
        interface, "legacy_policy_for", wraps=data_policy.legacy_policy_for
    ) as legacy_policy, patch.object(
        interface, "get_vendor", return_value="yfinance"
    ), patch.dict(
        interface.TOOLS_CATEGORIES,
        {"future_category": {"description": "x", "tools": ["get_future_unregistered"]}},
    ), patch.dict(
        interface.VENDOR_METHODS,
        {"get_future_unregistered": {"yfinance": fake_vendor}},
    ):
        result = interface.route_to_vendor("get_future_unregistered", "AAPL")

    assert result == "fundamentals"
    legacy_policy.assert_called_once_with("get_future_unregistered")


@pytest.mark.unit
def test_unregistered_method_with_runtime_context_preserves_legacy_arguments():
    from tradingagents.dataflows import data_policy, interface

    fake_vendor = MagicMock(return_value="fundamentals")
    with use_runtime_data_context(_runtime_context()), patch.object(
        interface, "legacy_policy_for", wraps=data_policy.legacy_policy_for
    ) as legacy_policy, patch.object(
        interface, "get_vendor", return_value="yfinance"
    ), patch.dict(
        interface.TOOLS_CATEGORIES,
        {"future_category": {"description": "x", "tools": ["get_future_unregistered"]}},
    ), patch.dict(
        interface.VENDOR_METHODS,
        {"get_future_unregistered": {"yfinance": fake_vendor}},
    ):
        result = interface.route_to_vendor(
            "get_future_unregistered", "AAPL", "2026-08-20"
        )

    assert result == "fundamentals"
    fake_vendor.assert_called_once_with("AAPL", "2026-08-20")
    legacy_policy.assert_called_once_with("get_future_unregistered")


@pytest.mark.unit
def test_payload_with_unknown_as_of_does_not_infer_request_end_date():
    from tradingagents.dataflows import interface

    fake_vendor = MagicMock(
        return_value=interface.VendorPayload(
            data="partial news",
            status="partial",
            as_of=None,
            reason="source did not establish freshness",
        )
    )
    with patch.object(interface, "get_vendor", return_value="smartmoney_db"), patch.dict(
        interface.VENDOR_METHODS["get_news"],
        {"smartmoney_db": fake_vendor},
        clear=True,
    ):
        result = interface.route_to_vendor_with_source(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )

    assert result.diagnostic is not None
    assert result.diagnostic.as_of is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("vendors", "vendor_config", "expected_status"),
    [
        (
            {"smartmoney_db": MagicMock(side_effect=PermissionError("unauthorized"))},
            "smartmoney_db",
            "unavailable",
        ),
        (
            {
                "smartmoney_db": MagicMock(
                    side_effect=RuntimeError("provider schema changed")
                )
            },
            "smartmoney_db",
            "unavailable",
        ),
        (
            {
                "smartmoney_db": MagicMock(
                    side_effect=ConnectionError("schema endpoint connection failed")
                )
            },
            "smartmoney_db",
            "failed",
        ),
        (
            {
                "smartmoney_db": MagicMock(
                    side_effect=TimeoutError("API key endpoint timed out")
                )
            },
            "smartmoney_db",
            "failed",
        ),
        (
            {
                "smartmoney_db": MagicMock(
                    side_effect=VendorNotConfiguredError("missing API key")
                ),
                "akshare": MagicMock(
                    side_effect=NoMarketDataError("600519.SS", detail="no rows")
                ),
            },
            "smartmoney_db,akshare",
            "unavailable",
        ),
        (
            {
                "smartmoney_db": MagicMock(
                    side_effect=VendorNotConfiguredError("missing API key")
                ),
                "akshare": MagicMock(side_effect=ConnectionError("provider offline")),
            },
            "smartmoney_db,akshare",
            "failed",
        ),
    ],
)
def test_provider_error_classification_is_stable_across_mixed_chains(
    vendors, vendor_config, expected_status
):
    from tradingagents.dataflows import interface

    with patch.object(interface, "get_vendor", return_value=vendor_config), patch.dict(
        interface.VENDOR_METHODS["get_research_reports"], vendors, clear=True
    ):
        result = interface.route_to_vendor_with_source(
            "get_research_reports", "600519.SS"
        )

    assert result.diagnostic is not None
    assert result.diagnostic.status == expected_status


@pytest.mark.unit
def test_runtime_memo_deduplicates_full_route_and_aggregates_diagnostic_calls():
    from tradingagents.dataflows import interface

    vendor_entered = Event()
    release_vendor = Event()
    vendor = MagicMock()

    def resolve_news(*args, **kwargs):
        vendor_entered.set()
        assert release_vendor.wait(timeout=5)
        return "news"

    vendor.side_effect = resolve_news
    runtime_context = _runtime_context()

    with (
        use_runtime_data_context(runtime_context),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.object(interface, "_build_vendor_chain", wraps=interface._build_vendor_chain) as build_chain,
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
        ThreadPoolExecutor(max_workers=5) as executor,
    ):
        contexts = [copy_context() for _ in range(5)]
        futures = [
            executor.submit(
                context.run,
                interface.route_to_vendor,
                "get_news",
                "600519.SS",
                date(2026, 8, 1),
                datetime(2026, 8, 16, 12, 30),
            )
            for context in contexts
        ]
        assert vendor_entered.wait(timeout=5)
        release_vendor.set()
        results = [future.result(timeout=5) for future in futures]

    assert results == ["news"] * 5
    vendor.assert_called_once()
    build_chain.assert_called_once()
    assert len(records) == 1
    assert records[0].status == "ok"
    assert records[0].call_count == 5


@pytest.mark.unit
def test_not_applicable_is_memoized_before_vendor_chain_construction():
    from tradingagents.dataflows import interface

    with (
        use_runtime_data_context(_runtime_context(ticker="0700.HK", market="XHKG")),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "_build_vendor_chain") as build_chain,
    ):
        first = interface.route_to_vendor_with_source(
            "get_company_announcements", "0700.HK", "2026-08-01", "2026-08-16"
        )
        second = interface.route_to_vendor_with_source(
            "get_company_announcements",
            "0700.HK",
            date(2026, 8, 1),
            datetime(2026, 8, 16, 9, 0),
        )

    assert first is second
    build_chain.assert_not_called()
    assert len(records) == 1
    assert records[0].status == "not_applicable"
    assert records[0].call_count == 2


@pytest.mark.unit
def test_runtime_memo_normalizes_equivalent_ticker_case_in_full_request_key():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="news")
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        first = interface.route_to_vendor(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )
        second = interface.route_to_vendor(
            "get_news", "600519.ss", "2026-08-01", "2026-08-16"
        )

    assert first == second == "news"
    vendor.assert_called_once()
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
def test_runtime_memo_normalizes_positional_and_keyword_request_forms():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="news")
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        positional = interface.route_to_vendor(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )
        keyword = interface.route_to_vendor(
            "get_news",
            ticker="600519.SS",
            start_date="2026-08-01",
            end_date="2026-08-16",
        )

    assert positional == keyword == "news"
    vendor.assert_called_once()
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
def test_runtime_memo_normalizes_symbol_and_ticker_aliases():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="news")
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        by_symbol = interface.route_to_vendor(
            "get_news",
            symbol="600519.SS",
            start_date="2026-08-01",
            end_date="2026-08-16",
        )
        by_ticker = interface.route_to_vendor(
            "get_news",
            ticker="600519.SS",
            start_date="2026-08-01",
            end_date="2026-08-16",
        )

    assert by_symbol == by_ticker == "news"
    vendor.assert_called_once()
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize("keyword_first", [False, True])
def test_runtime_memo_normalizes_global_news_residual_parameters(keyword_first):
    from tradingagents.dataflows import interface

    calls = []

    def strict_global_news(curr_date, look_back_days, limit):
        calls.append((curr_date, look_back_days, limit))
        return "global news"

    def positional():
        return interface.route_to_vendor("get_global_news", "2026-08-16", 7, 50)

    def keyword():
        return interface.route_to_vendor(
            "get_global_news",
            curr_date="2026-08-16",
            look_back_days=7,
            limit=50,
        )
    ordered_calls = (keyword, positional) if keyword_first else (positional, keyword)

    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="yfinance"),
        patch.dict(
            interface.VENDOR_METHODS["get_global_news"],
            {"yfinance": strict_global_news},
            clear=True,
        ),
    ):
        results = [call() for call in ordered_calls]

    assert results == ["global news", "global news"]
    assert calls == [("2026-08-16", 7, 50)]
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize("keyword_first", [False, True])
def test_runtime_memo_normalizes_indicator_residual_parameters(keyword_first):
    from tradingagents.dataflows import interface

    calls = []

    def strict_indicator(symbol, indicator, curr_date, look_back_days):
        calls.append((symbol, indicator, curr_date, look_back_days))
        return "indicator"

    def positional():
        return interface.route_to_vendor(
            "get_indicators", "600519.SS", "rsi_14", "2026-08-16", 30
        )

    def keyword():
        return interface.route_to_vendor(
            "get_indicators",
            symbol="600519.SS",
            indicator="rsi_14",
            curr_date="2026-08-16",
            look_back_days=30,
        )
    ordered_calls = (keyword, positional) if keyword_first else (positional, keyword)

    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_indicators"],
            {"smartmoney_db": strict_indicator},
            clear=True,
        ),
    ):
        results = [call() for call in ordered_calls]

    assert results == ["indicator", "indicator"]
    # latest_snapshot policy anchors curr_date to the last market session
    # (2026-08-16 is a Sunday; market_as_of is Friday 2026-08-14).
    assert calls == [("600519.SS", "rsi_14", "2026-08-14", 30)]
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize("omitted_first", [False, True])
def test_indicator_non_none_default_is_invoked_and_memoized(omitted_first):
    from tradingagents.dataflows import interface

    calls = []

    def strict_indicator(symbol, indicator, curr_date, look_back_days):
        calls.append((symbol, indicator, curr_date, look_back_days))
        return "indicator"

    def omitted_default():
        return interface.route_to_vendor(
            "get_indicators", "600519.SS", "rsi_14", "2026-08-16"
        )

    def explicit_default():
        return interface.route_to_vendor(
            "get_indicators",
            symbol="600519.SS",
            indicator="rsi_14",
            curr_date="2026-08-16",
            look_back_days=30,
        )

    ordered_calls = (
        (omitted_default, explicit_default)
        if omitted_first
        else (explicit_default, omitted_default)
    )
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_indicators"],
            {"smartmoney_db": strict_indicator},
            clear=True,
        ),
    ):
        results = [call() for call in ordered_calls]

    assert results == ["indicator", "indicator"]
    # curr_date is anchored to the last market session (2026-08-14) by the
    # latest_snapshot policy; both call shapes converge on the same request.
    assert calls == [("600519.SS", "rsi_14", "2026-08-14", 30)]
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
def test_keyword_ticker_owner_invokes_strict_symbol_vendor_positionally():
    from tradingagents.dataflows import interface

    calls = []

    def strict_symbol_vendor(symbol, start_date, end_date):
        calls.append((symbol, start_date, end_date))
        return "news"

    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": strict_symbol_vendor},
            clear=True,
        ),
    ):
        keyword_owner = interface.route_to_vendor(
            "get_news",
            ticker="600519.SS",
            start_date="2026-08-01",
            end_date="2026-08-16",
        )
        positional_hit = interface.route_to_vendor(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )

    assert keyword_owner == positional_hit == "news"
    assert calls == [("600519.SS", "2026-08-01", "2026-08-16")]
    assert len(records) == 1
    assert records[0].call_count == 2


@pytest.mark.unit
def test_direct_enrichment_route_is_memoized_and_diagnosed():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="concept data")
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS,
            {"get_concept_board": {"smartmoney_db": vendor}},
        ),
    ):
        first = interface.route_to_vendor("get_concept_board", "600519.SS")
        second = interface.route_to_vendor("get_concept_board", "600519.SS")

    assert first == second == "concept data"
    vendor.assert_called_once_with("600519.SS")
    assert len(records) == 1
    assert records[0].status == "ok"
    assert records[0].call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize("first_status", ["failed", "partial"])
def test_collector_replaces_degraded_diagnostic_after_success(first_status):
    from tradingagents.dataflows import interface

    if first_status == "failed":
        vendor = MagicMock(side_effect=[ConnectionError("offline"), "recovered"])
    else:
        vendor = MagicMock(
            side_effect=[
                interface.VendorPayload("partial rows", status="partial"),
                "recovered",
            ]
        )

    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_research_reports"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        interface.route_to_vendor("get_research_reports", "600519.SS")
        result = interface.route_to_vendor("get_research_reports", "600519.SS")

    assert result == "recovered"
    assert len(records) == 1
    assert records[0].status == "ok"
    assert records[0].selected_vendor == "smartmoney_db"
    assert records[0].call_count == 2


@pytest.mark.unit
def test_latest_snapshot_caps_explicit_weekend_date_without_adding_omitted_date():
    from tradingagents.dataflows import interface

    calls = []

    def vendor(*args):
        calls.append(args)
        return "flow"

    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_fund_flow"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        interface.route_to_vendor("get_fund_flow", "600519.SS", "2026-08-16")
        interface.route_to_vendor("get_fund_flow", "600519.SS")

    assert calls == [
        ("600519.SS", "2026-08-14"),
        ("600519.SS",),
    ]
    assert [record.as_of for record in records] == ["2026-08-14", None]


@pytest.mark.unit
def test_restricted_release_uses_only_supported_vendor_and_clean_empty_semantics():
    from tradingagents.dataflows import interface
    from tradingagents.dataflows.data_policy import policy_for

    assert "smartmoney_db" not in interface.VENDOR_METHODS["get_restricted_release"]
    assert "smartmoney_db" not in policy_for("get_restricted_release").allowed_vendors


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ("<Eastmoney hot keywords unavailable: JSONDecodeError>", "no_data"),
        (
            "Eastmoney 人气排名\n<hot-rank table unavailable: JSONDecodeError>\n"
            "历史排名与粉丝构成:\n[2026-08-14] 排名: 10",
            "partial",
        ),
    ],
)
def test_eastmoney_enrichment_placeholders_are_not_reported_as_clean_success(
    payload, expected_status
):
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value=interface._eastmoney_payload(payload, "600519.SS"))
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="eastmoney"),
        patch.dict(
            interface.VENDOR_METHODS["fetch_eastmoney_hot_rank"],
            {"eastmoney": vendor},
            clear=True,
        ),
    ):
        interface.route_to_vendor("fetch_eastmoney_hot_rank", "600519.SS")

    assert records[0].status == expected_status

    vendor = MagicMock(
        side_effect=NoMarketDataError("600519.SS", detail="no releases in window")
    )
    with (
        patch.object(interface, "get_vendor", return_value="akshare"),
        patch.dict(
            interface.VENDOR_METHODS["get_restricted_release"],
            {"akshare": vendor},
            clear=True,
        ),
    ):
        result = interface.route_to_vendor_with_source(
            "get_restricted_release",
            "600519.SS",
            "2026-08-01",
            "2026-08-16",
        )

    assert result.diagnostic is not None
    assert result.diagnostic.status == "valid_empty"
    assert result.diagnostic.attempted_vendors == ("akshare",)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "kwargs", "message"),
    [
        (
            (),
            {
                "ticker": "600519.SS",
                "symbol": "000001.SZ",
                "start_date": "2026-08-01",
                "end_date": "2026-08-16",
            },
            "conflicting aliases for 'ticker'",
        ),
        (
            ("600519.SS", "2026-08-01", "2026-08-16"),
            {"ticker": "600519.SS"},
            "multiple values for 'ticker'",
        ),
    ],
)
def test_canonical_route_schema_rejects_conflicting_parameter_sources(
    args, kwargs, message
):
    from tradingagents.dataflows import interface

    with pytest.raises(TypeError, match=message):
        interface.route_to_vendor("get_news", *args, **kwargs)


@pytest.mark.unit
def test_every_routed_method_has_a_canonical_parameter_schema():
    from tradingagents.dataflows import interface

    categorized = {
        method
        for category in interface.TOOLS_CATEGORIES.values()
        for method in category["tools"]
    }
    expected = categorized | set(interface.VENDOR_METHODS)

    assert expected <= set(interface._METHOD_PARAMETER_SCHEMAS)


@pytest.mark.unit
def test_runtime_memo_keeps_distinct_residual_options_separate():
    from tradingagents.dataflows import interface

    vendor = MagicMock(side_effect=lambda *args, **kwargs: f"limit={kwargs['limit']}")
    with (
        use_runtime_data_context(_runtime_context()),
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        first = interface.route_to_vendor(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16", limit=10
        )
        second = interface.route_to_vendor(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16", limit=20
        )

    assert first == "limit=10"
    assert second == "limit=20"
    assert vendor.call_count == 2
    assert len(records) == 2


@pytest.mark.unit
def test_cached_result_is_recorded_independently_in_each_collector_scope():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="news")
    with (
        use_runtime_data_context(_runtime_context()),
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        with interface.collect_route_diagnostics() as first_records:
            first = interface.route_to_vendor_with_source(
                "get_news", "600519.SS", "2026-08-01", "2026-08-16"
            )
        first_snapshot = first_records[0]

        with interface.collect_route_diagnostics() as second_records:
            second = interface.route_to_vendor_with_source(
                "get_news", "600519.SS", "2026-08-01", "2026-08-16"
            )

    assert first is second
    vendor.assert_called_once()
    assert len(first_records) == len(second_records) == 1
    assert first_records[0].call_count == second_records[0].call_count == 1
    assert first_records[0] is first_snapshot
    assert first_records[0] is not second_records[0]


@pytest.mark.unit
def test_cache_hit_after_uncollected_call_registers_in_current_collector():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="news")
    with (
        use_runtime_data_context(_runtime_context()),
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        interface.route_to_vendor(
            "get_news", "600519.SS", "2026-08-01", "2026-08-16"
        )
        with interface.collect_route_diagnostics() as records:
            interface.route_to_vendor(
                "get_news", "600519.SS", "2026-08-01", "2026-08-16"
            )

    vendor.assert_called_once()
    assert len(records) == 1
    assert records[0].call_count == 1


@pytest.mark.unit
def test_runtime_memo_does_not_leak_across_tickers_or_context_scopes():
    from tradingagents.dataflows import interface

    vendor = MagicMock(side_effect=lambda ticker, *args: f"news:{ticker}")
    contexts = [
        _runtime_context(ticker="600519.SS", market="XSHG"),
        _runtime_context(ticker="000001.SZ", market="XSHG"),
    ]

    with patch.object(interface, "get_vendor", return_value="smartmoney_db"), patch.dict(
        interface.VENDOR_METHODS["get_news"],
        {"smartmoney_db": vendor},
        clear=True,
    ):
        for runtime_context, ticker in zip(
            contexts, ("600519.SS", "000001.SZ"), strict=True
        ):
            with (
                use_runtime_data_context(runtime_context),
                interface.collect_route_diagnostics() as records,
            ):
                assert (
                    interface.route_to_vendor(
                        "get_news", ticker, "2026-08-01", "2026-08-16"
                    )
                    == f"news:{ticker}"
                )
                assert len(records) == 1
                assert records[0].call_count == 1

    assert vendor.call_count == 2


@pytest.mark.unit
def test_direct_calls_without_runtime_context_remain_unmemoized():
    from tradingagents.dataflows import interface

    vendor = MagicMock(return_value="news")
    with (
        interface.collect_route_diagnostics() as records,
        patch.object(interface, "get_vendor", return_value="smartmoney_db"),
        patch.dict(
            interface.VENDOR_METHODS["get_news"],
            {"smartmoney_db": vendor},
            clear=True,
        ),
    ):
        for _ in range(2):
            assert (
                interface.route_to_vendor(
                    "get_news", "600519.SS", "2026-08-01", "2026-08-16"
                )
                == "news"
            )

    assert vendor.call_count == 2
    assert len(records) == 2
