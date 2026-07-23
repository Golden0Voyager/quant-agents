from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestNewsDataTools:
    """Verify every @tool-decorated function in news_data_tools calls
    route_to_vendor with the correct method name and passes through
    all arguments faithfully."""

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_news(self, mock_route):
        mock_route.return_value = "news_result"
        from tradingagents.agents.utils.news_data_tools import get_news

        result = get_news.func("AAPL", "2026-01-01", "2026-01-31")
        mock_route.assert_called_once_with("get_news", "AAPL", "2026-01-01", "2026-01-31")
        assert result == "news_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_global_news_defaults(self, mock_route):
        mock_route.return_value = "global_result"
        from tradingagents.agents.utils.news_data_tools import get_global_news

        result = get_global_news.func("2026-06-01")
        mock_route.assert_called_once_with("get_global_news", "2026-06-01", None, None)
        assert result == "global_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_global_news_with_options(self, mock_route):
        mock_route.return_value = "global_result"
        from tradingagents.agents.utils.news_data_tools import get_global_news

        result = get_global_news.func("2026-06-01", look_back_days=7, limit=20)
        mock_route.assert_called_once_with("get_global_news", "2026-06-01", 7, 20)
        assert result == "global_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_insider_transactions(self, mock_route):
        mock_route.return_value = "insider_result"
        from tradingagents.agents.utils.news_data_tools import get_insider_transactions

        result = get_insider_transactions.func("AAPL")
        mock_route.assert_called_once_with("get_insider_transactions", "AAPL")
        assert result == "insider_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_company_announcements(self, mock_route):
        mock_route.return_value = "announcement_result"
        from tradingagents.agents.utils.news_data_tools import get_company_announcements

        result = get_company_announcements.func("600519.SS", "2026-01-01", "2026-01-31")
        mock_route.assert_called_once_with(
            "get_company_announcements", "600519.SS", "2026-01-01", "2026-01-31"
        )
        assert result == "announcement_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_restricted_release(self, mock_route):
        mock_route.return_value = "release_result"
        from tradingagents.agents.utils.news_data_tools import get_restricted_release

        result = get_restricted_release.func("000001.SZ", "2026-01-01", "2026-01-31")
        mock_route.assert_called_once_with(
            "get_restricted_release", "000001.SZ", "2026-01-01", "2026-01-31"
        )
        assert result == "release_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_institutional_holdings(self, mock_route):
        mock_route.return_value = "holdings_result"
        from tradingagents.agents.utils.news_data_tools import get_institutional_holdings

        result = get_institutional_holdings.func("AAPL")
        mock_route.assert_called_once_with("get_institutional_holdings", "AAPL")
        assert result == "holdings_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_northbound_hold(self, mock_route):
        mock_route.return_value = "northbound_result"
        from tradingagents.agents.utils.news_data_tools import get_northbound_hold

        result = get_northbound_hold.func("600519.SS")
        mock_route.assert_called_once_with("get_northbound_hold", "600519.SS")
        assert result == "northbound_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_dragon_tiger(self, mock_route):
        mock_route.return_value = "dragon_tiger_result"
        from tradingagents.agents.utils.news_data_tools import get_dragon_tiger

        result = get_dragon_tiger.func("300750.SZ")
        mock_route.assert_called_once_with("get_dragon_tiger", "300750.SZ")
        assert result == "dragon_tiger_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_block_trade(self, mock_route):
        mock_route.return_value = "block_trade_result"
        from tradingagents.agents.utils.news_data_tools import get_block_trade

        result = get_block_trade.func("000001.SZ")
        mock_route.assert_called_once_with("get_block_trade", "000001.SZ")
        assert result == "block_trade_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_pledge_ratio(self, mock_route):
        mock_route.return_value = "pledge_result"
        from tradingagents.agents.utils.news_data_tools import get_pledge_ratio

        result = get_pledge_ratio.func("600519.SS")
        mock_route.assert_called_once_with("get_pledge_ratio", "600519.SS")
        assert result == "pledge_result"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_research_reports(self, mock_route):
        mock_route.return_value = "research_result"
        from tradingagents.agents.utils.news_data_tools import get_research_reports

        result = get_research_reports.func("AAPL")
        mock_route.assert_called_once_with("get_research_reports", "AAPL")
        assert result == "research_result"

    # ---- curr_date branches (if None / else) ----

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_institutional_holdings_with_curr_date(self, mock_route):
        mock_route.return_value = "holdings_dated"
        from tradingagents.agents.utils.news_data_tools import get_institutional_holdings

        result = get_institutional_holdings.func("AAPL", curr_date="2026-07-03")
        mock_route.assert_called_once_with(
            "get_institutional_holdings", "AAPL", "2026-07-03"
        )
        assert result == "holdings_dated"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_northbound_hold_with_curr_date(self, mock_route):
        mock_route.return_value = "northbound_dated"
        from tradingagents.agents.utils.news_data_tools import get_northbound_hold

        result = get_northbound_hold.func("600519.SS", curr_date="2026-07-03")
        mock_route.assert_called_once_with(
            "get_northbound_hold", "600519.SS", "2026-07-03"
        )
        assert result == "northbound_dated"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_dragon_tiger_with_curr_date(self, mock_route):
        mock_route.return_value = "dragon_dated"
        from tradingagents.agents.utils.news_data_tools import get_dragon_tiger

        result = get_dragon_tiger.func("300750.SZ", curr_date="2026-07-03")
        mock_route.assert_called_once_with(
            "get_dragon_tiger", "300750.SZ", "2026-07-03"
        )
        assert result == "dragon_dated"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_block_trade_with_curr_date(self, mock_route):
        mock_route.return_value = "block_dated"
        from tradingagents.agents.utils.news_data_tools import get_block_trade

        result = get_block_trade.func("000001.SZ", curr_date="2026-07-03")
        mock_route.assert_called_once_with(
            "get_block_trade", "000001.SZ", "2026-07-03"
        )
        assert result == "block_dated"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor")
    def test_get_research_reports_with_curr_date(self, mock_route):
        mock_route.return_value = "research_dated"
        from tradingagents.agents.utils.news_data_tools import get_research_reports

        result = get_research_reports.func("AAPL", curr_date="2026-07-03")
        mock_route.assert_called_once_with(
            "get_research_reports", "AAPL", "2026-07-03"
        )
        assert result == "research_dated"


# ===================================================================
# get_institutional_intelligence (local import from smartmoney_vendor)
# ===================================================================


@pytest.mark.unit
class TestGetInstitutionalIntelligence:
    @patch(
        "tradingagents.dataflows.smartmoney_vendor.get_institutional_intelligence",
        return_value="survey: 10 institutions visited in Q2",
    )
    def test_returns_intelligence(self, mock_intel):
        from tradingagents.agents.utils.news_data_tools import get_institutional_intelligence

        result = get_institutional_intelligence.func("600519.SS")
        mock_intel.assert_called_once_with("600519.SS", None)
        assert "survey" in result

    @patch(
        "tradingagents.dataflows.smartmoney_vendor.get_institutional_intelligence",
        side_effect=ConnectionError("timeout"),
    )
    def test_error_returns_no_data_message(self, mock_intel):
        from tradingagents.agents.utils.news_data_tools import get_institutional_intelligence

        result = get_institutional_intelligence.func("000001.SZ")
        assert "NO_DATA_AVAILABLE" in result
        assert "000001.SZ" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.news_data_tools import get_institutional_intelligence

        assert hasattr(get_institutional_intelligence, "name")
        assert get_institutional_intelligence.name == "get_institutional_intelligence"

    def test_alias_get_institution_survey(self):
        from tradingagents.agents.utils.news_data_tools import (
            get_institution_survey,
            get_institutional_intelligence,
        )

        assert get_institution_survey is get_institutional_intelligence
