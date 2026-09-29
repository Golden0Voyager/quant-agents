from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_or_build_data_quality_summary,
    opponent_argument_or_opening,
)


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = opponent_argument_or_opening(
            investment_debate_state.get("current_response", ""), "bull analyst"
        )
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        instrument_context = get_instrument_context_from_state(state)
        data_quality_summary = get_or_build_data_quality_summary(state)
        analysis_date = state.get("trade_date", "unknown")
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Governance Risks: Highlight red flags from shareholder changes, financing announcements, or regulatory notices.
- Valuation Risks: Highlight if the stock trades at a premium to industry peers, if earnings estimates have been downgraded, or if macro headwinds threaten the sector.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.
- ⚠️ Anti-repetition: You may build upon your previous arguments or reaffirm your position. Only introduce new evidence if it is actually present in the provided reports — never fabricate data, events, dates, or financial figures. If you have nothing new to add, simply say "I maintain my previous position" and briefly summarize why.

Argument quality rules (mandatory):
- Evidence chain: every quantitative claim must trace to the reports below or the verified snapshot — never to model memory. If a number you want is not in the materials, argue without it; do not fill the gap.
- Scenario framing, not prophecy: argue in conditional, falsifiable terms ("IF the backlog fails to convert, the growth story breaks; the evidence for that risk is ..."). Do not assert outcomes as certainty.
- You may write: "The bull case rests on Q3 order growth, but the segment data shows backlog conversion slowed for two consecutive quarters, which weakens that premise."
- You may write: "Peers trade at 15-20x forward earnings while this stock is at 28x; per the fundamentals report the premium needs sustained 30%+ growth to hold, and estimates have been flat for two quarters."
- Do not write: "This stock will crash once the market wakes up." (unsupported price prophecy — no such number exists in the materials)
- Do not write: "Management is lying about the numbers." (unverifiable accusation — cite the specific disclosure that concerns you instead)
- Do not write: "Everyone knows the sector is overheated." (vague consensus claims without a source in the materials)
- Valuation context must state which layer(s) the evidence supports: absolute (earnings/cash-flow based), relative (vs peers), historical percentile (vs its own history), or market-implied expectations (what growth the current price already assumes). "Expensive" or "cheap" without naming the layer and its evidence is not an argument.

⚠️ Temporal integrity: The current analysis date is {analysis_date}. All data points, events, and financial figures you cite MUST have occurred on or before this date. Do not reference future events, future financial results, or future announcements.

Resources available:

{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}

Data quality summary:
{data_quality_summary}

Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
