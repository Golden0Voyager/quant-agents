"""Tests for the News Analyst agent node."""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.analysts.news_analyst import (
    create_news_analyst,
)

_BASE_STATE = {
    "company_of_interest": "AAPL",
    "trade_date": "2026-07-03",
    "company_name": "Apple Inc.",
    "asset_type": "stock",
    "instrument_context": "Company: Apple Inc.; Sector: Technology",
    "messages": [],
}


def _make_llm(content: str = "News report", tool_calls: list | None = None) -> MagicMock:
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
class TestNewsAnalystFactory:
    def test_returns_callable(self):
        assert callable(create_news_analyst(_make_llm()))

    def test_returns_callable_with_any_llm(self):
        assert callable(create_news_analyst("not_a_real_llm"))


@pytest.mark.unit
class TestNewsAnalystExecution:
    def test_returns_expected_keys(self):
        result = create_news_analyst(_make_llm("News analysis"))(dict(_BASE_STATE))
        assert "messages" in result
        assert "news_report" in result

    def test_content_used_as_report(self):
        """News analyst always uses result.content as report (no conditional)."""
        result = create_news_analyst(_make_llm("Latest news"))(dict(_BASE_STATE))
        assert result["news_report"] == "Latest news"

    def test_content_with_tool_calls_still_used(self):
        """Even with tool calls, report is result.content (no len(tool_calls) guard)."""
        llm = _make_llm(
            content="News after tool calls",
            tool_calls=[{"id": "c1", "name": "get_news", "args": {}}],
        )
        result = create_news_analyst(llm)(dict(_BASE_STATE))
        assert result["news_report"] == "News after tool calls"

    def test_empty_content_returns_empty(self):
        result = create_news_analyst(_make_llm(content=""))(dict(_BASE_STATE))
        assert result["news_report"] == ""

    def test_messages_updated(self):
        result = create_news_analyst(_make_llm("News done"))(dict(_BASE_STATE))
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == "News done"


@pytest.mark.unit
class TestNewsAnalystTickerGuard:
    def test_with_company_name_ticker_guard_present(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_news_analyst(llm)(dict(_BASE_STATE))
        assert "TICKER VERIFICATION" in captured["full"]

    def test_without_company_name_no_ticker_guard(self):
        captured = {}
        llm = _make_capture_llm(captured)
        s = dict(_BASE_STATE)
        s["company_name"] = ""
        create_news_analyst(llm)(s)
        assert "TICKER VERIFICATION" not in captured["full"]


@pytest.mark.unit
class TestNewsAnalystAssetLabel:
    def test_stock_asset_label_is_company(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_news_analyst(llm)(dict(_BASE_STATE))
        assert "company" in captured["full"].lower() or "stock" in captured["full"].lower()

    def test_crypto_asset_label_is_asset(self):
        captured = {}
        llm = _make_capture_llm(captured)
        s = dict(_BASE_STATE)
        s["asset_type"] = "crypto"
        create_news_analyst(llm)(s)
        assert "asset" in captured["full"].lower()


@pytest.mark.unit
class TestNewsAnalystInstrumentContext:
    def test_uses_instrument_context_from_state(self):
        with patch(
            "tradingagents.agents.analysts.news_analyst.get_instrument_context_from_state",
            return_value="Company: Apple Inc.",
        ) as mock_get:
            create_news_analyst(_make_llm())(dict(_BASE_STATE))
            mock_get.assert_called_once()


@pytest.mark.unit
class TestNewsAnalystSanitization:
    def test_sanitize_called(self):
        with patch(
            "tradingagents.agents.analysts.news_analyst.sanitize_company_name_in_report",
            return_value="Sanitized news",
        ) as ms:
            result = create_news_analyst(_make_llm("Raw news"))(dict(_BASE_STATE))
            ms.assert_called_once_with("Raw news", "AAPL", "Apple Inc.")
            assert result["news_report"] == "Sanitized news"


@pytest.mark.unit
class TestNewsAnalystPrompt:
    def test_missing_data_protocol(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_news_analyst(llm)(dict(_BASE_STATE))
        assert "Missing Data Protocol" in captured["full"]

    def test_prompt_includes_date(self):
        captured = {}
        llm = _make_capture_llm(captured)
        create_news_analyst(llm)(dict(_BASE_STATE))
        assert "2026-07-03" in captured["full"]


@pytest.mark.unit
class TestNewsAnalystEdgeCases:
    def test_none_company_name(self):
        s = dict(_BASE_STATE)
        s["company_name"] = None
        r = create_news_analyst(_make_llm("ok"))(s)
        assert r["news_report"] == "ok"

    def test_missing_company_name_key(self):
        s = dict(_BASE_STATE)
        del s["company_name"]
        r = create_news_analyst(_make_llm("ok"))(s)
        assert r["news_report"] == "ok"

    def test_missing_asset_type_defaults_stock(self):
        s = dict(_BASE_STATE)
        del s["asset_type"]
        r = create_news_analyst(_make_llm("ok"))(s)
        assert r["news_report"] == "ok"
