"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import logging
import re

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot

logger = logging.getLogger(__name__)


def _extract_snapshot_close(snapshot: str | None) -> float | None:
    """Parse the latest verified Close price out of the snapshot markdown table.

    The snapshot produced by ``build_verified_market_snapshot`` renders the
    latest OHLCV row in a markdown table; the Close row uses ``| Close | <n> |``.
    Returned as a float so the caller can compare against the Trader's quoted
    entry / stop and reject obvious miscalibrations. Returns ``None`` when the
    snapshot is unavailable or the table is missing.
    """
    if not snapshot:
        return None
    match = re.search(r"\|\s*Close\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|", snapshot)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]
        ticker = state["company_of_interest"]
        trade_date = state.get("trade_date", "")

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        holdings_context = state.get("holdings_context", {})
        transactions_context = state.get("transactions_context", [])
        holdings_line = ""
        if holdings_context:
            from tradingagents.portfolio import Portfolio, Holding, Transaction, build_pm_prompt
            portfolio = Portfolio(
                holdings={
                    t: Holding.from_dict(d, ticker=t) for t, d in holdings_context.items()
                }
            )
            transactions = [Transaction.from_dict(t) for t in transactions_context]
            holdings_prompt = build_pm_prompt(
                state["company_of_interest"], portfolio, transactions
            )
            if holdings_prompt:
                holdings_line = f"\n**Current Position:**\n{holdings_prompt}\n"

        # Re-verify the snapshot in the PM node so the final decision can
        # reject any entry / stop that the LLM may have invented upstream.
        # We fall back gracefully when the snapshot is unavailable.
        try:
            snapshot = build_verified_market_snapshot(ticker, trade_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PM could not refresh verified market snapshot for %s on %s: %s",
                ticker, trade_date, exc,
            )
            snapshot = None
        verified_close = _extract_snapshot_close(snapshot)
        snapshot_block = snapshot if snapshot else (
            "Verified market data is unavailable for this ticker on the "
            f"requested date ({trade_date}). Treat any Trader-quoted entry / "
            "stop as suspect and prefer leaving them null."
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}{holdings_line}
**Risk Analysts Debate History:**
{history}

---

**Verified Market Snapshot** (source of truth for any price level):

{snapshot_block}

---

**Decision Requirements:**
- Extract the entry price, stop-loss, and position sizing from the Trader's transaction proposal above and populate the corresponding fields in your decision.
- The Trader is required to quote entry and stop-loss from the snapshot below. If the Trader's quoted value differs from the snapshot's latest Close by more than ~25% in either direction, OR the snapshot is unavailable, leave entry_price and stop_loss as null rather than copying the suspect number. Do not estimate, extrapolate, or recall a price from training data, gross-margin ratios, or any other fundamental number.
- If the Trader's proposal lacks any of these values, leave that field empty rather than estimating or inventing a number.
- Position sizing may be a string (e.g. ``5% of portfolio``, ``1,000 shares``) and is not bound by the snapshot.
- Ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
        }

    return portfolio_manager_node
