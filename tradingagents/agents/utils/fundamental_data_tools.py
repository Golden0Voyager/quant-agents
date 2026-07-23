from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)


@tool
def get_earnings_estimates(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve analyst earnings estimate consensus for a given ticker.
    Shows forward-looking consensus expectations for revenue, EPS, and profit growth.
    Uses the configured fundamental_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of earnings estimates
    """
    return route_to_vendor("get_earnings_estimates", ticker)


@tool
def get_shareholder_count(
    ticker: Annotated[str, "Ticker symbol"],
    curr_date: Annotated[
        str | None,
        "Analysis date (YYYY-MM-DD); only data on or before this date will be returned.",
    ] = None,
) -> str:
    """
    Retrieve shareholder count (股东户数) data for a given ticker.
    Shows the number of shareholders and average shares per holder to assess筹码集中度.
    Declining shareholder count suggests institutional accumulation; rising count suggests retail influx.
    Uses the configured fundamental_data vendor (smartmoney_db local cache or akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
        curr_date (str | None): Analysis date (YYYY-MM-DD); only data on or before this date will be returned.
    Returns:
        str: A formatted report of shareholder count data
    """
    if curr_date is None:
        return route_to_vendor("get_shareholder_count", ticker)
    return route_to_vendor("get_shareholder_count", ticker, curr_date)


@tool
def get_dividend_history(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve dividend history (分红送转) for a given ticker.
    Shows past dividend schemes, ex-dividend dates, and record dates.
    Useful for dividend yield analysis and ex-dividend timing.
    Uses the configured shareholder_return vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of dividend history
    """
    return route_to_vendor("get_dividend_history", ticker)


@tool
def get_historical_valuation(
    ticker: Annotated[str, "Ticker symbol of the company e.g. 600519.SS"],
    curr_date: Annotated[str, "Current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve 3-year historical PE/PB valuation percentile rank and ROE matching.
    Helps identify deep value (PE percentile < 20% + solid ROE) vs value trap risks.
    """
    from tradingagents.dataflows.smartmoney_vendor import get_historical_valuation as _get_val
    try:
        return _get_val(ticker, curr_date)
    except Exception as exc:
        return f"NO_DATA_AVAILABLE: Historical valuation percentile unavailable for {ticker} ({exc})"


@tool
def get_earnings_forecast(
    ticker: Annotated[str, "Ticker symbol of the company e.g. 600519.SS"],
) -> str:
    """
    Retrieve earnings pre-announcement and profit forecast data for a ticker.
    Provides YoY net profit change expectations and performance pre-announcements.
    """
    from tradingagents.dataflows.smartmoney_vendor import get_earnings_forecast as _get_ef
    try:
        return _get_ef(ticker)
    except Exception as exc:
        return f"NO_DATA_AVAILABLE: Earnings forecast unavailable for {ticker} ({exc})"

