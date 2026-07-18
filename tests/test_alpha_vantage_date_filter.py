"""Regression tests for the Alpha Vantage fundamentals look-ahead filter.

Previously ``_make_api_request`` always returned the raw response text, so the
``isinstance(result, dict)`` guard in ``_filter_reports_by_date`` was never
true and BALANCE_SHEET / CASH_FLOW / INCOME_STATEMENT returned fiscal periods
ending after ``curr_date`` (look-ahead bias in backtests).

These tests mock the HTTP layer (``requests.get`` inside
``alpha_vantage_common``) and exercise the real request + filter path.
"""

import json
from unittest import mock

import pytest

from tradingagents.dataflows import alpha_vantage_common
from tradingagents.dataflows.alpha_vantage_fundamentals import (
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
)
from tradingagents.dataflows.errors import NoMarketDataError

pytestmark = pytest.mark.unit


def _statement_payload():
    """Two annual + two quarterly reports straddling 2026-06-15."""
    return {
        "symbol": "AAPL",
        "annualReports": [
            {"fiscalDateEnding": "2025-12-31", "totalAssets": "100"},
            {"fiscalDateEnding": "2026-12-31", "totalAssets": "200"},
        ],
        "quarterlyReports": [
            {"fiscalDateEnding": "2026-03-31", "totalAssets": "150"},
            {"fiscalDateEnding": "2026-09-30", "totalAssets": "180"},
        ],
    }


def _mock_response(text: str):
    resp = mock.MagicMock()
    resp.text = text
    resp.raise_for_status = lambda: None
    return resp


def _patched_http(body):
    """Patch the HTTP layer so _make_api_request returns ``body`` as the raw text."""
    return (
        mock.patch.object(alpha_vantage_common, "get_api_key", return_value="test_key"),
        mock.patch.object(
            alpha_vantage_common, "requests",
            **{"get.return_value": _mock_response(body)},
        ),
    )


class TestStatementDateFilter:
    """(a) Reports with fiscalDateEnding <= curr_date are kept, later ones dropped."""

    def test_balance_sheet_filters_future_annual_and_quarterly(self):
        key_patch, http_patch = _patched_http(json.dumps(_statement_payload()))
        with key_patch, http_patch:
            result = get_balance_sheet("AAPL", curr_date="2026-06-15")
        assert isinstance(result, dict)
        assert [r["fiscalDateEnding"] for r in result["annualReports"]] == ["2025-12-31"]
        assert [r["fiscalDateEnding"] for r in result["quarterlyReports"]] == ["2026-03-31"]

    def test_cashflow_filters_future_annual_and_quarterly(self):
        key_patch, http_patch = _patched_http(json.dumps(_statement_payload()))
        with key_patch, http_patch:
            result = get_cashflow("AAPL", curr_date="2026-06-15")
        assert [r["fiscalDateEnding"] for r in result["annualReports"]] == ["2025-12-31"]
        assert [r["fiscalDateEnding"] for r in result["quarterlyReports"]] == ["2026-03-31"]

    def test_income_statement_filters_future_annual_and_quarterly(self):
        key_patch, http_patch = _patched_http(json.dumps(_statement_payload()))
        with key_patch, http_patch:
            result = get_income_statement("AAPL", curr_date="2026-06-15")
        assert [r["fiscalDateEnding"] for r in result["annualReports"]] == ["2025-12-31"]
        assert [r["fiscalDateEnding"] for r in result["quarterlyReports"]] == ["2026-03-31"]


class TestBoundarySemantics:
    """(b) fiscalDateEnding == curr_date is kept ("exclude entries *after* curr_date")."""

    def test_fiscal_date_equal_to_curr_date_is_kept(self):
        payload = _statement_payload()
        payload["quarterlyReports"].append(
            {"fiscalDateEnding": "2026-06-15", "totalAssets": "160"}
        )
        key_patch, http_patch = _patched_http(json.dumps(payload))
        with key_patch, http_patch:
            result = get_balance_sheet("AAPL", curr_date="2026-06-15")
        assert [r["fiscalDateEnding"] for r in result["quarterlyReports"]] == [
            "2026-03-31",
            "2026-06-15",
        ]

    def test_no_curr_date_returns_all_reports(self):
        key_patch, http_patch = _patched_http(json.dumps(_statement_payload()))
        with key_patch, http_patch:
            result = get_balance_sheet("AAPL", curr_date=None)
        assert len(result["annualReports"]) == 2
        assert len(result["quarterlyReports"]) == 2


class TestErrorConventions:
    """(c) Non-JSON bodies and error payloads follow the existing conventions."""

    def test_non_json_body_returned_as_raw_text_unfiltered(self):
        """CSV-shaped (non-JSON) bodies are data: returned as text, filter no-ops."""
        csv_body = "fiscalDateEnding,totalAssets\n2026-12-31,200"
        key_patch, http_patch = _patched_http(csv_body)
        with key_patch, http_patch:
            result = get_balance_sheet("AAPL", curr_date="2026-06-15")
        assert result == csv_body

    def test_error_message_payload_raises_no_market_data(self):
        """Invalid-symbol "Error Message" JSON must not be passed through as data."""
        body = json.dumps({
            "Error Message": "the symbol INVALID was not found. Please check the symbol."
        })
        key_patch, http_patch = _patched_http(body)
        with key_patch, http_patch, pytest.raises(NoMarketDataError):
            get_balance_sheet("INVALID", curr_date="2026-06-15")

    def test_rate_limit_notice_still_raises_through_parse_json_path(self):
        body = json.dumps({
            "Information": "Thank you for using Alpha Vantage! Our standard API "
                           "rate limit is 25 requests per day."
        })
        key_patch, http_patch = _patched_http(body)
        with key_patch, http_patch, pytest.raises(alpha_vantage_common.AlphaVantageRateLimitError):
            get_income_statement("AAPL", curr_date="2026-06-15")


class TestDefaultStrBehaviorPreserved:
    """Callers that rely on the raw-text return (news, stock CSV) are unaffected."""

    def test_json_body_still_returned_as_text_without_parse_json(self):
        body = json.dumps({"feed": [{"title": "hello"}]})
        key_patch, http_patch = _patched_http(body)
        with key_patch, http_patch:
            result = alpha_vantage_common._make_api_request(
                "NEWS_SENTIMENT", {"tickers": "AAPL"}
            )
        assert isinstance(result, str)
        assert result == body

    def test_csv_body_returned_as_text_without_parse_json(self):
        csv_body = "timestamp,open,close\n2026-06-15,100,101"
        key_patch, http_patch = _patched_http(csv_body)
        with key_patch, http_patch:
            result = alpha_vantage_common._make_api_request(
                "TIME_SERIES_DAILY_ADJUSTED", {"symbol": "AAPL", "datatype": "csv"}
            )
        assert result == csv_body
