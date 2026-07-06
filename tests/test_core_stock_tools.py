"""Tests for core_stock_tools module.

The single ``@tool``-decorated function ``get_stock_data`` is a
``StructuredTool`` that delegates to ``route_to_vendor``.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
        return_value=return_value,
    )


@pytest.mark.unit
class TestGetStockData:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with _make_mock_vendor("ohlcv data") as mock_route:
            result = get_stock_data.invoke({
                "symbol": "AAPL",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
            })

        mock_route.assert_called_once_with(
            "get_stock_data", "AAPL", "2026-06-01", "2026-06-30"
        )
        assert result == "ohlcv data"

    def test_different_symbol_and_dates(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with _make_mock_vendor() as mock_route:
            get_stock_data.invoke({
                "symbol": "TSLA",
                "start_date": "2026-01-01",
                "end_date": "2026-03-31",
            })

        mock_route.assert_called_once_with(
            "get_stock_data", "TSLA", "2026-01-01", "2026-03-31"
        )

    def test_error_propagates(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_stock_data.invoke({
                    "symbol": "AAPL",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-30",
                })

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        assert hasattr(get_stock_data, "name")
        assert hasattr(get_stock_data, "args")
        assert hasattr(get_stock_data, "invoke")
        assert get_stock_data.name == "get_stock_data"
