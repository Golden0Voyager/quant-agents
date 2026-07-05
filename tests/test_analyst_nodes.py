"""Integration tests for LangGraph analyst node factories.

Each ``create_*_analyst(llm)`` factory produces a LangGraph-compatible node
function.  The node reads from a structured state dict, builds a prompt,
invokes the LLM, and writes results back to the state.

Tests use a mock LLM whose ``bind_tools`` chain returns an empty-tool-call
response so the internal ``report = result.content`` path is exercised.
Previously these node functions were only reachable through a full graph
execution (integration test), resulting in ~12-17& coverage.

Covers the following previously-uncovered node functions:
- ``market_analyst_node``   (:mod:`.market_analyst`)
- ``news_analyst_node``     (:mod:`.news_analyst`)
- ``fundamentals_analyst_node``  (:mod:`.fundamentals_analyst`)
- ``industry_analyst_node`` (:mod:`.industry_analyst`)
- ``governance_analyst_node``    (:mod:`.governance_analyst`)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_llm() -> MagicMock:
    """Return a mock LLM whose bind_tools chain returns an AIMessage-like result.

    ``result.tool_calls = []`` triggers the ``report = result.content`` path
    that all analyst nodes use to extract the written report.
    """
    result = MagicMock()
    result.content = "**Analyst Report**: AAPL showing strength."
    result.tool_calls = []

    bound = MagicMock()
    bound.return_value = result

    llm = MagicMock()
    llm.bind_tools.return_value = bound
    return llm


def _make_state(**overrides) -> dict:
    """Build a minimal state dict suitable for any analyst node."""
    state = {
        "company_of_interest": "AAPL",
        "company_name": "Apple Inc.",
        "trade_date": "2026-01-15",
        "messages": [HumanMessage(content="Analyze AAPL")],
        "instrument_context": "Company: Apple Inc. (AAPL).",
    }
    state.update(overrides)
    return state


# ===================================================================
# Market Analyst
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystNode:
    """Covers :func:`~.market_analyst.create_market_analyst` node internals."""

    def test_returns_callable(self):
        from tradingagents.agents.analysts.market_analyst import create_market_analyst

        node = create_market_analyst(_make_llm())
        assert callable(node)

    def test_returns_expected_keys(self):
        from tradingagents.agents.analysts.market_analyst import create_market_analyst

        node = create_market_analyst(_make_llm())
        result = node(_make_state())
        assert "messages" in result
        assert "market_report" in result

    def test_report_content_from_llm(self):
        from tradingagents.agents.analysts.market_analyst import create_market_analyst

        node = create_market_analyst(_make_llm())
        result = node(_make_state())
        assert result["market_report"] == "**Analyst Report**: AAPL showing strength."

    def test_with_company_name_uses_ticker_guard(self):
        from tradingagents.agents.analysts.market_analyst import create_market_analyst

        node = create_market_analyst(_make_llm())
        result = node(_make_state(company_name="Apple Inc."))
        assert result["market_report"] is not None

    def test_without_company_name(self):
        from tradingagents.agents.analysts.market_analyst import create_market_analyst

        node = create_market_analyst(_make_llm())
        result = node(_make_state(company_name=""))
        assert result["market_report"] is not None


# ===================================================================
# News Analyst
# ===================================================================


@pytest.mark.unit
class TestNewsAnalystNode:
    """Covers :func:`~.news_analyst.create_news_analyst` node internals."""

    def test_returns_callable(self):
        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        node = create_news_analyst(_make_llm())
        assert callable(node)

    def test_returns_expected_keys(self):
        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        node = create_news_analyst(_make_llm())
        result = node(_make_state(asset_type="stock"))
        assert "messages" in result
        assert "news_report" in result

    def test_report_content_from_llm(self):
        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        node = create_news_analyst(_make_llm())
        result = node(_make_state(asset_type="stock"))
        assert result["news_report"] == "**Analyst Report**: AAPL showing strength."

    def test_with_company_name(self):
        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        node = create_news_analyst(_make_llm())
        result = node(_make_state(company_name="Apple Inc.", asset_type="stock"))
        assert result["news_report"] is not None

    def test_without_company_name(self):
        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        node = create_news_analyst(_make_llm())
        result = node(_make_state(company_name="", asset_type="stock"))
        assert result["news_report"] is not None

    def test_crypto_asset_type(self):
        from tradingagents.agents.analysts.news_analyst import create_news_analyst

        node = create_news_analyst(_make_llm())
        result = node(_make_state(asset_type="crypto", company_name=""))
        assert result["news_report"] is not None


# ===================================================================
# Fundamentals Analyst
# ===================================================================


@pytest.mark.unit
class TestFundamentalsAnalystNode:
    """Covers :func:`~.fundamentals_analyst.create_fundamentals_analyst` node."""

    def test_returns_callable(self):
        from tradingagents.agents.analysts.fundamentals_analyst import (
            create_fundamentals_analyst,
        )

        node = create_fundamentals_analyst(_make_llm())
        assert callable(node)

    def test_returns_expected_keys(self):
        from tradingagents.agents.analysts.fundamentals_analyst import (
            create_fundamentals_analyst,
        )

        node = create_fundamentals_analyst(_make_llm())
        result = node(_make_state())
        assert "messages" in result
        assert "fundamentals_report" in result

    def test_report_content_from_llm(self):
        from tradingagents.agents.analysts.fundamentals_analyst import (
            create_fundamentals_analyst,
        )

        node = create_fundamentals_analyst(_make_llm())
        result = node(_make_state())
        assert result["fundamentals_report"] == "**Analyst Report**: AAPL showing strength."

    def test_without_company_name(self):
        """fundamentals_analyst does not use company_name from state."""
        from tradingagents.agents.analysts.fundamentals_analyst import (
            create_fundamentals_analyst,
        )

        node = create_fundamentals_analyst(_make_llm())
        result = node(_make_state(company_name=""))
        assert result["fundamentals_report"] is not None


# ===================================================================
# Industry Analyst
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystNode:
    """Covers :func:`~.industry_analyst.create_industry_analyst` node."""

    def test_returns_callable(self):
        from tradingagents.agents.analysts.industry_analyst import create_industry_analyst

        node = create_industry_analyst(_make_llm())
        assert callable(node)

    def test_returns_expected_keys(self):
        from tradingagents.agents.analysts.industry_analyst import create_industry_analyst

        node = create_industry_analyst(_make_llm())
        result = node(_make_state())
        assert "messages" in result
        assert "industry_report" in result

    def test_report_content_from_llm(self):
        from tradingagents.agents.analysts.industry_analyst import create_industry_analyst

        node = create_industry_analyst(_make_llm())
        result = node(_make_state())
        assert result["industry_report"] == "**Analyst Report**: AAPL showing strength."

    def test_with_company_name(self):
        from tradingagents.agents.analysts.industry_analyst import create_industry_analyst

        node = create_industry_analyst(_make_llm())
        result = node(_make_state(company_name="Apple Inc."))
        assert result["industry_report"] is not None

    def test_without_company_name(self):
        from tradingagents.agents.analysts.industry_analyst import create_industry_analyst

        node = create_industry_analyst(_make_llm())
        result = node(_make_state(company_name=""))
        assert result["industry_report"] is not None


# ===================================================================
# Governance Analyst
# ===================================================================


@pytest.mark.unit
class TestGovernanceAnalystNode:
    """Covers :func:`~.governance_analyst.create_governance_analyst` node."""

    def test_returns_callable(self):
        from tradingagents.agents.analysts.governance_analyst import (
            create_governance_analyst,
        )

        node = create_governance_analyst(_make_llm())
        assert callable(node)

    def test_returns_expected_keys(self):
        from tradingagents.agents.analysts.governance_analyst import (
            create_governance_analyst,
        )

        node = create_governance_analyst(_make_llm())
        result = node(_make_state())
        assert "messages" in result
        assert "governance_report" in result

    def test_report_content_from_llm(self):
        from tradingagents.agents.analysts.governance_analyst import (
            create_governance_analyst,
        )

        node = create_governance_analyst(_make_llm())
        result = node(_make_state())
        assert result["governance_report"] == "**Analyst Report**: AAPL showing strength."

    def test_with_company_name(self):
        from tradingagents.agents.analysts.governance_analyst import (
            create_governance_analyst,
        )

        node = create_governance_analyst(_make_llm())
        result = node(_make_state(company_name="Apple Inc."))
        assert result["governance_report"] is not None

    def test_without_company_name(self):
        from tradingagents.agents.analysts.governance_analyst import (
            create_governance_analyst,
        )

        node = create_governance_analyst(_make_llm())
        result = node(_make_state(company_name=""))
        assert result["governance_report"] is not None
