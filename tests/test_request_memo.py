from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Event, Lock

import pytest


@dataclass
class _Diagnostic:
    status: str
    call_count: int = 1


@dataclass
class _Result:
    diagnostic: _Diagnostic


def _key():
    from tradingagents.dataflows.request_memo import RequestKey

    return RequestKey(
        method="get_news",
        ticker="AAPL",
        start_date="2026-08-01",
        end_date="2026-08-16",
        frozen_kwargs={},
        policy_version="v1",
    )


@pytest.mark.unit
def test_request_key_normalizes_equivalent_dates_and_nested_kwargs():
    from tradingagents.dataflows.request_memo import RequestKey

    first = RequestKey(
        method="get_news",
        ticker="AAPL",
        start_date=date(2026, 8, 1),
        end_date=datetime(2026, 8, 16, 23, 59, tzinfo=UTC),
        frozen_kwargs={
            "limit": 20,
            "filters": {"sources": ["wire", "filing"], "language": "en"},
        },
        policy_version="v1",
    )
    second = RequestKey(
        method="get_news",
        ticker=" aapl ",
        start_date="2026-08-01T00:00:00Z",
        end_date="2026-08-16",
        frozen_kwargs={
            "filters": {"language": "en", "sources": ("wire", "filing")},
            "limit": 20,
        },
        policy_version="v1",
    )

    assert first == second
    assert hash(first) == hash(second)


@pytest.mark.unit
def test_five_concurrent_callers_execute_resolver_once():
    from tradingagents.dataflows.request_memo import RequestMemo

    memo = RequestMemo()
    resolver_entered = Event()
    release_resolver = Event()
    counter_lock = Lock()
    attempts = 0
    result = _Result(_Diagnostic("ok"))

    def resolver():
        nonlocal attempts
        with counter_lock:
            attempts += 1
        resolver_entered.set()
        assert release_resolver.wait(timeout=5)
        return result

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(memo.resolve, _key(), resolver) for _ in range(5)]
        assert resolver_entered.wait(timeout=5)
        release_resolver.set()
        resolved = [future.result(timeout=5) for future in futures]

    assert attempts == 1
    assert all(item is result for item in resolved)
    assert result.diagnostic.call_count == 1


@pytest.mark.unit
@pytest.mark.parametrize("status", ["ok", "ok_fallback", "valid_empty", "not_applicable"])
def test_stable_outcomes_are_cached(status):
    from tradingagents.dataflows.request_memo import RequestMemo

    memo = RequestMemo()
    attempts = 0

    def resolver():
        nonlocal attempts
        attempts += 1
        return _Result(_Diagnostic(status))

    first = memo.resolve(_key(), resolver)
    second = memo.resolve(_key(), resolver)

    assert first is second
    assert attempts == 1
    assert first.diagnostic.call_count == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "status", ["partial", "stale", "no_data", "unavailable", "failed"]
)
def test_unstable_outcomes_are_not_cached(status):
    from tradingagents.dataflows.request_memo import RequestMemo

    memo = RequestMemo()
    attempts = 0

    def resolver():
        nonlocal attempts
        attempts += 1
        return _Result(_Diagnostic(status))

    first = memo.resolve(_key(), resolver)
    second = memo.resolve(_key(), resolver)

    assert first is not second
    assert attempts == 2
    assert first.diagnostic.call_count == 1
    assert second.diagnostic.call_count == 1


@pytest.mark.unit
def test_failed_first_call_is_removed_and_retried():
    from tradingagents.dataflows.request_memo import RequestMemo

    memo = RequestMemo()
    attempts = 0
    recovered = _Result(_Diagnostic("ok_fallback"))

    def resolver():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return recovered

    with pytest.raises(RuntimeError, match="temporary failure"):
        memo.resolve(_key(), resolver)

    assert memo.resolve(_key(), resolver) is recovered
    assert attempts == 2


@pytest.mark.unit
def test_waiting_callers_receive_same_exception_object_and_next_call_retries():
    from tradingagents.dataflows.request_memo import RequestMemo

    memo = RequestMemo()
    resolver_entered = Event()
    release_resolver = Event()
    boom = RuntimeError("shared failure")
    attempts = 0

    def failing_resolver():
        nonlocal attempts
        attempts += 1
        resolver_entered.set()
        assert release_resolver.wait(timeout=5)
        raise boom

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(memo.resolve, _key(), failing_resolver) for _ in range(5)
        ]
        assert resolver_entered.wait(timeout=5)
        release_resolver.set()
        errors = [future.exception(timeout=5) for future in futures]

    assert attempts == 1
    assert all(error is boom for error in errors)

    recovered = _Result(_Diagnostic("ok"))
    assert memo.resolve(_key(), lambda: recovered) is recovered


@pytest.mark.unit
def test_runtime_data_scope_initializes_restores_and_resets_request_memo():
    from tradingagents.dataflows.runtime_context import (
        get_request_memo,
        runtime_data_context_for,
        use_runtime_data_context,
    )

    outer_context = runtime_data_context_for("AAPL", "2026-08-16")
    inner_context = runtime_data_context_for("600519.SS", "2026-08-16")

    assert get_request_memo() is None
    with use_runtime_data_context(outer_context):
        outer_memo = get_request_memo()
        assert outer_memo is not None
        with use_runtime_data_context(inner_context):
            inner_memo = get_request_memo()
            assert inner_memo is not None
            assert inner_memo is not outer_memo
        assert get_request_memo() is outer_memo
    assert get_request_memo() is None
