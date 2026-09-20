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


@tool
def get_ah_premium(
    symbol: Annotated[str, "A-share ticker of an A+H listed company e.g. 688981.SS"],
    periods: Annotated[int, "Number of recent trading days to return (default 60)"] = 60,
) -> str:
    """
    Retrieve the A/H premium series for an A+H dual-listed company.
    A persistently high premium means the A-share trades rich versus its
    H-share — relevant for valuation and cross-market arbitrage analysis.
    Uses the configured core_stock_apis vendor (smartmoney_db local archive only).
    Args:
        symbol (str): A-share ticker of an A+H company
        periods (int): Number of recent trading days to return (default 60)
    Returns:
        str: A formatted report of the A/H premium series
    """
    return route_to_vendor("get_ah_premium", symbol, periods)


@tool
def get_etf_daily(
    ts_code: Annotated[str, "ETF code e.g. 510300 (沪深300ETF) or 510050"],
    periods: Annotated[int, "Number of recent trading days to return (default 120)"] = 120,
) -> str:
    """
    Retrieve ETF daily OHLCV bars from the local archive (20 major ETFs,
    e.g. 510300 沪深300ETF, 510050 上证50ETF, 518880 黄金ETF, 588000 科创50ETF).
    Useful for benchmarking a stock against its index or tracking sector ETFs.
    Uses the configured core_stock_apis vendor (smartmoney_db local archive only).
    Args:
        ts_code (str): ETF code e.g. 510300
        periods (int): Number of recent trading days to return (default 120)
    Returns:
        str: A CSV-formatted table of ETF daily bars
    """
    return route_to_vendor("get_etf_daily", ts_code, periods)


@tool
def get_cb_quotation() -> str:
    """
    Retrieve the convertible-bond snapshot (可转债行情): top 50 bonds by
    double-low value (双低值 = price + premium, ascending) — the classic
    cheap-CB ranking. Bonds priced below 50 (delisted/junk artefacts) are
    excluded.
    Uses the configured core_stock_apis vendor (smartmoney_db local archive only).
    Returns:
        str: A formatted table of the cheapest convertible bonds
    """
    return route_to_vendor("get_cb_quotation")


@tool
def get_cb_redeem() -> str:
    """
    Retrieve convertible bonds with an active redemption flag (可转债强赎状态):
    已公告强赎 (redemption announced — sell or convert before the deadline) and
    公告不强赎 (no-redemption pledge). Essential when analyzing CBs or their
    underlying stocks.
    Uses the configured core_stock_apis vendor (smartmoney_db local archive only).
    Returns:
        str: A formatted table of CB redemption flags
    """
    return route_to_vendor("get_cb_redeem")


@tool
def get_cb_index(
    index_code: Annotated[
        str | None,
        "Convertible-bond index code, e.g. 'JSL_EW' (集思录可转债等权指数). "
        "Omit to get all indices as a wide table.",
    ] = None,
    periods: Annotated[int, "Number of recent trading days to return (default 120)"] = 120,
) -> str:
    """
    Retrieve convertible-bond index daily bars (转债指数) — the CB market
    trend and risk appetite gauge.
    Uses the configured core_stock_apis vendor (smartmoney_db local archive only).
    Args:
        index_code (str | None): CB index code; omit for all indices
        periods (int): Number of recent trading days to return (default 120)
    Returns:
        str: A formatted report of CB index bars
    """
    return route_to_vendor("get_cb_index", index_code, periods)
