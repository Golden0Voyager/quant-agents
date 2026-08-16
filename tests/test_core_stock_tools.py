"""Tests for core_stock_tools module."""

from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestGetStockData:
    def test_routes_stock_data_through_shared_runtime(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value="Date,Open,High,Low,Close,Volume\n2026-06-10,1,2,1,2,100",
        ) as mock_route:
            result = get_stock_data.invoke({
                "symbol": "AAPL",
                "start_date": "2026-06-10",
                "end_date": "2026-06-15",
            })

        mock_route.assert_called_once_with(
            "get_stock_data", "AAPL", "2026-06-10", "2026-06-15"
        )
        assert "Date,Open,High,Low,Close,Volume" in result
        assert "2026-06-10" in result

    def test_different_symbol_and_dates(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value="stock data",
        ) as mock_route:
            get_stock_data.invoke({
                "symbol": "TSLA",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
            })

        mock_route.assert_called_once_with(
            "get_stock_data", "TSLA", "2026-06-01", "2026-06-30"
        )

    def test_route_failure_sentinel_is_returned(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value="NO_DATA_AVAILABLE: vendor error",
        ):
            result = get_stock_data.invoke({
                "symbol": "AAPL",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
            })
        assert "NO_DATA_AVAILABLE" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        assert hasattr(get_stock_data, "name")
        assert hasattr(get_stock_data, "args")
        assert hasattr(get_stock_data, "invoke")
        assert get_stock_data.name == "get_stock_data"


@pytest.mark.unit
class TestGetChipDistribution:
    def test_returns_chip_distribution(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        with patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value="chip data: 70% concentrated at 85",
        ) as mock_inner:
            result = get_chip_distribution.invoke({
                "symbol": "600519.SS",
            })
        mock_inner.assert_called_once_with("get_chip_distribution", "600519.SS")
        assert "chip data" in result

    def test_returns_chip_distribution_with_curr_date(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        with patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value="chip data with date",
        ) as mock_inner:
            result = get_chip_distribution.invoke({
                "symbol": "000001.SZ",
                "curr_date": "2026-07-03",
            })
        mock_inner.assert_called_once_with(
            "get_chip_distribution", "000001.SZ", "2026-07-03"
        )
        assert "chip data" in result

    def test_error_returns_no_data_message(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        with patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value=(
                "NO_DATA_AVAILABLE: get_chip_distribution unavailable for "
                "600519.SS: API error"
            ),
        ):
            result = get_chip_distribution.invoke({
                "symbol": "600519.SS",
            })
        assert "NO_DATA_AVAILABLE" in result
        assert "600519.SS" in result
        assert "API error" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        assert hasattr(get_chip_distribution, "name")
        assert hasattr(get_chip_distribution, "args")
        assert hasattr(get_chip_distribution, "invoke")
        assert get_chip_distribution.name == "get_chip_distribution"
