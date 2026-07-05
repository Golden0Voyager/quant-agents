"""Tests for alpha_vantage_news module with mocked API calls.

``alpha_vantage_news.py`` exports three functions:
- ``get_news(ticker, start_date, end_date)``
- ``get_global_news(curr_date, look_back_days, limit)``
- ``get_insider_transactions(symbol)``

All three delegate to ``_make_api_request``.
"""

import json
from unittest.mock import patch

import pytest

from tradingagents.dataflows.alpha_vantage_common import (
    AlphaVantageNotConfiguredError,
    AlphaVantageRateLimitError,
)


def _fake_news_json(**overrides) -> str:
    """Return a minimal Alpha Vantage news JSON response."""
    base = {
        "items": "1",
        "sentiment_score_definition": "x > 0 => bullish",
        "feed": [
            {
                "title": "AAPL Surges on Strong Earnings",
                "url": "https://example.com/aapl-earnings",
                "time_published": "20260615T120000",
                "overall_sentiment_score": 0.75,
                "overall_sentiment_label": "Bullish",
            }
        ],
    }
    base.update(overrides)
    return json.dumps(base)


# =========================================================================
# get_news tests
# =========================================================================


@pytest.mark.unit
class TestAlphaVantageGetNews:
    """Tests for ``get_news``."""

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_news_correct_params(self, mock_api):
        """Verifies correct NEWS_SENTIMENT function and params."""
        from tradingagents.dataflows.alpha_vantage_news import get_news

        mock_api.return_value = _fake_news_json()
        get_news("AAPL", "2026-06-01", "2026-06-30")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        assert call_args[0][0] == "NEWS_SENTIMENT"
        params = call_args[0][1]
        assert params["tickers"] == "AAPL"
        assert params["time_from"] == "20260601T0000"
        assert params["time_to"] == "20260630T0000"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_news_returns_json_string(self, mock_api):
        """Returns the raw JSON string from the API."""
        from tradingagents.dataflows.alpha_vantage_news import get_news

        expected = _fake_news_json(title="Updated headline")
        mock_api.return_value = expected
        result = get_news("AAPL", "2026-06-01", "2026-06-30")
        assert result == expected

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_news_different_ticker(self, mock_api):
        """Different ticker is passed through to API params."""
        from tradingagents.dataflows.alpha_vantage_news import get_news

        mock_api.return_value = _fake_news_json()
        get_news("MSFT", "2026-06-01", "2026-06-30")
        params = mock_api.call_args[0][1]
        assert params["tickers"] == "MSFT"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_news_rate_limit_propagates(self, mock_api):
        """RateLimitError from _make_api_request propagates."""
        from tradingagents.dataflows.alpha_vantage_news import get_news

        mock_api.side_effect = AlphaVantageRateLimitError("rate limit")
        with pytest.raises(AlphaVantageRateLimitError):
            get_news("AAPL", "2026-06-01", "2026-06-30")

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_news_not_configured_propagates(self, mock_api):
        """NotConfiguredError from _make_api_request propagates."""
        from tradingagents.dataflows.alpha_vantage_news import get_news

        mock_api.side_effect = AlphaVantageNotConfiguredError("no key")
        with pytest.raises(AlphaVantageNotConfiguredError):
            get_news("AAPL", "2026-06-01", "2026-06-30")


# =========================================================================
# get_global_news tests
# =========================================================================


@pytest.mark.unit
class TestAlphaVantageGetGlobalNews:
    """Tests for ``get_global_news``."""

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_global_news_correct_params(self, mock_api):
        """Verifies correct function, topics, time range, and limit."""
        from tradingagents.dataflows.alpha_vantage_news import get_global_news

        mock_api.return_value = _fake_news_json()
        get_global_news("2026-07-03", look_back_days=7, limit=50)

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        assert call_args[0][0] == "NEWS_SENTIMENT"
        params = call_args[0][1]
        assert params["topics"] == "financial_markets,economy_macro,economy_monetary"
        assert params["limit"] == "50"
        # 7 days back from 2026-07-03 → 2026-06-26
        assert params["time_from"] == "20260626T0000"
        assert params["time_to"] == "20260703T0000"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_global_news_default_limit(self, mock_api):
        """Default limit is 50."""
        from tradingagents.dataflows.alpha_vantage_news import get_global_news

        mock_api.return_value = _fake_news_json()
        get_global_news("2026-07-03")

        params = mock_api.call_args[0][1]
        assert params["limit"] == "50"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_global_news_custom_lookback(self, mock_api):
        """Custom look_back_days changes the time_from."""
        from tradingagents.dataflows.alpha_vantage_news import get_global_news

        mock_api.return_value = _fake_news_json()
        get_global_news("2026-07-03", look_back_days=30, limit=100)

        params = mock_api.call_args[0][1]
        assert params["limit"] == "100"
        # 30 days back from 2026-07-03 → 2026-06-03
        assert params["time_from"] == "20260603T0000"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_global_news_single_day_lookback(self, mock_api):
        """look_back_days=1 still works correctly."""
        from tradingagents.dataflows.alpha_vantage_news import get_global_news

        mock_api.return_value = _fake_news_json()
        get_global_news("2026-07-03", look_back_days=1, limit=10)

        params = mock_api.call_args[0][1]
        assert params["limit"] == "10"
        assert params["time_from"] == "20260702T0000"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_global_news_empty_response(self, mock_api):
        """Empty response is passed through."""
        from tradingagents.dataflows.alpha_vantage_news import get_global_news

        mock_api.return_value = ""
        result = get_global_news("2026-07-03")
        assert result == ""

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_global_news_rate_limit_propagates(self, mock_api):
        """RateLimitError propagates."""
        from tradingagents.dataflows.alpha_vantage_news import get_global_news

        mock_api.side_effect = AlphaVantageRateLimitError("rate limit")
        with pytest.raises(AlphaVantageRateLimitError):
            get_global_news("2026-07-03")


# =========================================================================
# get_insider_transactions tests
# =========================================================================


@pytest.mark.unit
class TestAlphaVantageGetInsiderTransactions:
    """Tests for ``get_insider_transactions``."""

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_insider_transactions_correct_params(self, mock_api):
        """Verifies correct INSIDER_TRANSACTIONS function and symbol param."""
        from tradingagents.dataflows.alpha_vantage_news import get_insider_transactions

        mock_api.return_value = "{}"
        get_insider_transactions("IBM")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        assert call_args[0][0] == "INSIDER_TRANSACTIONS"
        params = call_args[0][1]
        assert params["symbol"] == "IBM"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_insider_transactions_different_symbol(self, mock_api):
        """Different symbol is passed through to params."""
        from tradingagents.dataflows.alpha_vantage_news import get_insider_transactions

        mock_api.return_value = "{}"
        get_insider_transactions("AAPL")
        params = mock_api.call_args[0][1]
        assert params["symbol"] == "AAPL"

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_insider_transactions_returns_json_string(self, mock_api):
        """Returns the raw response string from the API."""
        from tradingagents.dataflows.alpha_vantage_news import get_insider_transactions

        expected = json.dumps({"symbol": "IBM", "data": []})
        mock_api.return_value = expected
        result = get_insider_transactions("IBM")
        assert result == expected

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_insider_transactions_rate_limit_propagates(self, mock_api):
        """RateLimitError propagates."""
        from tradingagents.dataflows.alpha_vantage_news import get_insider_transactions

        mock_api.side_effect = AlphaVantageRateLimitError("rate limit")
        with pytest.raises(AlphaVantageRateLimitError):
            get_insider_transactions("IBM")

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_insider_transactions_not_configured_propagates(self, mock_api):
        """NotConfiguredError propagates."""
        from tradingagents.dataflows.alpha_vantage_news import get_insider_transactions

        mock_api.side_effect = AlphaVantageNotConfiguredError("no key")
        with pytest.raises(AlphaVantageNotConfiguredError):
            get_insider_transactions("IBM")

    @patch("tradingagents.dataflows.alpha_vantage_news._make_api_request")
    def test_get_insider_transactions_empty_response(self, mock_api):
        """Empty response is passed through."""
        from tradingagents.dataflows.alpha_vantage_news import get_insider_transactions

        mock_api.return_value = ""
        result = get_insider_transactions("IBM")
        assert result == ""
