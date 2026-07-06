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
        assert "DATA_UNAVAILABLE" in result
        assert "optional" in result

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

