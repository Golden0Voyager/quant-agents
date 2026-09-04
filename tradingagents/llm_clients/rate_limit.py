"""Process-wide shared rate limiters for LLM providers.

Batch mode runs multiple tickers concurrently (a ``ThreadPoolExecutor``), and
each ticker builds its own graph with its own LLM instances. A request quota,
however, is shared across every one of those instances — it is tied to the
account / API key, not to a single client object. A per-instance limiter
would therefore never coordinate, so this module hands out a *single* limiter
per quota scope, cached process-wide and safe to share across threads.

The quota scope is the provider by default, or the provider/model pair when
*model* is given. Plans like the SenseNova Token Plan (credit-based since
2026-08: 通用积分 + Flash-Lite 专属积分 pools with rolling 5h/weekly windows,
deducted by token usage) price each model differently, so pacing two models
through one bucket would both serialize them needlessly and let a fast model
drain the slow model's budget.

``langchain_core``'s :class:`InMemoryRateLimiter` is a token bucket guarded by
its own lock, so one shared instance paces every request that flows through it
regardless of which thread or think tier issued it.
"""
from __future__ import annotations

import logging
import threading

from langchain_core.rate_limiters import InMemoryRateLimiter

logger = logging.getLogger(__name__)

# quota scope (lowercased "provider" or "provider/model") -> shared limiter.
# Guarded by _LOCK for the check-then-create sequence so concurrent workers
# never build two limiters for the same scope.
_LIMITERS: dict[str, InMemoryRateLimiter] = {}
_LOCK = threading.Lock()

# How often a blocked request re-checks the bucket. Small enough to keep
# latency low, large enough to avoid busy-spinning across many waiters.
_CHECK_EVERY_N_SECONDS = 0.1


def get_shared_rate_limiter(
    provider: str, requests_per_minute: float, model: str | None = None
) -> InMemoryRateLimiter:
    """Return the process-wide shared rate limiter for a quota scope.

    The scope is *provider*, narrowed to the ``provider/model`` pair when
    *model* is given. Created once per scope and reused for every subsequent
    call, so all LLM instances in that scope — across both think tiers and
    all batch worker threads — pace their requests through one token bucket
    capped at *requests_per_minute*.

    The first caller's rate wins; a later call with a different rate reuses the
    existing limiter (logging a warning) so a scope always has exactly one
    consistent limiter.

    Args:
        provider: Provider name (case-insensitive), e.g. ``"sensenova"``.
        requests_per_minute: Aggregate request cap. Must be > 0.
        model: Optional model name. When set, the limiter is scoped to the
            ``provider/model`` pair because plans like the SenseNova Token
            Plan price each model differently (per-model credit rates).

    Returns:
        A shared :class:`InMemoryRateLimiter` for the scope.

    Raises:
        ValueError: If *requests_per_minute* is not positive.
    """
    if requests_per_minute <= 0:
        raise ValueError(
            f"requests_per_minute must be positive, got {requests_per_minute!r}"
        )

    key = provider.strip().lower()
    if model:
        key = f"{key}/{model.strip().lower()}"
    with _LOCK:
        existing = _LIMITERS.get(key)
        if existing is not None:
            existing_rpm = existing.requests_per_second * 60.0
            if abs(existing_rpm - requests_per_minute) > 1e-9:
                logger.warning(
                    "Rate limiter for '%s' already set to %.3g rpm; "
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
            "Created shared rate limiter for '%s': %.3g rpm.",
            key,
            requests_per_minute,
        )
        return limiter


def reset_rate_limiters() -> None:
    """Clear all cached limiters. Intended for test isolation."""
    with _LOCK:
        _LIMITERS.clear()
