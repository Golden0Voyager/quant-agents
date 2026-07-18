"""Targeted edge-case tests for uncovered code paths in fred.py.

Existing tests in test_fred.py cover ~82%. This file fills the remaining gaps:
_request 400/HTTP error handling, get_macro_data non-numeric delta,
observations with None/"" values, empty seasonal_adjustment_short,
missing units_short fallback, missing title fallback, and get_api_key
returning the key when set.
"""

from unittest import mock

import pytest
import requests

from tradingagents.dataflows import fred

_META = {
    "seriess": [
        {
            "title": "Unemployment Rate",
            "units_short": "%",
            "frequency": "Monthly",
            "seasonal_adjustment_short": "SA",
        }
    ]
}
_OBS = {
    "observations": [
        {"date": "2025-06-01", "value": "4.1"},
        {"date": "2025-07-01", "value": "4.3"},
    ]
}


# ---------------------------------------------------------------------------
# _request: HTTP error handling
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredRequestErrorHandling:

    FRED_API_KEY = "test_key_12345"

    def _mock_response(self, status_code, json_data=None, text=""):
        """Build a requests.Response-like object."""
        resp = mock.MagicMock(spec=requests.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.text = text
        return resp

    @mock.patch.dict("os.environ", {"FRED_API_KEY": FRED_API_KEY})
    def test_request_400_with_json_error(self):
        """FRED 400 + JSON error body → raises ValueError with error_message."""
        resp = self._mock_response(400, json_data={"error_message": "Bad series ID"})
        with mock.patch("requests.get", return_value=resp), pytest.raises(ValueError, match="FRED request failed: Bad series ID"):
            fred._request("series", {"series_id": "INVALID"})

    @mock.patch.dict("os.environ", {"FRED_API_KEY": FRED_API_KEY})
    def test_request_400_with_non_json_body(self):
        """FRED 400 + non-JSON body → raises ValueError with raw text."""
        resp = self._mock_response(400, json_data=None, text="Gateway timeout")
        # Force json() to raise ValueError so the fallback to text is exercised
        resp.json.side_effect = ValueError("not json")
        with mock.patch("requests.get", return_value=resp), pytest.raises(ValueError, match="FRED request failed: Gateway timeout"):
            fred._request("series", {"series_id": "INVALID"})

    @mock.patch.dict("os.environ", {"FRED_API_KEY": FRED_API_KEY})
    def test_request_500_raises_http_error(self):
        """FRED 500 → raise_for_status raises HTTPError."""
        resp = self._mock_response(500)
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        with mock.patch("requests.get", return_value=resp), pytest.raises(requests.HTTPError):
            fred._request("series", {"series_id": "TEST"})

    @mock.patch.dict("os.environ", {"FRED_API_KEY": FRED_API_KEY})
    def test_request_success_returns_json(self):
        """200 response → returns parsed JSON dict."""
        resp = self._mock_response(200, json_data={"seriess": [{"id": "TEST"}]})
        with mock.patch("requests.get", return_value=resp):
            result = fred._request("series", {"series_id": "TEST"})
        assert result == {"seriess": [{"id": "TEST"}]}


# ---------------------------------------------------------------------------
# get_macro_data: non-numeric values in delta calculation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredNonNumericDelta:

    def test_non_numeric_value_falls_back_to_no_change_line(self):
        """When first or last value can't be float-parsed → summary omits change."""
        obs = {
            "observations": [
                {"date": "2025-06-01", "value": "N/A"},
                {"date": "2025-07-01", "value": "4.3"},
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(obs=obs)):
            out = fred.get_macro_data("unemployment", "2025-07-15", 365)
        assert "**Latest:** 4.3 (2025-07-01)" in out
        # No change-over-window line when first value is non-numeric
        assert "Change over window" not in out


# ---------------------------------------------------------------------------
# get_macro_data: observations with None or "" values
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredObservationFiltering:

    def test_none_value_is_skipped(self):
        """Observations with value=None are filtered out."""
        obs = {
            "observations": [
                {"date": "2025-06-01", "value": "4.1"},
                {"date": "2025-07-01", "value": None},
                {"date": "2025-08-01", "value": "4.4"},
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(obs=obs)):
            out = fred.get_macro_data("unemployment", "2025-08-15", 365)
        assert "2025-06-01" in out
        assert "2025-07-01" not in out  # filtered out
        assert "2025-08-01" in out

    def test_empty_string_value_is_skipped(self):
        """Observations with value="" are filtered out."""
        obs = {
            "observations": [
                {"date": "2025-06-01", "value": "4.1"},
                {"date": "2025-07-01", "value": ""},
                {"date": "2025-08-01", "value": "4.4"},
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(obs=obs)):
            out = fred.get_macro_data("unemployment", "2025-08-15", 365)
        assert "2025-06-01" in out
        assert "2025-07-01" not in out  # filtered out
        assert "2025-08-01" in out

    def test_dot_value_is_skipped(self):
        """Observations with value='.' are filtered out (regression guard)."""
        obs = {
            "observations": [
                {"date": "2025-06-01", "value": "4.1"},
                {"date": "2025-07-01", "value": "."},
                {"date": "2025-08-01", "value": "4.4"},
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(obs=obs)):
            out = fred.get_macro_data("unemployment", "2025-08-15", 365)
        assert "2025-06-01" in out
        assert "2025-07-01" not in out
        assert "2025-08-01" in out


# ---------------------------------------------------------------------------
# get_macro_data: metadata edge cases
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredMetadataEdgeCases:

    def test_no_seasonal_adjustment_omits_parentheses(self):
        """When seasonal_adjustment_short is empty → no '(SA)' suffix in header."""
        meta = {
            "seriess": [
                {
                    "title": "CPI",
                    "units_short": "Index",
                    "frequency": "Monthly",
                    "seasonal_adjustment_short": "",
                }
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(meta=meta)):
            out = fred.get_macro_data("cpi", "2025-07-15", 365)
        assert "Monthly" in out
        assert "(SA)" not in out

    def test_missing_units_short_falls_back(self):
        """When units_short is missing → falls back to 'units' field."""
        meta = {
            "seriess": [
                {
                    "title": "CPI",
                    "units": "Index 1982-1984=100",
                    "frequency": "Monthly",
                    "seasonal_adjustment_short": "SA",
                }
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(meta=meta)):
            out = fred.get_macro_data("cpi", "2025-07-15", 365)
        assert "Index 1982-1984=100" in out

    def test_missing_units_short_and_units(self):
        """When both units_short and units are missing → empty units line."""
        meta = {
            "seriess": [
                {
                    "title": "CPI",
                    "frequency": "Monthly",
                    "seasonal_adjustment_short": "SA",
                }
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(meta=meta)):
            out = fred.get_macro_data("cpi", "2025-07-15", 365)
        assert "Units: " in out

    def test_missing_title_falls_back_to_series_id(self):
        """When title is missing → uses series_id as title."""
        meta = {
            "seriess": [
                {
                    "units_short": "%",
                    "frequency": "Daily",
                    "seasonal_adjustment_short": "NSA",
                }
            ]
        }
        with mock.patch.object(fred, "_request", side_effect=_make_stub(meta=meta)):
            out = fred.get_macro_data("DGS10", "2025-07-15", 365)
        assert "FRED: DGS10" in out

    def test_no_series_info_returns_empty_meta(self):
        """When seriess list is empty → returns not-found message."""
        meta = {"seriess": []}
        with mock.patch.object(fred, "_request", side_effect=_make_stub(meta=meta)):
            out = fred.get_macro_data("DGS10", "2025-07-15", 365)
        assert "not found" in out
        assert "DGS10" in out


# ---------------------------------------------------------------------------
# get_macro_data: look_back_days=None uses default
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredLookbackDefault:

    def test_none_lookback_uses_default(self):
        """When look_back_days is None → uses DEFAULT_LOOKBACK_DAYS (730)."""
        captured = {}

        def _capture(path, params):
            if path == "series":
                return _META
            if path == "series/observations":
                captured["observation_start"] = params.get("observation_start")
                return _OBS
            raise AssertionError(f"unexpected path: {path}")

        with mock.patch.object(fred, "_request", side_effect=_capture):
            fred.get_macro_data("unemployment", "2025-07-15")
        # 730 days before 2025-07-15 = 2023-07-16
        assert captured.get("observation_start") == "2023-07-16"

    def test_explicit_lookback_passed_through(self):
        """When look_back_days is explicitly 90 → uses 90-day window."""
        captured = {}

        def _capture(path, params):
            if path == "series":
                return _META
            if path == "series/observations":
                captured["observation_start"] = params.get("observation_start")
                return _OBS
            raise AssertionError(f"unexpected path: {path}")

        with mock.patch.object(fred, "_request", side_effect=_capture):
            fred.get_macro_data("unemployment", "2025-07-15", 90)
        assert captured.get("observation_start") == "2025-04-16"


# ---------------------------------------------------------------------------
# get_api_key: returns the key when set
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredGetApiKey:

    def test_get_api_key_returns_key_when_set(self):
        """When FRED_API_KEY is set → get_api_key returns it."""
        with mock.patch.dict("os.environ", {"FRED_API_KEY": "my_secret_key"}):
            assert fred.get_api_key() == "my_secret_key"

    def test_get_api_key_raises_when_unset(self):
        """When FRED_API_KEY is unset → raises FredNotConfiguredError."""
        with mock.patch.dict("os.environ", {}, clear=True), pytest.raises(fred.FredNotConfiguredError):
            fred.get_api_key()

    def test_get_api_key_raises_when_empty(self):
        """When FRED_API_KEY is empty string → raises FredNotConfiguredError."""
        with mock.patch.dict("os.environ", {"FRED_API_KEY": ""}), pytest.raises(fred.FredNotConfiguredError):
            fred.get_api_key()


# ---------------------------------------------------------------------------
# _resolve_series_id: empty input
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFredResolveSeriesIdEdgeCases:

    def test_empty_string_raises_value_error(self):
        """Empty indicator string → raises ValueError."""
        with pytest.raises(ValueError, match="not a known macro alias"):
            fred._resolve_series_id("")


# ---------------------------------------------------------------------------
# Helper: build _request stub
# ---------------------------------------------------------------------------

def _make_stub(meta=None, obs=None):
    """Build a _request replacement dispatching on endpoint path."""
    if meta is None:
        meta = _META
    if obs is None:
        obs = _OBS

    def _impl(path, params):
        if path == "series":
            return meta
        if path == "series/observations":
            return obs
        raise AssertionError(f"unexpected FRED path: {path}")

    return _impl
