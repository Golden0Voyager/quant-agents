from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve stock price data (OHLCV) for a given ticker symbol.
    Uses the shared vendor router so runtime memoization, fallback diagnostics,
    and market-session date policies apply consistently.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
    """
    return route_to_vendor("get_stock_data", symbol, start_date, end_date)


@tool
def get_chip_distribution(
    symbol: Annotated[str, "A-share ticker symbol e.g. 600519.SS or 000001.SZ"],
    curr_date: Annotated[str | None, "Current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve chip distribution (筹码分布), average holder cost, concentration, and cost bias.
    Provides key insights into profit ratio and dynamic cost support/resistance zones.
    """
    if curr_date is None:
        return route_to_vendor("get_chip_distribution", symbol)
    return route_to_vendor("get_chip_distribution", symbol, curr_date)
