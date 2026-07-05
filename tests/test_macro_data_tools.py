"""Tests for macro_data_tools module.

The single ``@tool``-decorated function ``get_macro_indicators`` is a
``StructuredTool`` that delegates to ``route_to_vendor``.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.macro_data_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_macro_indicators
# ===================================================================


@pytest.mark.unit
class TestGetMacroIndicators:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor("macro report") as mock_route:
            result = get_macro_indicators.invoke({
                "indicator": "cpi",
                "curr_date": "2026-07-15",
                "look_back_days": 365,
            })

        mock_route.assert_called_once_with(
            "get_macro_indicators", "cpi", "2026-07-15", 365
        )
        assert result == "macro report"

    def test_default_curr_date_and_lookback(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor("report") as mock_route:
            get_macro_indicators.invoke({"indicator": "unemployment"})

        mock_route.assert_called_once_with(
            "get_macro_indicators", "unemployment", None, None
        )

    def test_different_indicator_and_date(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor() as mock_route:
            get_macro_indicators.invoke({
                "indicator": "10y_treasury",
                "curr_date": "2026-06-01",
                "look_back_days": 90,
            })

        mock_route.assert_called_once_with(
            "get_macro_indicators", "10y_treasury", "2026-06-01", 90
        )


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestMacroDataToolsErrorPropagation:
    """All functions propagate exceptions from route_to_vendor."""

    def test_get_macro_indicators_propagates(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_macro_indicators.invoke({
                    "indicator": "cpi",
                    "curr_date": "2026-07-15",
                    "look_back_days": 365,
                })


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestMacroDataToolsMetadata:
    """Verify the function is a @tool-decorated StructuredTool."""

    def test_get_macro_indicators_is_tool(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        assert hasattr(get_macro_indicators, "name")
        assert hasattr(get_macro_indicators, "args")
        assert hasattr(get_macro_indicators, "invoke")
        assert get_macro_indicators.name == "get_macro_indicators"
