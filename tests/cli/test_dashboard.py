"""Unit tests for the unified dashboard state container and chunk processing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cli.dashboard import (
    ANALYST_AGENT_NAMES,
    ANALYST_ORDER,
    AnalysisDashboard,
    _classify_message,
    _extract_content_string,
    process_stream_chunk,
)


@pytest.mark.unit
class TestAnalysisDashboardInit:
    """Initialization of analyst status and report sections."""

    def test_init_for_analysis_includes_all_six_analysts(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(ANALYST_ORDER)

        assert set(dashboard.selected_analysts) == set(ANALYST_ORDER)
        for key in ANALYST_ORDER:
            assert dashboard.agent_status.get(ANALYST_AGENT_NAMES[key]) == "pending"

    def test_init_for_analysis_only_selected_analysts(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market", "news"])

        assert dashboard.agent_status["Market Analyst"] == "pending"
        assert dashboard.agent_status["News Analyst"] == "pending"
        assert "Fundamentals Analyst" not in dashboard.agent_status

    def test_init_for_analysis_resets_previous_state(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])
        dashboard.update_agent_status("Market Analyst", "completed")
        dashboard.update_report_section("market_report", "done")
        dashboard.add_message("System", "old")

        dashboard.init_for_analysis(["news"])
        assert dashboard.agent_status.get("Market Analyst") is None
        assert dashboard.report_sections.get("market_report") is None
        assert len(dashboard.messages) == 0


@pytest.mark.unit
class TestReportSectionUpdates:
    """Report section tracking and final report assembly."""

    def test_update_report_section_builds_current_report(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])
        dashboard.update_report_section("market_report", "Bullish trend")

        assert dashboard.report_sections["market_report"] == "Bullish trend"
        assert "Market Analysis" in (dashboard.current_report or "")

    def test_final_report_includes_all_sections(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(ANALYST_ORDER)
        dashboard.update_report_section("market_report", "m")
        dashboard.update_report_section("investment_plan", "plan")
        dashboard.update_report_section("trader_investment_plan", "trade")
        dashboard.update_report_section("final_trade_decision", "decide")

        final = dashboard.final_report or ""
        assert "## Analyst Team Reports" in final
        assert "## Research Team Decision" in final
        assert "## Trading Team Plan" in final
        assert "## Portfolio Management Decision" in final


@pytest.mark.unit
class TestMessageClassification:
    """Message extraction and classification helpers."""

    def test_extract_content_string_handles_text(self):
        assert _extract_content_string("  hello  ") == "hello"

    def test_extract_content_string_handles_dict_text(self):
        assert _extract_content_string({"text": "  hi  "}) == "hi"

    def test_extract_content_string_returns_none_for_empty(self):
        assert _extract_content_string("") is None
        assert _extract_content_string("   ") is None
        assert _extract_content_string(None) is None

    def test_classify_message_types(self):
        assert _classify_message(HumanMessage(content="x")) == ("User", "x")
        assert _classify_message(AIMessage(content="x")) == ("Agent", "x")
        assert _classify_message(ToolMessage(content="x", tool_call_id="1")) == ("Data", "x")
        assert _classify_message(MagicMock(content="x")) == ("System", "x")


@pytest.mark.unit
class TestProcessStreamChunk:
    """process_stream_chunk updates dashboard state from graph chunks."""

    def test_dedupes_messages_by_id(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])
        msg = AIMessage(content="hello", id="msg-1")

        processed = process_stream_chunk(dashboard, {"messages": [msg]})
        process_stream_chunk(dashboard, {"messages": [msg]}, processed_ids=processed)

        assert len(dashboard.messages) == 1
        assert "msg-1" in processed

    def test_adds_tool_calls_from_ai_message(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "tc-1"}],
            id="tc-1",
        )

        process_stream_chunk(dashboard, {"messages": [msg]})

        assert len(dashboard.tool_calls) == 1
        assert dashboard.tool_calls[0][1] == "get_stock_data"

    def test_analyst_status_updates_from_report_section(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market", "news"])

        process_stream_chunk(dashboard, {"market_report": "report content"})

        assert dashboard.agent_status["Market Analyst"] == "completed"
        assert dashboard.agent_status["News Analyst"] == "in_progress"

    def test_research_debate_transition(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])

        chunk = {
            "investment_debate_state": {
                "bull_history": "Bull case",
                "bear_history": "",
                "judge_decision": "",
            }
        }
        process_stream_chunk(dashboard, chunk)

        assert dashboard.agent_status["Bull Researcher"] == "in_progress"
        assert dashboard.agent_status["Research Manager"] == "in_progress"
        assert dashboard.current_stage == "Research Debate"

    def test_research_debate_completion_triggers_trader(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])

        chunk = {
            "investment_debate_state": {
                "bull_history": "Bull",
                "bear_history": "Bear",
                "judge_decision": "Hold",
            }
        }
        process_stream_chunk(dashboard, chunk)

        assert dashboard.agent_status["Research Manager"] == "completed"
        assert dashboard.agent_status["Trader"] == "in_progress"
        assert dashboard.report_sections["investment_plan"] == "Hold"

    def test_risk_debate_completion(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])
        dashboard.update_agent_status("Trader", "completed")

        chunk = {
            "risk_debate_state": {
                "aggressive_history": "Aggressive view",
                "conservative_history": "Conservative view",
                "neutral_history": "Neutral view",
                "judge_decision": "Buy",
            }
        }
        process_stream_chunk(dashboard, chunk)

        assert dashboard.agent_status["Portfolio Manager"] == "completed"
        assert dashboard.agent_status["Aggressive Analyst"] == "completed"
        assert "Buy" in (dashboard.report_sections.get("final_trade_decision") or "")


@pytest.mark.unit
class TestStageProgress:
    """Stage and progress tracking from agent status."""

    def test_analysts_stage_progress(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market", "news"])
        dashboard.update_agent_status("Market Analyst", "completed")
        dashboard.update_stage_from_chunk({})

        assert dashboard.current_stage == "Analysts"
        assert dashboard.overall_progress == pytest.approx(0.15)

    def test_portfolio_stage_is_final(self):
        dashboard = AnalysisDashboard()
        dashboard.init_for_analysis(["market"])
        dashboard.update_agent_status("Portfolio Manager", "completed")
        dashboard.update_stage_from_chunk({})

        assert dashboard.current_stage == "Portfolio"
        assert dashboard.overall_progress == 1.0
