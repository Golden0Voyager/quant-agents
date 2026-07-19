"""Process-wide shared rate limiters for LLM providers.

Batch mode runs multiple tickers concurrently (a ``ThreadPoolExecutor``), and
each ticker builds its own graph with its own LLM instances. A provider's
requests-per-minute quota, however, is shared across every one of those
instances — it is tied to the account / API key, not to a single client
object. A per-instance limiter would therefore never coordinate, so this module
hands out a *single* limiter per provider, cached process-wide and safe to
share across threads.

``langchain_core``'s :class:`InMemoryRateLimiter` is a token bucket guarded by
its own lock, so one shared instance paces every request that flows through it
regardless of which thread or think tier issued it.
"""
from __future__ import annotations

import logging
import threading

from langchain_core.rate_limiters import InMemoryRateLimiter

logger = logging.getLogger(__name__)

# provider (lowercased) -> shared limiter. Guarded by _LOCK for the
# check-then-create sequence so concurrent workers never build two limiters
# for the same provider.
_LIMITERS: dict[str, InMemoryRateLimiter] = {}
_LOCK = threading.Lock()

# How often a blocked request re-checks the bucket. Small enough to keep
# latency low, large enough to avoid busy-spinning across many waiters.
_CHECK_EVERY_N_SECONDS = 0.1


def get_shared_rate_limiter(
    provider: str, requests_per_minute: float
) -> InMemoryRateLimiter:
    """Return the process-wide shared rate limiter for *provider*.

    Created once per provider and reused for every subsequent call, so all LLM
    instances of that provider — across both think tiers and all batch worker
    threads — pace their requests through one token bucket capped at
    *requests_per_minute*.

    The first caller's rate wins; a later call with a different rate reuses the
    existing limiter (logging a warning) so a provider always has exactly one
    consistent limiter.

    Args:
        provider: Provider name (case-insensitive), e.g. ``"sensenova"``.
        requests_per_minute: Aggregate request cap. Must be > 0.

    Returns:
        A shared :class:`InMemoryRateLimiter` for *provider*.

    Raises:
        ValueError: If *requests_per_minute* is not positive.
    """
    if requests_per_minute <= 0:
        raise ValueError(
            f"requests_per_minute must be positive, got {requests_per_minute!r}"
        )

    key = provider.strip().lower()
    with _LOCK:
        existing = _LIMITERS.get(key)
        if existing is not None:
            existing_rpm = existing.requests_per_second * 60.0
            if abs(existing_rpm - requests_per_minute) > 1e-9:
                logger.warning(
                    "Rate limiter for provider '%s' already set to %.3g rpm; "
                    "ignoring new value %.3g rpm.",
                    key,
                    existing_rpm,
                    requests_per_minute,
                )
            return existing

        limiter = InMemoryRateLimiter(
            requests_per_second=requests_per_minute / 60.0,
            check_every_n_seconds=_CHECK_EVERY_N_SECONDS,
            max_bucket_size=1,
        )
        _LIMITERS[key] = limiter
        logger.info(
            "Created shared rate limiter for provider '%s': %.3g rpm.",
            key,
            requests_per_minute,
        )
        return limiter


def reset_rate_limiters() -> None:
    """Clear all cached limiters. Intended for test isolation."""
    with _LOCK:
        _LIMITERS.clear()
