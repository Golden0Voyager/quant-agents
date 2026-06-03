"""Shared utilities for the akshare vendor.

Provides symbol-format conversion, proxy isolation, unit normalization and
small numeric helpers. The akshare vendor functions in akshare_vendor.py
depend on this module exclusively for cross-cutting concerns.
"""
from __future__ import annotations

import logging
import math
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AShareSymbolError(ValueError):
    """Raised when an A-share ticker cannot be converted to akshare format."""


_SUFFIX_TO_PREFIX = {".SS": "SH", ".SZ": "SZ", ".BJ": "BJ"}


def is_a_share_ticker(ticker: str) -> bool:
    """Return True iff *ticker* ends with .SS, .SZ or .BJ (case-insensitive)."""
    if not isinstance(ticker, str) or not ticker:
        return False
    return ticker.upper().endswith((".SS", ".SZ", ".BJ"))


def to_akshare_symbol(ticker: str, style: str) -> str:
    """Convert an exchange-qualified ticker to the format akshare expects.

    style:
        "bare"          -> "600519"
        "upper_prefix"  -> "SH600519"
        "lower_prefix"  -> "sh600519"
    """
    if not is_a_share_ticker(ticker):
        raise AShareSymbolError(f"Not an A-share ticker: {ticker!r}")

    upper = ticker.upper()
    code, suffix = upper.split(".")
    prefix = _SUFFIX_TO_PREFIX[f".{suffix}"]

    if style == "bare":
        return code
    if style == "upper_prefix":
        return f"{prefix}{code}"
    if style == "lower_prefix":
        return f"{prefix.lower()}{code}"
    raise ValueError(f"Unknown style: {style!r}")


def _akshare_retry(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Execute an akshare call with exponential backoff on transient network errors."""
    import requests.exceptions as _re  # local import — requests is an akshare dependency

    _network_errors = (ConnectionError, TimeoutError, _re.ConnectionError, _re.Timeout)

    for attempt in range(max_retries + 1):
        try:
            return func()
        except _network_errors as exc:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Akshare network error (%s), retrying in %.0fs (%d/%d)",
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
            else:
                raise


@contextmanager
def no_proxy() -> Generator[None, None, None]:
    """Temporarily strip proxy env vars so domestic APIs are reached directly.

    Also monkey-patches ``requests.utils.getproxies`` because on macOS
    ``requests`` reads system proxy settings (e.g. Clash at 127.0.0.1:7897)
    via ``_scproxy`` even when no HTTP_PROXY env var is present.

    Additionally forces IPv4-only for socket resolution because Eastmoney's
    IPv6 endpoint (2408:870c::/32) frequently drops connections with
    ``RemoteDisconnected`` while IPv4 works reliably.
    """
    import requests.utils as _ru  # local import to avoid startup side-effects
    import socket

    keys = (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    )
    saved_env = {k: os.environ[k] for k in keys if k in os.environ}
    for k in saved_env:
        del os.environ[k]

    _orig_getproxies = _ru.getproxies
    _ru.getproxies = lambda: {}

    _orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_only_getaddrinfo(*args, **kwargs):
        res = _orig_getaddrinfo(*args, **kwargs)
        ipv4 = [r for r in res if r[0] == socket.AF_INET]
        return ipv4 if ipv4 else res

    socket.getaddrinfo = _ipv4_only_getaddrinfo
    try:
        yield
    finally:
        for k, v in saved_env.items():
            os.environ[k] = v
        _ru.getproxies = _orig_getproxies
        socket.getaddrinfo = _orig_getaddrinfo


_UNIT_FACTORS = {
    "yuan": 1.0,
    "wan": 10_000.0,
    "yi": 100_000_000.0,
}


def safe_float(value: Any) -> Optional[float]:
    """Best-effort float coercion; returns None on failure or NaN/Inf."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def to_yuan(value: Any, source_unit: str) -> Optional[float]:
    """Normalize a monetary value to yuan (元).

    source_unit ∈ {"yuan", "wan", "yi"}. This is the single point where the
    万元/亿元 conversion happens — keeping it centralized prevents the kind
    of systematic 10× errors recorded in financial_data_errors_report.md.
    """
    if source_unit not in _UNIT_FACTORS:
        raise ValueError(f"Unknown source_unit: {source_unit!r}")
    v = safe_float(value)
    if v is None:
        return None
    return v * _UNIT_FACTORS[source_unit]


def format_money_cn(value_yuan: Optional[float]) -> str:
    """Format yuan as a Chinese-friendly string with auto-scaled unit.

    Appends the English equivalent in parentheses to prevent LLM
    unit misinterpretation (e.g. 亿元 being read as billion).
    """
    if value_yuan is None:
        return "N/A"
    abs_v = abs(value_yuan)
    if abs_v >= 100_000_000:
        return f"{value_yuan / 100_000_000:.2f}亿元 (≈{value_yuan / 1_000_000_000:.2f} billion CNY)"
    if abs_v >= 10_000:
        return f"{value_yuan / 10_000:.2f}万元 (≈{value_yuan / 1_000_000:.2f} million CNY)"
    return f"{value_yuan:.2f}元"
