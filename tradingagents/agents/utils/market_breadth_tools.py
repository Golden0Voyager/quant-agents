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
