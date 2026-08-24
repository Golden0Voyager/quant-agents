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


# ===================================================================
# get_concept_board
# ===================================================================


@pytest.mark.unit
class TestGetConceptBoard:
    def test_returns_concept_board(self):
        from tradingagents.agents.utils.industry_data_tools import get_concept_board

        with patch(
            "tradingagents.agents.utils.industry_data_tools.route_to_vendor",
            return_value="concept data: AI, cloud computing",
        ) as mock_cb:
            result = get_concept_board.invoke({"ticker": "600519.SS"})

        mock_cb.assert_called_once_with("get_concept_board", "600519.SS")
        assert "concept data" in result

    def test_error_returns_no_data_message(self):
        from tradingagents.agents.utils.industry_data_tools import get_concept_board

        with patch(
            "tradingagents.agents.utils.industry_data_tools.route_to_vendor",
            return_value=(
                "NO_DATA_AVAILABLE: get_concept_board unavailable for "
                "000001.SZ: API failure"
            ),
        ):
            result = get_concept_board.invoke({"ticker": "000001.SZ"})

        assert "NO_DATA_AVAILABLE" in result
        assert "000001.SZ" in result
        assert "API failure" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.industry_data_tools import get_concept_board

        assert hasattr(get_concept_board, "name")
        assert hasattr(get_concept_board, "args")
        assert hasattr(get_concept_board, "invoke")
        assert get_concept_board.name == "get_concept_board"
