from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_cashflow,
    get_dividend_history,
    get_earnings_estimates,
    get_earnings_forecast,
    get_fundamentals,
    get_historical_valuation,
    get_income_statement,
    get_instrument_context_from_state,
    get_language_instruction,
    get_shareholder_count,
)
from tradingagents.agents.utils.tool_capabilities import tools_for_market
from tradingagents.market_context import infer_market


def create_fundamentals_analyst(llm):
    tools = [
        get_fundamentals,
        get_balance_sheet,
        get_cashflow,
        get_income_statement,
        get_historical_valuation,
        get_earnings_forecast,
        get_earnings_estimates,
        get_shareholder_count,
        get_dividend_history,
    ]
    bound_llms = {}

    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        fundamentals_snapshot_block = state.get("verified_fundamentals_snapshot") or (
            "Verified fundamentals snapshot is unavailable. "
            "Treat all fundamental numbers as unverified and set confidence to low."
        )

        market = state.get("market") or infer_market(ticker)
        if market not in bound_llms:
            market_tools = tools_for_market(tools, market)
            bound_llms[market] = (market_tools, llm.bind_tools(market_tools))
        market_tools, bound_llm = bound_llms[market]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " TERMINOLOGY MANDATE: Always specify whether net profit growth is '归母净利润同比 (YoY Net Profit Growth)' or '扣非净利润同比 (Deducted Net Profit Growth)'. Use '公司总市值' for total market capitalization."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + " Use `get_historical_valuation` to evaluate PE/PB percentile ranks over 3 years and match against ROE trends to identify deep value opportunities vs value trap risks."
            + " Use `get_earnings_forecast` for performance pre-announcements and YoY profit growth midpoints."
            + " Also use `get_earnings_estimates` to understand forward-looking consensus expectations for revenue, EPS, and profit growth. Compare estimates with historical actuals to identify expectation gaps."
            + " Use `get_shareholder_count` to assess筹码集中度 (declining count suggests institutional accumulation; rising count suggests retail influx)."
            + " Use `get_dividend_history` to evaluate shareholder return policy and dividend yield trends."
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
            )
            + "\n\n## Verified Fundamentals Snapshot\n\n"
            + fundamentals_snapshot_block
            + "\n\nUse the numbers in this snapshot as the source of truth for any exact "
            "fundamental claim (PE, PB, market cap, revenue, EPS, etc.). If the snapshot "
            "is unavailable, state that explicitly and set your confidence to low."
            + get_language_instruction(),
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
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
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

        report = result.content or ""

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
