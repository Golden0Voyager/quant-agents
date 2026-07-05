"""Report parity: the shared writer produces the report tree for the CLI and the
programmatic API alike (#1037)."""

from types import SimpleNamespace

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"judge_decision": "RM PLAN"},
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"judge_decision": "PM DECISION"},
    }


def _full_state():
    """State dict covering all A-Share report sections."""
    return {
        "company_name": "TestCorp Inc.",
        "trade_date": "2026-07-03",
        "market_report": "Bullish trend",
        "sentiment_report": "Positive sentiment",
        "news_report": "Earnings beat",
        "fundamentals_report": "P/E 15.2",
        "governance_report": "ESG score 82",
        "industry_report": "Semiconductor sector",
        "investment_debate_state": {
            "bull_history": "Bull case: strong growth",
            "bear_history": "Bear case: valuation risk",
            "judge_decision": "RM PLAN: Buy",
        },
        "trader_investment_plan": "Long 100 shares @ $150",
        "risk_debate_state": {
            "aggressive_history": "Aggressive: add more",
            "conservative_history": "Conservative: trim 20%",
            "neutral_history": "Neutral: hold",
            "judge_decision": "PM DECISION: Buy",
        },
        "final_trade_decision": "Executed: Buy 100",
    }


@pytest.mark.unit
def test_write_report_tree_creates_files(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path)
    assert out.name == "complete_report.md"
    assert (tmp_path / "1_analysts" / "market.md").read_text() == "MKT"
    assert (tmp_path / "1_analysts" / "news.md").read_text() == "NEWS"
    assert (tmp_path / "2_research" / "manager.md").read_text() == "RM PLAN"
    assert (tmp_path / "3_trading" / "trader.md").read_text() == "TRADE"
    assert (tmp_path / "5_portfolio" / "decision.md").read_text() == "PM DECISION"
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "MKT" in complete and "PM DECISION" in complete


@pytest.mark.unit
def test_save_reports_explicit_path(tmp_path):
    # Unbound: with an explicit save_path, the method doesn't touch self/config.
    out = TradingAgentsGraph.save_reports(None, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    mock_self = SimpleNamespace(config={"results_dir": str(tmp_path)})
    out = TradingAgentsGraph.save_reports(mock_self, _state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")


@pytest.mark.unit
def test_write_report_tree_full_state(tmp_path):
    """All A-Share report sections are written to disk."""
    out = write_report_tree(_full_state(), "000001", tmp_path)
    assert out.name == "complete_report.md"

    # 1. Analysts — all 6 types
    assert (tmp_path / "1_analysts" / "market.md").exists()
    assert (tmp_path / "1_analysts" / "sentiment.md").exists()
    assert (tmp_path / "1_analysts" / "news.md").exists()
    assert (tmp_path / "1_analysts" / "fundamentals.md").exists()
    assert (tmp_path / "1_analysts" / "governance.md").exists()
    assert (tmp_path / "1_analysts" / "industry.md").exists()

    # 2. Research — bull, bear, manager
    assert (tmp_path / "2_research" / "bull.md").exists()
    assert (tmp_path / "2_research" / "bear.md").exists()
    assert (tmp_path / "2_research" / "manager.md").exists()

    # 3. Trading
    assert (tmp_path / "3_trading" / "trader.md").exists()

    # 4. Risk — aggressive, conservative, neutral
    assert (tmp_path / "4_risk" / "aggressive.md").exists()
    assert (tmp_path / "4_risk" / "conservative.md").exists()
    assert (tmp_path / "4_risk" / "neutral.md").exists()

    # 5. Portfolio
    assert (tmp_path / "5_portfolio" / "decision.md").exists()

    # Consolidated report content
    complete = out.read_text()
    assert "Trading Analysis Report: 000001" in complete
    assert "Analysis Date: 2026-07-03" in complete
    assert "I. Analyst Team Reports" in complete
    assert "II. Research Team Decision" in complete
    assert "III. Trading Team Plan" in complete
    assert "IV. Risk Management Team Decision" in complete
    assert "V. Portfolio Manager Decision" in complete
    assert "Bullish trend" in complete
    assert "ESG score 82" in complete
    assert "Semiconductor sector" in complete
    assert "PM DECISION: Buy" in complete


@pytest.mark.unit
def test_write_report_tree_minimal_state(tmp_path):
    """Only a few report fields — no error, only present sections written."""
    state = {
        "market_report": "MKT",
        "trader_investment_plan": "TRADE",
    }
    out = write_report_tree(state, "AAPL", tmp_path)
    assert out.exists()
    assert (tmp_path / "1_analysts" / "market.md").exists()
    assert (tmp_path / "3_trading" / "trader.md").exists()
    # None of the optional directories exist
    assert not (tmp_path / "1_analysts" / "news.md").exists()
    assert not (tmp_path / "2_research").exists()
    assert not (tmp_path / "4_risk").exists()
    assert not (tmp_path / "5_portfolio").exists()
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "I. Analyst Team Reports" in complete
    assert "III. Trading Team Plan" in complete
    # No empty sections
    assert "II. Research" not in complete
    assert "IV. Risk" not in complete
    assert "V. Portfolio" not in complete
