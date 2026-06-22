"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools
import logging
import re

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


def _extract_market_analyst_price(market_report: str) -> str | None:
    """Extract the latest price mentioned in the market analyst's report.

    Looks for patterns like ``现价: 160.51``, ``Latest Close: 91.60``,
    or ``收盘价.*?(\d+\.\d+)`` in Chinese/English report text.
    Returns a formatted warning block if a price is found, or None.
    """
    if not market_report:
        return None
    patterns = [
        r"现价[：:]\s*(\d+\.?\d*)",
        r"收盘价[：:]\s*(\d+\.?\d*)",
        r"最新价[：:]\s*(\d+\.?\d*)",
        r"价格[：:]\s*(\d+\.?\d*)",
        r"[Cc]lose[：:]\s*(\d+\.?\d*)",
        r"[Pp]rice[：:]\s*(\d+\.?\d*)",
        r"当前.*?(\d+\.?\d*)",
    ]
    for pat in patterns:
        m = re.search(pat, market_report)
        if m:
            return m.group(1)
    # Try table row like | Close | 91.60 |
    m = re.search(r"\|\s*Close\s*\|\s*(\d+\.\d+)\s*\|", market_report)
    if m:
        return m.group(1)
    return None


def _build_verified_snapshot_block(ticker: str, trade_date: str, market_report: str = "") -> str:
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
        fallback_price = _extract_market_analyst_price(market_report)
        fallback_block = (
            f"Verified market data is unavailable for this ticker on the "
            f"requested date ({trade_date}). No OHLCV row from a verified vendor "
            f"can be cited. You MUST set entry_price and stop_loss to null in "
            f"your proposal — do not estimate or recall a price from prior "
            f"knowledge or training data."
        )
        if fallback_price:
            fallback_block += (
                f"\n\n"
                f"NOTE: The Market Analyst's report mentions a price of "
                f"{fallback_price} for {ticker}. This is UNVERIFIED (the "
                f"snapshot vendor returned no data), but you may use it as a "
                f"rough reference if the Research Plan explicitly names the "
                f"same level. If you use it, you MUST still leave entry_price "
                f"and stop_loss as null — do not promote an unverified number "
                f"into the structured output."
            )
        return fallback_block


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
            from tradingagents.portfolio import Holding, Portfolio, Transaction, build_trader_prompt
            portfolio = Portfolio(
                holdings={t: Holding.from_dict(d, ticker=t) for t, d in holdings_context.items()}
            )
            transactions = [Transaction.from_dict(t) for t in transactions_context]
            trader_prompt = build_trader_prompt(
                ticker, portfolio, transactions
            )
            if trader_prompt:
                holdings_line = f"\n{trader_prompt}\n"

        market_report = state.get("market_report", "")
        snapshot_block = _build_verified_snapshot_block(ticker, trade_date, market_report=market_report)

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
