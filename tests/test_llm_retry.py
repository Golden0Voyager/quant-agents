"""Tests for the LLM retry/backoff utilities."""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tradingagents.llm_clients.retry_utils import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    is_transient_llm_error,
    llm_retry,
)


@pytest.mark.unit
class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.enabled is True
        assert cfg.max_retries == 3
        assert cfg.base_delay == 2.0
        assert cfg.backoff_multiplier == 2.0

    def test_delay_for_attempt(self):
        cfg = RetryConfig(base_delay=1.0, backoff_multiplier=2.0)
        assert cfg.delay_for_attempt(0) == 1.0
        assert cfg.delay_for_attempt(1) == 2.0
        assert cfg.delay_for_attempt(2) == 4.0


@pytest.mark.unit
class TestIsTransientLlmError:
    def test_builtin_timeout_and_connection_errors_are_transient(self):
        assert is_transient_llm_error(TimeoutError("slow"))
        assert is_transient_llm_error(ConnectionError("reset"))

    def test_openai_rate_limit_is_transient(self):
        openai = pytest.importorskip("openai")
        exc = openai.RateLimitError(
            "rate limit exceeded",
            response=MagicMock(status_code=429),
            body={"error": {"message": "rate limit"}},
        )
        assert is_transient_llm_error(exc)

    def test_openai_api_connection_error_is_transient(self):
        openai = pytest.importorskip("openai")
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        exc = openai.APIConnectionError(message="connection failed", request=request)
        assert is_transient_llm_error(exc)

    def test_openai_bad_request_is_not_transient(self):
        openai = pytest.importorskip("openai")
        exc = openai.BadRequestError(
            "invalid request",
            response=MagicMock(status_code=400),
            body={"error": {"message": "invalid"}},
        )
        assert not is_transient_llm_error(exc)

    def test_status_code_based_transient(self):
        for code in (408, 429, 500, 502, 503, 504):
            exc = MagicMock()
            exc.status_code = code
            assert is_transient_llm_error(exc), f"status {code} should be transient"

    def test_status_code_based_non_transient(self):
        for code in (400, 401, 403, 404, 422):
            exc = MagicMock()
            exc.status_code = code
            assert not is_transient_llm_error(exc), f"status {code} should not be transient"

    def test_urllib_http_error_transient(self):
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )
        assert is_transient_llm_error(exc)

    def test_message_fallback_rate_limit(self):
        assert is_transient_llm_error(RuntimeError("rate limit exceeded on dimension: tpm"))

    def test_message_fallback_too_many_requests(self):
        assert is_transient_llm_error(ValueError("Too Many Requests"))

    def test_non_transient_exception(self):
        assert not is_transient_llm_error(ValueError("bad schema"))
        assert not is_transient_llm_error(TypeError("unexpected"))

    def test_openai_bad_request_with_internal_server_error_is_transient(self):
        """SenseNova returns 400 with code='internal_server_error' — treat as transient."""
        openai = pytest.importorskip("openai")
        exc = openai.BadRequestError(
            "Empty response received",
            response=MagicMock(status_code=400),
            body={"error": {"message": "Empty response received", "code": "internal_server_error"}},
        )
        assert is_transient_llm_error(exc)

    def test_plain_exception_with_internal_server_error_keyword(self):
        assert is_transient_llm_error(
            RuntimeError("{'error': {'message': 'Empty response received', 'code': 'internal_server_error'}}")
        )

    def test_plain_exception_with_empty_response_keyword(self):
        assert is_transient_llm_error(RuntimeError("Empty response received from API"))

    def test_null_choices_error_is_transient_and_retried(self):
        msg = "Received response with null value for 'choices'"
        assert is_transient_llm_error(ValueError(msg))

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_retries_on_null_choices_error(self, mock_sleep):
        calls = []
        error = ValueError("Received response with null value for 'choices'")

        def func():
            calls.append(len(calls))
            if len(calls) < 2:
                raise error
            return "ok"

        result = llm_retry(func, retry_config=RetryConfig(max_retries=3, base_delay=0.1))
        assert result == "ok"
        assert len(calls) == 2
        mock_sleep.assert_called_once_with(0.1)


@pytest.mark.unit
class TestLlmRetry:
    def test_returns_immediately_on_success(self):
        assert llm_retry(lambda: "ok") == "ok"

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        calls = []

        def func():
            calls.append(len(calls))
            if len(calls) < 2:
                raise TimeoutError("slow")
            return "ok"

        result = llm_retry(func, retry_config=RetryConfig(max_retries=3, base_delay=0.1))
        assert result == "ok"
        assert len(calls) == 2
        mock_sleep.assert_called_once_with(0.1)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_non_transient_error_raises_immediately(self, mock_sleep):
        func = MagicMock(side_effect=ValueError("bad"))
        with pytest.raises(ValueError, match="bad"):
            llm_retry(func, retry_config=RetryConfig(max_retries=3, base_delay=0.1))
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_exhaustion_raises_last_exception(self, mock_sleep):
        error = TimeoutError("slow")
        func = MagicMock(side_effect=error)
        with pytest.raises(TimeoutError, match="slow"):
            llm_retry(func, retry_config=RetryConfig(max_retries=2, base_delay=0.1))
        assert func.call_count == 3  # initial + 2 retries
        assert mock_sleep.call_count == 2

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_disabled_retry_raises_immediately(self, mock_sleep):
        error = TimeoutError("slow")
        func = MagicMock(side_effect=error)
        with pytest.raises(TimeoutError, match="slow"):
            llm_retry(
                func,
                retry_config=RetryConfig(enabled=False, max_retries=3, base_delay=0.1),
            )
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_zero_max_retries_raises_immediately(self, mock_sleep):
        error = TimeoutError("slow")
        func = MagicMock(side_effect=error)
        with pytest.raises(TimeoutError, match="slow"):
            llm_retry(func, retry_config=RetryConfig(max_retries=0, base_delay=0.1))
        func.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_uses_default_config_when_none_provided(self, mock_sleep):
        """When retry_config is None, llm_retry uses DEFAULT_RETRY_CONFIG.

        We do not mutate the global default; instead we just verify the retry
        path is active by failing once with a transient error and checking that
        a retry sleep is scheduled using the default base_delay.
        """
        calls = []

        def func():
            calls.append(len(calls))
            if len(calls) < 2:
                raise TimeoutError("slow")
            return "ok"

        result = llm_retry(func)  # no retry_config argument
        assert result == "ok"
        # DEFAULT_RETRY_CONFIG.base_delay is 2.0
        mock_sleep.assert_called_once_with(DEFAULT_RETRY_CONFIG.base_delay)

    @patch("tradingagents.llm_clients.retry_utils.logger")
    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_logs_retry_attempts(self, mock_sleep, mock_logger):
        func = MagicMock(side_effect=TimeoutError("slow"))
        with pytest.raises(TimeoutError):
            llm_retry(func, retry_config=RetryConfig(max_retries=1, base_delay=0.1))
        assert mock_logger.warning.call_count == 1
        log_message = mock_logger.warning.call_args[0][0]
        assert "transient error" in log_message.lower()
        assert "last retry" in log_message.lower()
