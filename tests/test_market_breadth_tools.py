"""Tests for market_breadth_tools module.

The single ``@tool``-decorated function ``get_limit_up_down`` is a
``StructuredTool`` that delegates to ``route_to_vendor``.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.market_breadth_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_limit_up_down
# ===================================================================


@pytest.mark.unit
class TestGetLimitUpDown:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.market_breadth_tools import get_limit_up_down

        with _make_mock_vendor("limit-up/down stats") as mock_route:
            result = get_limit_up_down.invoke({"trade_date": "2026-01-10"})

        mock_route.assert_called_once_with("get_limit_up_down", "2026-01-10")
        assert result == "limit-up/down stats"

    def test_different_date(self):
        from tradingagents.agents.utils.market_breadth_tools import get_limit_up_down

        with _make_mock_vendor() as mock_route:
            get_limit_up_down.invoke({"trade_date": "2026-06-15"})

        mock_route.assert_called_once_with("get_limit_up_down", "2026-06-15")


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestMarketBreadthToolsErrorPropagation:
    """Exceptions from route_to_vendor propagate through the tool."""

    def test_get_limit_up_down_propagates(self):
        from tradingagents.agents.utils.market_breadth_tools import get_limit_up_down

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_limit_up_down.invoke({"trade_date": "2026-01-10"})


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestMarketBreadthToolsMetadata:
    """Verify the function is a @tool-decorated StructuredTool."""

    def test_get_limit_up_down_is_tool(self):
        from tradingagents.agents.utils.market_breadth_tools import get_limit_up_down

        assert hasattr(get_limit_up_down, "name")
        assert hasattr(get_limit_up_down, "args")
        assert hasattr(get_limit_up_down, "invoke")
        assert get_limit_up_down.name == "get_limit_up_down"
