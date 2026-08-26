"""Tests for the HiThink (同花顺) Financial-API shared client.

Covers the ApiResponse envelope handling, retry/backoff semantics, error
mapping onto the vendor error taxonomy, symbol conversion, and the
tickers/search helper — all with mocked HTTP.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions

pytestmark = pytest.mark.unit

from tradingagents.dataflows import hithink_common
from tradingagents.dataflows.errors import (
    VendorNotConfiguredError,
    VendorRateLimitError,
)


def _ok_payload(data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "code": 0,
        "message": "success",
        "request_id": "req-1",
        "data": {"timestamp": 1716105600000, **(data or {})},
    }
    return resp


def _err_payload(code: int, message: str = "boom") -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "code": code,
        "message": message,
        "request_id": "req-err",
        "data": None,
    }
    return resp


class TestApiKey:
    def test_unset_by_default(self, monkeypatch):
        monkeypatch.delenv(hithink_common.API_KEY_ENV, raising=False)
        assert hithink_common.get_api_key() is None
        assert hithink_common.is_available() is False

    def test_set(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k-123")
        assert hithink_common.get_api_key() == "k-123"
        assert hithink_common.is_available() is True

    def test_empty_string_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "")
        assert hithink_common.is_available() is False


class TestHithinkGet:
    def test_missing_key_raises_not_configured(self, monkeypatch):
        monkeypatch.delenv(hithink_common.API_KEY_ENV, raising=False)
        with pytest.raises(VendorNotConfiguredError):
            hithink_common.hithink_get("/api/meta/tickers/search", {"q": "平安"})

    def test_success_returns_data(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with patch("requests.get", return_value=_ok_payload({"item": [1, 2]})) as mock_get:
            data = hithink_common.hithink_get("/api/meta/tickers/search", {"q": "平安"})
        assert data["item"] == [1, 2]
        assert mock_get.call_args.kwargs["headers"]["X-api-key"] == "k"

    def test_non_retryable_code_raises_api_error(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with (
            patch("requests.get", return_value=_err_payload(3001, "标的不存在")),
            pytest.raises(hithink_common.HithinkApiError) as exc_info,
        ):
            hithink_common.hithink_get("/api/x")
        assert exc_info.value.code == 3001
        assert "req-err" in str(exc_info.value)

    def test_rate_limit_retried_then_success(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with (
            patch("requests.get", side_effect=[_err_payload(4001), _ok_payload()]) as mock_get,
            patch("tradingagents.dataflows.hithink_common.time.sleep") as mock_sleep,
        ):
            data = hithink_common.hithink_get("/api/x")
        assert data["timestamp"] == 1716105600000
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once()

    def test_rate_limit_exhausted_raises_vendor_rate_limit(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with (
            patch("requests.get", return_value=_err_payload(4001)),
            patch("tradingagents.dataflows.hithink_common.time.sleep"),
            pytest.raises(VendorRateLimitError),
        ):
            hithink_common.hithink_get("/api/x", max_retries=2)

    def test_server_error_code_retried(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with (
            patch("requests.get", side_effect=[_err_payload(5002), _ok_payload()]),
            patch("tradingagents.dataflows.hithink_common.time.sleep"),
        ):
            data = hithink_common.hithink_get("/api/x")
        assert data["timestamp"] == 1716105600000

    def test_network_error_retried_then_raised(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with (
            patch(
                "requests.get",
                side_effect=requests.exceptions.ConnectionError("refused"),
            ),
            patch("tradingagents.dataflows.hithink_common.time.sleep"),
            pytest.raises(requests.exceptions.ConnectionError),
        ):
            hithink_common.hithink_get("/api/x", max_retries=1)


class TestSymbolConversion:
    def test_thscode_to_ticker(self):
        assert hithink_common.thscode_to_ticker("600519.SH") == "600519.SS"
        assert hithink_common.thscode_to_ticker("000001.SZ") == "000001.SZ"
        assert hithink_common.thscode_to_ticker("830123.BJ") == "830123.BJ"

    def test_ticker_to_thscode(self):
        assert hithink_common.ticker_to_thscode("600519.SS") == "600519.SH"
        assert hithink_common.ticker_to_thscode("002241.SZ") == "002241.SZ"

    def test_round_trip(self):
        assert hithink_common.ticker_to_thscode(
            hithink_common.thscode_to_ticker("688001.SH")
        ) == "688001.SH"

    def test_thscode_rejects_non_a_share(self):
        with pytest.raises(ValueError):
            hithink_common.thscode_to_ticker("1810.HK")

    def test_ticker_rejects_non_a_share(self):
        with pytest.raises(ValueError):
            hithink_common.ticker_to_thscode("AAPL")


class TestSearchTickers:
    def test_shapes_params_and_returns_items(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        items = [{"thscode": "601318.SH", "ticker": "601318", "name": "中国平安"}]
        with patch(
            "tradingagents.dataflows.hithink_common.hithink_get",
            return_value={"item": items},
        ) as mock_get:
            result = hithink_common.search_tickers("平安", exchange="SH", limit=20)
        assert result == items
        mock_get.assert_called_once_with(
            "/api/meta/tickers/search",
            {"q": "平安", "asset_type": "a-share", "limit": 20, "exchange": "SH"},
        )

    def test_non_list_item_returns_empty(self, monkeypatch):
        monkeypatch.setenv(hithink_common.API_KEY_ENV, "k")
        with patch(
            "tradingagents.dataflows.hithink_common.hithink_get",
            return_value={"item": None},
        ):
            assert hithink_common.search_tickers("xxx") == []
