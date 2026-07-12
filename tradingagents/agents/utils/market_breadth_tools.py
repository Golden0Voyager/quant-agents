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
