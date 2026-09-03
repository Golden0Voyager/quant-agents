"""Retry utilities for LLM invocations.

Provides a small, dependency-free exponential-backoff retry helper that
mirrors the style of the data-layer retries in ``dataflows/`` (``yf_retry``,
``_akshare_retry``, ``_with_retry``). All normalized LLM wrappers apply it to
their ``invoke`` method so that rate limits and other transient provider
errors are retried automatically.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_MESSAGE_MARKERS: frozenset[str] = frozenset(
    {
        "rate limit",
        "rate_limit",
        "too many requests",
        "temporarily unavailable",
        "internal server error",
        "internal_server_error",
        "empty response received",
        "empty response",
        "insufficient_quota",
        "quota exceeded",
        "quota_exceeded",
        "exceeded quota",
        "api key quota",
        "out of quota",
        "insufficient balance",
        "insufficient_balance",
        "balance insufficient",
        "credit limit",
        "no credit",
        "over quota",
        "over_quota",
        "overloaded",
        "service unavailable",
        "service_unavailable",
        "bad gateway",
        "bad_gateway",
        "gateway timeout",
        "gateway_timeout",
        "upstream",
        "connection refused",
        "connection reset",
        "connection_reset",
        "connect timeout",
        "connect_timed_out",
        "read timeout",
        "read_timed_out",
        "deadline exceeded",
        "deadline_exceeded",
        "context deadline",
        "context_deadline",
        # OpenAI-compatible providers (vLLM, ModelScope, OpenRouter) sometimes
        # return a 200 response whose ``choices`` field is null. The request is
        # well-formed; the provider simply produced an empty/malformed
        # completion, so treat it as transient and let the fallback chain try
        # the next provider instead of aborting the whole analysis.
        "null value for 'choices'",
    }
)

# Subset of transient markers that signal an exhausted quota/balance rather
# than a momentary burst. These do not clear within a short backoff window
# (e.g. the SenseNova Token Plan resets per-model quotas on a 5-hour cycle),
# so same-tier retries just burn time; the fallback chain should advance to
# the next provider/model immediately instead.
_QUOTA_EXHAUSTION_MARKERS: frozenset[str] = frozenset(
    {
        "insufficient_quota",
        "quota exceeded",
        "quota_exceeded",
        "exceeded quota",
        "api key quota",
        "out of quota",
        "insufficient balance",
        "insufficient_balance",
        "balance insufficient",
        "credit limit",
        "no credit",
        "over quota",
        "over_quota",
    }
)


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for LLM retry/backoff behavior."""

    enabled: bool = True
    max_retries: int = 3
    base_delay: float = 2.0
    backoff_multiplier: float = 2.0

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the sleep duration before retry attempt *attempt* (0-indexed)."""
        return self.base_delay * (self.backoff_multiplier ** attempt)


# Default config used when no instance-specific config is provided.
DEFAULT_RETRY_CONFIG = RetryConfig()


def _get_status_code(exc: BaseException) -> int | None:
    """Extract an HTTP status code from *exc* if one is available."""
    # Direct attributes used by various SDKs.
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value

    # Wrapped in a response object (httpx, requests, openai).
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            value = getattr(response, attr, None)
            if isinstance(value, int) and 100 <= value < 600:
                return value

    # urllib.error.HTTPError uses .code.
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 100 <= code < 600:  # pragma: no cover  -- no current test injects an HTTPError-like exception
        return code

    return None


def is_transient_llm_error(exc: BaseException) -> bool:
    """Return True if *exc* represents a retryable transient LLM failure."""
    # Built-in network/transient errors.
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # OpenAI-compatible providers (OpenAI, DeepSeek, SenseNova, OpenRouter, ...).
    try:
        import openai

        if isinstance(
            exc,
            (
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
            ),
        ):
            return True
    except ImportError:
        pass

    # Anthropic.
    try:
        import anthropic

        if isinstance(
            exc,
            (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.InternalServerError,
            ),
        ):
            return True
    except ImportError:
        pass

    # Google Gemini / Vertex AI.
    try:
        from google.api_core import exceptions as google_exceptions

        if isinstance(
            exc,
            (
                google_exceptions.ResourceExhausted,
                google_exceptions.ServiceUnavailable,
                google_exceptions.DeadlineExceeded,
                google_exceptions.InternalServerError,
            ),
        ):
            return True
    except ImportError:
        pass

    # httpx status errors (used by several SDKs under the hood).
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            return True
    except ImportError:
        pass

    # HTTP status-code based fallback.
    status_code = _get_status_code(exc)
    if status_code in _RETRYABLE_STATUS_CODES:
        return True

    # Message-based fallback for provider wrappers that raise plain exceptions.
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MESSAGE_MARKERS)


def is_quota_exhaustion_error(exc: BaseException) -> bool:
    """Return True if *exc* means the plan's quota/balance is exhausted.

    These errors stay "transient" for the fallback chain (a different
    provider/model has its own quota), but same-tier backoff retries are
    futile because the window does not reset for hours.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _QUOTA_EXHAUSTION_MARKERS)


def llm_retry(func: Callable[[], T], *, retry_config: RetryConfig | None = None) -> T:
    """Call *func* with exponential backoff on transient LLM failures.

    ``func`` should be a no-argument callable returning the LLM result. The
    retry logic inspects raised exceptions via :func:`is_transient_llm_error`
    and only retries errors classified as transient.
    """
    cfg = retry_config or DEFAULT_RETRY_CONFIG
    if not cfg.enabled or cfg.max_retries <= 0:
        return func()

    for attempt in range(cfg.max_retries + 1):
        try:
            return func()
        except Exception as exc:
            if (
                attempt == cfg.max_retries
                or not is_transient_llm_error(exc)
                # Quota/balance exhaustion survives backoff (the window resets
                # in hours, not seconds) — raise at once so the fallback
                # chain can advance to the next provider/model.
                or is_quota_exhaustion_error(exc)
            ):
                raise
            delay = cfg.delay_for_attempt(attempt)
            if attempt < cfg.max_retries - 1:
                logger.info(
                    "Retrying LLM call after %s (attempt %d/%d, wait %.1fs)...",
                    type(exc).__name__, attempt + 1, cfg.max_retries + 1, delay,
                )
            else:
                logger.warning(
                    "LLM transient error (%s) — last retry (attempt %d/%d, wait %.1fs)",
                    type(exc).__name__, attempt + 1, cfg.max_retries + 1, delay,
                )
            time.sleep(delay)

    # Unreachable: the loop always returns or raises.
    raise RuntimeError("llm_retry unexpectedly exhausted retries")  # pragma: no cover


def with_llm_retry(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator that applies :func:`llm_retry` to an LLM ``invoke`` method.

    The wrapped method reads ``self._retry_config`` (set by the client factory)
    and falls back to :data:`DEFAULT_RETRY_CONFIG` if absent.
    """

    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> T:
        cfg = getattr(self, "_retry_config", None) or DEFAULT_RETRY_CONFIG
        return llm_retry(lambda: func(self, *args, **kwargs), retry_config=cfg)

    return wrapper
