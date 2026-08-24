"""Per-run market and date context for data requests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from tradingagents.market_context import (
    AnalysisDates,
    Market,
    infer_market,
    resolve_analysis_dates,
)

from .request_memo import RequestMemo


@dataclass(frozen=True)
class RuntimeDataContext:
    """Canonical runtime identity shared by every request in one graph run."""

    ticker: str
    market: Market
    dates: AnalysisDates
    policy_version: str


_RUNTIME_DATA_CONTEXT: ContextVar[RuntimeDataContext | None] = ContextVar(
    "tradingagents_runtime_data_context", default=None
)
_REQUEST_MEMO: ContextVar[RequestMemo | None] = ContextVar(
    "tradingagents_request_memo", default=None
)


def runtime_data_context_for(
    ticker: str, analysis_date: str, *, policy_version: str = "v1"
) -> RuntimeDataContext:
    """Resolve the one immutable context for a canonical ticker and report day."""
    return RuntimeDataContext(
        ticker=ticker,
        market=infer_market(ticker),
        dates=resolve_analysis_dates(ticker, analysis_date),
        policy_version=policy_version,
    )


def get_runtime_data_context() -> RuntimeDataContext | None:
    """Return the context for this thread/task, if a graph run established one."""
    return _RUNTIME_DATA_CONTEXT.get()


def get_request_memo() -> RequestMemo | None:
    """Return the request memo owned by the active runtime data scope."""
    return _REQUEST_MEMO.get()


@contextmanager
def use_runtime_data_context(context: RuntimeDataContext) -> Iterator[RuntimeDataContext]:
    """Scope data context to one graph run without leaking to another worker."""
    token = _RUNTIME_DATA_CONTEXT.set(context)
    memo_token = _REQUEST_MEMO.set(RequestMemo())
    try:
        yield context
    finally:
        _REQUEST_MEMO.reset(memo_token)
        _RUNTIME_DATA_CONTEXT.reset(token)
