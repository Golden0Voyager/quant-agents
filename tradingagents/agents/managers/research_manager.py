"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from tradingagents.agents.schemas import ResearchPlan, render_research_plan
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_or_build_data_quality_summary,
)
from tradingagents.agents.utils.structured import (
    FALLBACK_MARKER,
    bind_structured,
    invoke_structured_or_freetext,
)


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        data_quality_summary = get_or_build_data_quality_summary(state)
        history = state["investment_debate_state"].get("history", "")
        analysis_date = state.get("trade_date", "unknown")

        investment_debate_state = state["investment_debate_state"]

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.
⚠️ Temporal integrity: The current analysis date is {analysis_date}. All data points, events, and financial figures you cite MUST have occurred on or before this date. Do not reference future events, future financial results, or future announcements.

{instrument_context}

**Data Quality of Analyst Reports:**
{data_quality_summary}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

---

**Structured Output Requirements:**
- Provide a `confidence` level (low / medium / high). If verified market data or key analyst data is unavailable, or the debate is evenly split, set confidence to `low` or leave it null.
- List the key assumptions behind your recommendation in `key_assumptions`. These should be the facts or beliefs that, if wrong, would change the recommendation.
- Fill `signal_weights`: for each analytical dimension that materially entered the debate (technical / fundamental / capital_flow / sentiment / news / governance / industry), record its direction (bullish / bearish / neutral) and the weight it carried in your final call. When dimensions conflict — e.g. deep-value fundamentals vs pledge-risk governance — the weights must make explicit which side won and the note must say why. Down-weight dimensions whose data was flagged missing or stale.

**Calibration note** (from backtesting this system's historical ratings): bearish calls (Sell/Underweight) have hit the mark far less often than bullish ones, especially when driven mainly by short-term technical weakness during a rebounding market. Before committing to Sell/Underweight, verify the bear case rests on more than momentum — require at least one fundamental, governance, or capital-flow signal pointing the same way, and say so in `signal_weights`.

---

**Debate History:**
{history}""" + get_language_instruction()

        investment_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
            "structured_fallback_agents": (
                ["Research Manager"] if FALLBACK_MARKER in investment_plan else []
            ),
        }

    return research_manager_node
