"""Per-runtime request keys and thread-safe single-flight resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set as AbstractSet
from dataclasses import dataclass, field
from datetime import date, datetime
from threading import Event, Lock
from typing import Any, Generic, TypeVar

_CACHEABLE_STATUSES = frozenset(
    {"ok", "ok_fallback", "valid_empty", "not_applicable"}
)
_MISSING = object()

T = TypeVar("T")


def _normalize_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return value

    candidate = value.strip()
    try:
        if "T" in candidate or " " in candidate:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return value


def _freeze(value: Any) -> Any:
    normalized = _normalize_date(value)
    if normalized is not value:
        return normalized
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                ((_freeze(key), _freeze(item)) for key, item in value.items()),
                key=repr,
            )
        )
    if isinstance(value, AbstractSet) and not isinstance(value, (str, bytes)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RequestKey:
    """Hashable identity for one normalized logical data request."""

    method: str
    ticker: str
    start_date: Any
    end_date: Any
    frozen_kwargs: Any
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", self.ticker.strip().upper())
        object.__setattr__(self, "start_date", _normalize_date(self.start_date))
        object.__setattr__(self, "end_date", _normalize_date(self.end_date))
        object.__setattr__(self, "frozen_kwargs", _freeze(self.frozen_kwargs))


@dataclass
class _Flight(Generic[T]):  # noqa: UP046 - package supports Python 3.10+
    event: Event = field(default_factory=Event)
    result: T | object = _MISSING
    exception: BaseException | None = None


class RequestMemo(Generic[T]):  # noqa: UP046 - package supports Python 3.10+
    """Resolve each in-flight key once and retain only stable outcomes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cache: dict[RequestKey, T] = {}
        self._in_flight: dict[RequestKey, _Flight[T]] = {}

    def resolve(self, key: RequestKey, resolver: Callable[[], T]) -> T:
        with self._lock:
            cached = self._cache.get(key, _MISSING)
            if cached is not _MISSING:
                self._record_additional_call(cached)
                return cached  # type: ignore[return-value]

            flight = self._in_flight.get(key)
            if flight is None:
                flight = _Flight()
                self._in_flight[key] = flight
                owner = True
            else:
                owner = False

        if owner:
            try:
                result = resolver()
            except BaseException as exc:
                with self._lock:
                    flight.exception = exc
                    self._in_flight.pop(key, None)
                    flight.event.set()
                raise

            with self._lock:
                flight.result = result
                if self._status(result) in _CACHEABLE_STATUSES:
                    self._cache[key] = result
                self._in_flight.pop(key, None)
                flight.event.set()
            return result

        flight.event.wait()
        with self._lock:
            if flight.exception is not None:
                raise flight.exception
            if flight.result is _MISSING:
                raise RuntimeError("Request flight completed without a result")
            self._record_additional_call(flight.result)
            return flight.result  # type: ignore[return-value]

    @staticmethod
    def _status(result: Any) -> str | None:
        diagnostic = getattr(result, "diagnostic", None)
        return getattr(diagnostic, "status", None)

    @staticmethod
    def _record_additional_call(result: Any) -> None:
        diagnostic = getattr(result, "diagnostic", None)
        if diagnostic is not None and hasattr(diagnostic, "call_count"):
            diagnostic.call_count += 1
