from __future__ import annotations

import threading

import pytest

from tradingagents.dataflows.runtime_context import (
    RuntimeDataContext,
    get_runtime_data_context,
    runtime_data_context_for,
    use_runtime_data_context,
)
from tradingagents.graph.propagation import Propagator
from tradingagents.market_context import AnalysisDates


@pytest.mark.unit
def test_runtime_context_is_unset_outside_a_run():
    assert get_runtime_data_context() is None


@pytest.mark.unit
def test_runtime_context_restores_the_previous_value_after_scope_exit():
    outer = runtime_data_context_for("AAPL", "2026-08-16")
    inner = runtime_data_context_for("600519.SS", "2026-08-16")

    with use_runtime_data_context(outer):
        assert get_runtime_data_context() == outer
        with use_runtime_data_context(inner):
            assert get_runtime_data_context() == inner
        assert get_runtime_data_context() == outer

    assert get_runtime_data_context() is None


@pytest.mark.unit
def test_thread_scoped_contexts_do_not_leak_tickers_or_dates():
    barrier = threading.Barrier(2)
    results: list[RuntimeDataContext | None] = []
    lock = threading.Lock()

    def read_context(ticker: str, analysis_date: str) -> None:
        context = runtime_data_context_for(ticker, analysis_date)
        with use_runtime_data_context(context):
            barrier.wait()
            with lock:
                results.append(get_runtime_data_context())

    threads = [
        threading.Thread(target=read_context, args=("600519.SS", "2026-08-16")),
        threading.Thread(target=read_context, args=("AAPL", "2026-08-17")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(results) == {
        RuntimeDataContext(
            ticker="600519.SS",
            market="XSHG",
            dates=AnalysisDates("2026-08-16", "2026-08-14", "2026-08-16"),
            policy_version="v1",
        ),
        RuntimeDataContext(
            ticker="AAPL",
            market="XNYS",
            dates=AnalysisDates("2026-08-17", "2026-08-17", "2026-08-17"),
            policy_version="v1",
        ),
    }


@pytest.mark.unit
def test_initial_state_carries_market_and_dates_with_checkpoint_safe_defaults():
    state = Propagator().create_initial_state("600519.SS", "2026-08-16")

    assert state["market"] == "XSHG"
    assert state["analysis_dates"] == {
        "analysis_date": "2026-08-16",
        "market_as_of_date": "2026-08-14",
        "evidence_window_end": "2026-08-16",
    }
