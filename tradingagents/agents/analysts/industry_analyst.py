from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_concept_board,
    get_industry_valuation,
    get_language_instruction,
    get_macro_indicators,
    get_sector_fund_flow,
    sanitize_company_name_in_report,
)
from tradingagents.agents.utils.tool_capabilities import (
    BoundToolsByMarket,
    tool_guidance_for,
)
from tradingagents.market_context import infer_market


def create_industry_analyst(llm):
    tools = [
        get_industry_valuation,
        get_concept_board,
        get_macro_indicators,
        get_sector_fund_flow,
    ]
    bound_tools = BoundToolsByMarket(llm, tools)
    tool_guidance = {
        "get_industry_valuation": "Use get_industry_valuation for peer and historical valuation comparisons.",
        "get_concept_board": "Use get_concept_board to identify concept themes, hot-sector topics, and theme momentum.",
        "get_macro_indicators": "Use get_macro_indicators to assess the macro backdrop influencing sector valuation.",
        "get_sector_fund_flow": "Use get_sector_fund_flow to track sector capital flows and rotation patterns.",
    }

    def industry_analyst_node(state):
        current_date = state["trade_date"]
        company_name = state.get("company_name", "")
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(
            ticker, company_name
        )

        market = state.get("market") or infer_market(ticker)
        market_tools, bound_llm = bound_tools.get(market)

        company_line = f"Target company: {company_name} ({ticker}). " if company_name else ""
        system_message = (
            company_line +
            "You are an Industry Analyst. Your job is to compare the target company's "
            "valuation (PE, PB, PS) against its industry peers and historical benchmarks. "
            + tool_guidance_for(market_tools, tool_guidance)
            + " "
            "Assess whether the stock is relatively overvalued, undervalued, or fairly priced within its sector. "
            "Highlight any valuation anomalies or regime shifts. Provide specific, actionable "
            "insights with supporting evidence to help traders make informed decisions."
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
            "industry_report": report,
        }

    return industry_analyst_node
