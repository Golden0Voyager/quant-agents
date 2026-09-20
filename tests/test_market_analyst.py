"""Tests for the Market Analyst agent node.

``market_analyst.py`` exports ``create_market_analyst(llm)`` which returns a
LangGraph-compatible node. The node reads from a structured state dict, builds
a prompt with tools, invokes the LLM, sanitises the report, and writes results
back to the state.
"""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.analysts.market_analyst import (
    create_market_analyst,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_STATE = {
    "company_of_interest": "AAPL",
    "trade_date": "2026-07-03",
    "company_name": "Apple Inc.",
    "asset_type": "stock",
    "instrument_context": "Company: Apple Inc.; Sector: Technology",
    "messages": [],
}


def _make_llm(content: str = "Market analysis report", tool_calls: list | None = None) -> MagicMock:
    """Create a mock LLM whose ``bind_tools().invoke()`` returns a controlled result.

    The mock chain calls ``prompt | llm.bind_tools(tools)`` under the hood,
    so we stub ``bind_tools`` to return a mock whose ``invoke`` we can set.
    """
    result = MagicMock()
    result.content = content
    result.tool_calls = tool_calls or []
    result.additional_kwargs = {}

    bound_llm = MagicMock()
    bound_llm.return_value = result
    bound_llm.name = "market_llm"

    llm = MagicMock()
    llm.bind_tools.return_value = bound_llm
    llm.name = "base_market_llm"
    return llm


# ===================================================================
# Factory behaviour
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystFactory:
    """Tests that the factory function returns a callable node."""

    def test_returns_callable(self):
        node = create_market_analyst(_make_llm())
        assert callable(node)

    def test_returns_callable_with_any_llm(self):
        node = create_market_analyst("not_a_real_llm")
        assert callable(node)


# ===================================================================
# Core execution paths
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystExecution:
    """Tests for the core execution logic of the market analyst node."""

    def test_returns_expected_keys(self):
        """Verify the return dict has ``messages`` and ``market_report``."""
        llm = _make_llm(content="Bullish trend confirmed")
        node = create_market_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert "messages" in result
        assert "market_report" in result

    def test_no_tool_calls_uses_content_as_report(self):
        """When the LLM returns no tool calls, the raw content is the report."""
        llm = _make_llm(content="Direct market analysis without tools")
        node = create_market_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert result["market_report"] == "Direct market analysis without tools"

    def test_with_tool_calls_report_stays_empty(self):
        """When the LLM returns tool calls, the report stays empty (content used
        only when no tool calls, as tools handle the response)."""
        llm = _make_llm(
            content="Market analysis after tool calls",
            tool_calls=[{"id": "call_1", "name": "get_stock_data", "args": {}}],
        )
        node = create_market_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert result["market_report"] == ""

    def test_empty_content_with_tool_calls(self):
        """Empty content with tool calls results in empty report."""
        llm = _make_llm(
            content="",
            tool_calls=[{"id": "call_1", "name": "get_indicators", "args": {"name": "rsi"}}],
        )
        node = create_market_analyst(llm)
        result = node(dict(_BASE_STATE))
        assert result["market_report"] == ""

    def test_messages_updated_with_llm_result(self):
        """The LLM result is appended to the messages list."""
        llm_obj = _make_llm(content="Bullish")
        node = create_market_analyst(llm_obj)
        result = node(dict(_BASE_STATE))
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == "Bullish"


# ===================================================================
# Company name / ticker guard
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystTickerGuard:
    """Tests for the ticker_guard conditional logic."""

    def test_with_company_name_creates_ticker_guard(self):
        """When company_name is set, the ticker guard appears in the prompt."""
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

        node = create_market_analyst(llm)
        node(dict(_BASE_STATE))

        prompt_text = captured.get("full", "")
        assert "Apple Inc." in prompt_text or "AAPL" in prompt_text

    def test_without_company_name_no_ticker_guard_prefix(self):
        """When company_name is empty, the ticker guard block is absent."""
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

        node = create_market_analyst(llm)
        state = dict(_BASE_STATE)
        state["company_name"] = ""
        node(state)

        full_text = captured.get("full", "")
        assert "TICKER VERIFICATION" not in full_text


# ===================================================================
# Instrument context
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystInstrumentContext:
    """Tests for instrument context resolution."""

    def test_uses_instrument_context_from_state(self):
        """When state has instrument_context, it's passed into the prompt."""
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

        node = create_market_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "Apple Inc." in full_text

    def test_instrument_context_fallback_builds_from_ticker(self):
        """When instrument_context is missing, it falls back to ticker-based context."""
        with patch(
            "tradingagents.agents.analysts.market_analyst.get_instrument_context_from_state",
            return_value="The instrument to analyze is `AAPL`.",
        ):
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

            node = create_market_analyst(llm)
            state = dict(_BASE_STATE)
            del state["instrument_context"]
            node(state)

            full_text = captured.get("full", "")
            assert "AAPL" in full_text


# ===================================================================
# Sanitization
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystSanitization:
    """Tests that the report goes through sanitize_company_name_in_report."""

    def test_sanitize_called_with_report_ticker_company(self):
        with patch(
            "tradingagents.agents.analysts.market_analyst.sanitize_company_name_in_report",
            return_value="Sanitized report",
        ) as mock_sanitize:
            llm = _make_llm(content="Raw report content")
            node = create_market_analyst(llm)
            result = node(dict(_BASE_STATE))

            mock_sanitize.assert_called_once_with("Raw report content", "AAPL", "Apple Inc.")
            assert result["market_report"] == "Sanitized report"

    def test_sanitize_skipped_for_empty_report(self):
        with patch(
            "tradingagents.agents.analysts.market_analyst.sanitize_company_name_in_report",
            return_value="",
        ) as mock_sanitize:
            llm = _make_llm(content="")
            node = create_market_analyst(llm)
            node(dict(_BASE_STATE))
            mock_sanitize.assert_called_once_with("", "AAPL", "Apple Inc.")


# ===================================================================
# System message structure
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystSystemMessage:
    """Tests that the system message includes key prompt sections."""

    def test_system_message_includes_data_quality_section(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(messages):
            captured["full"] = str(messages)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_market_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "Missing Data Protocol" in full_text
        assert "NO_DATA_AVAILABLE" in full_text
        assert "data_availability" in full_text

    def test_system_message_includes_market_snapshot_instruction(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(messages):
            captured["full"] = str(messages)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_market_analyst(llm)
        node(dict(_BASE_STATE))

        full_text = captured.get("full", "")
        assert "get_verified_market_snapshot" in full_text


# ===================================================================
# Edge cases
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystEdgeCases:
    """Edge-case tests for state variations."""

    def test_empty_messages_list(self):
        """Empty messages list still produces a valid result."""
        llm = _make_llm(content="Report from empty state")
        node = create_market_analyst(llm)
        result = node(dict(_BASE_STATE, messages=[]))
        assert result["market_report"] == "Report from empty state"
        assert len(result["messages"]) == 1

    def test_none_company_name_treated_as_empty(self):
        """None company_name should not produce a ticker guard."""
        llm = _make_llm(content="Analysis without company name")
        node = create_market_analyst(llm)
        state = dict(_BASE_STATE)
        state["company_name"] = None
        result = node(state)
        assert result["market_report"] == "Analysis without company name"

    def test_missing_company_name_key(self):
        """Missing company_name key should not raise KeyError."""
        llm = _make_llm(content="Analysis without company name key")
        node = create_market_analyst(llm)
        state = dict(_BASE_STATE)
        del state["company_name"]
        result = node(state)
        assert result["market_report"] == "Analysis without company name key"


# ===================================================================
# Global macro symbols guidance
# ===================================================================


@pytest.mark.unit
class TestMarketAnalystGlobalMacroGuidance:
    """get_stock_data guidance documents locally archived macro symbols."""

    def _system_message(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(messages):
            captured["full"] = str(messages)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        node = create_market_analyst(llm)
        node(dict(_BASE_STATE))
        return captured.get("full", "")

    def test_guidance_mentions_vix_term_structure(self):
        full_text = self._system_message()
        assert "^VIX" in full_text
        assert "^VIX3M" in full_text
        assert "inverted" in full_text

    def test_guidance_mentions_dollar_and_fx(self):
        full_text = self._system_message()
        assert "DX-Y.NYB" in full_text
        assert "USDCNY=X" in full_text

    def test_guidance_mentions_sector_benchmarks(self):
        full_text = self._system_message()
        assert "XLE" in full_text
        assert "^SOX" in full_text


# ===================================================================
# Full-table coverage tools
# ===================================================================


_ASHARE_STATE = {**_BASE_STATE, "company_of_interest": "600519.SS", "company_name": "贵州茅台"}


@pytest.mark.unit
class TestMarketFullCoverageTools:
    def test_market_tools_bound(self):
        llm = _make_llm()
        create_market_analyst(llm)(dict(_ASHARE_STATE))
        bound = llm.bind_tools.call_args[0][0]
        names = [tool.name for tool in bound]
        for expected in (
            "get_option_sentiment",
            "get_ah_premium",
            "get_etf_daily",
            "get_cb_quotation",
            "get_cb_redeem",
            "get_cb_index",
        ):
            assert expected in names, expected

    def test_tool_guidance_mentions_new_tools(self):
        llm = MagicMock()
        captured = {}

        def capture_invoke(messages):
            captured["full"] = str(messages)
            result = MagicMock()
            result.content = "Analysis"
            result.tool_calls = []
            result.additional_kwargs = {}
            return result

        bound_llm = MagicMock()
        bound_llm.side_effect = capture_invoke
        llm.bind_tools.return_value = bound_llm

        create_market_analyst(llm)(dict(_ASHARE_STATE))

        full_text = captured.get("full", "")
        for expected in ("get_option_sentiment", "get_ah_premium", "get_cb_redeem"):
            assert expected in full_text, expected
