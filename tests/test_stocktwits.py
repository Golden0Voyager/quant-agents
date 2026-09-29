"""Unit tests for tradingagents/dataflows/stocktwits.py."""
from __future__ import annotations

import http.client
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages

pytestmark = pytest.mark.unit

_ST_SAMPLE = {
    "messages": [
        {
            "created_at": "2026-06-15T10:00:00Z",
            "user": {"username": "trader1"},
            "entities": {"sentiment": {"basic": "Bullish"}},
            "body": "This stock is going to the moon!",
        },
        {
            "created_at": "2026-06-15T11:00:00Z",
            "user": {"username": "bear2"},
            "entities": {"sentiment": {"basic": "Bearish"}},
            "body": "Selling all shares.",
        },
    ]
}

_LONG_BODY = (
    "This is an extremely long message body that is well over two hundred "
    "and eighty characters in length and therefore needs to be truncated "
    "by the fetch_stocktwits_messages function so that each message fits "
    "within a reasonable display size and does not blow up the context "
    "window for the LLM consuming this data. " * 3
)


class StocktwitsFetchTests(unittest.TestCase):
    """Lines 47-49 (error handling), 53 (empty messages), 66-68 (date parse fail-open)."""

    def _mock_urlopen(self, data: bytes = None, side_effect: Exception = None):
        if side_effect:
            return patch("tradingagents.dataflows.stocktwits.urlopen", side_effect=side_effect)
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = data or b'{"messages": []}'
        return patch("tradingagents.dataflows.stocktwits.urlopen", return_value=mock_resp)

    def test_url_error_returns_unavailable(self):
        from urllib.error import URLError

        with self._mock_urlopen(side_effect=URLError("host unreachable")):
            result = fetch_stocktwits_messages("AAPL")
        self.assertIn("stocktwits unavailable", result)
        self.assertIn("URLError", result)

    def test_json_decode_error_returns_unavailable(self):
        with self._mock_urlopen(data=b"not json"):
            result = fetch_stocktwits_messages("AAPL")
        self.assertIn("stocktwits unavailable", result)
        self.assertIn("JSONDecodeError", result)

    def _urlopen_response_with_read_raising(self, exc):
        """A urlopen return value whose ``.read()`` raises ``exc``.

        ``http.client.IncompleteRead`` is raised inside ``.read()`` —
        *after* ``urlopen()`` returns successfully — so it cannot be
        modelled by ``urlopen=side_effect=exc`` (#1024).
        """

        class _Resp:
            def __enter__(inner):
                return inner

            def __exit__(inner, *exc_info):
                return False

            def read(inner):
                raise exc

        return _Resp()

    def test_incomplete_read_returns_placeholder(self):
        """#1024: ``http.client.IncompleteRead`` fires inside ``.read()``
        after a successful ``urlopen()`` — must still surface as the
        ``<stocktwits unavailable …>`` placeholder, not crash.
        """
        with patch(
            "tradingagents.dataflows.stocktwits.urlopen",
            return_value=self._urlopen_response_with_read_raising(
                http.client.IncompleteRead(b"")
            ),
        ):
            result = fetch_stocktwits_messages("NVDA")
        self.assertIn("unavailable", result.lower())
        self.assertTrue(result.startswith("<stocktwits unavailable"))

    def test_empty_messages_returns_no_messages(self):
        with self._mock_urlopen(data=b'{"messages": []}'):
            result = fetch_stocktwits_messages("AAPL")
        self.assertIn("no StockTwits messages found", result)
        self.assertIn("AAPL", result)

    def test_date_parse_fail_open(self):
        messages = [
            {"created_at": "invalid-date!!", "body": "Great stock!", "user": {"username": "trader1"}},
            {"created_at": "2026-06-22T10:30:00Z", "body": "To the moon!", "user": {"username": "trader2"}},
        ]
        payload = json.dumps({"messages": messages}).encode("utf-8")

        # days_back=0 disables date filtering so the test is not time-dependent.
        # Date-filtering behavior is covered by test_days_back_zero_includes_all
        # and test_success_path_returns_formatted_messages.
        with self._mock_urlopen(data=payload):
            result = fetch_stocktwits_messages("AAPL", days_back=0)
        self.assertIn("Great stock!", result)
        self.assertIn("To the moon!", result)

    def test_success_path_returns_formatted_messages(self):
        # Use dynamically-generated dates so the test remains deterministic
        # regardless of the current date (messages must be within days_back).
        now = datetime.now(UTC)
        d1 = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d2 = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        messages = [
            {"created_at": d1, "body": "Bullish on AAPL", "user": {"username": "bull1"},
             "entities": {"sentiment": {"basic": "Bullish"}}},
            {"created_at": d2, "body": "Too expensive", "user": {"username": "bear1"},
             "entities": {"sentiment": {"basic": "Bearish"}}},
        ]
        payload = json.dumps({"messages": messages}).encode("utf-8")

        with self._mock_urlopen(data=payload):
            result = fetch_stocktwits_messages("AAPL", days_back=30)
        self.assertIn("Bullish", result)
        self.assertIn("Bearish", result)
        self.assertIn("AAPL", result)


class TestStocktwitsEdgeCases(unittest.TestCase):
    """Body truncation, sentiment edge, days_back."""

    def test_body_truncation(self):
        data = dict(_ST_SAMPLE)
        data["messages"][0]["body"] = _LONG_BODY

        with patch("tradingagents.dataflows.stocktwits.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.read.return_value = json.dumps(data).encode("utf-8")
            mock_urlopen.return_value = mock_resp

            result = fetch_stocktwits_messages("AAPL", limit=5, days_back=0)
        self.assertIn("\u2026", result)

    def test_no_sentiment_key(self):
        data = {
            "messages": [
                {
                    "created_at": "2026-06-15T10:00:00Z",
                    "user": {"username": "trader1"},
                    "entities": {},
                    "body": "No sentiment here.",
                },
            ]
        }

        with patch("tradingagents.dataflows.stocktwits.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.read.return_value = json.dumps(data).encode("utf-8")
            mock_urlopen.return_value = mock_resp

            result = fetch_stocktwits_messages("AAPL", limit=5, days_back=0)
        self.assertIn("no-label", result)

    def test_no_entities_key(self):
        data = dict(_ST_SAMPLE)
        data["messages"][0].pop("entities", None)

        with patch("tradingagents.dataflows.stocktwits.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.read.return_value = json.dumps(data).encode("utf-8")
            mock_urlopen.return_value = mock_resp

            result = fetch_stocktwits_messages("AAPL", limit=5, days_back=0)
        self.assertIn("no-label", result)

    def test_days_back_zero_includes_all(self):
        with patch("tradingagents.dataflows.stocktwits.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.read.return_value = json.dumps(_ST_SAMPLE).encode("utf-8")
            mock_urlopen.return_value = mock_resp

            result = fetch_stocktwits_messages("AAPL", limit=5, days_back=0)
        self.assertIn("Bullish", result)

    def test_missing_user_object(self):
        data = dict(_ST_SAMPLE)
        data["messages"][0]["user"] = None

        with patch("tradingagents.dataflows.stocktwits.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.read.return_value = json.dumps(data).encode("utf-8")
            mock_urlopen.return_value = mock_resp

            result = fetch_stocktwits_messages("AAPL", limit=5)
        self.assertIn("Bullish", result)


# -----------------------------------------------------------------------------
# Module-level pytest cases for transport-level errors raised by ``urlopen()``
# itself. Kept outside ``unittest.TestCase`` so ``@pytest.mark.parametrize``
# can supply the exception per test-id — unittest's TestCase descriptor
# binding rejects extra positional args, raising TypeError on pytest invoke.
# The ``fetch_stocktwits_messages`` placement (side_effect=urlopen) is
# distinct from ``test_incomplete_read_returns_placeholder`` above, which
# raises inside ``.read()`` (#1024).
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_class_name"),
    [
        pytest.param(
            HTTPError("url", 404, "Not Found", {}, None),
            "HTTPError",
            id="http-error-404",
        ),
        pytest.param(
            TimeoutError("timed out"),
            "TimeoutError",
            id="timeout-error",
        ),
    ],
)
def test_urlopen_transport_errors_return_unavailable(exc, expected_class_name):
    """urlopen raises HTTPError / TimeoutError →
    ``<stocktwits unavailable …>`` with the exception class name.
    """
    with patch(
        "tradingagents.dataflows.stocktwits.urlopen", side_effect=exc
    ):
        result = fetch_stocktwits_messages("AAPL")
    assert "stocktwits unavailable" in result
    assert expected_class_name in result


# -----------------------------------------------------------------------------
# Additional edge-case tests for previously uncovered branches.
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_class_name"),
    [
        pytest.param(
            OSError("connection reset by peer"),
            "OSError",
            id="plain-os-error",
        ),
        pytest.param(
            http.client.BadStatusLine("''"),
            "BadStatusLine",
            id="bad-status-line",
        ),
    ],
)
def test_additional_transport_errors(exc, expected_class_name):
    """Plain ``OSError`` and ``http.client.BadStatusLine`` (both caught by
    the ``except`` clause at line 49) must also produce the placeholder.
    """
    with patch(
        "tradingagents.dataflows.stocktwits.urlopen", side_effect=exc
    ):
        result = fetch_stocktwits_messages("AAPL")
    assert "stocktwits unavailable" in result
    assert expected_class_name in result


def test_non_dict_json_response():
    """If the API returns a JSON array (not an object), the ``isinstance(data, dict)``
    guard should yield an empty messages list and return the no-messages placeholder."""
    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = b'["unexpected", "array"]'
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", days_back=0)
    assert "no StockTwits messages found" in result


def test_sentiment_object_is_not_a_dict():
    """If the ``sentiment`` field is not a dict (e.g. a string), the code must
    treat it as no-label rather than crashing."""
    data = {
        "messages": [
            {
                "created_at": "2026-06-15T10:00:00Z",
                "user": {"username": "trader1"},
                "entities": {"sentiment": "Bullish"},  # string, not dict
                "body": "Sentiment is a string!",
            },
        ]
    }
    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps(data).encode("utf-8")
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", limit=5, days_back=0)
    # ``sentiment_obj.get("basic")`` will raise AttributeError on a string,
    # but the code guards with ``isinstance(sentiment_obj, dict)`` → falls
    # to the ``else`` branch, so the tag should be ``no-label``.
    assert "no-label" in result
    assert "sentiment is a string" in result.lower()


def test_days_back_none_includes_all():
    """``days_back=None`` must skip date filtering (the condition checks
    ``days_back is not None`` first), returning all messages."""
    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps(_ST_SAMPLE).encode("utf-8")
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", limit=5, days_back=None)
    assert "Bullish" in result
    assert "Bearish" in result


def test_limit_respects_max_messages():
    """Only ``limit`` messages should appear in the output even when more are
    available."""
    now = datetime.now(UTC)
    messages = [
        {
            "created_at": (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": f"Message {i}",
            "user": {"username": f"user{i}"},
        }
        for i in range(10)
    ]
    payload = json.dumps({"messages": messages}).encode("utf-8")

    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", limit=3, days_back=30)
    assert "Message 0" in result
    assert "Message 1" in result
    assert "Message 2" in result
    assert "Message 3" not in result


def test_empty_body_does_not_crash():
    """A message with an empty or None body must not crash the formatter."""
    now = datetime.now(UTC)
    messages = [
        {
            "created_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "",
            "user": {"username": "empty1"},
            "entities": {"sentiment": {"basic": "Bullish"}},
        },
        {
            "created_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": None,
            "user": {"username": "none1"},
        },
    ]
    payload = json.dumps({"messages": messages}).encode("utf-8")

    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", limit=5, days_back=30)
    assert "Bullish" in result
    assert "no-label" in result


def test_lowercase_ticker_uppercased_in_output():
    """A lowercase ticker should be uppercased for the no-messages placeholder."""
    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = b'{"messages": []}'
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("aapl", days_back=0)
    assert "$AAPL" in result or "AAPL" in result


def test_old_messages_filtered_by_days_back():
    """Messages older than ``days_back`` should be excluded."""
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    messages = [
        {
            "created_at": old,
            "body": "This is old",
            "user": {"username": "oldie"},
            "entities": {"sentiment": {"basic": "Bearish"}},
        },
        {
            "created_at": recent,
            "body": "This is recent",
            "user": {"username": "newbie"},
            "entities": {"sentiment": {"basic": "Bullish"}},
        },
    ]
    payload = json.dumps({"messages": messages}).encode("utf-8")

    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        mock.return_value = mock_resp

        # days_back=7 means only messages within the last 7 days survive
        result = fetch_stocktwits_messages("AAPL", limit=5, days_back=7)
    assert "Bullish" in result  # recent message should be present
    assert "This is old" not in result  # old message should be filtered out


def test_html_entities_decoded_in_message_bodies():
    """Upstream 4187716: StockTwits serves bodies HTML-escaped (``&amp;``,
    ``&#39;``). The prompt must see plain text, not entities."""
    now = datetime.now(UTC)
    messages = [
        {
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "S&amp;P wasn&#39;t up but this stock was",
            "user": {"username": "trader1"},
        },
    ]
    payload = json.dumps({"messages": messages}).encode("utf-8")

    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", limit=5, days_back=7)
    assert "S&P wasn't up" in result
    assert "&amp;" not in result
    assert "&#39;" not in result


def test_screen_drops_off_topic_messages():
    """A screen that flags the second body off-topic removes it from the
    block, recomputes the counts, and prepends the note."""
    now = datetime.now(UTC)
    messages = [
        {
            "created_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "Real news about the company",
            "user": {"username": "real1"},
            "entities": {"sentiment": {"basic": "Bullish"}},
        },
        {
            "created_at": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "Spam promoting another ticker",
            "user": {"username": "spammer"},
            "entities": {"sentiment": {"basic": "Bullish"}},
        },
    ]
    payload = json.dumps({"messages": messages}).encode("utf-8")

    def screen(bodies):
        assert len(bodies) == 2
        return [True, False], "Screened by Jev: 1 of the 2 posts kept."

    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages("AAPL", limit=5, days_back=7, screen=screen)
    assert "Screened by Jev" in result
    assert "Real news about the company" in result
    assert "Spam promoting" not in result
    # Counts reflect only the kept message.
    assert "Bullish: 1 (100%)" in result
    assert "Total: 1 most-recent" in result


def test_screen_dropping_everything_returns_placeholder():
    now = datetime.now(UTC)
    messages = [
        {
            "created_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "irrelevant",
            "user": {"username": "u1"},
        },
    ]
    payload = json.dumps({"messages": messages}).encode("utf-8")

    with patch("tradingagents.dataflows.stocktwits.urlopen") as mock:
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        mock.return_value = mock_resp

        result = fetch_stocktwits_messages(
            "AAPL", limit=5, days_back=7,
            screen=lambda bodies: ([False], "note"),
        )
    assert "after screening" in result
    assert "1 fetched" in result


if __name__ == "__main__":
    unittest.main()
