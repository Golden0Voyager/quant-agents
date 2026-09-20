"""Tests for the Industry Analyst agent node.

``industry_analyst.py`` exports ``create_industry_analyst(llm)`` which returns a
LangGraph-compatible node. The node reads from a structured state dict, builds
a prompt with tools (get_industry_valuation), invokes the LLM, sanitises the
report, and writes results back to the state.
"""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.analysts.industry_analyst import (
    create_industry_analyst,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_STATE = {
    "company_of_interest": "AAPL",
    "trade_date": "2026-07-03",
    "company_name": "Apple Inc.",
    "asset_type": "stock",
    "messages": [],
}


def _make_llm(content: str = "Industry analysis report", tool_calls: list | None = None) -> MagicMock:
    """Create a mock LLM whose ``bind_tools()()`` returns a controlled result.

    LangChain's RunnableSequence wraps a MagicMock in RunnableLambda, which
    calls the mock via ``__call__`` rather than ``.invoke()``.
    """
    result = MagicMock()
    result.content = content
    result.tool_calls = tool_calls or []
    result.additional_kwargs = {}

    bound_llm = MagicMock()
    bound_llm.return_value = result

    llm = MagicMock()
    llm.bind_tools.return_value = bound_llm
    return llm


# ===================================================================
# Factory behaviour
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystFactory:
    """Tests that the factory function returns a callable node."""

    def test_returns_callable(self):
        node = create_industry_analyst(_make_llm())
        assert callable(node)

    def test_returns_callable_with_any_llm(self):
        node = create_industry_analyst("not_a_real_llm")
        assert callable(node)


# ===================================================================
# Core execution paths
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystExecution:
    """Tests for the core execution logic of the industry analyst node."""

    def test_returns_expected_keys(self):
        """Verify the return dict has ``messages`` and ``industry_report``."""
        llm = _make_llm(content="Industry valuation report")
        node = create_industry_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert "messages" in result
        assert "industry_report" in result

    def test_no_tool_calls_uses_content_as_report(self):
        """When the LLM returns no tool calls, the raw content is the report."""
        llm = _make_llm(content="Direct industry analysis")
        node = create_industry_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert result["industry_report"] == "Direct industry analysis"

    def test_with_tool_calls_report_stays_empty(self):
        """When the LLM returns tool calls, the report stays empty."""
        llm = _make_llm(
            content="Analysis after tool calls",
            tool_calls=[{"id": "call_1", "name": "get_industry_valuation", "args": {}}],
        )
        node = create_industry_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert result["industry_report"] == ""

    def test_empty_content_with_tool_calls(self):
        """Empty content with tool calls results in empty report."""
        llm = _make_llm(
            content="",
            tool_calls=[{"id": "call_1", "name": "get_industry_valuation", "args": {"symbol": "AAPL"}}],
        )
        node = create_industry_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert result["industry_report"] == ""

    def test_messages_updated_with_llm_result(self):
        """The LLM result is appended to the messages list."""
        llm_obj = _make_llm(content="Industry analysis complete")
        node = create_industry_analyst(llm_obj)
        result = node(dict(_BASE_STATE))
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == "Industry analysis complete"


# ===================================================================
# Company name / line prefix
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystCompanyLine:
    """Tests for the company_line conditional logic."""

    def test_with_company_name_includes_company_line(self):
        """When company_name is set, the company line appears in the prompt."""
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "Apple Inc." in full_text or "Target company" in full_text

    def test_without_company_name_no_company_line(self):
        """When company_name is empty, the company line is absent."""
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        state = dict(_BASE_STATE)
        state["company_name"] = ""
        node(state)

        full_text = captured.get("full", "")
        assert "Target company:" not in full_text


# ===================================================================
# Instrument context
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystInstrumentContext:
    """Tests for instrument context resolution via build_instrument_context."""

    def test_build_instrument_context_called_with_ticker_and_name(self):
        """build_instrument_context is called with the ticker and company_name."""
        with patch(
            "tradingagents.agents.analysts.industry_analyst.build_instrument_context",
            return_value="Company: Apple Inc.",
        ) as mock_build:
            llm = _make_llm(content="Analysis")
            node = create_industry_analyst(llm)
            node(dict(_BASE_STATE))
            mock_build.assert_called_once_with("AAPL", "Apple Inc.")

    def test_build_instrument_context_without_company_name(self):
        """build_instrument_context passes empty string when company_name missing."""
        with patch(
            "tradingagents.agents.analysts.industry_analyst.build_instrument_context",
            return_value="Ticker: AAPL",
        ) as mock_build:
            llm = _make_llm(content="Analysis")
            node = create_industry_analyst(llm)
            state = dict(_BASE_STATE)
            del state["company_name"]
            node(state)
            mock_build.assert_called_once_with("AAPL", "")


# ===================================================================
# Sanitization
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystSanitization:
    """Tests that the report goes through sanitize_company_name_in_report."""

    def test_sanitize_called_with_report_ticker_company(self):
        with patch(
            "tradingagents.agents.analysts.industry_analyst.sanitize_company_name_in_report",
            return_value="Sanitized report",
        ) as mock_sanitize:
            llm = _make_llm(content="Raw industry report")
            node = create_industry_analyst(llm)
            result = node(dict(_BASE_STATE))

            mock_sanitize.assert_called_once_with("Raw industry report", "AAPL", "Apple Inc.")
            assert result["industry_report"] == "Sanitized report"

    def test_sanitize_skipped_for_empty_report(self):
        with patch(
            "tradingagents.agents.analysts.industry_analyst.sanitize_company_name_in_report",
            return_value="",
        ) as mock_sanitize:
            llm = _make_llm(content="")
            node = create_industry_analyst(llm)
            node(dict(_BASE_STATE))
            mock_sanitize.assert_called_once_with("", "AAPL", "Apple Inc.")


# ===================================================================
# Prompt structure
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystPromptStructure:
    """Tests that the system message includes key prompt sections."""

    def test_system_message_includes_missing_data_protocol(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "Missing Data Protocol" in full_text
        assert "NO_DATA_AVAILABLE" in full_text
        assert "data_availability" in full_text

    def test_system_message_mentions_industry_valuation_only_for_supported_market(self):
        """The prompt mentions industry valuation only when it is available."""
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "get_industry_valuation" not in full_text
        assert "Industry Analyst" in full_text

        node(
            {
                **_BASE_STATE,
                "company_of_interest": "600519.SS",
                "company_name": "Kweichow Moutai",
                "market": "XSHG",
            }
        )
        assert "get_industry_valuation" in captured["full"]

    def test_prompt_includes_trade_date(self):
        """The prompt includes the trade date."""
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "2026-07-03" in full_text


# ===================================================================
# Edge cases
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystEdgeCases:
    """Edge-case tests for state variations."""

    def test_empty_messages_list(self):
        """Empty messages list still produces a valid result."""
        llm = _make_llm(content="Report from empty state")
        node = create_industry_analyst(llm)
        result = node(dict(_BASE_STATE, messages=[]))
        assert result["industry_report"] == "Report from empty state"
        assert len(result["messages"]) == 1

    def test_none_company_name_treated_as_empty(self):
        """None company_name should not produce a company line."""
        llm = _make_llm(content="Analysis without company name")
        node = create_industry_analyst(llm)
        state = dict(_BASE_STATE)
        state["company_name"] = None
        result = node(state)
        assert result["industry_report"] == "Analysis without company name"

    def test_missing_company_name_key(self):
        """Missing company_name key should not raise KeyError."""
        llm = _make_llm(content="Analysis without company name key")
        node = create_industry_analyst(llm)
        state = dict(_BASE_STATE)
        del state["company_name"]
        result = node(state)
        assert result["industry_report"] == "Analysis without company name key"


# ===================================================================
# Commodity tools (lithium spot / commodity futures)
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystCommodityTools:
    """The Industry analyst carries the commodity data tools and their guidance."""

    def test_commodity_tools_bound(self):
        llm = _make_llm()
        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))
        bound = llm.bind_tools.call_args[0][0]
        names = [tool.name for tool in bound]
        assert "get_lithium_spot" in names
        assert "get_commodity_futures" in names

    def test_tool_guidance_mentions_commodity_tools(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "get_lithium_spot" in full_text
        assert "get_commodity_futures" in full_text


# ===================================================================
# Macro archive tools (US macro / CFTC COT / EIA petroleum)
# ===================================================================


@pytest.mark.unit
class TestIndustryAnalystMacroTools:
    """The Industry analyst carries the macro archive tools and their guidance."""

    def test_macro_tools_bound(self):
        llm = _make_llm()
        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))
        bound = llm.bind_tools.call_args[0][0]
        names = [tool.name for tool in bound]
        assert "get_us_macro" in names
        assert "get_cftc_cot" in names
        assert "get_eia_petroleum" in names

    def test_tool_guidance_mentions_macro_tools(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(prompt_val):
            captured["full"] = str(prompt_val)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_industry_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "get_us_macro" in full_text
        assert "get_cftc_cot" in full_text
        assert "get_eia_petroleum" in full_text
