"""Tests for BatchRunner fallback detection and summary rendering."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.batch_runner import BatchRunner


@pytest.fixture(autouse=True)
def _mock_home(tmp_path, monkeypatch):
    """Isolate home-directory caches to a temporary directory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


@pytest.mark.unit
class TestBatchRunnerFallbackDetection:
    def test_extract_summary_flags_upstream_fallback_agents(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        final_state = {
            "final_trade_decision": "**Rating**: Buy\n**Confidence**: high\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
            "structured_fallback_agents": ["Research Manager"],
        }
        runner._extract_summary("AAPL", final_state)
        assert runner.summaries["AAPL"]["fallback"] is True
        assert runner.summaries["AAPL"]["confidence"] == "low"

    def test_extract_summary_flags_fallback_from_state(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        final_state = {
            "final_trade_decision": "**Rating**: Buy\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
            "_structured_fallback": True,
        }
        runner._extract_summary("AAPL", final_state)
        assert runner.summaries["AAPL"]["fallback"] is True
        assert runner.summaries["AAPL"]["confidence"] == "low"

    def test_extract_summary_flags_fallback_from_marker(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        marker = "<!--STRUCTURED_FALLBACK: schema validation failed, treat with low confidence-->"
        final_state = {
            "final_trade_decision": f"{marker}\n**Rating**: Buy\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
        }
        runner._extract_summary("AAPL", final_state)
        assert runner.summaries["AAPL"]["fallback"] is True
        assert runner.summaries["AAPL"]["confidence"] == "low"

    def test_extract_summary_parses_confidence_from_decision(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        final_state = {
            "final_trade_decision": "**Rating**: Buy\n**Confidence**: high\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
        }
        runner._extract_summary("AAPL", final_state)
        assert runner.summaries["AAPL"]["fallback"] is False
        assert runner.summaries["AAPL"]["confidence"] == "high"


@pytest.mark.unit
class TestBatchRunnerSummaryOutput:
    def test_generate_summary_appends_fallback_marker(self, tmp_path):
        output_dir = tmp_path / "reports" / "batch_20260101_000000"
        output_dir.mkdir(parents=True)
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=output_dir,
            workers=1,
        )
        runner.completed_tickers.add("AAPL")
        runner.summaries["AAPL"] = {
            "company": "Apple",
            "rating": "Buy",
            "entry": "150",
            "stop": "140",
            "size": "5%",
            "fallback": True,
            "confidence": "low",
        }

        md_path = runner.generate_summary()
        md = md_path.read_text(encoding="utf-8")
        assert "[fallback]" in md
        assert "low" in md

    def test_generate_summary_json_includes_fallback_and_low_confidence(self, tmp_path):
        output_dir = tmp_path / "reports" / "batch_20260101_000000"
        output_dir.mkdir(parents=True)
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=output_dir,
            workers=1,
        )
        runner.completed_tickers.add("AAPL")
        runner.summaries["AAPL"] = {
            "company": "Apple",
            "rating": "Buy",
            "entry": "150",
            "stop": "140",
            "size": "5%",
            "fallback": True,
            "confidence": "low",
        }

        runner.generate_summary()
        json_path = output_dir / "batch_summary.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))
        row = data["rows"][0]
        assert row["fallback"] is True
        assert row["confidence"] == "low"
