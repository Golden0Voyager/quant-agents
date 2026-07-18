"""Tests for index_data_tools module.

The single ``@tool``-decorated function ``get_index_daily`` is a
``StructuredTool`` that delegates to ``route_to_vendor``.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.index_data_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_index_daily
# ===================================================================


@pytest.mark.unit
class TestGetIndexDaily:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.index_data_tools import get_index_daily

        with _make_mock_vendor("OHLCV data") as mock_route:
            result = get_index_daily.invoke({
                "index_code": "000001.SS",
                "start_date": "2026-01-01",
                "end_date": "2026-01-10",
            })

        mock_route.assert_called_once_with(
            "get_index_daily", "000001.SS", "2026-01-01", "2026-01-10",
        )
        assert result == "OHLCV data"

    def test_different_index_code(self):
        from tradingagents.agents.utils.index_data_tools import get_index_daily

        with _make_mock_vendor() as mock_route:
            get_index_daily.invoke({
                "index_code": "399001.SZ",
                "start_date": "2026-02-01",
                "end_date": "2026-02-28",
            })

        mock_route.assert_called_once_with(
            "get_index_daily", "399001.SZ", "2026-02-01", "2026-02-28",
        )


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestIndexDataToolsErrorPropagation:
    """Exceptions from route_to_vendor propagate through the tool."""

    def test_get_index_daily_propagates(self):
        from tradingagents.agents.utils.index_data_tools import get_index_daily

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_index_daily.invoke({
                    "index_code": "000001.SS",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-10",
                })


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestIndexDataToolsMetadata:
    """Verify the function is a @tool-decorated StructuredTool."""

    def test_get_index_daily_is_tool(self):
        from tradingagents.agents.utils.index_data_tools import get_index_daily

        assert hasattr(get_index_daily, "name")
        assert hasattr(get_index_daily, "args")
        assert hasattr(get_index_daily, "invoke")
        assert get_index_daily.name == "get_index_daily"
