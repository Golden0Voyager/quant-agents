from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_block_trade,
    get_company_announcements,
    get_dragon_tiger,
    get_insider_transactions,
    get_institutional_intelligence,
    get_language_instruction,
    get_margin_trading,
    get_news,
    get_northbound_hold,
    get_pledge_ratio,
    get_restricted_release,
    sanitize_company_name_in_report,
)
from tradingagents.agents.utils.tool_capabilities import (
    BoundToolsByMarket,
    tool_guidance_for,
)
from tradingagents.market_context import infer_market


def create_governance_analyst(llm):
    tools = [
        get_company_announcements,
        get_insider_transactions,
        get_news,
        get_restricted_release,
        get_institutional_intelligence,
        get_northbound_hold,
        get_margin_trading,
        get_pledge_ratio,
        get_dragon_tiger,
        get_block_trade,
    ]
    bound_tools = BoundToolsByMarket(llm, tools)
    tool_guidance = {
        "get_company_announcements": "Use get_company_announcements for regulatory filings and notices.",
        "get_insider_transactions": "Use get_insider_transactions for shareholder-change and insider-transaction data.",
        "get_news": "Use get_news for related news coverage.",
        "get_restricted_release": "Use get_restricted_release to identify upcoming share unlocks and potential supply pressure.",
        "get_institutional_intelligence": "Use get_institutional_intelligence for shareholder positioning, fund holdings, surveys, and focus topics.",
        "get_northbound_hold": "Use get_northbound_hold to monitor foreign-investor positioning.",
        "get_margin_trading": "Use get_margin_trading to assess leverage and speculative sentiment.",
        "get_pledge_ratio": "Use get_pledge_ratio to evaluate equity-pledge and liquidation risk.",
        "get_dragon_tiger": "Use get_dragon_tiger to track hot-money and institutional trading activity.",
        "get_block_trade": "Use get_block_trade to monitor large-block transactions and premium/discount signals.",
    }

    def governance_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(
            state["company_of_interest"], state.get("company_name", "")
        )

        company_name = state.get("company_name", "")
        ticker = state["company_of_interest"]
        market = state.get("market") or infer_market(ticker)
        market_tools, bound_llm = bound_tools.get(market)
        company_line = f"Target company: {company_name} ({ticker}). " if company_name else ""

        system_message = (
            company_line +
            "You are a Corporate Governance Analyst tasked with analyzing "
            "company announcements, shareholder changes, insider transactions, "
            "and major corporate events for the target company over the past week. "
            "Your objective is to write a comprehensive long report detailing your "
            "analysis, insights, and implications for traders and investors. "
            "TERMINOLOGY MANDATE: Reserve '公司总市值' strictly for total company market cap. "
            "For institutional or northbound position value, explicitly write '机构持股市值' or '北向持股市值' to avoid ambiguity. "
            "Do NOT use plain '市值' for position values. "
            + tool_guidance_for(market_tools, tool_guidance)
            + " Provide specific, actionable insights on governance risks, capital structure "
            "changes, management signals, and any red flags that could impact investment decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + (
                "\n\n## Missing Data Protocol\n"
                "If any tool call returns NO_DATA_AVAILABLE or an empty result, "
                "explicitly state that the data dimension is unavailable. "
                "Never fabricate numbers, infer missing values, or extrapolate "
                "from partial data. "
                "Set your confidence level to low when data is insufficient "
                "(fewer than 3 data points or empty returns). "
                "Add a data_availability marker per data dimension in your "
                "report: ✅ (data available), ⚠️ (data partial/sparse), "
                "❌ (data unavailable)."
            ) + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(tool.name for tool in market_tools))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | bound_llm
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        report = sanitize_company_name_in_report(
            report, ticker, company_name
        )

        return {
            "messages": [result],
            "governance_report": report,
        }

    return governance_analyst_node
