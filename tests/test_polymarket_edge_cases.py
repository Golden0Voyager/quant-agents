"""Edge-case tests for uncovered code paths in polymarket.py.

Existing tests in test_polymarket.py cover filtering, formatting, routing,
and network errors. This file fills remaining gaps: _parse_json_list edge
cases (list input, invalid JSON, TypeError), _is_forward_looking edge cases
(bad endDate, missing outcomePrices/outcomes), markets without volumeNum,
_request HTTP error, and malformed probability/outcome data.
"""

from datetime import UTC, datetime
from unittest import mock

import pytest
import requests

from tradingagents.dataflows import polymarket


def _market(question, prob=None, *, volume=1_000, end_date="2030-12-31T00:00:00Z", **kw):
    m = {
        "question": question,
        "volumeNum": volume,
        "endDate": end_date,
        "closed": False,
    }
    if prob is not None:
        m["outcomes"] = '["Yes", "No"]'
        m["outcomePrices"] = f'["{prob}", "{round(1 - prob, 4)}"]'
    return {**m, **kw}


# ---------------------------------------------------------------------------
# _parse_json_list edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseJsonList:
    def test_list_passthrough(self):
        """Already a list → returned as-is."""
        assert polymarket._parse_json_list(["a", "b"]) == ["a", "b"]

    def test_valid_json_string(self):
        """Valid JSON string array → parsed."""
        assert polymarket._parse_json_list('["a", "b"]') == ["a", "b"]

    def test_invalid_json_string(self):
        """Invalid JSON string → empty list."""
        assert polymarket._parse_json_list("not-json") == []

    def test_none_value(self):
        """None → empty list."""
        assert polymarket._parse_json_list(None) == []

    def test_empty_string(self):
        """Empty string → empty list."""
        assert polymarket._parse_json_list("") == []


# ---------------------------------------------------------------------------
# _is_forward_looking edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsForwardLooking:
    def test_closed_market_returns_false(self):
        """closed=True → False."""
        now = datetime.now(UTC)
        m = _market("Test?", prob=0.5, closed=True)
        assert polymarket._is_forward_looking(m, now) is False

    def test_past_end_date_returns_false(self):
        """endDate in the past → False."""
        now = datetime(2026, 7, 1, tzinfo=UTC)
        m = _market("Past?", prob=0.5, end_date="2025-12-31T00:00:00Z")
        assert polymarket._is_forward_looking(m, now) is False

    def test_bad_end_date_format_does_not_crash(self):
        """Invalid endDate format → skip date check, continue."""
        now = datetime(2026, 7, 1, tzinfo=UTC)
        m = _market("Bad date?", prob=0.5, end_date="not-a-date")
        # Should not raise; falls through to outcomePrices/outcomes check
        assert polymarket._is_forward_looking(m, now) is True

    def test_missing_outcome_prices_returns_false(self):
        """No outcomePrices key → False."""
        now = datetime.now(UTC)
        m = _market("No prices?", end_date="2030-12-31T00:00:00Z")
        m.pop("outcomePrices", None)
        assert polymarket._is_forward_looking(m, now) is False

    def test_missing_outcomes_returns_false(self):
        """No outcomes key → False."""
        now = datetime.now(UTC)
        m = _market("No outcomes?", prob=0.5, end_date="2030-12-31T00:00:00Z")
        m.pop("outcomes", None)
        assert polymarket._is_forward_looking(m, now) is False


# ---------------------------------------------------------------------------
# _request HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestError:
    def test_http_error_raises(self):
        """Non-200 response → raise_for_status raises HTTPError."""
        resp = mock.MagicMock(spec=requests.Response)
        resp.status_code = 500
        resp.raise_for_status.side_effect = requests.HTTPError("500 error")
        with mock.patch("requests.get", return_value=resp), pytest.raises(requests.HTTPError):
            polymarket._request("events", {})


# ---------------------------------------------------------------------------
# get_prediction_markets: edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPredictionMarketsEdgeCases:
    def test_no_volume_num_in_market(self):
        """Market without volumeNum → sorted after markets with volume."""
        search = {
            "events": [
                {
                    "markets": [
                        _market("With volume", prob=0.5, volume=100),
                        {"question": "No volume?", "endDate": "2030-12-31T00:00:00Z",
                         "closed": False, "outcomes": '["Yes"]',
                         "outcomePrices": '["0.5"]'},
                    ]
                }
            ]
        }
        with mock.patch.object(polymarket, "_request", return_value=search):
            out = polymarket.get_prediction_markets("test", limit=10)
        assert "With volume" in out
        assert "No volume?" in out

    def test_bad_outcome_price_skipped(self):
        """Market with non-float outcome price → skipped silently."""
        search = {
            "events": [
                {
                    "markets": [
                        _market("Good", prob=0.5, volume=100),
                        _market("Bad price", end_date="2030-12-31T00:00:00Z",
                                outcomePrices='["NaN"]'),
                    ]
                }
            ]
        }
        with mock.patch.object(polymarket, "_request", return_value=search):
            out = polymarket.get_prediction_markets("test", limit=10)
        assert "Good" in out
        assert "Bad price" not in out

    def test_empty_outcomes_list(self):
        """Market with empty outcomes list → skipped."""
        search = {
            "events": [
                {
                    "markets": [
                        _market("Good", prob=0.5, volume=100),
                        _market("Empty outcomes", end_date="2030-12-31T00:00:00Z",
                                outcomes='[]', outcomePrices='["0.5"]'),
                    ]
                }
            ]
        }
        with mock.patch.object(polymarket, "_request", return_value=search):
            out = polymarket.get_prediction_markets("test", limit=10)
        assert "Good" in out
        assert "Empty outcomes" not in out

    def test_default_limit(self):
        """limit=None → uses DEFAULT_LIMIT (6)."""
        captured = {}

        def _capture(path, params):
            if path == "public-search":
                captured["limit_per_type"] = params.get("limit_per_type")
            return {"events": []}

        with mock.patch.object(polymarket, "_request", side_effect=_capture):
            polymarket.get_prediction_markets("test")
        assert captured["limit_per_type"] == 20

    def test_request_success_path(self):
        """
        _request success path: requests.get returns OK, JSON is parsed.
        Covers line 34 (return response.json()).
        """
        resp = mock.MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.json.return_value = {
            "events": [
                {
                    "markets": [
                        _market("Good", prob=0.5, volume=100),
                    ]
                }
            ]
        }
        with mock.patch("requests.get", return_value=resp):
            out = polymarket.get_prediction_markets("test", limit=10)
        assert "Good" in out

    def test_non_numeric_outcome_price_skipped(self):
        """
        Market with non-numeric outcome price → ValueError in float() → skipped.
        Covers lines 123-124 (except ValueError, IndexError: continue).

        Unlike test_bad_outcome_price_skipped (which lacks outcomes key, so
        _is_forward_looking filters it out), this test provides both
        outcomePrices and outcomes so the market reaches the formatting loop.
        """
        search = {
            "events": [
                {
                    "markets": [
                        _market("Good", prob=0.5, volume=100),
                        _market("Bad price", end_date="2030-12-31T00:00:00Z",
                                outcomes='["Yes", "No"]',
                                outcomePrices='["not_a_number", "0.4"]'),
                    ]
                }
            ]
        }
        with mock.patch.object(polymarket, "_request", return_value=search):
            out = polymarket.get_prediction_markets("test", limit=10)
        assert "Good" in out
        assert "Bad price" not in out
