from __future__ import annotations

import datetime as _dt
import logging
import os
import re as _re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import Any, Literal, cast

from tradingagents.market_context import Market, infer_market

# Import from vendor-specific modules
from .akshare_common import is_a_share_ticker
from .akshare_vendor import (
    get_balance_sheet as get_akshare_balance_sheet,
    get_block_trade as get_akshare_block_trade,
    get_cashflow as get_akshare_cashflow,
    get_company_announcements as get_akshare_company_announcements,
    get_company_announcements_cninfo as get_akshare_company_announcements_cninfo,
    get_dividend_history as get_akshare_dividend_history,
    get_dragon_tiger as get_akshare_dragon_tiger,
    get_earnings_estimates as get_akshare_earnings_estimates,
    get_fund_flow as get_akshare_fund_flow,
    get_fundamentals as get_akshare_fundamentals,
    get_income_statement as get_akshare_income_statement,
    get_indicators as get_akshare_indicators,
    get_industry_valuation as get_akshare_industry_valuation,
    get_insider_transactions as get_akshare_insider_transactions,
    get_institutional_holdings as get_akshare_institutional_holdings,
    get_macro_indicators as get_akshare_macro_indicators,
    get_margin_trading as get_akshare_margin_trading,
    get_news as get_akshare_news,
    get_northbound_hold as get_akshare_northbound_hold,
    get_pledge_ratio as get_akshare_pledge_ratio,
    get_research_reports as get_akshare_research_reports,
    get_restricted_release as get_akshare_restricted_release,
    get_sector_fund_flow as get_akshare_sector_fund_flow,
    get_shareholder_count as get_akshare_shareholder_count,
    get_stock_data as get_akshare_stock_data,
)
from .alpha_vantage import (
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_global_news as get_alpha_vantage_global_news,
    get_income_statement as get_alpha_vantage_income_statement,
    get_indicator as get_alpha_vantage_indicator,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_stock as get_alpha_vantage_stock,
)
from .cailianpress_vendor import (
    fetch_cailianpress_telegrams as get_cailianpress_telegrams,
)

# Configuration and routing logic
from .config import get_config
from .data_policy import (
    ToolPolicy,
    UnknownToolPolicyError,
    legacy_policy_for,
    normalized_dates,
    policy_for,
)
from .eastmoney_sentiment import (
    fetch_eastmoney_guba_sentiment,
    fetch_eastmoney_hot_keywords,
    fetch_eastmoney_hot_rank,
)
from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from .fred import get_macro_data as get_fred_macro_data
from .hithink_vendor import (
    get_anomaly_reason as get_hithink_anomaly_reason,
    get_auction_snapshot as get_hithink_auction_snapshot,
    get_balance_sheet as get_hithink_balance_sheet,
    get_cashflow as get_hithink_cashflow,
    get_dragon_tiger as get_hithink_dragon_tiger,
    get_hot_rank as get_hithink_hot_rank,
    get_income_statement as get_hithink_income_statement,
    get_indicators as get_hithink_indicators,
    get_limit_up_down as get_hithink_limit_up_down,
    get_short_term_benchmark as get_hithink_short_term_benchmark,
    get_valuation_snapshot as get_hithink_valuation_snapshot,
)
from .request_memo import RequestKey
from .runtime_context import (
    RuntimeDataContext,
    get_request_memo,
    get_runtime_data_context,
)

# Polymarket removed — prediction markets are US-only and inapplicable to A-shares.
# The get_prediction_markets method degraded via OPTIONAL_CATEGORIES → DATA_UNAVAILABLE sentinel.
from .smartmoney_vendor import (
    get_balance_sheet as get_smartmoney_balance_sheet,
    get_block_trade as get_smartmoney_block_trade,
    get_cashflow as get_smartmoney_cashflow,
    get_chip_distribution as get_smartmoney_chip_distribution,
    get_commodity_futures as get_smartmoney_commodity_futures,
    get_company_announcements as get_smartmoney_company_announcements,
    get_concept_board as get_smartmoney_concept_board,
    get_dragon_tiger as get_smartmoney_dragon_tiger,
    get_earnings_estimates as get_smartmoney_earnings_estimates,
    get_earnings_forecast as get_smartmoney_earnings_forecast,
    get_fund_flow as get_smartmoney_fund_flow,
    get_fundamentals as get_smartmoney_fundamentals,
    get_global_asset_data as get_smartmoney_global_asset_data,
    get_historical_valuation as get_smartmoney_historical_valuation,
    get_income_statement as get_smartmoney_income_statement,
    get_index_daily as get_smartmoney_index_daily,
    get_indicators as get_smartmoney_indicators,
    get_industry_valuation as get_smartmoney_industry_valuation,
    get_insider_transactions as get_smartmoney_insider_transactions,
    get_institutional_holdings as get_smartmoney_institutional_holdings,
    get_institutional_intelligence as get_smartmoney_institutional_intelligence,
    get_limit_up_down as get_smartmoney_limit_up_down,
    get_lithium_spot as get_smartmoney_lithium_spot,
    get_macro_indicators as get_smartmoney_macro_indicators,
    get_margin_trading as get_smartmoney_margin_trading,
    get_news as get_smartmoney_news,
    get_northbound_hold as get_smartmoney_northbound_hold,
    get_pledge_ratio as get_smartmoney_pledge_ratio,
    get_research_reports as get_smartmoney_research_reports,
    get_sector_fund_flow as get_smartmoney_sector_fund_flow,
    get_shareholder_count as get_smartmoney_shareholder_count,
    get_stock_data as get_smartmoney_stock_data,
)
from .tushare_vendor import (
    get_company_announcements as get_tushare_company_announcements,
    get_earnings_estimates as get_tushare_earnings_estimates,
    get_fund_flow as get_tushare_fund_flow,
    get_margin_trading as get_tushare_margin_trading,
    get_pledge_ratio as get_tushare_pledge_ratio,
)
from .y_finance import (
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_fundamentals as get_yfinance_fundamentals,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
    get_stock_stats_indicators_window,
    get_YFin_data_online,
)
from .yfinance_news import get_global_news_yfinance, get_news_yfinance

logger = logging.getLogger(__name__)


def _fetch_cailianpress_for_route(
    limit: int = 20, look_back_days: int | None = None
) -> str:
    """Adapt the public look-back argument without treating it as pagination."""
    del look_back_days
    return get_cailianpress_telegrams(limit=limit)


def _eastmoney_payload(data: str, subject: str) -> str | VendorPayload:
    """Translate Eastmoney prose placeholders into route-visible health states."""
    stripped = data.strip()
    degraded = bool(
        _re.search(r"<[^>]*(?:unavailable|no .+data|not found)[^>]*>", stripped, _re.I)
    )
    if degraded and stripped.startswith("<") and stripped.endswith(">"):
        return f"NO_DATA_AVAILABLE: Eastmoney returned no usable data for {subject}: {stripped}"
    if degraded:
        return VendorPayload(
            data=data,
            status="partial",
            reason="one or more Eastmoney enrichment sources were unavailable",
        )
    return data


def _fetch_eastmoney_hot_rank_for_route(ticker: str, limit: int = 20):
    return _eastmoney_payload(fetch_eastmoney_hot_rank(ticker, limit), ticker)


def _fetch_eastmoney_guba_for_route(ticker: str, limit: int = 10):
    return _eastmoney_payload(fetch_eastmoney_guba_sentiment(ticker, limit), ticker)


def _fetch_eastmoney_keywords_for_route(limit: int = 15):
    return _eastmoney_payload(fetch_eastmoney_hot_keywords(limit), "market hot keywords")

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data",
            "get_index_daily",
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators and market breadth",
        "tools": [
            "get_indicators",
            "get_fund_flow",
            "get_sector_fund_flow",
            "get_limit_up_down",
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement",
            "get_industry_valuation",
            "get_earnings_estimates",
            "get_shareholder_count",
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
            "get_restricted_release",
            "get_institutional_holdings",
            "get_northbound_hold",
        ]
    },
    "governance_risk": {
        "description": "Corporate governance and risk metrics",
        "tools": [
            "get_pledge_ratio",
            "get_company_announcements",
            "get_margin_trading",
            "get_dragon_tiger",
            "get_block_trade",
        ]
    },
    "shareholder_return": {
        "description": "Dividend and shareholder return data",
        "tools": [
            "get_dividend_history",
        ]
    },
    "research_opinion": {
        "description": "Analyst research reports and ratings",
        "tools": [
            "get_research_reports",
        ]
    },
    "macro_data": {
        "description": "Macroeconomic indicators (rates, inflation, labor, growth)",
        "tools": [
            "get_macro_indicators",
        ]
    },
    "commodity_data": {
        "description": "Commodity spot and futures prices (lithium, silver, base metals)",
        "tools": [
            "get_lithium_spot",
            "get_commodity_futures",
        ]
    },
    "prediction_markets": {
        "description": "Market-implied probabilities for forward-looking events",
        "tools": [
            "get_prediction_markets",
        ]
    },
    "analyst_enrichment": {
        "description": "Optional analyst enrichment and sentiment context",
        "tools": [
            "get_chip_distribution",
            "get_concept_board",
            "get_historical_valuation",
            "get_earnings_forecast",
            "get_institutional_intelligence",
            "get_cailianpress_telegrams",
            "fetch_eastmoney_hot_rank",
            "fetch_eastmoney_guba_sentiment",
            "fetch_eastmoney_hot_keywords",
            "get_anomaly_reason",
            "get_valuation_snapshot",
            "get_auction_snapshot",
            "get_short_term_benchmark",
        ],
    }
}

VENDOR_LIST = [
    "yfinance",
    "fred",

    "alpha_vantage",
    "akshare",
    "smartmoney_db",
    "cninfo",
    "tushare",
    # Local quant_core.db global_assets_bars (US stocks / crypto). Registered
    # under a distinct name because the router skips the ``smartmoney_db``
    # name for non-A-share tickers.
    "quant_db_global",
    "cailianpress",
    "eastmoney",
]

# Optional enrichment categories. These add macro/event context to the news
# analyst but are not core to a decision, so a vendor failure here degrades to a
# sentinel instead of aborting the run (a bad LLM-supplied indicator, a missing
# key, or a network blip should not crash an analysis over flavour data). Core
# categories (prices, fundamentals, news) still raise so a broken primary is loud.
OPTIONAL_CATEGORIES = {
    "macro_data",
    "prediction_markets",
    "research_opinion",
    "analyst_enrichment",
}


VendorPayloadStatus = Literal["ok", "valid_empty", "partial", "stale"]
_VENDOR_PAYLOAD_STATUSES = frozenset({"ok", "valid_empty", "partial", "stale"})
_INFER_AS_OF_FROM_REQUEST = object()
_TRANSPORT_EXCEPTION_NAMES = frozenset(
    {
        "ConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "NetworkError",
        "PoolTimeout",
        "ProtocolError",
        "ProxyError",
        "ReadError",
        "ReadTimeout",
        "RemoteDisconnected",
        "RequestError",
        "SSLError",
        "Timeout",
        "TimeoutError",
        "TransportError",
        "WriteError",
        "WriteTimeout",
    }
)


@dataclass(frozen=True)
class VendorPayload:
    """Explicit data and coverage metadata returned by policy-aware adapters."""

    data: Any
    status: VendorPayloadStatus = "ok"
    as_of: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _VENDOR_PAYLOAD_STATUSES:
            raise ValueError(f"Unsupported vendor payload status: {self.status}")


@dataclass(frozen=True)
class VendorRouteDiagnostic:
    """Explain how one logical data request was resolved."""

    method: str
    category: str
    status: str
    attempted_vendors: tuple[str, ...]
    selected_vendor: str | None
    as_of: str | None
    reason: str
    call_count: int = 1


@dataclass(frozen=True)
class VendorRouteResult:
    """Successful or graceful-degradation vendor result with provenance."""

    data: Any
    vendor: str | None
    diagnostic: VendorRouteDiagnostic | None = None


@dataclass
class _RouteDiagnosticCollector:
    """Collector-local aggregation; memoized results never own call counts."""

    records: list[VendorRouteDiagnostic] = field(default_factory=list)
    _indexes: dict[RequestKey, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    _STATUS_QUALITY = {
        "failed": 0,
        "unavailable": 1,
        "no_data": 2,
        "stale": 3,
        "partial": 4,
        "not_applicable": 5,
        "valid_empty": 5,
        "ok_fallback": 6,
        "ok": 7,
    }

    def record(
        self, diagnostic: VendorRouteDiagnostic, request_key: RequestKey | None = None
    ) -> None:
        with self._lock:
            if request_key is None:
                self.records.append(diagnostic)
                return
            index = self._indexes.get(request_key)
            if index is None:
                self._indexes[request_key] = len(self.records)
                self.records.append(replace(diagnostic, call_count=1))
                return
            current = self.records[index]
            if self._STATUS_QUALITY.get(diagnostic.status, -1) > self._STATUS_QUALITY.get(
                current.status, -1
            ):
                current = diagnostic
            self.records[index] = replace(
                current, call_count=self.records[index].call_count + 1
            )


_ROUTE_DIAGNOSTICS: ContextVar[_RouteDiagnosticCollector | None] = ContextVar(
    "tradingagents_route_diagnostics", default=None
)
_DEFER_MEMO_DIAGNOSTIC: ContextVar[bool] = ContextVar(
    "tradingagents_defer_memo_diagnostic", default=False
)
_ROUTE_CIRCUIT_BREAKERS: ContextVar[set[tuple[str, str]] | None] = ContextVar(
    "tradingagents_route_circuit_breakers", default=None
)


@contextmanager
def collect_route_diagnostics():
    """Collect route diagnostics for the current ticker/worker context."""
    collector = _RouteDiagnosticCollector()
    token = _ROUTE_DIAGNOSTICS.set(collector)
    breaker_token = _ROUTE_CIRCUIT_BREAKERS.set(set())
    try:
        yield collector.records
    finally:
        _ROUTE_CIRCUIT_BREAKERS.reset(breaker_token)
        _ROUTE_DIAGNOSTICS.reset(token)


def get_route_diagnostics() -> list[VendorRouteDiagnostic]:
    """Return a snapshot of diagnostics in the current context."""
    collector = _ROUTE_DIAGNOSTICS.get()
    return list(collector.records) if collector is not None else []


def _is_transport_error(exc: Exception) -> bool:
    """Classify typed network failures before inspecting provider messages."""
    if isinstance(exc, (ConnectionError, TimeoutError, VendorRateLimitError)):
        return True
    return any(
        cls.__name__ in _TRANSPORT_EXCEPTION_NAMES for cls in type(exc).__mro__
    )


def _is_provider_unavailable(exc: Exception) -> bool:
    """Whether a provider cannot serve this method until configuration changes."""
    if _is_transport_error(exc):
        return False
    if isinstance(exc, (VendorNotConfiguredError, PermissionError)):
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "permission",
            "forbidden",
            "unauthorized",
            "authentication",
            "invalid token",
            "invalid api key",
            "api key",
            "api_key",
            "schema",
        )
    )


def _should_circuit_break(vendor: str, exc: Exception) -> bool:
    """Identify failures that are stable for this vendor/method context."""
    return _is_provider_unavailable(exc)


def _route_as_of(method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    for key in ("as_of", "curr_date", "end_date", "trade_date"):
        value = kwargs.get(key)
        if value:
            return str(value)[:10]
    if len(args) > 1:
        return str(args[-1])[:10]
    if args:
        try:
            if policy_for(method).date_policy == "market_session":
                return str(args[0])[:10]
        except UnknownToolPolicyError:
            pass
    return None


def _route_result(
    *,
    data: Any,
    vendor: str | None,
    method: str,
    category: str,
    status: str,
    attempted_vendors: list[str],
    reason: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    as_of: str | None | object = _INFER_AS_OF_FROM_REQUEST,
) -> VendorRouteResult:
    diagnostic = VendorRouteDiagnostic(
        method=method,
        category=category,
        status=status,
        attempted_vendors=tuple(attempted_vendors),
        selected_vendor=vendor,
        as_of=(
            _route_as_of(method, args, kwargs)
            if as_of is _INFER_AS_OF_FROM_REQUEST
            else cast("str | None", as_of)
        ),
        reason=reason,
    )
    collector = _ROUTE_DIAGNOSTICS.get()
    if collector is not None and not _DEFER_MEMO_DIAGNOSTIC.get():
        collector.record(diagnostic)
    return VendorRouteResult(data, vendor, diagnostic)


def _request_policy(method: str) -> tuple[ToolPolicy, bool]:
    """Return a policy and whether it came from the strict registry."""
    try:
        return policy_for(method), True
    except UnknownToolPolicyError:
        return legacy_policy_for(method), False


def _request_market(
    policy: ToolPolicy,
    registered: bool,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    context: RuntimeDataContext | None,
) -> Market | None:
    if not registered:
        return context.market if context is not None else None
    if policy.date_policy == "market_session":
        return context.market if context is not None else None
    ticker = args[0] if args else kwargs.get("symbol") or kwargs.get("ticker")
    if isinstance(ticker, str):
        requested_market = infer_market(ticker)
        if requested_market != "UNKNOWN":
            return requested_market
    if context is not None:
        return context.market
    if not isinstance(ticker, str):
        return None
    return infer_market(ticker)


def _rewrite_policy_dates(
    method: str,
    policy: ToolPolicy,
    context: RuntimeDataContext | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Apply policy date anchors without widening the caller's evidence window."""
    if context is None:
        return args, kwargs

    rewritten_args = list(args)
    rewritten_kwargs = dict(kwargs)
    market_as_of, evidence_window_end = normalized_dates(method, context)

    if policy.date_policy == "market_session":
        if rewritten_args:
            rewritten_args[0] = market_as_of
        else:
            for key in ("trade_date", "curr_date", "as_of"):
                if key in rewritten_kwargs:
                    rewritten_kwargs[key] = market_as_of
                    break
    elif policy.date_policy == "latest_snapshot":
        schema = _METHOD_PARAMETER_SCHEMAS.get(method, ())
        date_names = ("curr_date", "as_of", "trade_date", "end_date")
        for index, (name, _default) in enumerate(schema):
            if name not in date_names or index >= len(rewritten_args):
                continue
            requested = rewritten_args[index]
            if requested is not None:
                normalized = _normalize_key_date(requested)
                if isinstance(normalized, str) and _date_like(normalized):
                    rewritten_args[index] = min(normalized[:10], market_as_of)
            break
        else:
            for key in date_names:
                requested = rewritten_kwargs.get(key)
                if requested is None:
                    continue
                normalized = _normalize_key_date(requested)
                if isinstance(normalized, str) and _date_like(normalized):
                    rewritten_kwargs[key] = min(normalized[:10], market_as_of)
                break
    elif policy.date_policy == "calendar_window" and evidence_window_end is not None:
        # Cap the window-END parameter only. The schema names it explicitly
        # (``end_date`` for ticker/start/end methods, ``curr_date`` for
        # window-back methods like get_global_news); blindly capping args[2]
        # would clobber a non-date third argument such as ``limit``.
        schema = _METHOD_PARAMETER_SCHEMAS.get(method, ())
        window_end_names = ("end_date", "curr_date")
        for index, (name, _default) in enumerate(schema):
            if name not in window_end_names or index >= len(rewritten_args):
                continue
            requested = rewritten_args[index]
            if requested is not None:
                normalized = _normalize_key_date(requested)
                if isinstance(normalized, str) and _date_like(normalized):
                    rewritten_args[index] = min(normalized[:10], evidence_window_end)
            break
        else:
            for key in window_end_names:
                requested = rewritten_kwargs.get(key)
                if requested is None:
                    continue
                normalized = _normalize_key_date(requested)
                if isinstance(normalized, str) and _date_like(normalized):
                    rewritten_kwargs[key] = min(normalized[:10], evidence_window_end)
                break
            else:
                # Schema-less legacy call: assume (symbol, start, end).
                if len(rewritten_args) >= 3:
                    rewritten_args[2] = min(str(rewritten_args[2]), evidence_window_end)

    return tuple(rewritten_args), rewritten_kwargs


def _date_like(value: Any) -> bool:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return True
    return isinstance(value, str) and bool(
        _re.match(r"^\d{4}-?\d{2}-?\d{2}(?:[T\s].*)?$", value.strip())
    )


def _normalize_key_date(value: Any) -> Any:
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    try:
        if "T" in candidate or " " in candidate:
            return _dt.datetime.fromisoformat(
                candidate.replace("Z", "+00:00")
            ).date().isoformat()
        return _dt.date.fromisoformat(candidate).isoformat()
    except ValueError:
        return value


_REQUIRED_PARAMETER = object()
_METHOD_PARAMETER_SCHEMAS: dict[str, tuple[tuple[str, Any], ...]] = {
    "get_stock_data": (
        ("symbol", _REQUIRED_PARAMETER),
        ("start_date", _REQUIRED_PARAMETER),
        ("end_date", _REQUIRED_PARAMETER),
    ),
    "get_index_daily": (
        ("index_code", _REQUIRED_PARAMETER),
        ("start_date", _REQUIRED_PARAMETER),
        ("end_date", _REQUIRED_PARAMETER),
    ),
    "get_indicators": (
        ("symbol", _REQUIRED_PARAMETER),
        ("indicator", _REQUIRED_PARAMETER),
        ("curr_date", _REQUIRED_PARAMETER),
        ("look_back_days", 30),
    ),
    "get_fund_flow": (("ticker", _REQUIRED_PARAMETER), ("curr_date", None)),
    "get_sector_fund_flow": (("sector_name", _REQUIRED_PARAMETER), ("ticker", None)),
    "get_limit_up_down": (("trade_date", _REQUIRED_PARAMETER),),
    "get_fundamentals": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_balance_sheet": (
        ("ticker", _REQUIRED_PARAMETER),
        ("freq", "quarterly"),
        ("curr_date", None),
    ),
    "get_cashflow": (
        ("ticker", _REQUIRED_PARAMETER),
        ("freq", "quarterly"),
        ("curr_date", None),
    ),
    "get_income_statement": (
        ("ticker", _REQUIRED_PARAMETER),
        ("freq", "quarterly"),
        ("curr_date", None),
    ),
    "get_industry_valuation": (("ticker", _REQUIRED_PARAMETER),),
    "get_chip_distribution": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_concept_board": (("ticker", _REQUIRED_PARAMETER),),
    "get_historical_valuation": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_earnings_forecast": (("ticker", _REQUIRED_PARAMETER),),
    "get_institutional_intelligence": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_cailianpress_telegrams": (
        ("limit", 20),
        ("look_back_days", None),
    ),
    "fetch_eastmoney_hot_rank": (
        ("ticker", _REQUIRED_PARAMETER),
        ("limit", 20),
    ),
    "fetch_eastmoney_guba_sentiment": (
        ("ticker", _REQUIRED_PARAMETER),
        ("limit", 10),
    ),
    "fetch_eastmoney_hot_keywords": (("limit", 15),),
    "get_anomaly_reason": (("ticker", _REQUIRED_PARAMETER),),
    "get_valuation_snapshot": (("ticker", _REQUIRED_PARAMETER),),
    "get_auction_snapshot": (("ticker", _REQUIRED_PARAMETER),),
    "get_short_term_benchmark": (),
    "get_earnings_estimates": (("ticker", _REQUIRED_PARAMETER),),
    "get_shareholder_count": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_news": (
        ("ticker", _REQUIRED_PARAMETER),
        ("start_date", _REQUIRED_PARAMETER),
        ("end_date", _REQUIRED_PARAMETER),
    ),
    "get_global_news": (
        ("curr_date", _REQUIRED_PARAMETER),
        ("look_back_days", None),
        ("limit", None),
    ),
    "get_insider_transactions": (("ticker", _REQUIRED_PARAMETER),),
    "get_company_announcements": (
        ("ticker", _REQUIRED_PARAMETER),
        ("start_date", _REQUIRED_PARAMETER),
        ("end_date", _REQUIRED_PARAMETER),
    ),
    "get_restricted_release": (
        ("ticker", _REQUIRED_PARAMETER),
        ("start_date", _REQUIRED_PARAMETER),
        ("end_date", _REQUIRED_PARAMETER),
    ),
    "get_institutional_holdings": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_northbound_hold": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_macro_indicators": (
        ("indicator", _REQUIRED_PARAMETER),
        ("curr_date", None),
        ("look_back_days", None),
    ),
    "get_lithium_spot": (("periods", 60),),
    "get_commodity_futures": (
        ("variety", _REQUIRED_PARAMETER),
        ("periods", 60),
    ),
    "get_pledge_ratio": (("ticker", _REQUIRED_PARAMETER),),
    "get_margin_trading": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_dragon_tiger": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_block_trade": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_dividend_history": (("ticker", _REQUIRED_PARAMETER),),
    "get_research_reports": (
        ("ticker", _REQUIRED_PARAMETER),
        ("curr_date", None),
    ),
    "get_prediction_markets": (
        ("topic", _REQUIRED_PARAMETER),
        ("limit", None),
    ),
}


def _parameter_aliases(name: str) -> tuple[str, ...]:
    if name in {"ticker", "symbol"}:
        return ("ticker", "symbol")
    if name == "curr_date":
        return ("curr_date", "as_of")
    if name == "trade_date":
        return ("trade_date", "curr_date", "as_of")
    if name == "end_date":
        return ("end_date", "evidence_window_end")
    return (name,)


def _canonicalize_route_call(
    method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any], int]:
    """Bind public logical parameters once for both memo keys and vendors."""
    schema = _METHOD_PARAMETER_SCHEMAS.get(method)
    if schema is None:
        return args, kwargs, len(args)

    residual_kwargs = dict(kwargs)
    canonical_args: list[Any] = []
    invocation_arg_count = len(args)
    for index, (name, default) in enumerate(schema):
        aliases = _parameter_aliases(name)
        supplied_aliases = [alias for alias in aliases if alias in residual_kwargs]
        has_positional = index < len(args)
        if has_positional and supplied_aliases:
            raise TypeError(f"{method} received multiple values for '{name}'")
        if len(supplied_aliases) > 1:
            joined = ", ".join(supplied_aliases)
            raise TypeError(f"{method} received conflicting aliases for '{name}': {joined}")
        if has_positional:
            value = args[index]
        elif supplied_aliases:
            value = residual_kwargs.pop(supplied_aliases[0])
            invocation_arg_count = max(invocation_arg_count, index + 1)
        elif default is not _REQUIRED_PARAMETER:
            value = default
            if default is not None:
                invocation_arg_count = max(invocation_arg_count, index + 1)
        else:
            raise TypeError(f"{method} missing required argument: '{name}'")
        canonical_args.append(value)

    canonical_args.extend(args[len(schema):])
    invocation_arg_count = max(invocation_arg_count, len(args))
    return tuple(canonical_args), residual_kwargs, invocation_arg_count


def _request_key(
    method: str,
    policy: ToolPolicy,
    registered_policy: bool,
    context: RuntimeDataContext,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> RequestKey:
    """Build a stable key after policy ticker/date normalization."""
    positional = list(args)
    residual_options = dict(kwargs)

    ticker_aliases = [
        residual_options.pop(name)
        for name in ("symbol", "ticker")
        if residual_options.get(name) is not None
    ]
    ticker: Any = ticker_aliases[0] if ticker_aliases else None
    if policy.date_policy == "market_session":
        ticker = context.ticker
    elif ticker is None and positional and not _date_like(positional[0]):
        ticker = positional.pop(0)
    if not isinstance(ticker, str):
        ticker = context.ticker
    normalized_ticker = ticker.strip().upper()
    if any(
        isinstance(alias, str) and alias.strip().upper() != normalized_ticker
        for alias in ticker_aliases[1:]
    ):
        residual_options["_ticker_aliases"] = ticker_aliases

    def pop_date_alias(names: tuple[str, ...]) -> Any:
        values = [
            residual_options.pop(name)
            for name in names
            if residual_options.get(name) is not None
        ]
        if len({_normalize_key_date(value) for value in values}) > 1:
            residual_options[f"_{names[0]}_aliases"] = values
        return values[0] if values else None

    start_date = pop_date_alias(("start_date", "trade_date", "curr_date", "as_of"))
    end_date = pop_date_alias(("end_date", "evidence_window_end"))
    if start_date is None:
        for index, value in enumerate(positional):
            if _date_like(value):
                start_date = positional.pop(index)
                break
    if end_date is None:
        for index, value in enumerate(positional):
            if _date_like(value):
                end_date = positional.pop(index)
                break
    if start_date is None and end_date is None and registered_policy:
        start_date, end_date = normalized_dates(method, context)

    return RequestKey(
        method=method,
        ticker=normalized_ticker,
        start_date=start_date,
        end_date=end_date,
        frozen_kwargs={"args": positional, "kwargs": residual_options},
        policy_version=context.policy_version,
    )

# Mapping of methods to their vendor-specific implementations
# method -> vendor -> implementation (a callable, or a list of callables
# tried in order). Any keeps the heterogeneous vendor signatures assignable.
VENDOR_METHODS: dict[str, dict[str, Any]] = {
    # core_stock_apis
    "get_stock_data": {
        "smartmoney_db": get_smartmoney_stock_data,
        "quant_db_global": get_smartmoney_global_asset_data,
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
        "akshare": get_akshare_stock_data,
    },
    "get_index_daily": {
        "smartmoney_db": get_smartmoney_index_daily,
    },
    # technical_indicators
    "get_indicators": {
        "smartmoney_db": get_smartmoney_indicators,
        "hithink": get_hithink_indicators,
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        "akshare": get_akshare_indicators,
    },
    "get_fund_flow": {
        "smartmoney_db": get_smartmoney_fund_flow,
        "akshare": get_akshare_fund_flow,
        "tushare": get_tushare_fund_flow,
    },
    "get_margin_trading": {
        "smartmoney_db": get_smartmoney_margin_trading,
        "akshare": get_akshare_margin_trading,
        "tushare": get_tushare_margin_trading,
    },
    "get_dragon_tiger": {
        "smartmoney_db": get_smartmoney_dragon_tiger,
        "hithink": get_hithink_dragon_tiger,
        "akshare": get_akshare_dragon_tiger,
    },
    "get_block_trade": {
        "smartmoney_db": get_smartmoney_block_trade,
        "akshare": get_akshare_block_trade,
    },
    "get_sector_fund_flow": {
        "smartmoney_db": get_smartmoney_sector_fund_flow,
        "akshare": get_akshare_sector_fund_flow,
    },
    "get_limit_up_down": {
        "smartmoney_db": get_smartmoney_limit_up_down,
        "hithink": get_hithink_limit_up_down,
    },
    "get_shareholder_count": {
        "smartmoney_db": get_smartmoney_shareholder_count,
        "akshare": get_akshare_shareholder_count,
    },
    # fundamental_data
    "get_fundamentals": {
        "smartmoney_db": get_smartmoney_fundamentals,
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "akshare": get_akshare_fundamentals,
    },
    "get_balance_sheet": {
        "smartmoney_db": get_smartmoney_balance_sheet,
        "hithink": get_hithink_balance_sheet,
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "akshare": get_akshare_balance_sheet,
    },
    "get_cashflow": {
        "smartmoney_db": get_smartmoney_cashflow,
        "hithink": get_hithink_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "akshare": get_akshare_cashflow,
    },
    "get_income_statement": {
        "smartmoney_db": get_smartmoney_income_statement,
        "hithink": get_hithink_income_statement,
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "akshare": get_akshare_income_statement,
    },
    "get_industry_valuation": {
        "smartmoney_db": get_smartmoney_industry_valuation,
        "akshare": get_akshare_industry_valuation,
    },
    # Optional analyst enrichments
    "get_chip_distribution": {
        "smartmoney_db": get_smartmoney_chip_distribution,
    },
    "get_concept_board": {
        "smartmoney_db": get_smartmoney_concept_board,
    },
    "get_historical_valuation": {
        "smartmoney_db": get_smartmoney_historical_valuation,
    },
    "get_earnings_forecast": {
        "smartmoney_db": get_smartmoney_earnings_forecast,
    },
    "get_institutional_intelligence": {
        "smartmoney_db": get_smartmoney_institutional_intelligence,
    },
    "get_cailianpress_telegrams": {
        "cailianpress": _fetch_cailianpress_for_route,
    },
    "fetch_eastmoney_hot_rank": {
        "hithink": get_hithink_hot_rank,
        "eastmoney": _fetch_eastmoney_hot_rank_for_route,
    },
    "fetch_eastmoney_guba_sentiment": {
        "eastmoney": _fetch_eastmoney_guba_for_route,
    },
    "fetch_eastmoney_hot_keywords": {
        "eastmoney": _fetch_eastmoney_keywords_for_route,
    },
    "get_anomaly_reason": {
        "hithink": get_hithink_anomaly_reason,
    },
    "get_valuation_snapshot": {
        "hithink": get_hithink_valuation_snapshot,
    },
    "get_auction_snapshot": {
        "hithink": get_hithink_auction_snapshot,
    },
    "get_short_term_benchmark": {
        "hithink": get_hithink_short_term_benchmark,
    },
    "get_earnings_estimates": {
        "smartmoney_db": get_smartmoney_earnings_estimates,
        "akshare": get_akshare_earnings_estimates,
        "tushare": get_tushare_earnings_estimates,
    },
    # news_data
    "get_news": {
        "smartmoney_db": get_smartmoney_news,
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        "akshare": get_akshare_news,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "smartmoney_db": get_smartmoney_insider_transactions,
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
        "akshare": get_akshare_insider_transactions,
    },
    "get_company_announcements": {
        "smartmoney_db": get_smartmoney_company_announcements,
        "cninfo": get_akshare_company_announcements_cninfo,
        "akshare": get_akshare_company_announcements,
        "tushare": get_tushare_company_announcements,
    },
    "get_restricted_release": {
        "akshare": get_akshare_restricted_release,
    },
    "get_institutional_holdings": {
        "smartmoney_db": get_smartmoney_institutional_holdings,
        "akshare": get_akshare_institutional_holdings,
    },
    "get_northbound_hold": {
        "smartmoney_db": get_smartmoney_northbound_hold,
        "akshare": get_akshare_northbound_hold,
    },
    "get_macro_indicators": {
        "smartmoney_db": get_smartmoney_macro_indicators,
        "akshare": get_akshare_macro_indicators,
        "fred": get_fred_macro_data,
    },
    # commodity_data — local archive only; akshare/hithink have no matching
    # interface for these tables, so there is deliberately no online fallback.
    "get_lithium_spot": {
        "smartmoney_db": get_smartmoney_lithium_spot,
    },
    "get_commodity_futures": {
        "smartmoney_db": get_smartmoney_commodity_futures,
    },
    # governance_risk (v2.2)
    "get_pledge_ratio": {
        "smartmoney_db": get_smartmoney_pledge_ratio,
        "akshare": get_akshare_pledge_ratio,
        "tushare": get_tushare_pledge_ratio,
    },
    # shareholder_return (v2.2)
    "get_dividend_history": {
        "akshare": get_akshare_dividend_history,
    },
    # research_opinion (v2.2)
    "get_research_reports": {
        "smartmoney_db": get_smartmoney_research_reports,
        "akshare": get_akshare_research_reports,
    },
    # prediction_markets — removed; prediction markets are US-only and
    # inapplicable to A-shares. The method degrades via OPTIONAL_CATEGORIES.
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str | None = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def _is_stale_research_data(result: str, max_days: int = 90) -> bool:
    """Check whether research-report text contains only dates older than *max_days*.

    Parses ``日期: YYYY-MM-DD`` lines from the formatted output.  If no
    parseable date is found, the data is treated as fresh (we assume the
    message has no date column rather than being provably stale).
    """
    today = _dt.date.today()
    dates = []
    for m in _re.finditer(r"日期:\s*(\d{4}-\d{2}-\d{2})", result):
        try:
            d = _dt.date.fromisoformat(m.group(1))
            dates.append(d)
        except ValueError:
            continue
    if not dates:
        return False  # no parseable date → can't prove staleness
    latest = max(dates)
    age = (today - latest).days
    if age > max_days:
        logger.info("Research report data is stale: latest=%s, age=%d days > limit=%d", latest, age, max_days)
        return True
    return False


def _is_failure_sentinel(result: str) -> bool:
    """Return True when *result* is a legacy prose failure string.

    This safety net ensures that any vendor implementation that still returns
    ``"Error ..."``, ``"No ... found"``, ``DATA_UNAVAILABLE`` or
    ``NO_DATA_AVAILABLE`` is treated as a failure and the router continues to
    the next vendor in the chain.
    """
    return bool(
        _re.search(
            r"^(Error|No .+ found|DATA_UNAVAILABLE|NO_DATA_AVAILABLE)",
            result.strip(),
        )
    )


def _build_vendor_chain(method: str, vendor_config: str, symbol: str | None) -> list[str]:
    """Build the ordered vendor chain for *method*.

    - Explicit vendor lists (anything other than ``"default"``) are respected
      verbatim, filtered to vendors that actually implement *method*.
    - The ``"default"`` sentinel enables A-share local-first ordering:
      ``smartmoney_db → akshare → others`` when the symbol is an A-share ticker.
    - ``DISABLE_YFINANCE_FALLBACK=1`` strips yfinance from A-share chains.
    """
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    explicit = [v for v in primary_vendors if v and v != "default"]
    if explicit:
        vendor_chain = [v for v in explicit if v in VENDOR_METHODS[method]]
        if not vendor_chain:
            raise ValueError(
                f"Configured vendor(s) {explicit} not available for '{method}'. "
                f"Available: {all_available_vendors}."
            )
    else:
        vendor_chain = list(all_available_vendors)

    # Tushare is an opt-in enrichment source.  It is appended only when the
    # dedicated setting is enabled; explicit tool/category chains remain
    # authoritative and can select it directly without this flag.
    config = get_config()
    tushare_enabled = bool(config.get("tushare_enabled")) or os.getenv("TUSHARE_ENABLED") == "1"
    if (
        tushare_enabled
        and "tushare" in all_available_vendors
        and "tushare" not in vendor_chain
    ):
        vendor_chain.append("tushare")

    is_ashare = isinstance(symbol, str) and is_a_share_ticker(symbol)
    if is_ashare and "akshare" in VENDOR_METHODS[method]:
        # Only the default chain gets local-first A-share promotion; explicit
        # user configuration is preserved verbatim.
        if vendor_config.strip() == "default":
            # hithink (official 同花顺 API) sits between the local DB and the
            # akshare online fallback when it implements the method.
            preferred = ("smartmoney_db", "hithink", "akshare")
            head = [v for v in preferred if v in vendor_chain]
            vendor_chain = head + [v for v in vendor_chain if v not in head]

        # DISABLE_YFINANCE_FALLBACK applies to all A-share chains regardless of
        # whether the vendor order was explicitly configured.
        if os.getenv("DISABLE_YFINANCE_FALLBACK") == "1":
            vendor_chain = [v for v in vendor_chain if v != "yfinance"]

    return vendor_chain


def _should_skip_ashare_filter(category: str, method: str) -> bool:
    """Return True when *method*'s first positional arg is not a ticker.

    These methods take sector names, dates, or indicator names as their first
    argument, so the non-A-share vendor filter must not strip akshare/smartmoney_db.
    """
    return category in ("macro_data", "commodity_data", "prediction_markets") or method in {
        "get_limit_up_down",
        "get_sector_fund_flow",
        "get_global_news",
    }


def _format_no_data_sentinel(
    last_no_data: NoMarketDataError,
    vendor_chain: list[str],
    method: str,
) -> str:
    """Format a canonical NO_DATA_AVAILABLE sentinel."""
    sym = last_no_data.symbol
    canonical = last_no_data.canonical
    resolved = "" if canonical == sym else f" (resolved to '{canonical}')"
    reason = f" ({last_no_data.detail})" if last_no_data.detail else ""
    tried = " → ".join(v for v in vendor_chain if v in VENDOR_METHODS[method])
    return (
        f"NO_DATA_AVAILABLE: No usable market data for '{sym}'{resolved} from "
        f"any configured vendor{reason}. Routing chain: {tried}. "
        f"The symbol may be invalid, delisted, not covered, or the vendor "
        f"returned stale data. Do not estimate or fabricate values — report "
        f"that data is unavailable for this symbol."
    )


def _format_optional_unavailable(
    category: str,
    first_error: Exception | None,
    method: str,
) -> str:
    """Format a graceful DATA_UNAVAILABLE sentinel for optional enrichment."""
    if first_error is not None:
        return (
            f"DATA_UNAVAILABLE: optional {category} could not be retrieved "
            f"({first_error}). Proceed without it; do not fabricate values."
        )
    return (
        f"DATA_UNAVAILABLE: '{method}' has no available data source. "
        f"Proceed without it; do not fabricate values."
    )


def _route_to_vendor_with_source(method: str, *args, **kwargs) -> VendorRouteResult:
    """Route a call and retain the vendor that produced the returned payload."""
    category = get_category_for_method(method)
    args, kwargs, invocation_arg_count = _canonicalize_route_call(
        method, args, kwargs
    )
    symbol = args[0] if args else kwargs.get("symbol") or kwargs.get("ticker")
    policy, registered_policy = _request_policy(method)
    runtime_context = get_runtime_data_context()
    market = _request_market(policy, registered_policy, args, kwargs, runtime_context)

    if registered_policy:
        args, kwargs = _rewrite_policy_dates(
            method, policy, runtime_context, args, kwargs
        )
    invocation_args = args[:invocation_arg_count]

    def resolver() -> VendorRouteResult:
        return _resolve_route_with_source(
            method=method,
            category=category,
            symbol=symbol,
            policy=policy,
            registered_policy=registered_policy,
            market=market,
            args=invocation_args,
            kwargs=kwargs,
        )

    memo = get_request_memo()
    if runtime_context is None or memo is None:
        return resolver()
    key = _request_key(
        method,
        policy,
        registered_policy,
        runtime_context,
        args,
        kwargs,
    )
    defer_token = _DEFER_MEMO_DIAGNOSTIC.set(True)
    try:
        result = memo.resolve(key, resolver)
    finally:
        _DEFER_MEMO_DIAGNOSTIC.reset(defer_token)
    collector = _ROUTE_DIAGNOSTICS.get()
    if collector is not None and result.diagnostic is not None:
        collector.record(result.diagnostic, key)
    return result


def _resolve_route_with_source(
    *,
    method: str,
    category: str,
    symbol: Any,
    policy: ToolPolicy,
    registered_policy: bool,
    market: Market | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> VendorRouteResult:
    """Resolve one complete vendor route after request normalization."""
    if (
        registered_policy
        and market is not None
        and market not in policy.applicable_markets
    ):
        return _route_result(
            data=(
                f"DATA_NOT_APPLICABLE: '{method}' does not apply to market "
                f"'{market}'. Proceed without it; do not fabricate values."
            ),
            vendor=None,
            method=method,
            category=category,
            status="not_applicable",
            attempted_vendors=[],
            reason=f"policy excludes market {market}",
            args=args,
            kwargs=kwargs,
        )

    if method not in VENDOR_METHODS:
        if category in OPTIONAL_CATEGORIES:
            logger.info(
                "Optional method '%s' (category=%s) has no configured vendor. "
                "Returning DATA_UNAVAILABLE.",
                method, category,
            )
            return _route_result(
                data=_format_optional_unavailable(category, None, method),
                vendor=None,
                method=method,
                category=category,
                status="unavailable",
                attempted_vendors=[],
                reason="no vendor implementation is configured",
                args=args,
                kwargs=kwargs,
            )
        raise ValueError(f"Method '{method}' not supported")

    vendor_config = get_vendor(category, method)
    vendor_chain = _build_vendor_chain(method, vendor_config, symbol)
    if registered_policy:
        vendor_chain = [
            vendor for vendor in vendor_chain if vendor in policy.allowed_vendors
        ]
        if not vendor_chain:
            return _route_result(
                data=(
                    f"DATA_UNAVAILABLE: No policy-allowed vendor is configured for "
                    f"'{method}'. Proceed without it; do not fabricate values."
                ),
                vendor=None,
                method=method,
                category=category,
                status="unavailable",
                attempted_vendors=[],
                reason="configured vendor chain contains no policy-allowed source",
                args=args,
                kwargs=kwargs,
            )

    last_no_data: NoMarketDataError | None = None
    first_unavailable_error: Exception | None = None
    first_failed_error: Exception | None = None

    # Track whether we are serving an A-share ticker for targeted logging
    is_ashare = isinstance(symbol, str) and is_a_share_ticker(symbol)

    # Skip A-share-only vendors for non-A-share tickers, except for methods
    # whose first argument is not a ticker (sector names, dates, indicators).
    skip_ashare_filter = _should_skip_ashare_filter(category, method)
    if not is_ashare and not skip_ashare_filter:
        filtered = [v for v in vendor_chain if v not in ("smartmoney_db", "akshare")]
        if not filtered:
            # All vendors removed — this method has no HK/US-capable fallback.
            logger.info(
                "Non-A-share ticker '%s': all configured vendors are A-share-only "
                "for method='%s'. Returning DATA_UNAVAILABLE.",
                symbol, method,
            )
            return _route_result(
                data=(
                    f"DATA_UNAVAILABLE: No global-market vendor configured for '{method}' "
                    f"with symbol '{symbol}'. This data source is A-share only. "
                    f"Proceed without it; do not fabricate values."
                ),
                vendor=None,
                method=method,
                category=category,
                status="unavailable",
                attempted_vendors=[],
                reason="all configured vendors are A-share only",
                args=args,
                kwargs=kwargs,
            )
        if filtered != vendor_chain:
            logger.info(
                "Non-A-share ticker '%s': skipping A-share-only vendors (smartmoney_db, akshare) "
                "for method='%s'. Chain: %s → %s",
                symbol, method, vendor_chain, filtered,
            )
        vendor_chain = filtered

    attempted_vendors: list[str] = []
    primary_vendor = vendor_chain[0]
    circuit_breakers = _ROUTE_CIRCUIT_BREAKERS.get()
    for vendor in vendor_chain:
        if circuit_breakers is not None and (vendor, method) in circuit_breakers:
            logger.info(
                "Skipping circuit-broken vendor %r for method=%s",
                vendor,
                method,
            )
            if first_unavailable_error is None:
                first_unavailable_error = VendorNotConfiguredError(
                    f"vendor {vendor!r} is disabled for {method} in this run context"
                )
            continue
        attempted_vendors.append(vendor)
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            result = impl_func(*args, **kwargs)
            payload = result if isinstance(result, VendorPayload) else None
            data = payload.data if payload is not None else result

            # Safety net: legacy prose failure strings should not be returned as
            # successful data. Treat them as no-data and keep falling back.
            if isinstance(data, str) and _is_failure_sentinel(data):
                logger.warning(
                    "Vendor %r returned a failure sentinel for %s; trying next vendor.",
                    vendor, method,
                )
                last_no_data = NoMarketDataError(
                    symbol or method, detail=data[:200]
                )
                continue

            # Warn when A-share data ultimately came from yfinance (data quality risk)
            if is_ashare and vendor == "yfinance":
                logger.warning(
                    "A-share symbol '%s' method='%s' fell back to yfinance. "
                    "Data source differs from AkShare; indicators may have "
                    "systematic bias due to different adjustment factors.",
                    symbol,
                    method,
                )
            # Freshness check: if smartmoney_db returned stale research reports,
            # fall through to akshare which fetches live from Eastmoney.
            if (
                method == "get_research_reports"
                and vendor == "smartmoney_db"
                and isinstance(data, str)
                and _is_stale_research_data(data)
            ):
                logger.info(
                    "Stale research reports from smartmoney_db for '%s'; "
                    "falling through to next vendor (akshare).",
                    symbol,
                )
                continue
            reason = "selected primary vendor"
            status: str = payload.status if payload is not None else "ok"
            if vendor != primary_vendor and status == "ok":
                status = "ok_fallback"
            if vendor != primary_vendor:
                reason = f"selected after fallback from {attempted_vendors[:-1]}"
            if payload is not None and payload.reason is not None:
                reason = payload.reason
            return _route_result(
                data=data,
                vendor=vendor,
                method=method,
                category=category,
                status=status,
                attempted_vendors=attempted_vendors,
                reason=reason,
                args=args,
                kwargs=kwargs,
                as_of=(
                    payload.as_of
                    if payload is not None
                    else _INFER_AS_OF_FROM_REQUEST
                ),
            )
        except VendorRateLimitError as exc:
            logger.warning("Vendor %r rate-limited for %s; trying next vendor.", vendor, method)
            if first_failed_error is None:
                first_failed_error = exc
            continue
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next vendor.", vendor, method)
            if first_unavailable_error is None:
                first_unavailable_error = e
            if circuit_breakers is not None:
                circuit_breakers.add((vendor, method))
            continue
        except NoMarketDataError as e:
            last_no_data = e  # No data here; another configured vendor may have it
            continue
        except Exception as exc:
            # For A-share tickers, elevate AkShare failure from debug -> warning
            if is_ashare and vendor == "akshare":
                logger.warning(
                    "AkShare failed for '%s' method='%s': %s(%s). "
                    "Will attempt fallback vendor next.",
                    symbol,
                    method,
                    type(exc).__name__,
                    exc,
                )
            else:
                # Don't let one vendor's failure crash the call when another can
                # serve it, but never swallow silently (#989).
                logger.warning("Vendor %r failed for %s: %s", vendor, method, exc)
            if _is_provider_unavailable(exc):
                if first_unavailable_error is None:
                    first_unavailable_error = exc
            elif first_failed_error is None:
                first_failed_error = exc
            if circuit_breakers is not None and _should_circuit_break(vendor, exc):
                circuit_breakers.add((vendor, method))
            continue  # Try next vendor in fallback chain

    # If any vendor reported "no data", the symbol is genuinely unavailable.
    # Return one explicit, instructive sentinel rather than a vendor-specific
    # empty string, so the agent reports "unavailable" instead of inventing a
    # value. This takes precedence over incidental fallback errors.
    if last_no_data is not None:
        provider_error = first_failed_error or first_unavailable_error
        if provider_error is not None:
            # A vendor also hit a real error; surface it in logs so the no-data
            # verdict can't hide a broken primary (network/auth/etc.).
            logger.warning(
                "Returning NO_DATA for %s, but a vendor errored earlier: %s",
                method, provider_error,
            )
        if first_failed_error is not None:
            status = "failed"
        elif first_unavailable_error is not None:
            status = "unavailable"
        elif registered_policy and policy.empty_semantics == "confirmed_empty":
            status = "valid_empty"
        else:
            status = "no_data"
        reason = last_no_data.detail or "all attempted vendors returned no usable rows"
        if provider_error is not None:
            reason = f"{reason}; provider error: {provider_error}"
        return _route_result(
            data=_format_no_data_sentinel(last_no_data, vendor_chain, method),
            vendor=None,
            method=method,
            category=category,
            status=status,
            attempted_vendors=attempted_vendors,
            reason=reason,
            args=args,
            kwargs=kwargs,
        )

    # No vendor returned data and none reported clean "no data" — surface the
    # first real error (e.g. the primary vendor's network failure). Optional
    # enrichment categories degrade to a sentinel instead, so flavour data can't
    # abort the run.
    provider_error = first_failed_error or first_unavailable_error
    if provider_error is not None:
        if category in OPTIONAL_CATEGORIES:
            logger.warning("Optional %s unavailable for %s: %s", category, method, provider_error)
            status = "failed" if first_failed_error is not None else "unavailable"
            return _route_result(
                data=_format_optional_unavailable(category, provider_error, method),
                vendor=None,
                method=method,
                category=category,
                status=status,
                attempted_vendors=attempted_vendors,
                reason=str(provider_error),
                args=args,
                kwargs=kwargs,
            )
        raise provider_error

    logger.error("No available vendor for method='%s' symbol='%s'", method, symbol)
    raise RuntimeError(f"No available vendor for '{method}'")


def route_to_vendor_with_source(method: str, *args, **kwargs) -> VendorRouteResult:
    """Route a method call and return its payload plus selected vendor name."""
    return _route_to_vendor_with_source(method, *args, **kwargs)


def route_to_vendor(method: str, *args, **kwargs):
    """Route a method call while preserving the legacy payload-only interface."""
    return _route_to_vendor_with_source(method, *args, **kwargs).data
