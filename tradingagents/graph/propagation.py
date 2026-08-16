# TradingAgents/graph/propagation.py

from dataclasses import asdict
from typing import Any

from tradingagents.agents.utils.agent_states import (
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.market_context import AnalysisDates, Market, infer_market, resolve_analysis_dates


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
        holdings_context: dict | None = None,
        transactions_context: list | None = None,
        market: Market | None = None,
        analysis_dates: AnalysisDates | None = None,
    ) -> dict[str, Any]:
        """Create the initial state for the agent graph.

        ``instrument_context`` is the deterministic ticker-identity string
        resolved once at run start (see
        ``TradingAgentsGraph.resolve_instrument_context``). When empty, agents
        fall back to ticker-only context via
        ``get_instrument_context_from_state``.
        """
        resolved_market = market or infer_market(company_name)
        resolved_dates = analysis_dates or resolve_analysis_dates(company_name, str(trade_date))
        return {
            "messages": [("human", company_name)],
            "company_of_interest": company_name,
            "asset_type": asset_type,
            "instrument_context": instrument_context,
            "trade_date": str(trade_date),
            "market": resolved_market,
            "analysis_dates": asdict(resolved_dates),
            "past_context": past_context,
            "holdings_context": holdings_context or {},
            "transactions_context": transactions_context or [],
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",
                    "bear_history": "",
                    "history": "",
                    "current_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "aggressive_history": "",
                    "conservative_history": "",
                    "neutral_history": "",
                    "history": "",
                    "latest_speaker": "",
                    "current_aggressive_response": "",
                    "current_conservative_response": "",
                    "current_neutral_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "market_report": "",
            "verified_market_snapshot": "",
            "verified_fundamentals_snapshot": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
            "governance_report": "",
            "industry_report": "",
            "structured_fallback_agents": [],
            # One record per routed data request; populated by the vendor
            # router during a run and rendered in the final report.
            "data_coverage": [],
        }

    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """Get arguments for the graph invocation.

        Args:
            callbacks: Optional list of callback handlers for tool execution tracking.
                       Note: LLM callbacks are handled separately via LLM constructor.
        """
        config = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {
            "stream_mode": "values",
            "config": config,
        }
