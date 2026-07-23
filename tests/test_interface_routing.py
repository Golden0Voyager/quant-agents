import unittest
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dataflows.symbol_utils import NoMarketDataError


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

        fake_ak = MagicMock(return_value="AKSHARE_RESULT")
        # get_pledge_ratio belongs to governance_risk which has NO entry in
        # default data_vendors → get_vendor returns "default" → primary_vendors = ["default"]
        # → smartmoney_db NOT in primary_vendors → else branch (line 348)
        with patch.dict(
            interface.VENDOR_METHODS["get_pledge_ratio"],
            {"akshare": fake_ak},
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

    def test_non_ashare_returns_data_unavailable_when_all_vendors_ashare_only(self):
        """
        Non-A-share ticker with only A-share-only vendors returns DATA_UNAVAILABLE.
        Covers lines 382-387.
        """
        from tradingagents.dataflows import interface

        with patch("tradingagents.dataflows.interface.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "data_vendors": {"governance_risk": "akshare"},
                "tool_vendors": {},
            }
            result = interface.route_to_vendor("get_pledge_ratio", "AAPL")
        assert "DATA_UNAVAILABLE" in result
        assert "A-share only" in result

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

    def test_empty_vendor_chain_raises_runtime_error(self):
        """
        When no vendor is available and none raised errors, raise RuntimeError.
        Covers lines 486-487.
        """
        from tradingagents.dataflows import interface

        with patch.dict(interface.VENDOR_METHODS, {"get_pledge_ratio": {}}, clear=False), \
             pytest.raises(RuntimeError, match="No available vendor"):
            interface.route_to_vendor("get_pledge_ratio", "600519.SS")


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
