"""Tests for the process-wide shared LLM rate limiters."""
from __future__ import annotations

import threading

import pytest

from tradingagents.llm_clients.rate_limit import (
    get_shared_rate_limiter,
    reset_rate_limiters,
)


@pytest.mark.unit
class TestSharedRateLimiter:
    def setup_method(self) -> None:
        reset_rate_limiters()

    def teardown_method(self) -> None:
        reset_rate_limiters()

    def test_same_provider_returns_shared_instance(self) -> None:
        a = get_shared_rate_limiter("sensenova", 15)
        b = get_shared_rate_limiter("sensenova", 15)
        assert a is b

    def test_provider_key_is_case_insensitive(self) -> None:
        a = get_shared_rate_limiter("SenseNova", 15)
        b = get_shared_rate_limiter("  sensenova ", 15)
        assert a is b

    def test_distinct_providers_get_distinct_limiters(self) -> None:
        a = get_shared_rate_limiter("sensenova", 15)
        b = get_shared_rate_limiter("modelscope", 15)
        assert a is not b

    def test_rpm_converted_to_requests_per_second(self) -> None:
        limiter = get_shared_rate_limiter("sensenova", 30)
        assert limiter.requests_per_second == pytest.approx(0.5)

    def test_first_rate_wins(self) -> None:
        # A provider always keeps one consistent limiter; a later differing rate
        # reuses the first instance rather than creating a second.
        first = get_shared_rate_limiter("sensenova", 15)
        second = get_shared_rate_limiter("sensenova", 60)
        assert second is first
        assert first.requests_per_second == pytest.approx(15 / 60)

    def test_shared_across_threads(self) -> None:
        # Concurrent batch workers must resolve to a single limiter instance.
        results: list[object] = []

        def grab() -> None:
            results.append(get_shared_rate_limiter("sensenova", 15))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len({id(r) for r in results}) == 1

    def test_non_positive_rpm_rejected(self) -> None:
        # Zero or negative rpm would create a broken limiter; reject early.
        for bad in (0, -1, 0.0):
            with pytest.raises(ValueError):
                get_shared_rate_limiter("sensenova", bad)
