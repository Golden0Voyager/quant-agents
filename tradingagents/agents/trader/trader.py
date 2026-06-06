"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        ticker = company_name
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]

        holdings_context = state.get("holdings_context", {})
        transactions_context = state.get("transactions_context", [])
        holdings_line = ""
        if holdings_context:
            from tradingagents.portfolio import Portfolio, Holding, Transaction, build_trader_prompt
            portfolio = Portfolio(
                holdings={t: Holding.from_dict(d, ticker=t) for t, d in holdings_context.items()}
            )
            transactions = [Transaction.from_dict(t) for t in transactions_context]
            trader_prompt = build_trader_prompt(
                ticker, portfolio, transactions
            )
            if trader_prompt:
                holdings_line = f"\n{trader_prompt}\n"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan. "
                    "You MUST include concrete entry price, stop-loss price, and position sizing "
                    "guidance in your response. Do not omit these fields even if the research plan "
                    "does not explicitly state them—derive them from the technical and fundamental data."
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {ticker} ({company_name}). {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\n"
                    f"Proposed Investment Plan: {investment_plan}{holdings_line}\n\n"
                    f"Your response MUST contain the following concrete fields:\n"
                    f"- Entry Price: a specific price level at which to enter the position\n"
                    f"- Stop Loss: a specific price level to limit downside risk\n"
                    f"- Position Sizing: a concrete sizing instruction (e.g., '5% of portfolio', '1,000 shares')\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
