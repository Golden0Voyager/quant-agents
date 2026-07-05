"""Tests for fund_flow_tools module.

All 4 ``@tool``-decorated functions are ``StructuredTool`` objects that
delegate to ``route_to_vendor``. Tests use ``.invoke()`` and mock
``route_to_vendor`` at module level.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.fund_flow_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_fund_flow
# ===================================================================


@pytest.mark.unit
class TestGetFundFlow:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fund_flow_tools import get_fund_flow

        with _make_mock_vendor("fund flow data") as mock_route:
            result = get_fund_flow.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with("get_fund_flow", "AAPL")
        assert result == "fund flow data"

    def test_different_ticker(self):
        from tradingagents.agents.utils.fund_flow_tools import get_fund_flow

        with _make_mock_vendor() as mock_route:
            get_fund_flow.invoke({"ticker": "MSFT"})

        mock_route.assert_called_once_with("get_fund_flow", "MSFT")


# ===================================================================
# get_northbound_hold
# ===================================================================


@pytest.mark.unit
class TestGetNorthboundHold:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fund_flow_tools import get_northbound_hold

        with _make_mock_vendor("northbound data") as mock_route:
            result = get_northbound_hold.invoke({"ticker": "600519.SS"})

        mock_route.assert_called_once_with("get_northbound_hold", "600519.SS")
        assert result == "northbound data"

    def test_different_ticker(self):
        from tradingagents.agents.utils.fund_flow_tools import get_northbound_hold

        with _make_mock_vendor() as mock_route:
            get_northbound_hold.invoke({"ticker": "000858.SZ"})

        mock_route.assert_called_once_with("get_northbound_hold", "000858.SZ")


# ===================================================================
# get_margin_trading
# ===================================================================


@pytest.mark.unit
class TestGetMarginTrading:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fund_flow_tools import get_margin_trading

        with _make_mock_vendor("margin data") as mock_route:
            result = get_margin_trading.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with("get_margin_trading", "AAPL")
        assert result == "margin data"

    def test_different_ticker(self):
        from tradingagents.agents.utils.fund_flow_tools import get_margin_trading

        with _make_mock_vendor() as mock_route:
            get_margin_trading.invoke({"ticker": "TSLA"})

        mock_route.assert_called_once_with("get_margin_trading", "TSLA")


# ===================================================================
# get_sector_fund_flow
# ===================================================================


@pytest.mark.unit
class TestGetSectorFundFlow:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.fund_flow_tools import get_sector_fund_flow

        with _make_mock_vendor("sector flow data") as mock_route:
            result = get_sector_fund_flow.invoke({"sector_name": "白酒"})

        mock_route.assert_called_once_with("get_sector_fund_flow", "白酒")
        assert result == "sector flow data"

    def test_different_sector(self):
        from tradingagents.agents.utils.fund_flow_tools import get_sector_fund_flow

        with _make_mock_vendor() as mock_route:
            get_sector_fund_flow.invoke({"sector_name": "新能源"})

        mock_route.assert_called_once_with("get_sector_fund_flow", "新能源")


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestFundFlowToolsErrorPropagation:
    """All 4 functions propagate exceptions from route_to_vendor."""

    def _check_propagates(self, tool, kwargs):
        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                tool.invoke(kwargs)

    def test_get_fund_flow_propagates(self):
        from tradingagents.agents.utils.fund_flow_tools import get_fund_flow

        self._check_propagates(get_fund_flow, {"ticker": "AAPL"})

    def test_get_northbound_hold_propagates(self):
        from tradingagents.agents.utils.fund_flow_tools import get_northbound_hold

        self._check_propagates(get_northbound_hold, {"ticker": "600519.SS"})

    def test_get_margin_trading_propagates(self):
        from tradingagents.agents.utils.fund_flow_tools import get_margin_trading

        self._check_propagates(get_margin_trading, {"ticker": "AAPL"})

    def test_get_sector_fund_flow_propagates(self):
        from tradingagents.agents.utils.fund_flow_tools import get_sector_fund_flow

        self._check_propagates(get_sector_fund_flow, {"sector_name": "银行"})


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestFundFlowToolsMetadata:
    """Verify each function is a @tool-decorated StructuredTool."""

    def _check_is_tool(self, func):
        assert hasattr(func, "name")
        assert hasattr(func, "args")
        assert hasattr(func, "invoke")

    def test_get_fund_flow_is_tool(self):
        from tradingagents.agents.utils.fund_flow_tools import get_fund_flow

        self._check_is_tool(get_fund_flow)
        assert get_fund_flow.name == "get_fund_flow"

    def test_get_northbound_hold_is_tool(self):
        from tradingagents.agents.utils.fund_flow_tools import get_northbound_hold

        self._check_is_tool(get_northbound_hold)
        assert get_northbound_hold.name == "get_northbound_hold"

    def test_get_margin_trading_is_tool(self):
        from tradingagents.agents.utils.fund_flow_tools import get_margin_trading

        self._check_is_tool(get_margin_trading)
        assert get_margin_trading.name == "get_margin_trading"

    def test_get_sector_fund_flow_is_tool(self):
        from tradingagents.agents.utils.fund_flow_tools import get_sector_fund_flow

        self._check_is_tool(get_sector_fund_flow)
        assert get_sector_fund_flow.name == "get_sector_fund_flow"
