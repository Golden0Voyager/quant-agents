import functools
import logging
from collections.abc import Mapping
from typing import Any

import yfinance as yf
from langchain_core.messages import HumanMessage, RemoveMessage

from tradingagents.agents.utils.commodity_data_tools import (  # noqa: F401
    get_commodity_futures,
    get_gold_price,
    get_lithium_spot,
)

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (  # noqa: F401
    get_ah_premium,
    get_cb_index,
    get_cb_quotation,
    get_cb_redeem,
    get_chip_distribution,
    get_etf_daily,
    get_stock_data,
)
from tradingagents.agents.utils.fund_flow_tools import (  # noqa: F401
    get_fund_flow,
    get_margin_trading,
    get_sector_fund_flow,
    get_south_flow,
)
from tradingagents.agents.utils.fundamental_data_tools import (  # noqa: F401
    get_balance_sheet,
    get_cashflow,
    get_dividend_history,
    get_dividend_summary,
    get_earnings_estimates,
    get_earnings_forecast,
    get_fundamentals,
    get_historical_valuation,
    get_income_statement,
    get_shareholder_count,
)
from tradingagents.agents.utils.index_data_tools import get_index_daily  # noqa: F401
from tradingagents.agents.utils.industry_data_tools import (  # noqa: F401
    get_concept_board,
    get_industry_valuation,
)
from tradingagents.agents.utils.macro_data_tools import (  # noqa: F401
    get_central_bank_balance,
    get_cftc_cot,
    get_eia_petroleum,
    get_fx_rate,
    get_hk_tech_index,
    get_macro_indicators,
    get_us_macro,
)
from tradingagents.agents.utils.market_breadth_tools import (  # noqa: F401
    get_auction_snapshot,
    get_index_futures_basis,
    get_limit_up_down,
    get_option_sentiment,
    get_sector_daily,
    get_sector_valuation,
    get_short_term_benchmark,
)
from tradingagents.agents.utils.market_data_validation_tools import get_verified_market_snapshot  # noqa: F401
from tradingagents.agents.utils.news_data_tools import (  # noqa: F401
    get_block_trade,
    get_cailianpress_telegrams,
    get_company_announcements,
    get_dragon_tiger,
    get_global_news,
    get_insider_transactions,
    get_institution_survey,
    get_institutional_holdings,
    get_institutional_intelligence,
    get_news,
    get_northbound_hold,
    get_placement_announcements,
    get_pledge_ratio,
    get_research_reports,
    get_restricted_release,
    get_stock_repurchase,
)
from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets  # noqa: F401
from tradingagents.agents.utils.technical_indicators_tools import get_indicators  # noqa: F401

logger = logging.getLogger(__name__)


def opponent_argument_or_opening(text: str, opponent: str) -> str:
    """Opponent's latest argument, or an explicit opening marker when empty.

    Upstream #1176: the first speaker in each debate round receives an empty
    opponent response; interpolating it into a "refute the opponent" prompt
    makes the model fabricate the other side's position. Returning a clear
    "has not spoken yet" marker instead lets it open with its own case.
    """
    text = (text or "").strip()
    if text:
        return text
    return f"(The {opponent} has not spoken yet — open the debate with your own case.)"


def observation_mode_instruction() -> str:
    """Analyst-layer boundary rules (observation, not decision).

    TradingAgents-CN parity: analysts produce research observations for a
    downstream debate and trading layer, so trading vocabulary has no place in
    an analyst report. Grounds the boundary in the fork's own architecture —
    the Trader / Portfolio Manager layer owns every decision artifact.
    """
    return (
        "\n\n## Observation Mode (analyst boundary)\n"
        "You are an ANALYST, not a decision maker. Your report is one input to a "
        "downstream debate and trading layer that owns every decision.\n"
        "- Do NOT output buy/sell/hold/accumulate/trim recommendations, position "
        "sizes, entry/exit plans, price targets, fair-value ranges, stop-losses, "
        "take-profits, or expected-return percentages.\n"
        "- You MAY state valuation levels, support/resistance zones, and "
        "upside/downside drivers as research observations with the evidence "
        "behind them — as long as they are not packaged as a plan.\n"
        "- Every quantitative claim must come from tool output or the verified "
        "snapshot in this prompt, never from model memory.\n"
        "- Avoid unquantified directional language ('breakout confirmed', "
        "'firmly above support'): attach the concrete number and its date, or "
        "drop the claim."
    )


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def _clean_identity_value(value: Any) -> str | None:
    """Return a trimmed string, or None for empty / placeholder-ish values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


@functools.lru_cache(maxsize=256)
def resolve_instrument_identity(ticker: str) -> dict:
    """Resolve deterministic identity metadata (company name, sector, …) for a ticker.

    This exists to stop the pipeline from hallucinating a *different* company
    when a chart pattern suggests a different industry than the real one
    (#814): without a ground-truth name, the market analyst would pattern-match
    the price action to a narrative and invent an identity that then cascaded
    through every downstream agent.

    Best-effort by design: if yfinance is unavailable, rate-limited, or doesn't
    recognise the ticker, we return ``{}`` and the caller falls back to
    ticker-only context rather than failing before analysis starts. Cached so
    the lookup happens at most once per ticker per process.

    The symbol is normalized first (e.g. ``XAUUSD`` -> ``GC=F``) so identity
    resolves for the same instrument the price path actually fetches (#983).
    """
    from tradingagents.dataflows.symbol_utils import normalize_symbol

    try:
        info = yf.Ticker(normalize_symbol(ticker)).info or {}
    except Exception as exc:  # noqa: BLE001 — fail open, never block the run
        logger.debug("Could not resolve instrument identity for %s: %s", ticker, exc)
        return {}

    identity: dict[str, str] = {}
    company_name = _clean_identity_value(info.get("longName")) or _clean_identity_value(
        info.get("shortName")
    )
    if company_name:
        identity["company_name"] = company_name
    for source_key, target_key in (
        ("sector", "sector"),
        ("industry", "industry"),
        ("exchange", "exchange"),
        ("quoteType", "quote_type"),
    ):
        value = _clean_identity_value(info.get(source_key))
        if value:
            identity[target_key] = value
    return identity


def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    identity: Mapping[str, str] | None = None,
    confirmed_name: str | None = None,
) -> str:
    """Describe the exact instrument so agents preserve identity and ticker.

    When ``identity`` is provided (resolved deterministically via
    :func:`resolve_instrument_identity`), the company name and business
    classification are injected so agents anchor to the real company rather
    than pattern-matching the price chart to a wrong one (#814).

    ``confirmed_name`` is the user-confirmed company name from the CLI
    (resolved via akshare for A-shares). When provided, it takes priority
    over the yfinance identity to prevent name mismatches.
    """
    is_crypto = asset_type == "crypto"
    instrument_label = "asset" if is_crypto else "instrument"
    context = (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `.SS`, `.SZ`, `-USD`)."
    )

    details = []
    if confirmed_name:
        details.append(f"{'Name' if is_crypto else 'Company'}: {confirmed_name}")
    elif identity:
        name = identity.get("company_name") or identity.get("name")
        if name:
            details.append(f"{'Name' if is_crypto else 'Company'}: {name}")

    # Sector/industry/exchange always come from yfinance identity when available.
    if identity:
        sector, industry = identity.get("sector"), identity.get("industry")
        if sector and industry:
            details.append(f"Business classification: {sector} / {industry}")
        elif sector:
            details.append(f"Sector: {sector}")
        elif industry:
            details.append(f"Industry: {industry}")
        if identity.get("exchange"):
            details.append(f"Exchange: {identity['exchange']}")

    if details:
        context += (
            f" Resolved identity: {'; '.join(details)}. "
            "Do not substitute a different company or ticker unless a tool "
            "result explicitly disproves this resolved identity."
        )

    if is_crypto:
        context += (
            " Treat it as a crypto asset rather than a company, and do not "
            "assume company fundamentals are available."
        )
    return context


def _all_a_share_names() -> set[str]:
    """Return all known A-share company names from akshare, cached."""
    try:
        # Avoid circular import; akshare is always available when this runs.
        import akshare as ak
        df = ak.stock_info_a_code_name()
        return {str(row["name"]).strip() for _, row in df.iterrows()}
    except Exception:
        return set()


def sanitize_company_name_in_report(report: str, ticker: str, company_name: str) -> str:
    """Post-process analyst reports to correct company-name hallucinations.

    Scans the report for any A-share company name that differs from the
    expected ``company_name`` and replaces it. This catches cases where the
    LLM (or the underlying data vendor) confuses one Chinese company with
    another for the same ticker. Non-A-share tickers are passed through.
    """
    if not company_name or not report:
        return report
    suffix = ticker.split(".")[-1].upper() if "." in ticker else ""
    if suffix not in ("SS", "SZ", "BJ"):
        return report

    wrong_names: list[str] = []
    for candidate in _all_a_share_names():
        if candidate != company_name and candidate in report:
            wrong_names.append(candidate)

    for wrong in wrong_names:
        report = report.replace(wrong, company_name)

    return report


def get_instrument_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the instrument context for the current run.

    Prefers the identity-resolved context computed once at run start and
    stored on the state (see ``TradingAgentsGraph.resolve_instrument_context``).
    Falls back to a ticker-only context — with no network lookup — when the
    state was constructed without it (bare programmatic states, tests), so a
    consumer is never forced to make a yfinance call mid-graph.
    """
    context = state.get("instrument_context")
    if isinstance(context, str) and context.strip():
        return context
    return build_instrument_context(
        str(state["company_of_interest"]),
        state.get("asset_type", "stock"),
    )


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add a context-anchored placeholder.

        The placeholder must not be a bare ``"Continue"``: some
        OpenAI-compatible providers interpret that literally as the user task
        and produce output about the word "continue" instead of analysing the
        instrument (#888). Anchoring it to the resolved instrument context and
        date keeps the next analyst on-task even if the provider treats the
        placeholder as a standalone request.
        """
        messages = state["messages"]
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        instrument_context = get_instrument_context_from_state(state)
        trade_date = state.get("trade_date", "the requested date")
        placeholder = HumanMessage(
            content=(
                f"Proceed with your assigned analysis for this workflow. "
                f"{instrument_context} The analysis date is {trade_date}."
            )
        )
        return {"messages": removal_operations + [placeholder]}

    return delete_messages


def get_or_build_data_quality_summary(state: Mapping[str, Any]) -> str:
    """Return existing data_quality_summary, or build it from analyst reports in state."""
    existing = state.get("data_quality_summary")
    if isinstance(existing, str) and existing.strip():
        return existing

    from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS, build_data_quality_summary

    specs = list(ANALYST_NODE_SPECS.values())
    return build_data_quality_summary(state, specs)



