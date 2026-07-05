"""Tests for utility tool functions in ``tradingagents/agents/utils/``.

Each ``@tool``-decorated function delegates to ``route_to_vendor`` (or
``build_verified_market_snapshot`` for the market-data validation tool).
These are only reachable through an LLM tool-call in production; this suite
tests them directly with mocked vendor calls.

Also covers the heuristic ``parse_rating`` parser in ``rating.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ===================================================================
# technical_indicators_tools  —  33%  → 100%  (lines 30-48)
# ===================================================================


@pytest.mark.unit
class TestGetIndicators:
    """get_indicators — loop, guard clause, error handling.

    Uses ``.func`` to access the raw Python function from the LangChain
    ``StructuredTool`` wrapper so we can call it with positional args.
    """

    _MOD = "tradingagents.agents.utils.technical_indicators_tools"

    @pytest.fixture
    def tool(self):
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators
        return get_indicators.func

    def test_single_indicator(self, tool):
        with patch(f"{self._MOD}.route_to_vendor", return_value="RSI: 55.2") as mock_route:
            result = tool("AAPL", "rsi", "2026-01-15", 30)
        assert "RSI: 55.2" in result
        mock_route.assert_called_once_with("get_indicators", "AAPL", "rsi", "2026-01-15", 30)

    def test_multiple_comma_separated_indicators(self, tool):
        with patch(f"{self._MOD}.route_to_vendor", return_value="MACD: bullish") as mock_route:
            result = tool("AAPL", "macd, rsi", "2026-01-15", 30)
        assert mock_route.call_count == 2
        assert "MACD: bullish" in result

    def test_skips_get_fund_flow_guard(self, tool):
        with patch(f"{self._MOD}.route_to_vendor", return_value="x") as mock_route:
            result = tool("AAPL", "get_fund_flow", "2026-01-15", 30)
        assert "standalone tool" in result
        mock_route.assert_not_called()

    def test_runtime_error_logged(self, tool):
        with patch(f"{self._MOD}.route_to_vendor",
                   side_effect=RuntimeError("所有数据源均不可用")):
            result = tool("AAPL", "rsi", "2026-01-15", 30)
        assert "所有数据源均不可用" in result

    def test_generic_exception_caught(self, tool):
        with patch(f"{self._MOD}.route_to_vendor",
                   side_effect=ValueError("unexpected")):
            result = tool("AAPL", "rsi", "2026-01-15", 30)
        assert "unexpected" in result

    def test_mixed_guard_and_valid(self, tool):
        with patch(f"{self._MOD}.route_to_vendor", return_value="RSI: 55.2") as mock_route:
            result = tool("AAPL", "get_fund_flow, rsi", "2026-01-15", 30)
        assert mock_route.call_count == 1
        assert "standalone tool" in result
        assert "RSI: 55.2" in result


# ===================================================================
# fundamental_data_tools  —  71%  → 100%  (7 tools, 7 missing lines)
# ===================================================================


@pytest.mark.unit
class TestFundamentalDataTools:
    """Each @tool delegates to route_to_vendor."""

    _MOD = "tradingagents.agents.utils.fundamental_data_tools"

    def _tool(self, name):
        import importlib
        mod = importlib.import_module(self._MOD)
        return getattr(mod, name).func

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="PE: 25.5")
    def test_get_fundamentals(self, mock_route):
        fn = self._tool("get_fundamentals")
        assert fn("AAPL", "2026-01-15") == "PE: 25.5"
        mock_route.assert_called_once_with("get_fundamentals", "AAPL", "2026-01-15")

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="Total Assets: 350B")
    def test_get_balance_sheet(self, mock_route):
        fn = self._tool("get_balance_sheet")
        assert fn("AAPL", "quarterly", "2026-01-15") == "Total Assets: 350B"
        mock_route.assert_called_once_with("get_balance_sheet", "AAPL", "quarterly", "2026-01-15")

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="Operating: 120B")
    def test_get_cashflow(self, mock_route):
        fn = self._tool("get_cashflow")
        assert fn("AAPL", "annual", "2026-01-15") == "Operating: 120B"
        mock_route.assert_called_once_with("get_cashflow", "AAPL", "annual", "2026-01-15")

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="Revenue: 390B")
    def test_get_income_statement(self, mock_route):
        fn = self._tool("get_income_statement")
        assert fn("AAPL", "quarterly", "2026-01-15") == "Revenue: 390B"
        mock_route.assert_called_once_with("get_income_statement", "AAPL", "quarterly", "2026-01-15")

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="EPS: 6.5")
    def test_get_earnings_estimates(self, mock_route):
        fn = self._tool("get_earnings_estimates")
        assert fn("AAPL") == "EPS: 6.5"
        mock_route.assert_called_once_with("get_earnings_estimates", "AAPL")

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="Shareholders: 45000")
    def test_get_shareholder_count(self, mock_route):
        fn = self._tool("get_shareholder_count")
        assert fn("AAPL") == "Shareholders: 45000"
        mock_route.assert_called_once_with("get_shareholder_count", "AAPL")

    @patch("tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
           return_value="Dividend: 0.96")
    def test_get_dividend_history(self, mock_route):
        fn = self._tool("get_dividend_history")
        assert fn("AAPL") == "Dividend: 0.96"
        mock_route.assert_called_once_with("get_dividend_history", "AAPL")


# ===================================================================
# news_data_tools  —  72%  → 100%  (10 tools, 10 missing lines)
# ===================================================================


@pytest.mark.unit
class TestNewsDataTools:
    """Each @tool delegates to route_to_vendor."""

    _MOD = "tradingagents.agents.utils.news_data_tools"

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="AAPL up 2%")
    def test_get_news(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_news
        assert get_news.func("AAPL", "2026-01-08", "2026-01-15") == "AAPL up 2%"
        mock_route.assert_called_once_with("get_news", "AAPL", "2026-01-08", "2026-01-15")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Global markets mixed")
    def test_get_global_news(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_global_news
        result = get_global_news.func("2026-01-15", 7, 10)
        assert result == "Global markets mixed"
        mock_route.assert_called_once_with("get_global_news", "2026-01-15", 7, 10)

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Insider sold 5000 shares")
    def test_get_insider_transactions(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_insider_transactions
        result = get_insider_transactions.func("AAPL")
        assert result == "Insider sold 5000 shares"
        mock_route.assert_called_once_with("get_insider_transactions", "AAPL")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Board meeting notice")
    def test_get_company_announcements(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_company_announcements
        result = get_company_announcements.func("AAPL", "2026-01-08", "2026-01-15")
        assert result == "Board meeting notice"
        mock_route.assert_called_once_with("get_company_announcements", "AAPL", "2026-01-08", "2026-01-15")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Restricted shares: 1M")
    def test_get_restricted_release(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_restricted_release
        result = get_restricted_release.func("AAPL", "2026-01-08", "2026-01-15")
        assert result == "Restricted shares: 1M"
        mock_route.assert_called_once_with("get_restricted_release", "AAPL", "2026-01-08", "2026-01-15")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Institutional: +200K shares")
    def test_get_institutional_holdings(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_institutional_holdings
        result = get_institutional_holdings.func("AAPL")
        assert result == "Institutional: +200K shares"
        mock_route.assert_called_once_with("get_institutional_holdings", "AAPL")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Northbound: 10B")
    def test_get_northbound_hold_news(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_northbound_hold
        result = get_northbound_hold.func("AAPL")
        assert result == "Northbound: 10B"
        mock_route.assert_called_once_with("get_northbound_hold", "AAPL")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Dragon-tiger: buy 50M")
    def test_get_dragon_tiger(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_dragon_tiger
        result = get_dragon_tiger.func("AAPL")
        assert result == "Dragon-tiger: buy 50M"
        mock_route.assert_called_once_with("get_dragon_tiger", "AAPL")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Block trade: 100K @ 150")
    def test_get_block_trade(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_block_trade
        result = get_block_trade.func("AAPL")
        assert result == "Block trade: 100K @ 150"
        mock_route.assert_called_once_with("get_block_trade", "AAPL")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Pledge ratio: 15%")
    def test_get_pledge_ratio(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_pledge_ratio
        result = get_pledge_ratio.func("AAPL")
        assert result == "Pledge ratio: 15%"
        mock_route.assert_called_once_with("get_pledge_ratio", "AAPL")

    @patch("tradingagents.agents.utils.news_data_tools.route_to_vendor",
           return_value="Target: 200 by Q4")
    def test_get_research_reports(self, mock_route):
        from tradingagents.agents.utils.news_data_tools import get_research_reports
        result = get_research_reports.func("AAPL")
        assert result == "Target: 200 by Q4"
        mock_route.assert_called_once_with("get_research_reports", "AAPL")


# ===================================================================
# fund_flow_tools  —  73%  → 100%  (4 tools, 4 missing lines)
# ===================================================================


@pytest.mark.unit
class TestFundFlowTools:
    """Each @tool delegates to route_to_vendor."""

    _MOD = "tradingagents.agents.utils.fund_flow_tools"

    @patch("tradingagents.agents.utils.fund_flow_tools.route_to_vendor",
           return_value="Main force inflow: 50M")
    def test_get_fund_flow(self, mock_route):
        from tradingagents.agents.utils.fund_flow_tools import get_fund_flow
        assert get_fund_flow.func("AAPL") == "Main force inflow: 50M"
        mock_route.assert_called_once_with("get_fund_flow", "AAPL")

    @patch("tradingagents.agents.utils.fund_flow_tools.route_to_vendor",
           return_value="Margin: 2.5B")
    def test_get_margin_trading(self, mock_route):
        from tradingagents.agents.utils.fund_flow_tools import get_margin_trading
        assert get_margin_trading.func("AAPL") == "Margin: 2.5B"
        mock_route.assert_called_once_with("get_margin_trading", "AAPL")

    @patch("tradingagents.agents.utils.fund_flow_tools.route_to_vendor",
           return_value="Tech sector inflow: 1B")
    def test_get_sector_fund_flow(self, mock_route):
        from tradingagents.agents.utils.fund_flow_tools import get_sector_fund_flow
        assert get_sector_fund_flow.func("科技") == "Tech sector inflow: 1B"
        mock_route.assert_called_once_with("get_sector_fund_flow", "科技")

    @patch("tradingagents.agents.utils.fund_flow_tools.route_to_vendor",
           return_value="Northbound: 5B")
    def test_get_northbound_hold_flow(self, mock_route):
        from tradingagents.agents.utils.fund_flow_tools import get_northbound_hold
        assert get_northbound_hold.func("AAPL") == "Northbound: 5B"
        mock_route.assert_called_once_with("get_northbound_hold", "AAPL")


# ===================================================================
# Single-tool modules  —  83%  → 100%
# ===================================================================


@pytest.mark.unit
class TestSingleToolModules:
    """industry_data_tools, core_stock_tools, prediction_markets_tools, macro_data_tools."""

    @patch("tradingagents.agents.utils.industry_data_tools.route_to_vendor",
           return_value="PE: 25, PB: 4.5")
    def test_get_industry_valuation(self, mock_route):
        from tradingagents.agents.utils.industry_data_tools import get_industry_valuation
        assert get_industry_valuation.func("AAPL") == "PE: 25, PB: 4.5"
        mock_route.assert_called_once_with("get_industry_valuation", "AAPL")

    @patch("tradingagents.agents.utils.core_stock_tools.route_to_vendor",
           return_value="OHLCV data")
    def test_get_stock_data(self, mock_route):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data
        result = get_stock_data.func("AAPL", "2026-01-01", "2026-01-15")
        assert result == "OHLCV data"
        mock_route.assert_called_once_with("get_stock_data", "AAPL", "2026-01-01", "2026-01-15")

    @patch("tradingagents.agents.utils.prediction_markets_tools.route_to_vendor",
           return_value="Fed cut: 65%")
    def test_get_prediction_markets(self, mock_route):
        from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets
        result = get_prediction_markets.func("Fed rate cut")
        assert result == "Fed cut: 65%"
        mock_route.assert_called_once_with("get_prediction_markets", "Fed rate cut", None)

    @patch("tradingagents.agents.utils.macro_data_tools.route_to_vendor",
           return_value="CPI: 3.2%")
    def test_get_macro_indicators(self, mock_route):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators
        result = get_macro_indicators.func("cpi", "2026-01-15", 365)
        assert result == "CPI: 3.2%"
        mock_route.assert_called_once_with("get_macro_indicators", "cpi", "2026-01-15", 365)


# ===================================================================
# market_data_validation_tools  —  83%  → 100%
# ===================================================================


@pytest.mark.unit
class TestMarketDataValidationTools:
    """get_verified_market_snapshot delegates to build_verified_market_snapshot."""

    @patch(
        "tradingagents.agents.utils.market_data_validation_tools.build_verified_market_snapshot",
        return_value="Close: 150.25",
    )
    def test_get_verified_market_snapshot(self, mock_build):
        from tradingagents.agents.utils.market_data_validation_tools import (
            get_verified_market_snapshot,
        )

        result = get_verified_market_snapshot.func("AAPL", "2026-01-15", 30)
        assert result == "Close: 150.25"
        mock_build.assert_called_once_with("AAPL", "2026-01-15", 30)


# ===================================================================
# rating.py  —  96%  → 100%  (line 62: Chinese word second pass)
# ===================================================================


@pytest.mark.unit
class TestParseRating:
    """parse_rating — three-pass heuristic parser coverage completion."""

    def test_label_with_chinese_word_via_mapping(self):
        """Chinese label + Chinese word → first pass, _CN_TO_EN_RATING lookup."""
        from tradingagents.agents.utils.rating import parse_rating
        assert parse_rating("建议：买入") == "Buy"

    def test_chinese_word_second_pass(self):
        """Bare Chinese word (no label) → second pass matches _CN_TO_EN_RATING."""
        from tradingagents.agents.utils.rating import parse_rating
        assert parse_rating("增持") == "Overweight"

    def test_english_word_third_pass(self):
        """No label, no Chinese word → third pass matches _RATING_SET."""
        from tradingagents.agents.utils.rating import parse_rating
        assert parse_rating("The market looks bullish, recommend Buy.") == "Buy"

    def test_falls_back_to_default(self):
        """No rating found → default."""
        from tradingagents.agents.utils.rating import parse_rating
        assert parse_rating("No clear signal.") == "Hold"

    def test_chinese_sell_via_label(self):
        """Chinese label 评级：卖出 → first pass maps to Sell."""
        from tradingagents.agents.utils.rating import parse_rating
        assert parse_rating("评级：卖出") == "Sell"
