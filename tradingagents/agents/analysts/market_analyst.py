from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_chip_distribution,
    get_fund_flow,
    get_index_daily,
    get_indicators,
    get_instrument_context_from_state,
    get_language_instruction,
    get_limit_up_down,
    get_sector_fund_flow,
    get_stock_data,
    get_verified_market_snapshot,
    sanitize_company_name_in_report,
)
from tradingagents.agents.utils.prefetch import prefetch_for_market
from tradingagents.agents.utils.tool_capabilities import (
    BoundToolsByMarket,
    tool_guidance_for,
)
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.market_context import infer_market


def create_market_analyst(llm):
    tools = [
        get_stock_data,
        get_indicators,
        get_chip_distribution,
        get_fund_flow,
        get_sector_fund_flow,
        get_limit_up_down,
        get_index_daily,
        get_verified_market_snapshot,
    ]
    bound_tools = BoundToolsByMarket(llm, tools)
    tool_guidance = {
        "get_stock_data": "Call get_stock_data first to retrieve the CSV needed to generate indicators. For global macro cross-asset context it also reads locally archived macro symbols: ^VIX, ^VIX9D, ^VIX3M (volatility term structure — VIX3M trading below VIX, i.e. an inverted term structure, signals near-term stress), ^MOVE (bond-market volatility), DX-Y.NYB (US dollar index), USDCNY=X (renminbi exchange rate), ^HSI (Hang Seng), and sector benchmarks such as XLE/OIH (energy), ^SOX (semiconductors) and SLV (silver).",
        "get_indicators": "Then call get_indicators with exact indicator names from the list above.",
        "get_chip_distribution": "Call get_chip_distribution to assess profit ratios, average holder costs, chip concentration, and price-to-cost bias at key support/resistance levels.",
        "get_fund_flow": "Call the standalone get_fund_flow tool directly (do not pass it to get_indicators) to analyze capital-flow trends.",
        "get_sector_fund_flow": "Use get_sector_fund_flow to analyze sector-level fund flow and industry rotation.",
        "get_limit_up_down": "Call get_limit_up_down with the current date to gauge daily limit-up/limit-down market breadth.",
        "get_index_daily": "Call get_index_daily for relevant major indices to compare the stock with its home market or board.",
        "get_verified_market_snapshot": "Before the final report, call get_verified_market_snapshot for this ticker and date; use it as the source of truth for exact OHLCV, price-level, and indicator claims.",
    }

    def market_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state.get("company_name", "")
        instrument_context = get_instrument_context_from_state(state)
        market = state.get("market") or infer_market(ticker)
        market_tools, bound_llm = bound_tools.get(market)

        # Pre-fetch the call-auction context (A-share only) instead of leaving
        # it to optional tool calls — in the 20260826 batch 10/23 tickers
        # skipped the calls and then reported the dimension as "tool
        # unavailable". Pre-fetched blocks are always in the prompt.
        auction_block = prefetch_for_market(
            "get_auction_snapshot",
            market,
            "call-auction snapshot (集合竞价)",
            lambda: route_to_vendor("get_auction_snapshot", ticker),
        )
        benchmark_block = prefetch_for_market(
            "get_short_term_benchmark",
            market,
            "short-term benchmark (短线风向标)",
            lambda: route_to_vendor("get_short_term_benchmark"),
        )
        auction_section = f"""
## Pre-fetched call-auction context (A-share only)

### 集合竞价快照 — pre-open auction strength for {ticker}
Auction pct, volume ratio, and unmatched volume explain the day's open. A DATA_UNAVAILABLE placeholder means the auction data could not be retrieved (e.g. before the auction starts or vendor outage) — do not fabricate values.

<start_of_auction>
{auction_block}
<end_of_auction>

### 短线风向标 — market-wide short-term money direction at the open
Benchmark stocks with their call-auction pct change and sector tags: a quick read on where short-term money pointed at the open.

<start_of_short_term_benchmark>
{benchmark_block}
<end_of_short_term_benchmark>
"""

        ticker_guard = (
            f"TICKER VERIFICATION — You are analyzing {company_name} ({ticker}). "
            f"DO NOT change the company, ticker, or industry focus. ALL data calls "
            f"and analysis MUST be for {ticker} only.\n\n"
        ) if company_name else ""

        system_message = ticker_guard + (
            """You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

- Select indicators that provide diverse and complementary information. Avoid redundancy (e.g., do not select both rsi and stochrsi). Also briefly explain why they are suitable for the given market context. When calling an available indicator tool, use the exact indicator names above because they are defined parameters. If tool outputs conflict, flag the discrepancy rather than inventing a reconciled number. Do not claim historical validation, support/resistance bounces, or exact percentage moves unless directly supported by concrete dates and prices."""
            + tool_guidance_for(market_tools, tool_guidance)
            + auction_section
            + " Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
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

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        report = sanitize_company_name_in_report(
            report, ticker, company_name
        )

        return {
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node
