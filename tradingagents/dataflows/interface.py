import datetime as _dt
import logging
import os
import re as _re
from dataclasses import dataclass
from typing import Any

# Import from vendor-specific modules
from .akshare_common import is_a_share_ticker
from .akshare_vendor import (
    get_balance_sheet as get_akshare_balance_sheet,
    get_block_trade as get_akshare_block_trade,
    get_cashflow as get_akshare_cashflow,
    get_company_announcements as get_akshare_company_announcements,
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
from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from .fred import get_macro_data as get_fred_macro_data

# Polymarket removed — prediction markets are US-only and inapplicable to A-shares.
# The get_prediction_markets method degraded via OPTIONAL_CATEGORIES → DATA_UNAVAILABLE sentinel.
from .smartmoney_vendor import (
    get_balance_sheet as get_smartmoney_balance_sheet,
    get_block_trade as get_smartmoney_block_trade,
    get_cashflow as get_smartmoney_cashflow,
    get_chip_distribution as get_smartmoney_chip_distribution,
    get_company_announcements as get_smartmoney_company_announcements,
    get_concept_board as get_smartmoney_concept_board,
    get_dragon_tiger as get_smartmoney_dragon_tiger,
    get_earnings_estimates as get_smartmoney_earnings_estimates,
    get_earnings_forecast as get_smartmoney_earnings_forecast,
    get_fund_flow as get_smartmoney_fund_flow,
    get_fundamentals as get_smartmoney_fundamentals,
    get_historical_valuation as get_smartmoney_historical_valuation,
    get_income_statement as get_smartmoney_income_statement,
    get_index_daily as get_smartmoney_index_daily,
    get_indicators as get_smartmoney_indicators,
    get_industry_valuation as get_smartmoney_industry_valuation,
    get_insider_transactions as get_smartmoney_insider_transactions,
    get_institution_survey as get_smartmoney_institution_survey,
    get_institutional_holdings as get_smartmoney_institutional_holdings,
    get_institutional_intelligence as get_smartmoney_institutional_intelligence,
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
]

# Optional enrichment categories. These add macro/event context to the news
# analyst but are not core to a decision, so a vendor failure here degrades to a
# sentinel instead of aborting the run (a bad LLM-supplied indicator, a missing
# key, or a network blip should not crash an analysis over flavour data). Core
# categories (prices, fundamentals, news) still raise so a broken primary is loud.
OPTIONAL_CATEGORIES = {"macro_data", "prediction_markets", "research_opinion"}


@dataclass(frozen=True)
class VendorRouteResult:
    """Successful or graceful-degradation vendor result with provenance."""

    data: Any
    vendor: str | None

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "smartmoney_db": get_smartmoney_stock_data,
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
    },
    "get_margin_trading": {
        "smartmoney_db": get_smartmoney_margin_trading,
        "akshare": get_akshare_margin_trading,
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
        "akshare": get_akshare_company_announcements,
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

def get_vendor(category: str, method: str = None) -> str:
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
    vendor_config = get_vendor(category, method)
    symbol = args[0] if args else kwargs.get("symbol") or kwargs.get("ticker")

    if method not in VENDOR_METHODS:
        if category in OPTIONAL_CATEGORIES:
            logger.info(
                "Optional method '%s' (category=%s) has no configured vendor. "
                "Returning DATA_UNAVAILABLE.",
                method, category,
            )
            return VendorRouteResult(
                _format_optional_unavailable(category, None, method), None
            )
        raise ValueError(f"Method '{method}' not supported")

    vendor_chain = _build_vendor_chain(method, vendor_config, symbol)

    last_no_data: NoMarketDataError | None = None
    first_error: Exception | None = None

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
            return VendorRouteResult(
                (
                    f"DATA_UNAVAILABLE: No global-market vendor configured for '{method}' "
                    f"with symbol '{symbol}'. This data source is A-share only. "
                    f"Proceed without it; do not fabricate values."
                ),
                None,
            )
        if filtered != vendor_chain:
            logger.info(
                "Non-A-share ticker '%s': skipping A-share-only vendors (smartmoney_db, akshare) "
                "for method='%s'. Chain: %s → %s",
                symbol, method, vendor_chain, filtered,
            )
        vendor_chain = filtered

    for vendor in vendor_chain:
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            result = impl_func(*args, **kwargs)

            # Safety net: legacy prose failure strings should not be returned as
            # successful data. Treat them as no-data and keep falling back.
            if isinstance(result, str) and _is_failure_sentinel(result):
                logger.warning(
                    "Vendor %r returned a failure sentinel for %s; trying next vendor.",
                    vendor, method,
                )
                last_no_data = NoMarketDataError(
                    symbol or method, detail=result[:200]
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
                and _is_stale_research_data(result)
            ):
                logger.info(
                    "Stale research reports from smartmoney_db for '%s'; "
                    "falling through to next vendor (akshare).",
                    symbol,
                )
                continue
            return VendorRouteResult(result, vendor)
        except VendorRateLimitError as exc:
            logger.warning("Vendor %r rate-limited for %s; trying next vendor.", vendor, method)
            if first_error is None:
                first_error = exc
            continue
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next vendor.", vendor, method)
            if first_error is None:
                first_error = e
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
            if first_error is None:
                first_error = exc
            continue  # Try next vendor in fallback chain

    # If any vendor reported "no data", the symbol is genuinely unavailable.
    # Return one explicit, instructive sentinel rather than a vendor-specific
    # empty string, so the agent reports "unavailable" instead of inventing a
    # value. This takes precedence over incidental fallback errors.
    if last_no_data is not None:
        if first_error is not None:
            # A vendor also hit a real error; surface it in logs so the no-data
            # verdict can't hide a broken primary (network/auth/etc.).
            logger.warning(
                "Returning NO_DATA for %s, but a vendor errored earlier: %s",
                method, first_error,
            )
        return VendorRouteResult(
            _format_no_data_sentinel(last_no_data, vendor_chain, method), None
        )

    # No vendor returned data and none reported clean "no data" — surface the
    # first real error (e.g. the primary vendor's network failure). Optional
    # enrichment categories degrade to a sentinel instead, so flavour data can't
    # abort the run.
    if first_error is not None:
        if category in OPTIONAL_CATEGORIES:
            logger.warning("Optional %s unavailable for %s: %s", category, method, first_error)
            return VendorRouteResult(
                _format_optional_unavailable(category, first_error, method), None
            )
        raise first_error

    logger.error("No available vendor for method='%s' symbol='%s'", method, symbol)
    raise RuntimeError(f"No available vendor for '{method}'")


def route_to_vendor_with_source(method: str, *args, **kwargs) -> VendorRouteResult:
    """Route a method call and return its payload plus selected vendor name."""
    return _route_to_vendor_with_source(method, *args, **kwargs)


def route_to_vendor(method: str, *args, **kwargs):
    """Route a method call while preserving the legacy payload-only interface."""
    return _route_to_vendor_with_source(method, *args, **kwargs).data
