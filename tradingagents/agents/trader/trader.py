"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools
import logging

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
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot

logger = logging.getLogger(__name__)


def _build_verified_snapshot_block(ticker: str, trade_date: str) -> str:
    """Render the verified market-data snapshot for the Trader prompt.

    Tries to load the deterministic OHLCV snapshot used by the Market Analyst
    verification path. If the vendor returns no rows, the load_ohlcv layer
    raises ``NoMarketDataError`` and ``build_verified_market_snapshot`` raises
    ``ValueError``; we surface a stub that tells the Trader to leave
    entry/stop empty instead of inventing a number. The price the model sees
    in the snapshot is the price the model must quote — no exceptions.
    """
    try:
        return build_verified_market_snapshot(ticker, trade_date)
    except Exception as exc:  # noqa: BLE001 — vendor failure must not break the pipeline
        logger.warning(
            "Verified market snapshot unavailable for %s on %s: %s",
            ticker, trade_date, exc,
        )
        return (
            "Verified market data is unavailable for this ticker on the "
            f"requested date ({trade_date}). No current price, indicator, or "
            "OHLCV row can be cited. You MUST set entry_price and stop_loss "
            "to null in your proposal — do not estimate or recall a price "
            "from prior knowledge or training data."
        )


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        ticker = company_name
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]
        trade_date = state.get("trade_date", "")

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

        snapshot_block = _build_verified_snapshot_block(ticker, trade_date)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan. "
                    "Entry Price and Stop Loss must be quoted from the Verified Market Snapshot "
                    "below — never invent, recall from training data, or extrapolate from gross-margin "
                    "or other fundamental ratios. If the snapshot reports the data is unavailable, "
                    "you MUST set entry_price and stop_loss to null rather than guessing. Position "
                    "sizing should reflect the volatility implied by the snapshot (ATR) and the "
                    "rating the research plan recommends."
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
                    f"---\n\n"
                    f"Verified Market Snapshot (source of truth for entry / stop / ATR):\n\n"
                    f"{snapshot_block}\n\n"
                    f"---\n\n"
                    f"Required response fields:\n"
                    f"- Entry Price: a specific price level quoted from the snapshot above, "
                    f"or null if the snapshot says data is unavailable.\n"
                    f"- Stop Loss: a specific price level quoted from the snapshot above, "
                    f"or null if the snapshot says data is unavailable.\n"
                    f"- Position Sizing: a concrete sizing instruction "
                    f"(e.g., '5% of portfolio', '1,000 shares').\n\n"
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
