"""Tests for the Fundamentals Analyst agent node."""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)

_BASE_STATE = {
    "company_of_interest": "AAPL",
    "trade_date": "2026-07-03",
    "company_name": "Apple Inc.",
    "asset_type": "stock",
    "instrument_context": "Company: Apple Inc.; Sector: Technology",
    "messages": [],
}


def _make_llm(content: str = "Fundamentals report", tool_calls: list | None = None) -> MagicMock:
    result = MagicMock()
    result.content = content
    result.tool_calls = tool_calls or []
    result.additional_kwargs = {}
    bound_llm = MagicMock()
    bound_llm.return_value = result
    llm = MagicMock()
    llm.bind_tools.return_value = bound_llm
    return llm


def _capture_side_effect(captured):
    def inner(pv):
        captured["full"] = str(pv)
        r = MagicMock()
        r.content = "A"
        r.tool_calls = []
        r.additional_kwargs = {}
        return r
    return inner


def _make_capture_llm(captured):
    llm = MagicMock()
    b = MagicMock()
    b.side_effect = _capture_side_effect(captured)
    llm.bind_tools.return_value = b
    return llm


@pytest.mark.unit
class TestFundamentalsAnalystFactory:
    def test_returns_callable(self):
        assert callable(create_fundamentals_analyst(_make_llm()))

    def test_returns_callable_with_any_llm(self):
        assert callable(create_fundamentals_analyst("not_a_real_llm"))


@pytest.mark.unit
class TestFundamentalsAnalystExecution:
    def test_returns_expected_keys(self):
        result = create_fundamentals_analyst(_make_llm("Fundamentals analysis"))(dict(_BASE_STATE))
        assert "messages" in result
        assert "fundamentals_report" in result

    def test_content_used_as_report(self):
        """Fundamentals always uses content (no conditional)."""
        result = create_fundamentals_analyst(_make_llm("PE ratio: 25"))(dict(_BASE_STATE))
        assert result["fundamentals_report"] == "PE ratio: 25"

    def test_content_with_tool_calls_still_used(self):
        llm = _make_llm(
            content="Fundamentals after tool calls",
            tool_calls=[{"id": "c1", "name": "get_fundamentals", "args": {}}],
        )
        result = create_fundamentals_analyst(llm)(dict(_BASE_STATE))
        assert result["fundamentals_report"] == "Fundamentals after tool calls"

    def test_empty_content_returns_empty(self):
        result = create_fundamentals_analyst(_make_llm(content=""))(dict(_BASE_STATE))
        assert result["fundamentals_report"] == ""

    def test_messages_updated(self):
        result = create_fundamentals_analyst(_make_llm("Done"))(dict(_BASE_STATE))
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == "Done"


@pytest.mark.unit
class TestFundamentalsAnalystInstrumentContext:
    def test_uses_instrument_context_from_state(self):
        with patch(
            "tradingagents.agents.analysts.fundamentals_analyst.get_instrument_context_from_state",
            return_value="Company: Apple Inc.",
        ) as mock_get:
            create_fundamentals_analyst(_make_llm())(dict(_BASE_STATE))
            mock_get.assert_called_once()


@pytest.mark.unit
class TestFundamentalsAnalystPrompt:
    def test_missing_data_protocol(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_fundamentals_analyst(llm)(dict(_BASE_STATE))
        assert "Missing Data Protocol" in captured["full"]
        assert "NO_DATA_AVAILABLE" in captured["full"]

    def test_prompt_includes_date(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_fundamentals_analyst(llm)(dict(_BASE_STATE))
        assert "2026-07-03" in captured["full"]

    def test_prompt_mentions_earnings_estimates_only_when_market_supports_it(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_fundamentals_analyst(llm)(dict(_BASE_STATE))
        assert "get_earnings_estimates" not in captured["full"]

        a_share_state = {
            **_BASE_STATE,
            "company_of_interest": "600519.SS",
            "company_name": "Kweichow Moutai",
            "market": "XSHG",
        }
        create_fundamentals_analyst(llm)(a_share_state)
        assert "get_earnings_estimates" in captured["full"]
