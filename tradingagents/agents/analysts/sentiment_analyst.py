"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines       — Yahoo Finance (institutional framing)
  2. Eastmoney hot rank   — 个股人气排名, rank-trajectory and fan
                            composition (新晋粉丝 / 铁杆粉丝)
  3. Eastmoney Guba       — 千股千评 综合评分, 用户关注指数,
                            参与意愿趋势 from 东方财富 股吧

StockTwits and Reddit were removed in 2026-Q3 because their public
endpoints became reliably unreliable (StockTwits 403, Reddit SSL errors).

The agent does not use tool-calling; the data is in the prompt from
turn 0. Output uses the structured-output pattern (json_schema for
OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic), falling
back to free-text generation for providers that lack native support, so
the sentiment header (band + score + confidence) is deterministic across
runs and providers instead of free-form per-model prose.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
    sanitize_company_name_in_report,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.eastmoney_sentiment import (
    fetch_eastmoney_guba_sentiment,
    fetch_eastmoney_hot_rank,
)


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news (Yahoo Finance) + Eastmoney hot rank + Eastmoney Guba
    sentiment indicators, injects them into the prompt as structured blocks,
    and produces a deterministic sentiment report via structured output
    (with a free-text fallback for providers that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        company_name = state.get("company_name", "")
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = get_instrument_context_from_state(state)

        # Pre-fetch all three sources. Each fetcher degrades gracefully and
        # returns a string (no exceptions surface from here), so the LLM
        # always sees something — either real data or a clear placeholder.
        news_block = get_news.func(ticker, start_date, end_date)
        hot_rank_block = fetch_eastmoney_hot_rank(ticker)
        guba_block = fetch_eastmoney_guba_sentiment(ticker)

        system_message = _build_system_message(
            ticker=ticker,
            company_name=company_name,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            hot_rank_block=hot_rank_block,
            guba_block=guba_block,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}"
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        report_text = sanitize_company_name_in_report(
            report_text, ticker, company_name
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    company_name: str = "",
    start_date: str,
    end_date: str,
    news_block: str,
    hot_rank_block: str,
    guba_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    ticker_guard = (
        f"TICKER VERIFICATION — The assigned company is {company_name} ({ticker}). "
        f"DO NOT change the company, ticker, or industry focus.\n\n"
    ) if company_name else ""
    return ticker_guard + f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on three complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### Eastmoney 人气排名 — retail investor attention ranking on 东方财富
Aggregate ranking of the stock among all A-shares on the Eastmoney platform. Shows the stock's current rank in the top-100 hot list (price, change %), plus a historical time-series of rank position and fan-composition metrics (新晋粉丝 = new followers, 铁杆粉丝 = loyal fans). Higher rank + rising loyal-fan ratio signals growing retail conviction.

<start_of_hot_rank>
{hot_rank_block}
<end_of_hot_rank>

### Eastmoney 股吧情绪 — Guba sentiment indicators from 千股千评
Three quantitative sentiment signals from the Eastmoney 股吧 (stock bar) community:

  - **综合得分** (Comprehensive Score, 0–100): Composite rating based on technicals, fundamentals, and institutional participation. Higher = more bullish aggregate sentiment.
  - **用户关注指数** (User Attention Index, 0–100): Daily attention intensity. A sudden spike often precedes significant price moves.
  - **参与意愿** (Participation Willingness): Whether retail investors are inclined to buy (higher = more willing). Trend direction (5-day average and daily change) is more informative than the absolute level.

<start_of_guba>
{guba_block}
<end_of_guba>

## How to analyze this data (best practices)

1. **Read the 人气排名 trend as a retail-attention signal.** A stock rising in rank (lower number = better) with increasing 铁杆粉丝 ratio suggests growing retail conviction. A sudden spike into the top 10 without a news catalyst may indicate coordinated retail attention (contrarian risk).

2. **Look for cross-source divergences.** If news framing is bearish but Eastmoney 综合得分 is high (e.g., >70) and 参与意愿 is rising, that mismatch is itself a signal — retail may be leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Weight the 用户关注指数 spike as a leading indicator.** A sudden jump in attention (e.g., from 50 to 90) typically precedes price movement. The direction of the move depends on concurrent news — a spike with positive news is bullish; a spike with negative news is bearish; a spike with no news is high uncertainty.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; quantitative sentiment indicators (综合得分, 参与意愿) are crowd-behavior measurements. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up in the news? That's the dominant narrative driving current sentiment. Complement it with the quantitative sentiment signals above.

6. **Be honest about data limits.** If one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this explicitly in the `confidence` field and the narrative. For non-A-share tickers, the Eastmoney sources will return a clear placeholder — explain that the analysis is based on news alone.

7. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown (news + Eastmoney hot rank + Guba sentiment indicators), divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
