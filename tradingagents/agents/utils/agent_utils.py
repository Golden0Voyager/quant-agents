from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_earnings_estimates,
    get_shareholder_count,
    get_dividend_history,
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news,
    get_company_announcements,
    get_restricted_release,
    get_institutional_holdings,
    get_northbound_hold,
    get_dragon_tiger,
    get_block_trade,
    get_pledge_ratio,
    get_research_reports,
)
from tradingagents.agents.utils.fund_flow_tools import (
    get_fund_flow,
    get_margin_trading,
    get_sector_fund_flow,
)
from tradingagents.agents.utils.macro_data_tools import (
    get_macro_indicators,
)
from tradingagents.agents.utils.industry_data_tools import (
    get_industry_valuation,
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Only applied to user-facing agents (analysts, portfolio manager).
    Internal debate agents stay in English for reasoning quality.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str, company_name: str = "") -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    if company_name:
        ctx = (
            f'CRITICAL IDENTITY CONSTRAINT: You are analyzing "{company_name}" (ticker: {ticker}). '
            "You MUST use this exact company name — and ONLY this name — in every title, paragraph, "
            "table, and sentence of your report. "
            "You are STRICTLY FORBIDDEN from substituting it with any other name, "
            "even if your training data associates this ticker with a different company. "
            "If you are unsure about the company name, trust the name provided here and do not guess. "
            "\n\n"
        )
    else:
        ctx = ""
    ctx += f"The instrument to analyze is `{ticker}`. "
    ctx += (
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `.SS`, `.SZ`)."
    )
    return ctx


def sanitize_company_name_in_report(report: str, ticker: str, company_name: str) -> str:
    """Post-process analyst reports to correct company-name hallucinations.

    Replaces common wrong names associated with the ticker while preserving
    the rest of the report unchanged. Returns the report as-is if no company
    name is known.
    """
    if not company_name or not report:
        return report

    # Map of (wrong_name, right_name) pairs extracted heuristically.
    # This is intentionally conservative: only exact matches are replaced.
    bare = ticker.split(".")[0]

    # Collect candidate wrong names that differ from the correct one.
    # The caller can expand this list when new hallucinations are observed.
    wrong_names: list[str] = []
    for candidate in ["江苏银行", "江苏苏垦农发", "苏垦农发"]:
        if candidate != company_name and candidate in report:
            wrong_names.append(candidate)

    for wrong in wrong_names:
        report = report.replace(wrong, company_name)

    return report


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
