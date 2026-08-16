from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_industry_valuation(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve industry valuation comparison (PE, PB) for the given ticker.
    Compares the stock's valuation against industry peers and historical benchmarks.
    Uses the configured fundamental_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of industry valuation comparison
    """
    return route_to_vendor("get_industry_valuation", ticker)


@tool
def get_concept_board(
    ticker: Annotated[str, "Ticker symbol of the company e.g. 600519.SS"],
) -> str:
    """
    Retrieve belonging concept boards (归属概念题材) and sector themes for a given ticker.
    Useful for sector rotation and theme momentum analysis.
    """
    return route_to_vendor("get_concept_board", ticker)
