"""Tests for prediction_markets_tools module.

The single ``@tool``-decorated function ``get_prediction_markets`` is a
``StructuredTool`` that delegates to ``route_to_vendor``.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.prediction_markets_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_prediction_markets
# ===================================================================


@pytest.mark.unit
class TestGetPredictionMarkets:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets

        with _make_mock_vendor("poly data") as mock_route:
            result = get_prediction_markets.invoke({
                "topic": "Fed rate cut",
                "limit": 10,
            })

        mock_route.assert_called_once_with("get_prediction_markets", "Fed rate cut", 10)
        assert result == "poly data"

    def test_default_limit(self):
        from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets

        with _make_mock_vendor() as mock_route:
            get_prediction_markets.invoke({"topic": "US election"})

        mock_route.assert_called_once_with("get_prediction_markets", "US election", None)

    def test_different_topic(self):
        from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets

        with _make_mock_vendor() as mock_route:
            get_prediction_markets.invoke({
                "topic": "recession 2026",
                "limit": 3,
            })

        mock_route.assert_called_once_with("get_prediction_markets", "recession 2026", 3)


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestPredictionMarketsToolsErrorPropagation:
    """All functions propagate exceptions from route_to_vendor."""

    def test_get_prediction_markets_propagates(self):
        from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_prediction_markets.invoke({"topic": "crypto", "limit": 5})


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestPredictionMarketsToolsMetadata:
    """Verify the function is a @tool-decorated StructuredTool."""

    def test_get_prediction_markets_is_tool(self):
        from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets

        assert hasattr(get_prediction_markets, "name")
        assert hasattr(get_prediction_markets, "args")
        assert hasattr(get_prediction_markets, "invoke")
        assert get_prediction_markets.name == "get_prediction_markets"
