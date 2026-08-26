"""Shared client for the HiThink (同花顺) Financial-API.

Wraps the REST contract at https://fuyao.aicubes.cn (see docs/tonghuashun_api.md):

- Auth: ``X-api-key`` request header, key from env ``HITHINK_FINANCE_API_KEY``.
- Envelope: every response is HTTP 200 with ``ApiResponse{code, message,
  request_id, data}``; business errors are expressed via ``code``.
- Retry: exponential backoff on rate-limit (``4001``) and server/upstream
  errors (``5001``/``5002``/``5003``); network errors are retried the same way.
- Proxy: the endpoint is domestic, so calls go through ``no_proxy()`` like the
  akshare vendor to bypass system proxies (e.g. Clash on macOS).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from tradingagents.dataflows.akshare_common import no_proxy
from tradingagents.dataflows.errors import (
    VendorError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("HITHINK_BASE_URL", "https://fuyao.aicubes.cn")
API_KEY_ENV = "HITHINK_FINANCE_API_KEY"

# Business codes worth retrying: 4001 = QPS throttle; 5xxx = server/upstream.
_RETRYABLE_CODES = {4001, 5001, 5002, 5003}

_DEFAULT_TIMEOUT = 15.0
_MAX_RETRIES = 3
_BASE_DELAY = 1.0


class HithinkApiError(VendorError):
    """Non-retryable business error from the ApiResponse envelope (code != 0).

    Subclasses ``VendorError`` so the routing layer treats it as "this vendor
    failed" and moves on to the next vendor in the chain.
    """

    def __init__(self, code: int, message: str, request_id: str | None = None):
        self.code = code
        self.request_id = request_id
        detail = f"hithink API error code={code}: {message}"
        if request_id:
            detail += f" (request_id={request_id})"
        super().__init__(detail)


def get_api_key() -> str | None:
    """Return the configured API key, or None when unset."""
    return os.getenv(API_KEY_ENV) or None


def is_available() -> bool:
    """True iff an API key is configured (says nothing about its validity)."""
    return get_api_key() is not None


def hithink_get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
    max_retries: int = _MAX_RETRIES,
) -> dict[str, Any]:
    """GET ``path`` and return the envelope's ``data`` object.

    Raises:
        VendorNotConfiguredError: no API key configured.
        VendorRateLimitError: still throttled (4001) after all retries.
        HithinkApiError: any other non-zero business code.
        requests.exceptions.RequestException: network failure after retries.
    """
    key = get_api_key()
    if not key:
        raise VendorNotConfiguredError(
            f"hithink vendor requires env {API_KEY_ENV}"
        )

    import requests  # local import — consistent with akshare_common

    url = f"{BASE_URL}{path}"
    headers = {"X-api-key": key}
    effective_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT

    for attempt in range(max_retries + 1):
        try:
            with no_proxy():
                resp = requests.get(
                    url, params=params, headers=headers, timeout=effective_timeout
                )
            payload = resp.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            # ValueError covers JSON decode failures on a non-JSON body.
            if attempt < max_retries:
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    "hithink network error (%s), retrying in %.0fs (%d/%d)",
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            raise

        code = payload.get("code")
        if code == 0:
            data = payload.get("data")
            return data if isinstance(data, dict) else {}

        message = str(payload.get("message") or "")
        request_id = payload.get("request_id")
        if code in _RETRYABLE_CODES and attempt < max_retries:
            delay = _BASE_DELAY * (2**attempt)
            logger.warning(
                "hithink code=%s (%s), retrying in %.0fs (%d/%d)",
                code,
                message,
                delay,
                attempt + 1,
                max_retries,
            )
            time.sleep(delay)
            continue
        if code == 4001:
            raise VendorRateLimitError(
                f"hithink rate limit persists after {max_retries + 1} attempts"
                + (f" (request_id={request_id})" if request_id else "")
            )
        raise HithinkApiError(code, message, request_id)

    raise RuntimeError("unreachable: retry loop always returns or raises")  # pragma: no cover


# ---------------------------------------------------------------------------
# Symbol conversion (project uses yfinance-style .SS; HiThink uses .SH)
# ---------------------------------------------------------------------------

_THS_EXCHANGE_TO_SUFFIX = {"SH": ".SS", "SZ": ".SZ", "BJ": ".BJ"}
_SUFFIX_TO_THS_EXCHANGE = {v: k for k, v in _THS_EXCHANGE_TO_SUFFIX.items()}


def thscode_to_ticker(thscode: str) -> str:
    """Convert HiThink thscode (``600519.SH``) to project ticker (``600519.SS``)."""
    code, dot, exch = thscode.strip().upper().partition(".")
    suffix = _THS_EXCHANGE_TO_SUFFIX.get(exch)
    if not dot or suffix is None or not code:
        raise ValueError(f"Unsupported thscode: {thscode!r}")
    return code + suffix


def ticker_to_thscode(ticker: str) -> str:
    """Convert project ticker (``600519.SS``) to HiThink thscode (``600519.SH``)."""
    code, dot, suffix = ticker.strip().upper().partition(".")
    exch = _SUFFIX_TO_THS_EXCHANGE.get(dot + suffix)
    if not dot or exch is None or not code:
        raise ValueError(f"Not an A-share ticker: {ticker!r}")
    return f"{code}.{exch}"


# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------


def search_tickers(
    q: str,
    *,
    asset_type: str = "a-share",
    exchange: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search tickers by thscode / code / name (``/api/meta/tickers/search``).

    Returns the ``item`` list (dicts with thscode/ticker/name/exchange/
    asset_type/currency); empty list when the search yields nothing.
    """
    params: dict[str, Any] = {"q": q, "asset_type": asset_type, "limit": limit}
    if exchange:
        params["exchange"] = exchange
    data = hithink_get("/api/meta/tickers/search", params)
    items = data.get("item")
    return items if isinstance(items, list) else []
