"""Smoke test: verify Cailianpress telegrams appear in News Analyst report.

Simulates the LangGraph tool loop that the full graph provides:
  1. News Analyst LLM (bound to tools) → generates tool_calls or content
  2. If tool_calls → ToolNode executes them → results fed back → goto 1
  3. If no tool_calls → content is the final report

Usage:
    python scripts/smoke_cailianpress_in_news.py

Requires a configured LLM (default: sensenova).  Set env vars like
TRADINGAGENTS_LLM_PROVIDER / TRADINGAGENTS_QUICK_THINK_LLM to switch.
"""

from __future__ import annotations

import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.default_config import default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients import create_llm_client


def _find_tool_node(ticker: str, trade_date: str) -> object:
    """Return the news ToolNode from a throwaway graph instance."""
    g = TradingAgentsGraph(config=default_config())
    return g.tool_nodes["news"]


def main() -> int:
    config = default_config()

    ticker = "600519.SS"
    company_name = "贵州茅台"
    trade_date = "2026-07-23"

    provider = config["llm_provider"]
    model = config["quick_think_llm"]
    base_url = config.get("backend_url")
    max_tool_rounds = 8

    print(f"Provider: {provider}")
    print(f"Model:    {model}")
    print(f"Ticker:   {ticker} ({company_name})")
    print(f"Date:     {trade_date}")
    print()

    # ------------------------------------------------------------------
    # 1. Build LLM + news_analyst node
    # ------------------------------------------------------------------
    client = create_llm_client(provider=provider, model=model, base_url=base_url)
    llm = client.get_llm()
    news_analyst_node = create_news_analyst(llm)

    # Build the news ToolNode (same one the graph uses).
    tool_node = _find_tool_node(ticker, trade_date)

    # ------------------------------------------------------------------
    # 2. Initial state
    # ------------------------------------------------------------------
    state = {
        "trade_date": trade_date,
        "company_of_interest": ticker,
        "company_name": company_name,
        "asset_type": "stock",
        "messages": [
            HumanMessage(
                content=(
                    f"Analyze recent news and trends for {company_name} ({ticker}) "
                    f"as of {trade_date}. Use all available tools including "
                    f"get_cailianpress_telegrams to fetch real-time flash news. "
                    f"After gathering data, produce a comprehensive news report."
                )
            ),
        ],
    }

    # ------------------------------------------------------------------
    # 3. Simulate the tool loop
    # ------------------------------------------------------------------
    print("=" * 60)
    print("Running news_analyst with tool loop...")
    print("=" * 60)
    print()

    tool_calls_made = set()

    for round_idx in range(max_tool_rounds):
        result = news_analyst_node(state)
        last = result["messages"][-1]

        if isinstance(last, AIMessage):
            content = (last.content or "").strip()
            tcs = getattr(last, "tool_calls", None) or []

            if tcs:
                for tc in tcs:
                    name = tc.get("name", "?")
                    args = tc.get("args", {})
                    tool_calls_made.add(name)
                    print(f"  Round {round_idx}: LLM calls {name}({args})")
                    print() if round_idx == 0 else None

                # Add the AIMessage to state so ToolNode can read tool_calls.
                state["messages"].append(last)
                tool_result = tool_node.invoke(state)
                # tool_result is a list of ToolMessages — add them all.
                for msg in tool_result:
                    state["messages"].append(msg)
                continue

            if content:
                # LLM produced final content — done.
                report = result.get("news_report", "")
                if not report:
                    report = content
                break

        # Fallback: use whatever report the node produced.
        report = result.get("news_report", "") or (last.content or "")
        break
    else:
        print(f"  ⚠️  Reached max {max_tool_rounds} tool rounds without a final answer")
        report = ""

    # ------------------------------------------------------------------
    # 4. Display the report
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("TOOLS CALLED")
    print("=" * 60)
    for t in sorted(tool_calls_made):
        mark = "✅" if t == "get_cailianpress_telegrams" else "  "
        print(f"  {mark} {t}")

    print()
    print("=" * 60)
    print("NEWS ANALYST REPORT")
    print("=" * 60)
    print()
    print(report[:4000] if len(report) > 4000 else report)

    # ------------------------------------------------------------------
    # 5. Verification
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("VERIFICATION CHECKS")
    print("=" * 60)
    print()

    checks = [
        ("Report is non-empty", bool(report.strip())),
        ("get_cailianpress_telegrams was called",
         "get_cailianpress_telegrams" in tool_calls_made),
        ("Contains 财联社 or Cailianpress",
         "财联社" in report or "Cailianpress" in report),
        ("Contains ticker 600519", "600519" in report),
        ("Contains 贵州茅台", "贵州茅台" in report),
    ]

    all_pass = True
    for label, ok in checks:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {label}")
        all_pass = all_pass and ok

    print()
    if all_pass:
        print("✅ All checks passed — Cailianpress is integrated in the news report!")
    else:
        print("❌ Some checks failed — review the output above.")
    print()

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
