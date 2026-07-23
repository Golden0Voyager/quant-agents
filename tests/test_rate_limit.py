"""Tests for the process-wide shared LLM rate limiters."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator

import pytest

from tradingagents.llm_clients.rate_limit import (
    _CHECK_EVERY_N_SECONDS,
    _LIMITERS,
    _LOCK,
    get_shared_rate_limiter,
    reset_rate_limiters,
)


@pytest.fixture(autouse=True)
def _clean_rate_limiters() -> Iterator[None]:
    """Reset the process-wide limiter cache before and after each test."""
    reset_rate_limiters()
    yield
    reset_rate_limiters()


@pytest.mark.unit
class TestBasicCreation:
    """Fundamental creation and caching behaviour."""

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
        """A provider always keeps one consistent limiter; a later differing rate
        reuses the first instance rather than creating a second."""
        first = get_shared_rate_limiter("sensenova", 15)
        second = get_shared_rate_limiter("sensenova", 60)
        assert second is first
        assert first.requests_per_second == pytest.approx(15 / 60)


@pytest.mark.unit
class TestValidation:
    """Input validation for requests_per_minute."""

    @pytest.mark.parametrize("bad", [0, -1, 0.0, -0.1, -100])
    def test_non_positive_rpm_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            get_shared_rate_limiter("sensenova", bad)




@pytest.mark.unit
class TestLogging:
    """Logging on creation and rate mismatch."""

    def test_info_logged_on_first_creation(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        get_shared_rate_limiter("openai", 30)
        assert any("Created shared rate limiter" in msg and "openai" in msg for msg in caplog.messages)

    def test_info_not_logged_on_reuse(self, caplog: pytest.LogCaptureFixture) -> None:
        get_shared_rate_limiter("openai", 30)
        caplog.clear()
        get_shared_rate_limiter("openai", 30)
        assert not any("Created shared rate limiter" in msg for msg in caplog.messages)

    def test_warning_on_rate_mismatch(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        get_shared_rate_limiter("openai", 30)
        get_shared_rate_limiter("openai", 60)
        assert any("already set to" in msg for msg in caplog.messages)

    def test_no_warning_when_rate_matches(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        get_shared_rate_limiter("openai", 30)
        get_shared_rate_limiter("openai", 30)
        assert not any("already set to" in msg for msg in caplog.messages)

    def test_warning_references_both_rates(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        get_shared_rate_limiter("openai", 30)
        get_shared_rate_limiter("openai", 60)
        assert any("30" in msg and "60" in msg for msg in caplog.messages)


@pytest.mark.unit
class TestInMemoryRateLimiterParameters:
    """Verify InMemoryRateLimiter is constructed with expected params."""

    def test_check_every_n_seconds_is_set(self) -> None:
        limiter = get_shared_rate_limiter("deepseek", 30)
        assert limiter.check_every_n_seconds == _CHECK_EVERY_N_SECONDS

    def test_max_bucket_size_is_one(self) -> None:
        limiter = get_shared_rate_limiter("deepseek", 30)
        assert limiter.max_bucket_size == 1

    def test_requests_per_second_precision(self) -> None:
        """Fractional RPM should be accurately converted."""
        limiter = get_shared_rate_limiter("deepseek", 1)
        assert limiter.requests_per_second == pytest.approx(1 / 60)

        limiter2 = get_shared_rate_limiter("deepseek2", 0.5)
        assert limiter2.requests_per_second == pytest.approx(0.5 / 60)


@pytest.mark.unit
class TestResetRateLimiters:
    """reset_rate_limiters behaviour."""

    def test_reset_returns_new_instance(self) -> None:
        a = get_shared_rate_limiter("sensenova", 15)
        reset_rate_limiters()
        b = get_shared_rate_limiter("sensenova", 15)
        assert a is not b

    def test_reset_clears_all_providers(self) -> None:
        a1 = get_shared_rate_limiter("sensenova", 15)
        a2 = get_shared_rate_limiter("openai", 30)
        reset_rate_limiters()
        b1 = get_shared_rate_limiter("sensenova", 15)
        b2 = get_shared_rate_limiter("openai", 30)
        assert a1 is not b1
        assert a2 is not b2

    def test_multiple_resets_safe(self) -> None:
        for _ in range(10):
            get_shared_rate_limiter("sensenova", 15)
            reset_rate_limiters()
        # Should not raise — idempotent
        assert get_shared_rate_limiter("sensenova", 15) is not None

    def test_reset_with_no_limiters_does_not_raise(self) -> None:
        # _LIMITERS is already empty
        reset_rate_limiters()  # should not raise
        reset_rate_limiters()  # second call also safe


@pytest.mark.unit
class TestThreadSafety:
    """Concurrent access patterns."""

    def test_shared_across_threads(self) -> None:
        """Concurrent batch workers must resolve to a single limiter instance."""
        results: list[object] = []

        def grab() -> None:
            results.append(get_shared_rate_limiter("sensenova", 15))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len({id(r) for r in results}) == 1

    def test_concurrent_distinct_providers(self) -> None:
        """Different providers being created concurrently should each get their own."""
        results: dict[str, list[object]] = {}
        _lock = threading.Lock()

        def grab(provider: str) -> None:
            limiter = get_shared_rate_limiter(provider, 15)
            with _lock:
                results.setdefault(provider, []).append(limiter)

        providers = [f"provider_{i}" for i in range(10)]
        threads = [threading.Thread(target=grab, args=(p,)) for p in providers]
        threads += [threading.Thread(target=grab, args=(p,)) for p in providers]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads for the same provider got the same instance
        for provider, limiters in results.items():
            assert len({id(r) for r in limiters}) == 1, f"{provider} not shared"
        # Different providers have different instances
        ids = {id(results[p][0]) for p in providers}
        assert len(ids) == len(providers)

    def test_concurrent_reset_and_get(self) -> None:
        """Simulate a race between reset and get in separate threads."""
        barrier = threading.Barrier(4)

        def getter() -> None:
            # Ensure all threads hit the barrier around the same time
            barrier.wait(timeout=5)
            for _ in range(50):
                get_shared_rate_limiter("race_provider", 15)

        def reseter() -> None:
            barrier.wait(timeout=5)
            for _ in range(10):
                reset_rate_limiters()

        threads = [threading.Thread(target=getter) for _ in range(3)]
        threads.append(threading.Thread(target=reseter))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_many_concurrent_get_first_calls(self) -> None:
        """Many threads calling get for the first time on the same provider
        must all resolve to a single limiter (no double-create race)."""
        results: list[object] = []
        _lock = threading.Lock()

        def first_get() -> None:
            limiter = get_shared_rate_limiter("race_provider", 15)
            with _lock:
                results.append(limiter)

        threads = [threading.Thread(target=first_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len({id(r) for r in results}) == 1


@pytest.mark.unit
class TestProviderKeyEdgeCases:
    """Edge cases for provider key normalisation."""

    def test_provider_with_unsual_characters(self) -> None:
        a = get_shared_rate_limiter("my-provider_v2!", 15)
        b = get_shared_rate_limiter("  My-Provider_V2!  ", 15)
        assert a is b

    def test_provider_with_unicode(self) -> None:
        a = get_shared_rate_limiter("阿里云", 15)
        b = get_shared_rate_limiter("  阿里云  ", 15)
        assert a is b

    def test_provider_trailing_whitespace_normalized(self) -> None:
        a = get_shared_rate_limiter("provider  ", 15)
        b = get_shared_rate_limiter("  PROVIDER", 15)
        assert a is b

    def test_long_provider_name(self) -> None:
        long_name = "a" * 1000
        a = get_shared_rate_limiter(long_name, 15)
        b = get_shared_rate_limiter(long_name.upper(), 15)
        assert a is b

    def test_provider_empty_after_strip(self) -> None:
        """A whitespace-only provider name becomes an empty key after .strip().lower()."""
        limiter = get_shared_rate_limiter("   ", 15)
        assert limiter is not None

    def test_provider_empty_after_strip_shared(self) -> None:
        a = get_shared_rate_limiter("   ", 15)
        b = get_shared_rate_limiter("\t\n ", 15)
        assert a is b


@pytest.mark.unit
class TestRateLimiterFunctionality:
    """Functional tests: the limiter actually paces requests."""

    def test_single_acquire_returns_quickly(self) -> None:
        """A single acquire at a high rate limit should return True quickly."""
        limiter = get_shared_rate_limiter("test_provider", 1_000_000)
        start = time.perf_counter()
        result = limiter.acquire(blocking=True)
        elapsed = time.perf_counter() - start
        assert result is True
        # High enough limit — should pass quickly (< 0.5s)
        assert elapsed < 0.5

    def test_multiple_acquires_dont_hang(self) -> None:
        """Multiple acquires at a reasonable rate should all succeed quickly."""
        limiter = get_shared_rate_limiter("test_provider", 60)
        start = time.perf_counter()
        results = [limiter.acquire(blocking=True) for _ in range(3)]
        elapsed = time.perf_counter() - start
        assert all(results)
        # 3 acquires at 60 rpm (1 rps) with bucket_size=1: each req waits ~1s
        # Allow some margin; should finish well under 5s
        assert elapsed < 5.0


@pytest.mark.unit
class TestModuleState:
    """Access to module internals for diagnostics."""

    def test_limiter_cache_is_dict(self) -> None:
        assert isinstance(_LIMITERS, dict)

    def test_lock_is_rlock_or_lock(self) -> None:
        assert isinstance(_LOCK, type(threading.Lock()))

    def test_cache_holds_limiters(self) -> None:
        limiter = get_shared_rate_limiter("sensenova", 15)
        assert _LIMITERS["sensenova"] is limiter

    def test_cache_key_is_lowercased(self) -> None:
        get_shared_rate_limiter("MixedCase", 15)
        assert "mixedcase" in _LIMITERS
        assert "MixedCase" not in _LIMITERS

    def test_reset_clears_cache_directly(self) -> None:
        get_shared_rate_limiter("sensenova", 15)
        assert len(_LIMITERS) == 1
        reset_rate_limiters()
        assert len(_LIMITERS) == 0
