"""Smoke tests for the single-stock CLI analysis flow."""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

from cli.main import run_analysis
from cli.models import AnalystType


@pytest.mark.unit
class TestRunAnalysis:
    """Smoke tests for the unified-dashboard run_analysis path."""

    def _make_selections(self, analysts=None):
        return {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "analysis_date": "2026-07-08",
            "asset_type": "stock",
            "analysts": analysts or [AnalystType.MARKET, AnalystType.NEWS],
            "research_depth": 1,
            "shallow_thinker": "qwen3.7-plus",
            "deep_thinker": "qwen3.7-max",
            "backend_url": None,
            "llm_provider": "openai",
            "google_thinking_level": None,
            "openai_reasoning_effort": None,
            "anthropic_effort": None,
            "output_language": "English",
            "force_regenerate": True,
        }

    def _make_fake_graph(self, chunks):
        fake_graph = MagicMock()
        fake_graph.propagator.create_initial_state.return_value = {"messages": []}
        fake_graph.propagator.get_graph_args.return_value = {}
        fake_graph.resolve_instrument_context.return_value = {}
        fake_graph.graph.stream.return_value = iter(chunks)
        fake_graph.process_signal.return_value = None
        return fake_graph

    @patch("typer.prompt", return_value="Y")
    @patch("cli.main.display_complete_report")
    @patch("cli.main.save_report_to_disk")
    @patch("cli.main.console")
    @patch("cli.main.BatchRunner._is_outside_trading_hours", return_value=False)
    @patch("cli.main.Path.mkdir")
    @patch("cli.main.Path.touch")
    @patch("cli.main.StatsCallbackHandler")
    @patch("cli.main.TradingAgentsGraph")
    @patch("cli.main.process_stream_chunk")
    def test_run_analysis_processes_stream_with_dashboard(
        self,
        mock_process_stream_chunk,
        mock_trading_graph,
        mock_stats_handler_cls,
        mock_touch,
        mock_mkdir,
        mock_is_outside,
        mock_console,
        mock_save_report,
        mock_display_report,
        mock_typer_prompt,
    ):
        selections = self._make_selections(
            analysts=[AnalystType.MARKET, AnalystType.NEWS, AnalystType.GOVERNANCE]
        )
        chunks = [
            {"market_report": "Market analysis content"},
            {"news_report": "News analysis content"},
            {
                "investment_debate_state": {
                    "bull_history": "Bull",
                    "bear_history": "Bear",
                    "judge_decision": "Hold",
                }
            },
            {"trader_investment_plan": "Buy at 200"},
            {
                "risk_debate_state": {
                    "aggressive_history": "Agg",
                    "conservative_history": "Cons",
                    "neutral_history": "Neu",
                    "judge_decision": "Overweight",
                }
            },
            {"final_trade_decision": "Buy"},
        ]
        fake_graph = self._make_fake_graph(chunks)
        mock_trading_graph.return_value = fake_graph

        mock_stats_handler = MagicMock()
        mock_stats_handler.get_stats.return_value = {
            "llm_calls": 3,
            "tool_calls": 2,
            "tokens_in": 1000,
            "tokens_out": 500,
        }
        mock_stats_handler_cls.return_value = mock_stats_handler

        with patch("builtins.open", mock_open()):
            run_analysis(checkpoint=False, selections=selections)

        # process_stream_chunk called once per chunk
        assert mock_process_stream_chunk.call_count == len(chunks)

        # save_report_to_disk and display_complete_report are invoked
        mock_save_report.assert_called_once()
        mock_display_report.assert_called_once()

