"""Tests for industry_data_tools module.

The single ``@tool``-decorated function ``get_industry_valuation`` is a
``StructuredTool`` that delegates to ``route_to_vendor``.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.industry_data_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_industry_valuation
# ===================================================================


@pytest.mark.unit
class TestGetIndustryValuation:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.industry_data_tools import get_industry_valuation

        with _make_mock_vendor("industry PE/PB data") as mock_route:
            result = get_industry_valuation.invoke({"ticker": "AAPL"})

        mock_route.assert_called_once_with("get_industry_valuation", "AAPL")
        assert result == "industry PE/PB data"

    def test_different_ticker(self):
        from tradingagents.agents.utils.industry_data_tools import get_industry_valuation

        with _make_mock_vendor() as mock_route:
            get_industry_valuation.invoke({"ticker": "600519.SS"})

        mock_route.assert_called_once_with("get_industry_valuation", "600519.SS")


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestIndustryDataToolsErrorPropagation:
    """All functions propagate exceptions from route_to_vendor."""

    def test_get_industry_valuation_propagates(self):
        from tradingagents.agents.utils.industry_data_tools import get_industry_valuation

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_industry_valuation.invoke({"ticker": "AAPL"})


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestIndustryDataToolsMetadata:
    """Verify the function is a @tool-decorated StructuredTool."""

    def test_get_industry_valuation_is_tool(self):
        from tradingagents.agents.utils.industry_data_tools import get_industry_valuation

        assert hasattr(get_industry_valuation, "name")
        assert hasattr(get_industry_valuation, "args")
        assert hasattr(get_industry_valuation, "invoke")
        assert get_industry_valuation.name == "get_industry_valuation"
