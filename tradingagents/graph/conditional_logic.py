# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_states import AgentState


class ConditionalLogic:
    """Handles conditional logic for determining graph flow.

    NOTE: The per-analyst ``should_continue_*`` methods return hard-coded
    node-name strings. If you rename a node in ``AnalystNodeSpec``, you
    must update the matching method here. A future refactor could generate
    these methods dynamically from the spec table, but the explicit
    per-method form makes stack-traces and static analysis easier to read.
    """

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    @staticmethod
    def _continue_tool_or_clear(state: AgentState, tool_node: str, clear_node: str) -> str:
        """Common helper: return ``tool_node`` if the last message has tool_calls, else ``clear_node``."""
        last_message = state["messages"][-1]
        # getattr: only AIMessage carries tool_calls; other message types in the
        # union (Human/System/Tool...) simply route to the clear node.
        return tool_node if getattr(last_message, "tool_calls", None) else clear_node

    def should_continue_market(self, state: AgentState):
        return self._continue_tool_or_clear(state, "tools_market", "Msg Clear Market")

    def should_continue_social(self, state: AgentState):
        """Determine if sentiment-analyst tool round should continue.

        Method name keeps the legacy ``social`` suffix to match the
        ``AnalystType.SOCIAL = "social"`` wire value (saved-config
        back-compat); the returned ``clear_node`` label uses the v0.2.5
        rename so it matches the node registered by the execution plan.
        """
        return self._continue_tool_or_clear(state, "tools_social", "Msg Clear Sentiment")

    def should_continue_news(self, state: AgentState):
        return self._continue_tool_or_clear(state, "tools_news", "Msg Clear News")

    def should_continue_fundamentals(self, state: AgentState):
        return self._continue_tool_or_clear(state, "tools_fundamentals", "Msg Clear Fundamentals")

    def should_continue_governance(self, state: AgentState):
        return self._continue_tool_or_clear(state, "tools_governance", "Msg Clear Governance")

    def should_continue_industry(self, state: AgentState):
        return self._continue_tool_or_clear(state, "tools_industry", "Msg Clear Industry")

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
