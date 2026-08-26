"""Central data-request policy registry.

``normalized_dates`` returns policy anchors, not rewritten vendor arguments.
Task 3 uses these anchors while preserving a caller's original start date and
only replacing or capping the relevant date/end-date argument.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from tradingagents.market_context import Market

from .runtime_context import RuntimeDataContext

logger = logging.getLogger(__name__)

DatePolicy = Literal["market_session", "calendar_window", "latest_snapshot"]
EmptySemantics = Literal["confirmed_empty", "coverage_gap"]
Impact = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ToolPolicy:
    """Central semantics and capability boundary for one logical tool method."""

    applicable_markets: frozenset[Market]
    date_policy: DatePolicy
    empty_semantics: EmptySemantics
    impact: Impact
    allowed_vendors: tuple[str, ...]


class UnknownToolPolicyError(KeyError):
    """Raised when a production caller omitted a required data policy."""


_ALL_MARKETS: frozenset[Market] = frozenset({"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"})
_POLICIES: dict[str, ToolPolicy] = {
    "get_stock_data": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="calendar_window",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=(
            "smartmoney_db",
            "quant_db_global",
            "alpha_vantage",
            "yfinance",
            "akshare",
        ),
    ),
    # Cross-market fundamentals and indicators keep the full market set: the
    # vendors themselves decide per-ticker coverage (akshare/smartmoney_db for
    # A-shares, yfinance/alpha_vantage for US/HK), same convention as
    # get_stock_data. UNKNOWN stays applicable so non-US/HK/CN tickers still
    # attempt yfinance instead of degrading to not_applicable.
    "get_fundamentals": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "alpha_vantage", "yfinance", "akshare"),
    ),
    "get_balance_sheet": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "hithink", "alpha_vantage", "yfinance", "akshare"),
    ),
    "get_cashflow": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "hithink", "alpha_vantage", "yfinance", "akshare"),
    ),
    "get_income_statement": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "hithink", "alpha_vantage", "yfinance", "akshare"),
    ),
    "get_indicators": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "hithink", "alpha_vantage", "yfinance", "akshare"),
    ),
    # Global news takes a window end (curr_date) as its first argument rather
    # than a ticker, so market resolution falls back to the runtime context or
    # UNKNOWN; keep UNKNOWN applicable for direct callers without a context.
    "get_global_news": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="calendar_window",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("yfinance", "alpha_vantage"),
    ),
    "get_insider_transactions": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("smartmoney_db", "alpha_vantage", "yfinance", "akshare"),
    ),
    # A-share-only coverage: both vendors read Chinese institutional data.
    "get_institutional_holdings": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    # Macro context is market-agnostic enrichment (China via smartmoney_db /
    # akshare, US via fred); applicable wherever the industry analyst runs.
    "get_macro_indicators": ToolPolicy(
        applicable_markets=_ALL_MARKETS,
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare", "fred"),
    ),
    "get_limit_up_down": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="market_session",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "hithink"),
    ),
    "get_restricted_release": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="calendar_window",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("akshare",),
    ),
    "get_news": ToolPolicy(
        applicable_markets=frozenset({"XSHG", "XHKG", "XNYS", "CRYPTO"}),
        date_policy="calendar_window",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "alpha_vantage", "yfinance", "akshare"),
    ),
    "get_company_announcements": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="calendar_window",
        empty_semantics="confirmed_empty",
        impact="high",
        allowed_vendors=("smartmoney_db", "cninfo", "akshare", "tushare"),
    ),
    "get_northbound_hold": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    "get_pledge_ratio": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare", "tushare"),
    ),
    "get_margin_trading": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare", "tushare"),
    ),
    "get_dragon_tiger": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("smartmoney_db", "hithink", "akshare"),
    ),
    "get_block_trade": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    "get_fund_flow": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db", "akshare", "tushare"),
    ),
    "get_sector_fund_flow": ToolPolicy(
        # The public legacy call accepts a Chinese sector name rather than a
        # ticker, so direct callers without runtime context resolve UNKNOWN.
        # Keep that explicit compatibility path while still excluding XHKG.
        applicable_markets=frozenset({"XSHG", "UNKNOWN"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    "get_index_daily": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="calendar_window",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db",),
    ),
    "get_industry_valuation": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    "get_earnings_estimates": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare", "tushare"),
    ),
    "get_shareholder_count": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    "get_dividend_history": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("akshare",),
    ),
    "get_research_reports": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("smartmoney_db", "akshare"),
    ),
    # Optional A-share enrichments use the same routed capability source as
    # core tools, including memoization and diagnostics.
    "get_chip_distribution": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="high",
        allowed_vendors=("smartmoney_db",),
    ),
    "get_concept_board": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db",),
    ),
    "get_historical_valuation": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db",),
    ),
    "get_earnings_forecast": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="medium",
        allowed_vendors=("smartmoney_db",),
    ),
    "get_institutional_intelligence": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("smartmoney_db",),
    ),
    "get_cailianpress_telegrams": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="confirmed_empty",
        impact="low",
        allowed_vendors=("cailianpress",),
    ),
    "fetch_eastmoney_hot_rank": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("hithink", "eastmoney"),
    ),
    "fetch_eastmoney_guba_sentiment": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("eastmoney",),
    ),
    "fetch_eastmoney_hot_keywords": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="low",
        allowed_vendors=("eastmoney",),
    ),
    "get_anomaly_reason": ToolPolicy(
        applicable_markets=frozenset({"XSHG"}),
        date_policy="latest_snapshot",
        empty_semantics="coverage_gap",
        impact="medium",
        allowed_vendors=("hithink",),
    ),
}

_LEGACY_POLICY = ToolPolicy(
    applicable_markets=_ALL_MARKETS,
    date_policy="calendar_window",
    empty_semantics="coverage_gap",
    impact="low",
    allowed_vendors=(),
)


def policy_for(method: str) -> ToolPolicy:
    """Return a registered policy or fail loudly when policy is missing."""
    try:
        return _POLICIES[method]
    except KeyError as exc:
        raise UnknownToolPolicyError(method) from exc


def legacy_policy_for(method: str) -> ToolPolicy:
    """Return the documented pre-registry policy for deliberate compatibility use.

    This is intentionally separate from :func:`policy_for`: unknown production
    methods must opt into the compatibility behavior and leave an audit trail.
    """
    logger.warning("Using legacy data policy for unregistered method %s", method)
    return _LEGACY_POLICY


def is_applicable(method: str, market: Market) -> bool:
    """Whether a method is meaningful for the market in this run."""
    return market in policy_for(method).applicable_markets


def normalized_dates(method: str, context: RuntimeDataContext) -> tuple[str, str | None]:
    """Return policy date anchors, never rewritten request arguments.

    ``market_session`` and ``latest_snapshot`` use the latest applicable
    market session. ``calendar_window`` preserves the report observation date
    and the evidence coverage end. Vendor-argument rewriting is Task 3.
    """
    policy = policy_for(method)
    if policy.date_policy in {"market_session", "latest_snapshot"}:
        return context.dates.market_as_of_date, None
    return context.dates.analysis_date, context.dates.evidence_window_end
