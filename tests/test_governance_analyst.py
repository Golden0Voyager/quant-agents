"""Tests for the Governance Analyst agent node."""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.analysts.governance_analyst import (
    create_governance_analyst,
)

_BASE_STATE = {
    "company_of_interest": "AAPL",
    "trade_date": "2026-07-03",
    "company_name": "Apple Inc.",
    "asset_type": "stock",
    "messages": [],
}


def _make_llm(content: str = "Governance report", tool_calls: list | None = None) -> MagicMock:
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
    """Return a side_effect function that captures the prompt as string."""
    def inner(pv):
        captured["full"] = str(pv)
        r = MagicMock()
        r.content = "A"
        r.tool_calls = []
        r.additional_kwargs = {}
        return r
    return inner


def _make_capture_llm(captured):
    """Create a mock LLM that captures the formatted prompt."""
    llm = MagicMock()
    b = MagicMock()
    b.side_effect = _capture_side_effect(captured)
    llm.bind_tools.return_value = b
    return llm


@pytest.mark.unit
class TestGovernanceAnalystFactory:
    def test_returns_callable(self):
        assert callable(create_governance_analyst(_make_llm()))

    def test_returns_callable_with_any_llm(self):
        assert callable(create_governance_analyst("not_a_real_llm"))


@pytest.mark.unit
class TestGovernanceAnalystExecution:
    def test_returns_expected_keys(self):
        llm = _make_llm(content="Governance analysis")
        result = create_governance_analyst(llm)(dict(_BASE_STATE))
        assert "messages" in result
        assert "governance_report" in result

    def test_no_tool_calls_uses_content_as_report(self):
        llm = _make_llm(content="Direct governance analysis")
        result = create_governance_analyst(llm)(dict(_BASE_STATE))
        assert result["governance_report"] == "Direct governance analysis"

    def test_with_tool_calls_report_stays_empty(self):
        llm = _make_llm(
            content="Analysis after tools",
            tool_calls=[{"id": "c1", "name": "get_insider_transactions", "args": {}}],
        )
        result = create_governance_analyst(llm)(dict(_BASE_STATE))
        assert result["governance_report"] == ""

    def test_messages_updated(self):
        llm = _make_llm(content="Done")
        result = create_governance_analyst(llm)(dict(_BASE_STATE))
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == "Done"


@pytest.mark.unit
class TestGovernanceAnalystCompanyLine:
    def test_with_company_name_includes_line(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_governance_analyst(llm)(dict(_BASE_STATE))
        assert "Apple Inc." in captured["full"] or "Target company" in captured["full"]

    def test_without_company_name_no_line(self):
        captured = {}
        llm = _make_capture_llm(captured)
        s = dict(_BASE_STATE)
        s["company_name"] = ""
        create_governance_analyst(llm)(s)
        assert "Target company:" not in captured["full"]


@pytest.mark.unit
class TestGovernanceAnalystInstrumentContext:
    def test_build_instrument_context_called(self):
        with patch(
            "tradingagents.agents.analysts.governance_analyst.build_instrument_context",
            return_value="Company: Apple Inc.",
        ) as mb:
            create_governance_analyst(_make_llm())(dict(_BASE_STATE))
            mb.assert_called_once_with("AAPL", "Apple Inc.")

    def test_build_instrument_context_no_name(self):
        with patch(
            "tradingagents.agents.analysts.governance_analyst.build_instrument_context",
            return_value="Ticker: AAPL",
        ) as mb:
            s = dict(_BASE_STATE)
            del s["company_name"]
            create_governance_analyst(_make_llm())(s)
            mb.assert_called_once_with("AAPL", "")


@pytest.mark.unit
class TestGovernanceAnalystSanitization:
    def test_sanitize_called(self):
        with patch(
            "tradingagents.agents.analysts.governance_analyst.sanitize_company_name_in_report",
            return_value="Sanitized",
        ) as ms:
            llm = _make_llm(content="Raw governance")
            result = create_governance_analyst(llm)(dict(_BASE_STATE))
            ms.assert_called_once_with("Raw governance", "AAPL", "Apple Inc.")
            assert result["governance_report"] == "Sanitized"


@pytest.mark.unit
class TestGovernanceAnalystPrompt:
    def test_missing_data_protocol(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_governance_analyst(llm)(dict(_BASE_STATE))
        assert "Missing Data Protocol" in captured["full"]
        assert "NO_DATA_AVAILABLE" in captured["full"]

    def test_prompt_includes_date(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_governance_analyst(llm)(dict(_BASE_STATE))
        assert "2026-07-03" in captured["full"]


@pytest.mark.unit
class TestGovernanceAnalystEdgeCases:
    def test_none_company_name(self):
        s = dict(_BASE_STATE)
        s["company_name"] = None
        r = create_governance_analyst(_make_llm(content="ok"))(s)
        assert r["governance_report"] == "ok"

    def test_missing_company_name_key(self):
        s = dict(_BASE_STATE)
        del s["company_name"]
        r = create_governance_analyst(_make_llm(content="ok"))(s)
        assert r["governance_report"] == "ok"
