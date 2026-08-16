import datetime as _dt
import logging
import os
import re as _re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

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

# Configuration and routing logic
from .config import get_config
from .data_policy import (
    ToolPolicy,
    UnknownToolPolicyError,
    legacy_policy_for,
    normalized_dates,
    policy_for,
)
from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from .fred import get_macro_data as get_fred_macro_data
from .runtime_context import RuntimeDataContext, get_runtime_data_context

# Polymarket removed — prediction markets are US-only and inapplicable to A-shares.
# The get_prediction_markets method degraded via OPTIONAL_CATEGORIES → DATA_UNAVAILABLE sentinel.
from .smartmoney_vendor import (
    get_balance_sheet as get_smartmoney_balance_sheet,
    get_block_trade as get_smartmoney_block_trade,
    get_cashflow as get_smartmoney_cashflow,
    get_company_announcements as get_smartmoney_company_announcements,
    get_dragon_tiger as get_smartmoney_dragon_tiger,
    get_earnings_estimates as get_smartmoney_earnings_estimates,
    get_fund_flow as get_smartmoney_fund_flow,
    get_fundamentals as get_smartmoney_fundamentals,
    get_global_asset_data as get_smartmoney_global_asset_data,
    get_income_statement as get_smartmoney_income_statement,
    get_index_daily as get_smartmoney_index_daily,
    get_indicators as get_smartmoney_indicators,
    get_industry_valuation as get_smartmoney_industry_valuation,
    get_insider_transactions as get_smartmoney_insider_transactions,
    get_institutional_holdings as get_smartmoney_institutional_holdings,
    get_limit_up_down as get_smartmoney_limit_up_down,
    get_macro_indicators as get_smartmoney_macro_indicators,
    get_margin_trading as get_smartmoney_margin_trading,
    get_news as get_smartmoney_news,
    get_northbound_hold as get_smartmoney_northbound_hold,
    get_pledge_ratio as get_smartmoney_pledge_ratio,
    get_research_reports as get_smartmoney_research_reports,
    get_restricted_release as get_smartmoney_restricted_release,
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
    "prediction_markets": {
        "description": "Market-implied probabilities for forward-looking events",
        "tools": [
            "get_prediction_markets",
        ]
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
]

# Optional enrichment categories. These add macro/event context to the news
# analyst but are not core to a decision, so a vendor failure here degrades to a
# sentinel instead of aborting the run (a bad LLM-supplied indicator, a missing
# key, or a network blip should not crash an analysis over flavour data). Core
# categories (prices, fundamentals, news) still raise so a broken primary is loud.
OPTIONAL_CATEGORIES = {"macro_data", "prediction_markets", "research_opinion"}


VendorPayloadStatus = Literal["ok", "valid_empty", "partial", "stale"]
_VENDOR_PAYLOAD_STATUSES = frozenset({"ok", "valid_empty", "partial", "stale"})
_INFER_AS_OF_FROM_REQUEST = object()


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


@dataclass(frozen=True)
class VendorRouteResult:
    """Successful or graceful-degradation vendor result with provenance."""

    data: Any
    vendor: str | None
    diagnostic: VendorRouteDiagnostic | None = None


_ROUTE_DIAGNOSTICS: ContextVar[list[VendorRouteDiagnostic] | None] = ContextVar(
    "tradingagents_route_diagnostics", default=None
)
_ROUTE_CIRCUIT_BREAKERS: ContextVar[set[tuple[str, str]] | None] = ContextVar(
    "tradingagents_route_circuit_breakers", default=None
)


@contextmanager
def collect_route_diagnostics():
    """Collect route diagnostics for the current ticker/worker context."""
    token = _ROUTE_DIAGNOSTICS.set([])
    breaker_token = _ROUTE_CIRCUIT_BREAKERS.set(set())
    try:
        records = _ROUTE_DIAGNOSTICS.get()
        assert records is not None
        yield records
    finally:
        _ROUTE_CIRCUIT_BREAKERS.reset(breaker_token)
        _ROUTE_DIAGNOSTICS.reset(token)


def get_route_diagnostics() -> list[VendorRouteDiagnostic]:
    """Return a snapshot of diagnostics in the current context."""
    return list(_ROUTE_DIAGNOSTICS.get() or [])


def _is_provider_unavailable(exc: Exception) -> bool:
    """Whether a provider cannot serve this method until configuration changes."""
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
            else as_of
        ),
        reason=reason,
    )
    records = _ROUTE_DIAGNOSTICS.get()
    if records is not None:
        records.append(diagnostic)
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
    elif policy.date_policy == "calendar_window" and evidence_window_end is not None:
        if len(rewritten_args) >= 3:
            rewritten_args[2] = min(str(rewritten_args[2]), evidence_window_end)
        elif "end_date" in rewritten_kwargs:
            rewritten_kwargs["end_date"] = min(
                str(rewritten_kwargs["end_date"]), evidence_window_end
            )

    return tuple(rewritten_args), rewritten_kwargs

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
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "akshare": get_akshare_balance_sheet,
    },
    "get_cashflow": {
        "smartmoney_db": get_smartmoney_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "akshare": get_akshare_cashflow,
    },
    "get_income_statement": {
        "smartmoney_db": get_smartmoney_income_statement,
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "akshare": get_akshare_income_statement,
    },
    "get_industry_valuation": {
        "smartmoney_db": get_smartmoney_industry_valuation,
        "akshare": get_akshare_industry_valuation,
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
        "smartmoney_db": get_smartmoney_restricted_release,
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
            if "smartmoney_db" in vendor_chain:
                vendor_chain = ["smartmoney_db", "akshare"] + [
                    v for v in vendor_chain
                    if v not in ("smartmoney_db", "akshare")
                ]
            else:
                vendor_chain = ["akshare"] + [
                    v for v in vendor_chain if v != "akshare"
                ]

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
    return category in ("macro_data", "prediction_markets") or method in {
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
    symbol = args[0] if args else kwargs.get("symbol") or kwargs.get("ticker")
    policy, registered_policy = _request_policy(method)
    runtime_context = get_runtime_data_context()
    market = _request_market(policy, registered_policy, args, kwargs, runtime_context)

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

    if registered_policy:
        args, kwargs = _rewrite_policy_dates(
            method, policy, runtime_context, args, kwargs
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
            status = payload.status if payload is not None else "ok"
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
