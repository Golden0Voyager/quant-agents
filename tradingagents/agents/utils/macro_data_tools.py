from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_macro_indicators(
    indicator: Annotated[str, 'Macro indicator type: "pmi", "cpi", "m2", "social_finance"'],
) -> str:
    """
    Retrieve China macro quantitative indicators (PMI, CPI, M2, Social Financing).
    Provides top-down context for assessing the macroeconomic environment.
    Uses the configured news_data vendor (akshare for A-shares).
    Args:
        indicator (str): Indicator type - "pmi", "cpi", "m2", or "social_finance"
    Returns:
        str: A formatted report of macro indicator data
    """
    return route_to_vendor("get_macro_indicators", indicator)
