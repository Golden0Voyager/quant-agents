from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_index_daily(
    index_code: Annotated[
        str,
        "A-share index code, e.g. 000001.SS (Shanghai Composite), 399001.SZ (Shenzhen Component), "
        "399006.SZ (ChiNext), 000688.SS (STAR Market).",
    ],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve daily OHLCV history for a major A-share market index.

    Use this to compare the stock's price action against the broad market or
    its home board (Shanghai/Shenzhen/ChiNext/STAR Market). The data comes from
    the configured core_stock_apis vendor (smartmoney_db local cache for A-shares).

    Args:
        index_code: A-share index code with exchange suffix.
        start_date: Start date in yyyy-mm-dd format.
        end_date: End date in yyyy-mm-dd format.

    Returns:
        str: A CSV-formatted OHLCV table for the requested index and date range.
    """
    return route_to_vendor("get_index_daily", index_code, start_date, end_date)
