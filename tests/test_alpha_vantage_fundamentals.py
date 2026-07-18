"""Tests for alpha_vantage_fundamentals module with mocked API calls."""

import json
from unittest.mock import patch

import pytest

from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError


def _fake_overview(**overrides) -> str:
    base = {
        "Symbol": "IBM",
        "Name": "International Business Machines",
        "Description": "A technology company.",
        "MarketCapitalization": "200000000000",
        "PERatio": "25.5",
    }
    base.update(overrides)
    return json.dumps(base)


def _fake_financial_reports(fiscal_date: str = "2025-12-31") -> str:
    return json.dumps({
        "annualReports": [{"fiscalDateEnding": fiscal_date, "totalAssets": "100000"}],
        "quarterlyReports": [{"fiscalDateEnding": fiscal_date, "totalAssets": "95000"}],
    })


@pytest.mark.unit
class TestGetFundamentals:
    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_fundamentals_correct_params(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_fundamentals
        mock_api.return_value = _fake_overview()
        get_fundamentals("IBM")
        mock_api.assert_called_once_with("OVERVIEW", {"symbol": "IBM"})

    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_fundamentals_returns_json(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_fundamentals
        expected = _fake_overview(Symbol="AAPL")
        mock_api.return_value = expected
        result = get_fundamentals("AAPL")
        assert result == expected

    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_fundamentals_rate_limit_propagates(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_fundamentals
        mock_api.side_effect = AlphaVantageRateLimitError("rate limit")
        with pytest.raises(AlphaVantageRateLimitError):
            get_fundamentals("IBM")


@pytest.mark.unit
class TestGetBalanceSheet:
    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_balance_sheet_correct_params(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_balance_sheet
        mock_api.return_value = _fake_financial_reports()
        get_balance_sheet("IBM")
        mock_api.assert_called_once_with("BALANCE_SHEET", {"symbol": "IBM"}, parse_json=True)

    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_balance_sheet_returns_raw_json(self, mock_api):
        """When the request layer yields a raw string (non-JSON body), the
        date filter is a no-op pass-through."""
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_balance_sheet
        reports_json = json.dumps({
            "annualReports": [
                {"fiscalDateEnding": "2025-12-31", "totalAssets": "100"},
                {"fiscalDateEnding": "2026-12-31", "totalAssets": "200"},
            ],
            "quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31", "totalAssets": "150"},
                {"fiscalDateEnding": "2026-06-30", "totalAssets": "180"},
            ],
        })
        mock_api.return_value = reports_json
        result = get_balance_sheet("IBM", curr_date="2026-06-15")
        assert result == reports_json

    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_balance_sheet_rate_limit_propagates(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_balance_sheet
        mock_api.side_effect = AlphaVantageRateLimitError("rate limit")
        with pytest.raises(AlphaVantageRateLimitError):
            get_balance_sheet("IBM")


@pytest.mark.unit
class TestGetCashFlow:
    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_cashflow_correct_params(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_cashflow
        mock_api.return_value = _fake_financial_reports()
        get_cashflow("IBM")
        mock_api.assert_called_once_with("CASH_FLOW", {"symbol": "IBM"}, parse_json=True)

    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_cashflow_returns_raw_json(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_cashflow
        reports_json = json.dumps({
            "annualReports": [{"fiscalDateEnding": "2025-12-31", "totalAssets": "100"}],
            "quarterlyReports": [{"fiscalDateEnding": "2025-09-30", "totalAssets": "90"}],
        })
        mock_api.return_value = reports_json
        result = get_cashflow("IBM", curr_date="2026-06-15")
        assert result == reports_json


@pytest.mark.unit
class TestGetIncomeStatement:
    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_income_statement_correct_params(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_income_statement
        mock_api.return_value = _fake_financial_reports()
        get_income_statement("IBM")
        mock_api.assert_called_once_with("INCOME_STATEMENT", {"symbol": "IBM"}, parse_json=True)

    @patch("tradingagents.dataflows.alpha_vantage_fundamentals._make_api_request")
    def test_get_income_statement_returns_raw_json(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_fundamentals import get_income_statement
        reports_json = json.dumps({
            "annualReports": [{"fiscalDateEnding": "2025-12-31", "totalAssets": "100"}],
            "quarterlyReports": [{"fiscalDateEnding": "2025-09-30", "totalAssets": "90"}],
        })
        mock_api.return_value = reports_json
        result = get_income_statement("IBM", curr_date="2026-06-15")
        assert result == reports_json


@pytest.mark.unit
class TestFilterReportsByDate:
    def test_noop_without_curr_date(self):
        from tradingagents.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date
        data = {"annualReports": [{"fiscalDateEnding": "2026-12-31"}]}
        result = _filter_reports_by_date(data, None)
        assert result == data

    def test_noop_with_non_dict(self):
        from tradingagents.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date
        result = _filter_reports_by_date("string", "2026-06-15")
        assert result == "string"

    def test_filters_both_report_types(self):
        from tradingagents.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date
        data = {
            "annualReports": [
                {"fiscalDateEnding": "2025-12-31"},
                {"fiscalDateEnding": "2026-12-31"},
            ],
            "quarterlyReports": [
                {"fiscalDateEnding": "2026-03-31"},
                {"fiscalDateEnding": "2026-09-30"},
            ],
        }
        result = _filter_reports_by_date(data, "2026-06-15")
        assert len(result["annualReports"]) == 1
        assert result["annualReports"][0]["fiscalDateEnding"] == "2025-12-31"
        assert len(result["quarterlyReports"]) == 1
        assert result["quarterlyReports"][0]["fiscalDateEnding"] == "2026-03-31"

    def test_missing_report_keys_preserved(self):
        from tradingagents.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date
        data = {"someOtherKey": "value"}
        result = _filter_reports_by_date(data, "2026-06-15")
        assert result == data
