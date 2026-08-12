"""End-to-end tests for report-based skip logic and checkpoint pre-checks."""

import json
from unittest.mock import patch

import pytest

from cli.batch_runner import BatchRunner
from cli.main import save_report_to_disk

pytestmark = pytest.mark.unit


@pytest.fixture
def sample_final_state():
    """Minimal final_state dict for save_report_to_disk."""
    return {
        "trade_date": "2026-05-19",
        "market_report": "Market looks bullish.",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "governance_report": "",
        "industry_report": "",
        "investment_debate_state": {
            "bull_history": "",
            "bear_history": "",
            "history": "",
            "current_response": "",
            "judge_decision": "",
        },
        "trader_investment_plan": "",
        "risk_debate_state": {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "Buy AAPL at 180. Stop 170. Size 5%.",
        },
        "investment_plan": "",
        "final_trade_decision": "Buy AAPL at 180. Stop 170. Size 5%.",
    }


class TestParseReportAnalysisDate:
    def test_extracts_date_from_header(self, tmp_path):
        report = tmp_path / "complete_report.md"
        report.write_text(
            "# Trading Analysis Report: AAPL\n\n"
            "Generated: 2026-05-19 10:00:00\n\n"
            "Analysis Date: 2026-05-19\n\n"
            "Some content",
            encoding="utf-8",
        )
        assert BatchRunner._parse_report_analysis_date(report) == "2026-05-19"

    def test_returns_none_when_missing(self, tmp_path):
        report = tmp_path / "complete_report.md"
        report.write_text(
            "# Trading Analysis Report: AAPL\n\n"
            "Generated: 2026-05-19 10:00:00\n\n"
            "Some content",
            encoding="utf-8",
        )
        assert BatchRunner._parse_report_analysis_date(report) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        report = tmp_path / "complete_report.md"
        report.write_text("", encoding="utf-8")
        assert BatchRunner._parse_report_analysis_date(report) is None


class TestIsAlreadyCompleted:
    def test_matches_when_analysis_date_equal(self, tmp_path):
        ticker_dir = tmp_path / "AAPL"
        ticker_dir.mkdir()
        report = ticker_dir / "complete_report.md"
        report.write_text(
            "# Report\n\nAnalysis Date: 2026-05-19\n\nContent",
            encoding="utf-8",
        )

        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"analysis_date": "2026-05-19"},
            output_dir=tmp_path,
        )
        assert runner._is_already_completed("AAPL") is True

    def test_no_match_when_analysis_date_differs(self, tmp_path):
        ticker_dir = tmp_path / "AAPL"
        ticker_dir.mkdir()
        report = ticker_dir / "complete_report.md"
        report.write_text(
            "# Report\n\nAnalysis Date: 2026-05-18\n\nContent",
            encoding="utf-8",
        )

        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"analysis_date": "2026-05-19"},
            output_dir=tmp_path,
        )
        assert runner._is_already_completed("AAPL") is False

    def test_falls_back_to_mtime_for_legacy_reports(self, tmp_path):
        """Old reports without 'Analysis Date:' still use file mtime == today."""
        ticker_dir = tmp_path / "AAPL"
        ticker_dir.mkdir()
        report = ticker_dir / "complete_report.md"
        report.write_text("# Report\n\nOld content", encoding="utf-8")

        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"analysis_date": "2026-05-19"},
            output_dir=tmp_path,
        )
        # Because the file was just created, mtime.date() == today, so it
        # should be treated as a match when no Analysis Date is present.
        result = runner._is_already_completed("AAPL")
        assert result is True

    def test_ignores_empty_file(self, tmp_path):
        ticker_dir = tmp_path / "AAPL"
        ticker_dir.mkdir()
        report = ticker_dir / "complete_report.md"
        report.write_text("", encoding="utf-8")

        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"analysis_date": "2026-05-19"},
            output_dir=tmp_path,
        )
        assert runner._is_already_completed("AAPL") is False

    def test_searches_historical_batch_directories(self, tmp_path):
        """Reports in sibling batch_* folders are also considered."""
        batch_dir = tmp_path / "batch_20260519_120000"
        ticker_dir = batch_dir / "AAPL"
        ticker_dir.mkdir(parents=True)
        report = ticker_dir / "complete_report.md"
        report.write_text(
            "# Report\n\nAnalysis Date: 2026-05-19\n\nContent",
            encoding="utf-8",
        )

        current_batch = tmp_path / "batch_20260519_130000"
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"analysis_date": "2026-05-19"},
            output_dir=current_batch,
        )
        assert runner._is_already_completed("AAPL") is True


class TestForceRegeneration:
    def test_force_disables_checkpoint_cache(self, tmp_path):
        """Forced batch runs must execute the graph instead of reusing state logs."""
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"checkpoint_enabled": True},
            output_dir=tmp_path,
            checkpoint=True,
            force=True,
        )

        assert runner._build_config()["checkpoint_enabled"] is False


class TestSaveReportToDisk:
    def test_includes_analysis_date_in_header(self, tmp_path, sample_final_state):
        save_path = tmp_path / "reports" / "AAPL"
        report_path = save_report_to_disk(sample_final_state, "AAPL", save_path)
        content = report_path.read_text(encoding="utf-8")
        assert "Analysis Date: 2026-05-19" in content

    def test_creates_subfolders_and_consolidated_report(self, tmp_path, sample_final_state):
        save_path = tmp_path / "reports" / "AAPL"
        save_report_to_disk(sample_final_state, "AAPL", save_path)

        assert (save_path / "1_analysts" / "market.md").exists()
        assert (save_path / "complete_report.md").exists()


class TestPropagateSkipsWhenStateLogExists:
    def test_loads_cached_state_and_returns_without_graph(
        self, tmp_path, mock_llm_client
    ):
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = DEFAULT_CONFIG.copy()
        config["results_dir"] = str(tmp_path)
        config["data_cache_dir"] = str(tmp_path / "cache")
        config["checkpoint_enabled"] = True
        config["llm_provider"] = "openai"
        config["deep_think_llm"] = "gpt-fake"
        config["quick_think_llm"] = "gpt-fake-mini"

        # Pre-create a completed state log
        state_log_dir = tmp_path / "AAPL" / "TradingAgentsStrategy_logs"
        state_log_dir.mkdir(parents=True)
        cached_state = {
            "company_of_interest": "AAPL",
            "trade_date": "2026-05-19",
            "market_report": "Cached market report",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "governance_report": "",
            "industry_report": "",
            "investment_debate_state": {
                "bull_history": "",
                "bear_history": "",
                "history": "",
                "current_response": "",
                "judge_decision": "",
            },
            "trader_investment_decision": "",
            "risk_debate_state": {
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "history": "",
                "judge_decision": "Cached decision: Hold",
            },
            "investment_plan": "",
            "final_trade_decision": "Cached decision: Hold",
        }
        (state_log_dir / "full_states_log_2026-05-19.json").write_text(
            json.dumps(cached_state), encoding="utf-8"
        )

        with patch(
            "tradingagents.ticker_resolver.resolve_ticker",
            return_value={"ticker": "AAPL", "company_name": "Apple Inc."},
        ):
            graph = TradingAgentsGraph(
                selected_analysts=["market"],
                config=config,
                debug=False,
            )
            with patch.object(graph, "_resolve_pending_entries"):
                final_state, signal = graph.propagate("AAPL", "2026-05-19")

        assert final_state["final_trade_decision"] == "Cached decision: Hold"
        assert signal is not None
