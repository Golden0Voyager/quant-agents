from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestRouteToVendor:
    def test_a_share_prefers_akshare(self):
        """A-share tickers (.SS/.SZ/.BJ) should be served by akshare first."""
        from tradingagents.dataflows import interface

        fake_ak = MagicMock(return_value="AKSHARE_RESULT")
        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"akshare": fake_ak, "yfinance": fake_yf},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "600519.SS", "2026-05-14"
            )
        assert result == "AKSHARE_RESULT"
        fake_ak.assert_called_once_with("600519.SS", "2026-05-14")
        fake_yf.assert_not_called()

    def test_us_share_keeps_yfinance(self):
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
        fake_ak.assert_called_once()
        fake_yf.assert_called_once()

    def test_a_share_falls_back_to_yfinance_on_rate_limit(self):
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.alpha_vantage_common import (
            AlphaVantageRateLimitError,
        )

        def ak_raises(*a, **kw):
            raise AlphaVantageRateLimitError("simulated akshare unavailability")

        fake_yf = MagicMock(return_value="YFINANCE_RESULT")
        with patch.dict(
            interface.VENDOR_METHODS["get_fundamentals"],
            {"akshare": ak_raises, "yfinance": fake_yf},
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

