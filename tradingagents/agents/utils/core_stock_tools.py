from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.stockstats_utils import load_ohlcv


@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve stock price data (OHLCV) for a given ticker symbol.
    Uses the same cached OHLCV source as the verified market snapshot so
    analysts and downstream agents see consistent prices.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
    """
    data = load_ohlcv(symbol, end_date)
    filtered = data[
        (data["Date"] >= start_date) & (data["Date"] <= end_date)
    ]
    return filtered.to_csv(index=False)


@tool
def get_chip_distribution(
    symbol: Annotated[str, "A-share ticker symbol e.g. 600519.SS or 000001.SZ"],
    curr_date: Annotated[str | None, "Current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve chip distribution (筹码分布), average holder cost, concentration, and cost bias.
    Provides key insights into profit ratio and dynamic cost support/resistance zones.
    """
    from tradingagents.dataflows.smartmoney_vendor import get_chip_distribution as _get_chip
    try:
        return _get_chip(symbol, curr_date)
    except Exception as exc:
        return f"NO_DATA_AVAILABLE: Chip distribution unavailable for {symbol} ({exc})"

