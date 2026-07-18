"""Tests for tradingagents.dataflows.yfinance_news."""

import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from yfinance.exceptions import YFRateLimitError

import tradingagents.dataflows.yfinance_news as ynews
from tradingagents.dataflows.errors import VendorRateLimitError


def _epoch(date_str: str) -> int:
    return int(time.mktime(datetime.strptime(date_str, "%Y-%m-%d").timetuple()))

import pytest

from tradingagents.dataflows.yfinance_news import (
    _extract_article_data,
    get_global_news_yfinance,
    get_news_yfinance,
)

pytestmark = pytest.mark.unit


def _mock_config(**overrides):
    base = {
        "news_article_limit": 20,
        "global_news_article_limit": 10,
        "global_news_lookback_days": 7,
        "global_news_queries": ["stock market", "economy"],
    }
    base.update(overrides)
    return base


def _nested_article(
    title="Test Article",
    summary="Test summary",
    publisher="Test Publisher",
    url="https://example.com/article",
    pub_date="2025-06-15T10:00:00Z",
):
    return {
        "content": {
            "title": title,
            "summary": summary,
            "provider": {"displayName": publisher},
            "canonicalUrl": {"url": url},
            "pubDate": pub_date,
        }
    }


def _flat_article(
    title="Flat Article",
    summary="Flat summary",
    publisher="Flat Publisher",
    link="https://example.com/flat",
    provider_publish_time=None,
):
    article = {
        "title": title,
        "summary": summary,
        "publisher": publisher,
        "link": link,
    }
    if provider_publish_time is not None:
        article["providerPublishTime"] = provider_publish_time
    return article


@pytest.mark.unit
class ExtractArticleDataTests(unittest.TestCase):
    def test_extract_nested_content_structure(self):
        article = _nested_article()
        result = _extract_article_data(article)
        self.assertEqual(result["title"], "Test Article")
        self.assertEqual(result["summary"], "Test summary")
        self.assertEqual(result["publisher"], "Test Publisher")
        self.assertEqual(result["link"], "https://example.com/article")
        self.assertIsNotNone(result["pub_date"])
        self.assertIsInstance(result["pub_date"], datetime)

    def test_extract_nested_no_title_fallback(self):
        article = {"content": {"summary": "no title"}}
        result = _extract_article_data(article)
        self.assertEqual(result["title"], "No title")
        self.assertEqual(result["summary"], "no title")
        self.assertEqual(result["publisher"], "Unknown")
        self.assertEqual(result["link"], "")

    def test_extract_nested_no_pub_date(self):
        article = {"content": {"title": "No Date"}}
        result = _extract_article_data(article)
        self.assertIsNone(result["pub_date"])

    def test_extract_nested_invalid_pub_date_suppresses_error(self):
        article = {"content": {"title": "Bad Date", "pubDate": "not-a-date"}}
        result = _extract_article_data(article)
        self.assertIsNone(result["pub_date"])

    def test_extract_uses_click_through_url_fallback(self):
        article = {
            "content": {
                "title": "Click",
                "clickThroughUrl": {"url": "https://example.com/click"},
            }
        }
        result = _extract_article_data(article)
        self.assertEqual(result["link"], "https://example.com/click")

    def test_extract_flat_structure(self):
        article = _flat_article()
        result = _extract_article_data(article)
        self.assertEqual(result["title"], "Flat Article")
        self.assertEqual(result["summary"], "Flat summary")
        self.assertEqual(result["publisher"], "Flat Publisher")
        self.assertEqual(result["link"], "https://example.com/flat")
        self.assertIsNone(result["pub_date"])

    def test_extract_flat_structure_missing_fields(self):
        article = {}
        result = _extract_article_data(article)
        self.assertEqual(result["title"], "No title")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["publisher"], "Unknown")
        self.assertEqual(result["link"], "")
        self.assertIsNone(result["pub_date"])


@pytest.mark.unit
class GetNewsYFinanceTests(unittest.TestCase):
    def setUp(self):
        self.ticker = "AAPL"
        self.start = "2025-06-01"
        self.end = "2025-06-30"
        self.article_limit = 20

    def test_returns_formatted_news(self):
        mock_news = [
            _nested_article(title="Apple News 1", pub_date="2025-06-10T12:00:00Z"),
            _nested_article(title="Apple News 2", pub_date="2025-06-15T12:00:00Z"),
        ]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertIn("AAPL News, from 2025-06-01 to 2025-06-30", result)
        self.assertIn("Apple News 1", result)
        self.assertIn("Apple News 2", result)
        self.assertIn("Test Publisher", result)

    def test_no_news_returns_message(self):
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = []
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertEqual(result, "No news found for AAPL")

    def test_none_news_returns_message(self):
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = None
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertEqual(result, "No news found for AAPL")

    def test_filters_articles_outside_date_range(self):
        mock_news = [
            _nested_article(title="Outside Range", pub_date="2025-05-01T12:00:00Z"),
            _nested_article(title="Inside Range", pub_date="2025-06-10T12:00:00Z"),
        ]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertNotIn("Outside Range", result)
        self.assertIn("Inside Range", result)

    def test_undated_articles_excluded_in_historical_window(self):
        mock_news = [
            _nested_article(title="No Date", pub_date=""),
            {"content": {"title": "No PubDate Field"}},
        ]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertNotIn("No Date", result)
        self.assertNotIn("No PubDate Field", result)
        self.assertIn("No news found", result)

    def test_all_articles_filtered_out_returns_message(self):
        mock_news = [
            _nested_article(title="Old News", pub_date="2025-05-01T12:00:00Z"),
        ]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertEqual(
            result,
            "No news found for AAPL between 2025-06-01 and 2025-06-30",
        )

    def test_includes_summary_and_link_in_output(self):
        mock_news = [
            _nested_article(
                title="Full Article",
                summary="Detailed content here",
                url="https://example.com/full",
                pub_date="2025-06-10T12:00:00Z",
            ),
        ]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertIn("Detailed content here", result)
        self.assertIn("https://example.com/full", result)

    def test_exception_returns_error_string(self):
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.side_effect = ConnectionError("network failure")
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertIn("Error fetching news for AAPL", result)
        self.assertIn("network failure", result)

    def test_passes_article_limit_from_config(self):
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(news_article_limit=5),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = []
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                get_news_yfinance(self.ticker, self.start, self.end)

        mock_ticker.get_news.assert_called_once_with(count=5)

    def test_uses_yf_retry_wrapper(self):
        mock_news = [_nested_article(pub_date="2025-06-10T12:00:00Z")]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ):
                retry_mock = MagicMock(side_effect=lambda f, **kwargs: f())
                with patch(
                    "tradingagents.dataflows.yfinance_news.yf_retry",
                    retry_mock,
                ):
                    get_news_yfinance(self.ticker, self.start, self.end)

        retry_mock.assert_called_once()
        call_arg = retry_mock.call_args[1].get("count", None)
        if call_arg is None:
            args, _ = retry_mock.call_args
            self.assertTrue(callable(args[0]) if args else False)

    def test_yf_rate_limit_converted_to_vendor_rate_limit_error(self):
        """Covers line 133: YFRateLimitError -> VendorRateLimitError."""
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.side_effect = YFRateLimitError()
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ), self.assertRaises(VendorRateLimitError) as ctx:
                get_news_yfinance(self.ticker, self.start, self.end)

        self.assertIn("Yahoo Finance rate-limited", str(ctx.exception))
        self.assertIn("AAPL", str(ctx.exception))

    def test_handles_flat_article_structure(self):
        mock_news = [
            _flat_article(
                title="Flat News",
                summary="Flat summary",
                link="https://flat.example.com",
                provider_publish_time=_epoch("2025-06-15"),
            ),
        ]
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance(self.ticker, self.start, self.end)

        self.assertIn("Flat News", result)
        self.assertIn("Flat summary", result)
        self.assertIn("https://flat.example.com", result)


@pytest.mark.unit
class GetGlobalNewsYFinanceTests(unittest.TestCase):
    def setUp(self):
        self.curr_date = "2025-06-20"

    def test_returns_formatted_global_news(self):
        mock_search_news = [
            _nested_article(
                title="Global News 1",
                summary="Global summary 1",
                publisher="Reuters",
                url="https://reuters.com/article1",
                pub_date="2025-06-18T12:00:00Z",
            ),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertIn("Global Market News", result)
        self.assertIn("Global News 1", result)
        self.assertIn("Reuters", result)
        self.assertIn("https://reuters.com/article1", result)

    def test_deduplicates_by_title(self):
        mock_search_news = [
            _nested_article(title="Duplicate Title", pub_date="2025-06-18T12:00:00Z"),
            _nested_article(title="Duplicate Title", pub_date="2025-06-18T12:00:00Z"),
            _nested_article(title="Unique Title", pub_date="2025-06-18T12:00:00Z"),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertIn("Duplicate Title", result)
        self.assertIn("Unique Title", result)
        self.assertEqual(result.count("Duplicate Title"), 1)

    def test_skips_articles_without_title(self):
        mock_search_news = [
            {"title": "", "summary": "empty title"},
            _nested_article(title="Real Article", pub_date="2025-06-18T12:00:00Z"),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertNotIn("empty title", result)
        self.assertIn("Real Article", result)

    def test_look_ahead_guard_skips_future_articles(self):
        mock_search_news = [
            _nested_article(
                title="Future Article",
                pub_date="2025-06-25T12:00:00Z",
            ),
            _nested_article(
                title="Past Article",
                pub_date="2025-06-15T12:00:00Z",
            ),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertNotIn("Future Article", result)
        self.assertIn("Past Article", result)

    def test_handles_flat_article_structure_in_global_news(self):
        mock_search_news = [
            _flat_article(
                title="Flat Global",
                publisher="Bloomberg",
                link="https://bloomberg.com/article",
                provider_publish_time=_epoch("2025-06-18"),
            ),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertIn("Flat Global", result)
        self.assertIn("Bloomberg", result)

    def test_no_news_returns_message(self):
        mock_search = MagicMock()
        mock_search.news = []

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertEqual(result, "No global news found for 2025-06-20")

    def test_stops_early_when_limit_reached(self):
        mock_search_news = [
            _nested_article(
                title=f"Article {i}",
                pub_date="2025-06-18T12:00:00Z",
            )
            for i in range(5)
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(global_news_article_limit=3),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertIn("Article 0", result)
        self.assertIn("Article 1", result)
        self.assertIn("Article 2", result)
        self.assertNotIn("Article 3", result)
        self.assertNotIn("Article 4", result)

    def test_exception_returns_error_string(self):
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            side_effect=RuntimeError("search failed"),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertIn("Error fetching global news", result)
        self.assertIn("search failed", result)

    def test_respects_explicit_limit_and_lookback(self):
        mock_search_news = [
            _nested_article(
                title="Article",
                pub_date="2025-06-18T12:00:00Z",
            ),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(
                self.curr_date,
                look_back_days=30,
                limit=5,
            )

        self.assertIn("Global Market News", result)
        self.assertIn("from 2025-05-21 to 2025-06-20", result)

    def test_queries_all_search_queries(self):
        mock_search_news = [
            _nested_article(
                title="Query Result",
                pub_date="2025-06-18T12:00:00Z",
            ),
        ]
        mock_search_obj = MagicMock()
        mock_search_obj.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search_obj,
        ) as mock_search_patch, patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            get_global_news_yfinance(self.curr_date)

        self.assertEqual(mock_search_patch.call_count, 2)

    def test_all_articles_filtered_out_by_date_window(self):
        """Line 214: all articles pass dedup but fail _in_news_window."""
        # Flat articles without providerPublishTime have pub_date=None.
        # In a historical window (2025-06-20), _in_news_window(None, ...)
        # returns False, so all are filtered out → kept == 0 → line 214.
        mock_search_news = [
            _flat_article(title="Undated Article 1"),
            _flat_article(title="Undated Article 2"),
        ]
        mock_search = MagicMock()
        mock_search.news = mock_search_news

        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            return_value=mock_search,
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ):
            result = get_global_news_yfinance(self.curr_date)

        self.assertEqual(
            result,
            "No global news found between 2025-06-13 and 2025-06-20",
        )

    def test_global_news_yf_rate_limit_converted_to_vendor_rate_limit_error(self):
        """Covers line 226: YFRateLimitError -> VendorRateLimitError in global news."""
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config",
            return_value=_mock_config(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf.Search",
            side_effect=YFRateLimitError(),
        ), patch(
            "tradingagents.dataflows.yfinance_news.yf_retry",
            side_effect=lambda f, **kwargs: f(),
        ), self.assertRaises(VendorRateLimitError) as ctx:
            get_global_news_yfinance(self.curr_date)

        self.assertIn("Yahoo Finance rate-limited", str(ctx.exception))


# =========================================================================
# Tests merged from test_news_lookahead.py and test_symbol_normalization_paths.py
# =========================================================================


class FlatArticlePublishTimeTests(unittest.TestCase):
    """Flat yfinance articles now carry a pub_date parsed from
    providerPublishTime (#992). Bad timestamps must fail open (#992)."""

    def test_flat_article_publish_time_is_parsed(self):
        data = ynews._extract_article_data(
            {"title": "X", "publisher": "P", "link": "l",
             "providerPublishTime": _epoch("2025-05-09")}
        )
        self.assertIsNotNone(data["pub_date"])
        self.assertEqual(data["pub_date"].strftime("%Y-%m-%d"), "2025-05-09")

    def test_flat_article_invalid_timestamp_handled_gracefully(self):
        data = ynews._extract_article_data(
            {"title": "X", "publisher": "P", "link": "l",
             "providerPublishTime": "not-a-number"}
        )
        self.assertIsNone(data["pub_date"])


class NewsWindowRegressionTests(unittest.TestCase):
    """Regressions for #992/993/1007: lookahead guards on global news."""

    def test_window_excludes_future_and_undated_in_backtest(self):
        start = datetime(2025, 5, 1)
        end = datetime(2025, 5, 9)
        inside = datetime(2025, 5, 5)
        future = datetime(2025, 6, 1)
        self.assertTrue(ynews._in_news_window(inside, start, end))
        self.assertFalse(ynews._in_news_window(future, start, end))
        self.assertFalse(ynews._in_news_window(None, start, end))

    def test_window_keeps_undated_in_live_window(self):
        start = datetime.now()
        end = datetime.now()
        self.assertTrue(ynews._in_news_window(None, start, end))

    def test_global_news_future_flat_article_excluded(self):
        """#1007: a flat, future-dated global article must not appear."""
        future_article = {"title": "FUTURE EVENT", "publisher": "P", "link": "l",
                          "providerPublishTime": _epoch("2025-06-01")}
        past_article = {"title": "PAST EVENT", "publisher": "P", "link": "l",
                        "providerPublishTime": _epoch("2025-05-05")}

        class FakeSearch:
            def __init__(self, *a, **k):
                self.news = [future_article, past_article]

        with patch("tradingagents.dataflows.yfinance_news.yf.Search", FakeSearch):
            out = ynews.get_global_news_yfinance("2025-05-09", look_back_days=7, limit=10)
        self.assertIn("PAST EVENT", out)
        self.assertNotIn("FUTURE EVENT", out)

    def test_global_news_empty_after_filter_is_informative(self):
        """#993: everything filtered out -> a clear message, not a blank body."""
        only_future = {"title": "FUTURE", "publisher": "P", "link": "l",
                       "providerPublishTime": _epoch("2025-06-01")}

        class FakeSearch:
            def __init__(self, *a, **k):
                self.news = [only_future]

        with patch("tradingagents.dataflows.yfinance_news.yf.Search", FakeSearch):
            out = ynews.get_global_news_yfinance("2025-05-09", look_back_days=7, limit=10)
        self.assertIn("No global news found", out)
        self.assertNotIn("###", out)  # no empty article body


class YfSymbolNormalizationForNewsTests(unittest.TestCase):
    """Symbol normalization must propagate to the news path
    (#983/#984/#1007). Merged from test_symbol_normalization_paths.py.
    """

    def test_news_lookup_normalizes_symbol(self):
        seen = {}

        class FakeTicker:
            def __init__(self, symbol):
                seen["symbol"] = symbol

            def get_news(self, count):
                return []

        with patch("tradingagents.dataflows.yfinance_news.yf.Ticker", FakeTicker), \
             patch("tradingagents.dataflows.yfinance_news.yf_retry", lambda fn, **kwargs: fn()):
            out = ynews.get_news_yfinance("XAUUSD", "2025-01-01", "2025-01-10")

        self.assertEqual(seen["symbol"], "GC=F")
        self.assertIn("XAUUSD", out)
        self.assertIn("GC=F", out)


if __name__ == "__main__":
    unittest.main()

