from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_lithium_spot(
    periods: Annotated[int, "Number of recent spot records to return (default 60)"] = 60,
) -> str:
    """
    Retrieve China lithium carbonate (碳酸锂) spot price and futures basis data.
    Shows the 生意社 spot quote, near/dominant GFEX contract prices, and the
    dominant-contract basis — key input cost signals for lithium battery /
    lithium mining / new-energy supply-chain names.
    Uses the configured commodity_data vendor (smartmoney_db local archive only).
    Args:
        periods (int): Number of recent spot records to return (default 60)
    Returns:
        str: A formatted report of lithium spot prices and basis
    """
    return route_to_vendor("get_lithium_spot", periods)


@tool
def get_commodity_futures(
    variety: Annotated[
        str,
        "Futures variety code, e.g. 'AG' (白银/silver), 'LC' (碳酸锂/lithium "
        "carbonate), 'CU' (铜/copper), 'AL' (铝/aluminium). "
        "This is a variety code, NOT a stock ticker.",
    ],
    periods: Annotated[int, "Number of recent trading days to return (default 60)"] = 60,
) -> str:
    """
    Retrieve Chinese commodity futures daily bars (OHLCV, volume, open interest,
    daily change %) for one variety. Track the underlying commodity's price
    trend and positioning when analyzing commodity-sensitive names (precious
    metals, nonferrous, lithium, chemicals).
    Uses the configured commodity_data vendor (smartmoney_db local archive only).
    Args:
        variety (str): Futures variety code such as 'AG' or 'LC' (NOT a ticker)
        periods (int): Number of recent trading days to return (default 60)
    Returns:
        str: A formatted report of futures daily bars
    """
    return route_to_vendor("get_commodity_futures", variety, periods)


@tool
def get_gold_price(
    periods: Annotated[int, "Number of recent quotes to return (default 60)"] = 60,
) -> str:
    """
    Retrieve SGE gold prices (上海金交所金价): daily evening and morning
    settlement quotes. Key input for gold miners, jewellery retailers and
    precious-metals-linked names.
    Uses the configured commodity_data vendor (smartmoney_db local archive only).
    Args:
        periods (int): Number of recent quotes to return (default 60)
    Returns:
        str: A formatted table of SGE gold prices
    """
    return route_to_vendor("get_gold_price", periods)
