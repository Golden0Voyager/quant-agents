from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_fund_flow(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[
        str | None,
        "Analysis date (YYYY-MM-DD); only data on or before this date will be returned.",
    ] = None,
) -> str:
    """
    Retrieve individual stock fund flow data (main force, super-large, large, medium, small orders).
    Shows capital inflow/outflow trends to identify smart money accumulation or distribution.
    Uses the configured technical_indicators vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
        curr_date (str | None): Analysis date (YYYY-MM-DD); only data on or before this date will be returned.
    Returns:
        str: A formatted report of fund flow data
    """
    if curr_date is None:
        return route_to_vendor("get_fund_flow", ticker)
    return route_to_vendor("get_fund_flow", ticker, curr_date)


@tool
def get_northbound_hold(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[
        str | None,
        "Analysis date (YYYY-MM-DD); only data on or before this date will be returned.",
    ] = None,
) -> str:
    """
    Retrieve northbound (Stock Connect) foreign investor holding data.
    Shows foreign institutional investor positioning in A-shares via HKEX Stock Connect.
    Uses the configured news_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
        curr_date (str | None): Analysis date (YYYY-MM-DD); only data on or before this date will be returned.
    Returns:
        str: A formatted report of northbound holdings
    """
    if curr_date is None:
        return route_to_vendor("get_northbound_hold", ticker)
    return route_to_vendor("get_northbound_hold", ticker, curr_date)


@tool
def get_margin_trading(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[
        str | None,
        "Analysis date (YYYY-MM-DD); only data on or before this date will be returned.",
    ] = None,
) -> str:
    """
    Retrieve margin-trading (融资融券) data for a given ticker.
    Shows margin balance, short balance, and total leverage to assess speculative sentiment.
    Uses the configured technical_indicators vendor (smartmoney_db local cache or akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
        curr_date (str | None): Analysis date (YYYY-MM-DD); only data on or before this date will be returned.
    Returns:
        str: A formatted report of margin trading data
    """
    if curr_date is None:
        return route_to_vendor("get_margin_trading", ticker)
    return route_to_vendor("get_margin_trading", ticker, curr_date)


@tool
def get_sector_fund_flow(
    sector_name: Annotated[
        str,
        "Sector or industry name in Chinese, e.g. 白酒, 银行, 新能源. "
        "This is NOT a ticker symbol — pass the Chinese industry name "
        "exactly as it appears on Eastmoney (akshare) or in the local DB.",
    ],
    ticker: Annotated[
        str | None,
        "Optional ticker symbol of the target stock (e.g. 600519.SS). "
        "For A-shares ALWAYS pass it: if the sector name does not match, "
        "the system resolves the sector from the stock's registered "
        "industry classification instead of guessing.",
    ] = None,
) -> str:
    """
    Retrieve sector-level fund flow data (板块资金流向).
    Shows main-force, super-large, large, medium and small-order net inflow by industry.
    Uses the configured technical_indicators vendor (smartmoney_db local cache or akshare for A-shares).
    Args:
        sector_name (str): Sector or industry name in Chinese (NOT a ticker)
        ticker (str | None): Target stock ticker; enables registered-industry
            fallback when the sector name misses (A-shares only)
    Returns:
        str: A formatted report of sector fund flow data
    """
    if ticker:
        return route_to_vendor("get_sector_fund_flow", sector_name, ticker=ticker)
    return route_to_vendor("get_sector_fund_flow", sector_name)
