from langchain_core.tools import tool
from typing import Annotated, Optional
from tradingagents.dataflows.interface import route_to_vendor

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[Optional[int], "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[Optional[int], "Max articles to return; omit to use the configured default"] = None,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)


@tool
def get_company_announcements(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve company announcements (notices, reports, regulatory filings) for a given ticker.
    Covers significant matters, financial reports, financing announcements, risk warnings,
    asset restructuring, information changes, and shareholding changes.
    Uses the configured news_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing company announcements
    """
    return route_to_vendor("get_company_announcements", ticker, start_date, end_date)


@tool
def get_restricted_release(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve restricted share release (unlock) events for a given ticker.
    Shows upcoming share supply pressure from locked shares becoming tradable.
    Uses the configured news_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted report of restricted share release events
    """
    return route_to_vendor("get_restricted_release", ticker, start_date, end_date)


@tool
def get_institutional_holdings(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve institutional holdings and top shareholder data for a given ticker.
    Shows fund holdings, shareholder structure, and smart money positioning.
    Uses the configured news_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of institutional holdings
    """
    return route_to_vendor("get_institutional_holdings", ticker)


@tool
def get_northbound_hold(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve northbound (Stock Connect) foreign investor holding data.
    Shows foreign institutional investor positioning in A-shares via HKEX Stock Connect.
    Uses the configured news_data vendor (akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of northbound holdings
    """
    return route_to_vendor("get_northbound_hold", ticker)


@tool
def get_dragon_tiger(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve dragon-tiger board (龙虎榜) data for a given ticker.
    Shows daily limit-up/exceptional volatility listings with institutional and hot-money trading details.
    Useful for identifying short-term sentiment and hot-money accumulation/distribution.
    Uses the configured news_data vendor (smartmoney_db local cache or akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of dragon-tiger board appearances
    """
    return route_to_vendor("get_dragon_tiger", ticker)


@tool
def get_block_trade(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve block-trade (大宗交易) data for a given ticker.
    Shows off-exchange large-block transactions, discount/premium rates, and buyer/seller brokerages.
    Large discounts may signal institutional selling; premiums suggest institutional buying.
    Uses the configured news_data vendor (smartmoney_db local cache or akshare for A-shares).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of block-trade transactions
    """
    return route_to_vendor("get_block_trade", ticker)


@tool
def get_pledge_ratio(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve pledge-ratio (股权质押) data for a given ticker.
    Shows shareholder pledge details, pledge ratio, and estimated liquidation lines.
    High pledge ratio combined with falling stock price indicates liquidation risk.
    Uses the configured governance_risk vendor (akshare for A-shares, real-time).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of pledge ratio details
    """
    return route_to_vendor("get_pledge_ratio", ticker)


@tool
def get_research_reports(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Retrieve research reports (个股研报) for a given ticker.
    Shows broker ratings, target prices, and analyst opinions.
    Useful for understanding institutional consensus and forward-looking expectations.
    Uses the configured research_opinion vendor (akshare for A-shares, real-time).
    Args:
        ticker (str): Ticker symbol
    Returns:
        str: A formatted report of research reports
    """
    return route_to_vendor("get_research_reports", ticker)
