"""Comprehensive unit tests for tradingagents/llm_clients/retry_utils.py.

Targets >95 % coverage with self-contained tests — no external network calls,
no dependency on optional SDKs (openai, anthropic, google, httpx are mocked).
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from tradingagents.llm_clients.retry_utils import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    _get_status_code,
    is_transient_llm_error,
    llm_retry,
    with_llm_retry,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_exc_class(name: str, bases: tuple = (Exception,)) -> type:
    """Create a single-use exception class with the given *name*."""
    return type(name, bases, {})


def _make_module(name: str, **attrs: object) -> types.ModuleType:
    """Return a minimal mock module whose ``isinstance`` checks work."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


_OpenAIExc = _make_exc_class("_OpenAIExc")
_AnthropicExc = _make_exc_class("_AnthropicExc")
_GoogleExc = _make_exc_class("_GoogleExc")


# ── RetryConfig ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRetryConfig:
    """RetryConfig dataclass and delay_for_attempt()."""

    def test_default_values(self) -> None:
        cfg = RetryConfig()
        assert cfg.enabled is True
        assert cfg.max_retries == 3
        assert cfg.base_delay == 2.0
        assert cfg.backoff_multiplier == 2.0

    def test_delay_for_attempt_zero_returns_base(self) -> None:
        assert RetryConfig().delay_for_attempt(0) == 2.0

    def test_delay_for_attempt_backoff(self) -> None:
        cfg = RetryConfig(base_delay=1.0, backoff_multiplier=3.0)
        assert cfg.delay_for_attempt(2) == 9.0

    def test_delay_for_attempt_custom_base(self) -> None:
        cfg = RetryConfig(base_delay=5.0)
        assert cfg.delay_for_attempt(1) == 10.0

    def test_config_is_frozen(self) -> None:
        cfg = RetryConfig()
        with pytest.raises(AttributeError):
            cfg.enabled = False  # type: ignore[misc]

    def test_default_config_singleton(self) -> None:
        assert RetryConfig() == DEFAULT_RETRY_CONFIG
        assert DEFAULT_RETRY_CONFIG.enabled is True


# ── _get_status_code ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestGetStatusCode:
    """_get_status_code() extraction from various exception shapes."""

    def test_from_status_code_attr(self) -> None:
        exc = MagicMock(status_code=429)
        assert _get_status_code(exc) == 429

    def test_from_code_attr(self) -> None:
        exc = MagicMock(code=503)
        assert _get_status_code(exc) == 503

    def test_from_status_attr(self) -> None:
        exc = MagicMock(status=500)
        assert _get_status_code(exc) == 500

    def test_from_response_status_code(self) -> None:
        exc = MagicMock(response=MagicMock(status_code=504))
        assert _get_status_code(exc) == 504

    def test_from_response_status(self) -> None:
        exc = MagicMock(response=MagicMock(status=500))
        assert _get_status_code(exc) == 500

    def test_from_urllib_code(self) -> None:
        """urllib.error.HTTPError stores the status on .code (checked last)."""
        # After direct attrs (status_code/code/status) and response are all
        # absent, the function checks exc.code as a last resort.
        exc = MagicMock(spec=[])  # no status_code, code, status, or response
        # Add a .code attribute dynamically (beyond the spec).
        type(exc).code = PropertyMock(return_value=429)
        result = _get_status_code(exc)
        assert result == 429

    def test_returns_none_when_no_status(self) -> None:
        assert _get_status_code(Exception("plain")) is None

    def test_returns_none_when_value_not_int(self) -> None:
        exc = MagicMock(status_code="429")  # string, not int
        assert _get_status_code(exc) is None

    def test_returns_none_when_out_of_range(self) -> None:
        exc = MagicMock(status_code=99)
        assert _get_status_code(exc) is None

    def test_response_out_of_range_is_none(self) -> None:
        exc = MagicMock(response=MagicMock(status_code=999))
        assert _get_status_code(exc) is None


# ── is_transient_llm_error ───────────────────────────────────────────────────


@pytest.mark.unit
class TestIsTransientError:
    """is_transient_llm_error() classification of transient vs. permanent."""

    # ── Built-in network errors ──────────────────────────────────────────

    def test_timeout_error_returns_true(self) -> None:
        assert is_transient_llm_error(TimeoutError()) is True

    def test_connection_error_returns_true(self) -> None:
        assert is_transient_llm_error(ConnectionError()) is True

    def test_generic_exception_returns_false(self) -> None:
        assert is_transient_llm_error(ValueError("bad")) is False

    # ── OpenAI ───────────────────────────────────────────────────────────

    def test_openai_rate_limit(self) -> None:
        _OpenAIRateLimit = _make_exc_class("RateLimitError", (_OpenAIExc,))
        _OpenAIAPIConn = _make_exc_class("APIConnectionError", (_OpenAIExc,))
        _OpenAIAPITimeout = _make_exc_class("APITimeoutError", (_OpenAIExc,))
        _OpenAIInternal = _make_exc_class("InternalServerError", (_OpenAIExc,))
        mock_mod = _make_module(
            "openai",
            RateLimitError=_OpenAIRateLimit,
            APIConnectionError=_OpenAIAPIConn,
            APITimeoutError=_OpenAIAPITimeout,
            InternalServerError=_OpenAIInternal,
        )
        with patch.dict("sys.modules", {"openai": mock_mod}):
            assert is_transient_llm_error(_OpenAIRateLimit("over limit")) is True
            assert is_transient_llm_error(_OpenAIAPIConn("conn lost")) is True
            assert is_transient_llm_error(_OpenAIAPITimeout("timed out")) is True
            assert is_transient_llm_error(_OpenAIInternal("500")) is True

    # ── Anthropic ────────────────────────────────────────────────────────

    def test_anthropic_rate_limit(self) -> None:
        _ARateLimit = _make_exc_class("RateLimitError", (_AnthropicExc,))
        _AAPIConn = _make_exc_class("APIConnectionError", (_AnthropicExc,))
        _AAPITimeout = _make_exc_class("APITimeoutError", (_AnthropicExc,))
        _AInternal = _make_exc_class("InternalServerError", (_AnthropicExc,))
        mock_mod = _make_module(
            "anthropic",
            RateLimitError=_ARateLimit,
            APIConnectionError=_AAPIConn,
            APITimeoutError=_AAPITimeout,
            InternalServerError=_AInternal,
        )
        with patch.dict("sys.modules", {"anthropic": mock_mod}):
            assert is_transient_llm_error(_ARateLimit("over")) is True
            assert is_transient_llm_error(_AAPIConn("conn")) is True
            assert is_transient_llm_error(_AAPITimeout("timeout")) is True
            assert is_transient_llm_error(_AInternal("500")) is True

    # ── Google ───────────────────────────────────────────────────────────

    def test_google_resource_exhausted(self) -> None:
        _RExc = _make_exc_class("ResourceExhausted", (_GoogleExc,))
        _SvcUnavail = _make_exc_class("ServiceUnavailable", (_GoogleExc,))
        _Deadline = _make_exc_class("DeadlineExceeded", (_GoogleExc,))
        _Internal = _make_exc_class("InternalServerError", (_GoogleExc,))
        mock_exceptions = _make_module(
            "google.api_core.exceptions",
            ResourceExhausted=_RExc,
            ServiceUnavailable=_SvcUnavail,
            DeadlineExceeded=_Deadline,
            InternalServerError=_Internal,
        )
        mock_api_core = _make_module("google.api_core")
        mock_api_core.exceptions = mock_exceptions
        with patch.dict(
            "sys.modules",
            {
                "google": _make_module("google"),
                "google.api_core": mock_api_core,
                "google.api_core.exceptions": mock_exceptions,
            },
        ):
            assert is_transient_llm_error(_RExc("quota")) is True
            assert is_transient_llm_error(_SvcUnavail("unavail")) is True
            assert is_transient_llm_error(_Deadline("deadline")) is True
            assert is_transient_llm_error(_Internal("500")) is True

    # ── httpx ────────────────────────────────────────────────────────────

    def test_httpx_status_error(self) -> None:
        _HTTPStatusErr = _make_exc_class("HTTPStatusError")
        mock_mod = _make_module("httpx", HTTPStatusError=_HTTPStatusErr)
        with patch.dict("sys.modules", {"httpx": mock_mod}):
            assert is_transient_llm_error(_HTTPStatusErr("500")) is True

    # ── Status-code based ────────────────────────────────────────────────

    def test_status_code_429_returns_true(self) -> None:
        exc = MagicMock(status_code=429)
        assert is_transient_llm_error(exc) is True

    def test_status_code_200_returns_false(self) -> None:
        exc = MagicMock(status_code=200)
        assert is_transient_llm_error(exc) is False

    def test_all_retryable_codes(self) -> None:
        for code in (408, 429, 500, 502, 503, 504):
            exc = MagicMock(status_code=code)
            assert is_transient_llm_error(exc) is True, f"{code} should be transient"

    def test_non_retryable_codes(self) -> None:
        for code in (400, 401, 403, 404, 422):
            exc = MagicMock(status_code=code)
            assert is_transient_llm_error(exc) is False, f"{code} should not be transient"

    # ── Message-marker based ─────────────────────────────────────────────

    def test_message_rate_limit_marker(self) -> None:
        assert is_transient_llm_error(ValueError("rate limit exceeded")) is True

    def test_message_too_many_requests_marker(self) -> None:
        assert is_transient_llm_error(RuntimeError("too many requests")) is True

    def test_message_temporarily_unavailable_marker(self) -> None:
        assert is_transient_llm_error(RuntimeError("temporarily unavailable")) is True

    def test_message_internal_server_error_marker(self) -> None:
        assert is_transient_llm_error(RuntimeError("internal server error")) is True

    def test_message_empty_response_marker(self) -> None:
        assert is_transient_llm_error(RuntimeError("empty response received")) is True

    def test_message_empty_response_short_marker(self) -> None:
        assert is_transient_llm_error(RuntimeError("empty response")) is True

    def test_message_marker_case_insensitive(self) -> None:
        assert is_transient_llm_error(ValueError("RATE LIMIT Exceeded")) is True

    def test_message_no_marker_returns_false(self) -> None:
        assert is_transient_llm_error(ValueError("some other error")) is False

    # ── Non-transient subclasses of transient base ───────────────────────
    # These would still be caught by the isinstance check, which is correct.

    # ── ImportError branches ─────────────────────────────────────────────
    # When openai / anthropic / google / httpx are NOT installed, the
    # try/except ImportError is taken and the check falls through.

    def test_falls_through_when_openai_not_installed(self) -> None:
        with patch.dict("sys.modules", {"openai": None, "openai.api": None}):
            # If openai is not importable, the block is skipped.
            exc = _make_exc_class("RateLimitError")("test")
            # Without the openai isinstance branch, this should fall through
            # to status-code / message checks, both of which return False
            # for this plain exception.
            assert is_transient_llm_error(exc) is False

    def test_falls_through_when_anthropic_not_installed(self) -> None:
        with patch.dict("sys.modules", {"anthropic": None}):
            exc = _make_exc_class("RateLimitError")("test")
            assert is_transient_llm_error(exc) is False

    def test_falls_through_when_google_not_installed(self) -> None:
        with patch.dict(
            "sys.modules",
            {"google": None, "google.api_core": None, "google.api_core.exceptions": None},
        ):
            exc = _make_exc_class("ResourceExhausted")("test")
            assert is_transient_llm_error(exc) is False

    def test_falls_through_when_httpx_not_installed(self) -> None:
        with patch.dict("sys.modules", {"httpx": None}):
            exc = _make_exc_class("HTTPStatusError")("test")
            assert is_transient_llm_error(exc) is False


# ── llm_retry ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestLlmRetry:
    """llm_retry() exponential backoff behaviour."""

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_success_on_first_attempt(self, mock_sleep: MagicMock) -> None:
        fn = MagicMock(return_value="ok")
        assert llm_retry(fn) == "ok"
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_retries_on_transient(self, mock_sleep: MagicMock) -> None:
        calls: list[int] = []

        def fn() -> str:
            calls.append(len(calls))
            if len(calls) < 2:
                raise TimeoutError("slow")
            return "ok"

        result = llm_retry(fn, retry_config=RetryConfig(max_retries=3, base_delay=0.1))
        assert result == "ok"
        assert len(calls) == 2
        mock_sleep.assert_called_once_with(0.1)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_raises_after_exhaustion(self, mock_sleep: MagicMock) -> None:
        fn = MagicMock(side_effect=TimeoutError("always"))
        with pytest.raises(TimeoutError, match="always"):
            llm_retry(fn, retry_config=RetryConfig(max_retries=2, base_delay=0.1))
        # initial + 2 retries
        assert fn.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_raises_non_transient_immediately(self, mock_sleep: MagicMock) -> None:
        fn = MagicMock(side_effect=TypeError("bad type"))
        with pytest.raises(TypeError, match="bad type"):
            llm_retry(fn, retry_config=RetryConfig(max_retries=3, base_delay=0.1))
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_disabled_config_skips_retry(self, mock_sleep: MagicMock) -> None:
        fn = MagicMock(side_effect=TimeoutError("slow"))
        with pytest.raises(TimeoutError):
            llm_retry(fn, retry_config=RetryConfig(enabled=False, max_retries=3))
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_zero_max_retries_skips_retry(self, mock_sleep: MagicMock) -> None:
        fn = MagicMock(side_effect=TimeoutError("slow"))
        with pytest.raises(TimeoutError):
            llm_retry(fn, retry_config=RetryConfig(max_retries=0))
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_uses_default_config_when_none(self, mock_sleep: MagicMock) -> None:
        calls: list[int] = []

        def fn() -> str:
            calls.append(len(calls))
            if len(calls) < 2:
                raise TimeoutError("slow")
            return "ok"

        result = llm_retry(fn)  # no retry_config → DEFAULT_RETRY_CONFIG
        assert result == "ok"
        mock_sleep.assert_called_once_with(DEFAULT_RETRY_CONFIG.base_delay)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    @patch("tradingagents.llm_clients.retry_utils.logger")
    def test_logs_warning_on_retry(
        self, mock_logger: MagicMock, mock_sleep: MagicMock
    ) -> None:
        fn = MagicMock(side_effect=TimeoutError("slow"))
        with pytest.raises(TimeoutError):
            llm_retry(fn, retry_config=RetryConfig(max_retries=1, base_delay=0.1))
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args[0][0]
        assert "transient error" in msg

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_backoff_delay_increases_with_attempts(
        self, mock_sleep: MagicMock
    ) -> None:
        fn = MagicMock(side_effect=TimeoutError("slow"))
        with pytest.raises(TimeoutError):
            llm_retry(fn, retry_config=RetryConfig(max_retries=3, base_delay=1.0))
        # delays: attempt 0 → 1.0, attempt 1 → 2.0, attempt 2 → 4.0
        assert mock_sleep.call_count == 3
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]


# ── with_llm_retry ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestWithLlmRetry:
    """with_llm_retry() decorator behaviour."""

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_reads_retry_config_from_instance(self, mock_sleep: MagicMock) -> None:
        """When ``self._retry_config`` is set, the decorator uses it."""

        class FakeClient:
            _retry_config = RetryConfig(max_retries=1, base_delay=0.5)

            @with_llm_retry
            def invoke(self) -> str:
                raise TimeoutError("transient")

        client = FakeClient()
        with pytest.raises(TimeoutError):
            client.invoke()
        # RetryConfig(max_retries=1) → 1 retry → 1 sleep
        mock_sleep.assert_called_once_with(0.5)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_falls_back_to_default(self, mock_sleep: MagicMock) -> None:
        """When ``self._retry_config`` is absent, DEFAULT_RETRY_CONFIG is used."""

        class FakeClient:
            @with_llm_retry
            def invoke(self) -> str:
                return "ok"

        client = FakeClient()
        result = client.invoke()
        assert result == "ok"
        mock_sleep.assert_not_called()

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_passes_args_and_kwargs(self, mock_sleep: MagicMock) -> None:
        """The decorator forwards positional and keyword arguments."""

        class FakeClient:
            _retry_config = RetryConfig(enabled=False)

            @with_llm_retry
            def invoke(self, a: int, b: int, *, c: str) -> str:
                return f"{a},{b},{c}"

        client = FakeClient()
        assert client.invoke(1, 2, c="x") == "1,2,x"

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_preserves_func_name(self, mock_sleep: MagicMock) -> None:
        """@functools.wraps preserves the original method name."""

        class FakeClient:
            @with_llm_retry
            def invoke(self) -> str:
                return "ok"

        assert FakeClient.invoke.__name__ == "invoke"

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_retry_config_none_falls_to_default(self, mock_sleep: MagicMock) -> None:
        """When self._retry_config is None, DEFAULT_RETRY_CONFIG is used."""

        class FakeClient:
            _retry_config = None

            @with_llm_retry
            def invoke(self) -> str:
                return "ok"

        client = FakeClient()
        assert client.invoke() == "ok"
