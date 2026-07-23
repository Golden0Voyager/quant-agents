"""Tests for fundamental_data_tools module.

All 7 ``@tool``-decorated functions are ``StructuredTool`` objects (not
directly callable) that delegate to ``route_to_vendor``. Tests use
``.invoke()`` to call them and mock ``route_to_vendor`` at module level.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    """Patch route_to_vendor at the fundamental_data_tools module level."""
    return patch(
        "tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_fundamentals
# ===================================================================


@pytest.mark.unit
class TestGetFundamentals:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals

        with _make_mock_vendor("fundamentals data") as mock_route:
            result = get_fundamentals.invoke({"ticker": "AAPL", "curr_date": "2026-07-03"})

        mock_route.assert_called_once_with("get_fundamentals", "AAPL", "2026-07-03")
        assert result == "fundamentals data"

    def test_different_ticker(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals

        with _make_mock_vendor() as mock_route:
            get_fundamentals.invoke({"ticker": "MSFT", "curr_date": "2026-07-03"})

        mock_route.assert_called_once_with("get_fundamentals", "MSFT", "2026-07-03")


# ===================================================================
# get_balance_sheet
# ===================================================================


@pytest.mark.unit
class TestGetBalanceSheet:
    def test_calls_route_to_vendor_with_defaults(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_balance_sheet

        with _make_mock_vendor("balance sheet") as mock_route:
            result = get_balance_sheet.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with(
            "get_balance_sheet", "AAPL", "quarterly", None
        )
        assert result == "balance sheet"

    def test_custom_freq_and_date(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_balance_sheet

        with _make_mock_vendor() as mock_route:
            get_balance_sheet.invoke({
                "ticker": "AAPL", "freq": "annual", "curr_date": "2026-06-15"
            })

        mock_route.assert_called_once_with(
            "get_balance_sheet", "AAPL", "annual", "2026-06-15"
        )


# ===================================================================
# get_cashflow
# ===================================================================


@pytest.mark.unit
class TestGetCashFlow:
    def test_calls_route_to_vendor_with_defaults(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_cashflow

        with _make_mock_vendor("cashflow data") as mock_route:
            result = get_cashflow.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with(
            "get_cashflow", "AAPL", "quarterly", None
        )
        assert result == "cashflow data"

    def test_custom_freq(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_cashflow

        with _make_mock_vendor() as mock_route:
            get_cashflow.invoke({"ticker": "AAPL", "freq": "annual"})

        mock_route.assert_called_once_with(
            "get_cashflow", "AAPL", "annual", None
        )


# ===================================================================
# get_income_statement
# ===================================================================


@pytest.mark.unit
class TestGetIncomeStatement:
    def test_calls_route_to_vendor_with_defaults(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_income_statement

        with _make_mock_vendor("income statement") as mock_route:
            result = get_income_statement.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with(
            "get_income_statement", "AAPL", "quarterly", None
        )
        assert result == "income statement"

    def test_custom_freq(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_income_statement

        with _make_mock_vendor() as mock_route:
            get_income_statement.invoke({"ticker": "AAPL", "freq": "annual"})

        mock_route.assert_called_once_with(
            "get_income_statement", "AAPL", "annual", None
        )


# ===================================================================
# get_earnings_estimates
# ===================================================================


@pytest.mark.unit
class TestGetEarningsEstimates:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_estimates

        with _make_mock_vendor("EPS estimates") as mock_route:
            result = get_earnings_estimates.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with("get_earnings_estimates", "AAPL")
        assert result == "EPS estimates"

    def test_different_ticker(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_estimates

        with _make_mock_vendor() as mock_route:
            get_earnings_estimates.invoke({"ticker": "TSLA"})

        mock_route.assert_called_once_with("get_earnings_estimates", "TSLA")


# ===================================================================
# get_shareholder_count
# ===================================================================


@pytest.mark.unit
class TestGetShareholderCount:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_shareholder_count

        with _make_mock_vendor("10000 shareholders") as mock_route:
            result = get_shareholder_count.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with("get_shareholder_count", "AAPL")
        assert result == "10000 shareholders"

    def test_with_curr_date_includes_date_arg(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_shareholder_count

        with _make_mock_vendor("data with date") as mock_route:
            result = get_shareholder_count.invoke({
                "ticker": "AAPL", "curr_date": "2026-07-03"
            })

        mock_route.assert_called_once_with(
            "get_shareholder_count", "AAPL", "2026-07-03"
        )
        assert result == "data with date"


# ===================================================================
# get_dividend_history
# ===================================================================


@pytest.mark.unit
class TestGetDividendHistory:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_dividend_history

        with _make_mock_vendor("dividend history") as mock_route:
            result = get_dividend_history.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with("get_dividend_history", "AAPL")
        assert result == "dividend history"


# ===================================================================
# get_historical_valuation
# ===================================================================


@pytest.mark.unit
class TestGetHistoricalValuation:
    def test_returns_historical_valuation(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_historical_valuation

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_historical_valuation",
            return_value="PE percentile: 25%",
        ) as mock_val:
            result = get_historical_valuation.invoke({"ticker": "600519.SS"})

        mock_val.assert_called_once_with("600519.SS", None)
        assert "PE percentile" in result

    def test_error_returns_no_data_message(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_historical_valuation

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_historical_valuation",
            side_effect=ValueError("API error"),
        ):
            result = get_historical_valuation.invoke({"ticker": "000001.SZ"})

        assert "NO_DATA_AVAILABLE" in result
        assert "000001.SZ" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_historical_valuation

        assert hasattr(get_historical_valuation, "name")
        assert get_historical_valuation.name == "get_historical_valuation"


# ===================================================================
# get_earnings_forecast
# ===================================================================


@pytest.mark.unit
class TestGetEarningsForecast:
    def test_returns_earnings_forecast(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_forecast

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_earnings_forecast",
            return_value="EPS growth: 15% YoY",
        ) as mock_ef:
            result = get_earnings_forecast.invoke({"ticker": "600519.SS"})

        mock_ef.assert_called_once_with("600519.SS")
        assert "EPS growth" in result

    def test_error_returns_no_data_message(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_forecast

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_earnings_forecast",
            side_effect=RuntimeError("timeout"),
        ):
            result = get_earnings_forecast.invoke({"ticker": "TSLA"})

        assert "NO_DATA_AVAILABLE" in result
        assert "TSLA" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_forecast

        assert hasattr(get_earnings_forecast, "name")
        assert get_earnings_forecast.name == "get_earnings_forecast"


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestFundamentalDataToolsErrorPropagation:
    """All 7 functions should propagate exceptions from route_to_vendor."""

    def _check_propagates(self, tool, kwargs):
        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                tool.invoke(kwargs)

    def test_get_fundamentals_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals

        self._check_propagates(get_fundamentals, {"ticker": "AAPL", "curr_date": "2026-07-03"})

    def test_get_balance_sheet_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_balance_sheet

        self._check_propagates(get_balance_sheet, {"ticker": "AAPL"})

    def test_get_cashflow_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_cashflow

        self._check_propagates(get_cashflow, {"ticker": "AAPL"})

    def test_get_income_statement_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_income_statement

        self._check_propagates(get_income_statement, {"ticker": "AAPL"})

    def test_get_earnings_estimates_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_estimates

        self._check_propagates(get_earnings_estimates, {"ticker": "AAPL"})

    def test_get_shareholder_count_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_shareholder_count

        self._check_propagates(get_shareholder_count, {"ticker": "AAPL"})

    def test_get_dividend_history_propagates(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_dividend_history

        self._check_propagates(get_dividend_history, {"ticker": "AAPL"})


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestFundamentalDataToolsMetadata:
    """Verify each function is a @tool-decorated LangChain tool."""

    def _check_is_tool(self, func):
        assert hasattr(func, "name")
        assert hasattr(func, "args")
        assert hasattr(func, "invoke")

    def test_get_fundamentals_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals

        self._check_is_tool(get_fundamentals)
        assert get_fundamentals.name == "get_fundamentals"

    def test_get_balance_sheet_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_balance_sheet

        self._check_is_tool(get_balance_sheet)
        assert get_balance_sheet.name == "get_balance_sheet"

    def test_get_cashflow_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_cashflow

        self._check_is_tool(get_cashflow)
        assert get_cashflow.name == "get_cashflow"

    def test_get_income_statement_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_income_statement

        self._check_is_tool(get_income_statement)
        assert get_income_statement.name == "get_income_statement"

    def test_get_earnings_estimates_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_earnings_estimates

        self._check_is_tool(get_earnings_estimates)
        assert get_earnings_estimates.name == "get_earnings_estimates"

    def test_get_shareholder_count_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_shareholder_count

        self._check_is_tool(get_shareholder_count)
        assert get_shareholder_count.name == "get_shareholder_count"

    def test_get_dividend_history_is_tool(self):
        from tradingagents.agents.utils.fundamental_data_tools import get_dividend_history

        self._check_is_tool(get_dividend_history)
        assert get_dividend_history.name == "get_dividend_history"
