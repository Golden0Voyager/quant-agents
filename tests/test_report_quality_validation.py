import pytest

from tradingagents.graph.analyst_execution import (
    ANALYST_NODE_SPECS,
    build_data_quality_summary,
    validate_report_quality,
)


@pytest.mark.unit
class TestValidateReportQuality:

    def test_reliable_report_marked_reliable(self):
        report = (
            "## Market Analysis\n"
            "The stock showed strong momentum with volume increasing 20%.\n"
            "Price action broke above the 50-day moving average on heavy\n"
            "volume which confirms the bullish breakout. Support sits at\n"
            "the 200-day MA while resistance is at the recent high. RSI\n"
            "is trending higher and MACD has crossed bullish. The overall\n"
            "trend remains positive with breadth supporting further upside\n"
            "in the near term. Traders should watch for a pullback to\n"
            "support levels before adding positions.\n"
        )
        assert validate_report_quality("market_report", report) == "reliable"

    def test_empty_report_marked_no_data(self):
        assert validate_report_quality("market_report", "") == "no_data"

    def test_report_with_no_data_sentinel_marked_no_data(self):
        report = (
            "## Market Analysis\n"
            "NO_DATA_AVAILABLE: No market data found for 'FAKE'.\n"
        )
        assert validate_report_quality("market_report", report) == "no_data"

    def test_very_short_report_marked_sparse(self):
        assert validate_report_quality("news_report", "Very little data.") == "sparse"

    def test_50_word_report_marked_reliable(self):
        report = "word " * 51
        assert validate_report_quality("sentiment_report", report) == "reliable"

    def test_49_word_report_marked_sparse(self):
        report = "word " * 49
        assert validate_report_quality("sentiment_report", report) == "sparse"


@pytest.mark.unit
class TestBuildDataQualitySummary:

    def test_summary_includes_all_analysts(self):
        state = {
            "market_report": "## Detailed market analysis\n" * 20,
            "sentiment_report": "",
            "news_report": "NO_DATA_AVAILABLE for ticker",
            "fundamentals_report": "",
            "governance_report": "Some governance data",
            "industry_report": "Industry analysis with data\n" * 10,
        }
        specs = list(ANALYST_NODE_SPECS.values())
        summary = build_data_quality_summary(state, specs)

        assert "market" in summary.lower() or "Market" in summary
        assert "sentiment" in summary.lower() or "Sentiment" in summary
        assert "news" in summary.lower()
        assert "fundamentals" in summary.lower()
        assert "governance" in summary.lower()
        assert "industry" in summary.lower()

    def test_summary_highlights_unavailable_reports(self):
        state = {
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "governance_report": "",
            "industry_report": "",
        }
        specs = list(ANALYST_NODE_SPECS.values())
        summary = build_data_quality_summary(state, specs)
        assert "❌" in summary or "no_data" in summary.lower()
