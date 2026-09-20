from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_limit_up_down(
    trade_date: Annotated[
        str,
        "Trading date in YYYY-MM-DD format. "
        "Caller should pass the analysis date.",
    ],
) -> str:
    """
    Retrieve market-wide limit-up and limit-down statistics for an A-share trading day.

    Shows the number of stocks that hit the daily upper limit (涨停) and lower limit
    (跌停), plus sample stocks when available. This is a short-term market sentiment
    and breadth indicator: many limit-up stocks with few limit-down stocks suggests
    strong risk appetite, while the reverse suggests risk-off sentiment.

    Uses the configured technical_indicators vendor (smartmoney_db local cache for
    A-shares).

    Args:
        trade_date: Trading date in YYYY-MM-DD format.

    Returns:
        str: A formatted report of limit-up/limit-down statistics.
    """
    return route_to_vendor("get_limit_up_down", trade_date)


@tool
def get_auction_snapshot(
    ticker: Annotated[
        str,
        "A-share ticker in any supported format (e.g. 600519.SS).",
    ],
) -> str:
    """
    Retrieve the call-auction (集合竞价) snapshot for an A-share stock.

    Shows auction price/pct change, auction volume and amount, unmatched
    volume, auction turnover, and volume ratio versus yesterday's auction —
    a direct read on pre-open strength and opening auction sentiment. The
    output carries the auction phase (live vs. closed/final); after the close
    it describes that morning's auction, which still explains the day's open.

    Args:
        ticker: A-share ticker.

    Returns:
        str: A formatted auction snapshot block.
    """
    return route_to_vendor("get_auction_snapshot", ticker)


@tool
def get_short_term_benchmark() -> str:
    """
    Retrieve the market-wide short-term sentiment gauge (短线风向标).

    Lists a handful of benchmark stocks with their call-auction pct change
    and sector tags — a quick read on where short-term money is pointing
    before/at the open.

    Returns:
        str: A formatted table of benchmark stocks.
    """
    return route_to_vendor("get_short_term_benchmark")


@tool
def get_option_sentiment(
    periods: Annotated[int, "Number of recent trading days to return (default 60)"] = 60,
) -> str:
    """
    Retrieve 50ETF option sentiment (期权情绪): QVIX (China's VIX), put-call
    ratio (PCR), put/call volumes and open interest. Elevated QVIX or PCR
    spikes flag fear/hedging extremes in the A-share market.
    Uses the configured technical_indicators vendor (smartmoney_db local archive only).
    Args:
        periods (int): Number of recent trading days to return (default 60)
    Returns:
        str: A formatted table of option sentiment indicators
    """
    return route_to_vendor("get_option_sentiment", periods)


@tool
def get_index_futures_basis(
    futures_code: Annotated[
        str | None,
        "Index futures code, e.g. 'IF0' (沪深300), 'IC0' (中证500), 'IH0' "
        "(上证50), 'IM0' (中证1000). Omit for all contracts as a wide table.",
    ] = None,
    periods: Annotated[int, "Number of recent trading days to return (default 60)"] = 60,
) -> str:
    """
    Retrieve index-futures basis (期指基差): futures vs index price and the
    basis percentage. Deepening discount (negative basis) signals bearish
    hedging sentiment — a market-risk input.
    Uses the configured technical_indicators vendor (smartmoney_db local archive only).
    Args:
        futures_code (str | None): Futures code e.g. 'IF0'; omit for all
        periods (int): Number of recent trading days to return (default 60)
    Returns:
        str: A formatted report of index-futures basis
    """
    return route_to_vendor("get_index_futures_basis", futures_code, periods)


@tool
def get_sector_daily(
    sector_name: Annotated[str, "Sector name in Chinese exactly as stored, e.g. 半导体"],
    periods: Annotated[int, "Number of recent trading days to return (default 120)"] = 120,
) -> str:
    """
    Retrieve sector daily OHLCV bars (板块日线) from the local archive —
    the sector's own price trend, complementing sector fund-flow data.
    Uses the configured technical_indicators vendor (smartmoney_db local archive only).
    Args:
        sector_name (str): Sector name in Chinese (NOT a ticker)
        periods (int): Number of recent trading days to return (default 120)
    Returns:
        str: A CSV-formatted table of sector daily bars
    """
    return route_to_vendor("get_sector_daily", sector_name, periods)


@tool
def get_sector_valuation(
    sector_name: Annotated[str, "Sector name in Chinese exactly as stored, e.g. 半导体"],
    periods: Annotated[int, "Number of recent days to return (default 120)"] = 120,
) -> str:
    """
    Retrieve sector valuation (板块估值): PE, PB and total market value
    series — anchor a stock's valuation against its own sector's history.
    Uses the configured technical_indicators vendor (smartmoney_db local archive only).
    Args:
        sector_name (str): Sector name in Chinese (NOT a ticker)
        periods (int): Number of recent days to return (default 120)
    Returns:
        str: A formatted table of sector valuation
    """
    return route_to_vendor("get_sector_valuation", sector_name, periods)
