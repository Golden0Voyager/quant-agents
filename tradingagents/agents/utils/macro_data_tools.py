from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator: a friendly alias such as 'cpi', 'core_pce', "
        "'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve', "
        "'real_gdp', 'vix', 'pmi', 'm2', 'social_finance', or a raw FRED "
        "series ID such as 'CPIAUCSL'.",
    ],
    curr_date: Annotated[str | None, "Current date in yyyy-mm-dd format; the end of the window"] = None,
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
) -> str:
    """
    Retrieve macroeconomic indicators: FRED series (Federal Reserve Economic Data
    for policy rates, Treasury yields, inflation, labor, growth) or A-share macro
    data (PMI, CPI, M2, Social Financing via akshare). Uses the configured
    macro_data vendor.

    Args:
        indicator (str): Friendly alias or raw series ID
        curr_date (str): Current date in yyyy-mm-dd format; end of the window
        look_back_days (int): Trailing window length; omit for a 1-year window

    Returns:
        str: A formatted markdown report of the macro series
    """
    return route_to_vendor("get_macro_indicators", indicator, curr_date, look_back_days)


@tool
def get_us_macro(
    periods: Annotated[int, "Number of recent trading days to return (default 120)"] = 120,
) -> str:
    """
    Retrieve US daily macro indicators from the local archive: Fed funds rate
    (effr), 3M/2Y/10Y Treasury yields, 10Y-3M term spread, 10Y real rate,
    5Y/10Y breakeven inflation expectations, initial claims (icsa),
    HY/IG credit spreads (OAS) and the STLFSI financial stress index.
    Credit spreads and STLFSI are high-value inputs for risk assessment.
    Uses the configured macro_data vendor (smartmoney_db local archive only).
    Args:
        periods (int): Number of recent trading days to return (default 120)
    Returns:
        str: A CSV-formatted table of US macro daily indicators
    """
    return route_to_vendor("get_us_macro", periods)


@tool
def get_cftc_cot(
    instrument: Annotated[
        str | None,
        "Commodity instrument Chinese name, e.g. '白银', '黄金', '纽约原油', "
        "'大豆', '玉米', '棉花', '原糖', '豆油', '豆粕', '铂金', '钯金', "
        "'纽约天然气'. Omit to get the whole goods complex (12 commodities) "
        "as a wide net-position table. This is NOT a stock ticker.",
    ] = None,
    periods: Annotated[int, "Number of recent weeks to return (default 52)"] = 52,
) -> str:
    """
    Retrieve CFTC Commitments of Traders (每周持仓报告) positioning: weekly
    long/short/net positions. Use it for commodity-sensitive names to gauge
    speculative positioning extremes and crowded trades in the underlying.
    Uses the configured macro_data vendor (smartmoney_db local archive only).
    Args:
        instrument (str | None): Commodity instrument Chinese name; omit for all goods
        periods (int): Number of recent weeks to return (default 52)
    Returns:
        str: A formatted report of CFTC positioning
    """
    return route_to_vendor("get_cftc_cot", instrument, periods)


@tool
def get_eia_petroleum(
    series_id: Annotated[
        str | None,
        "EIA series ID, e.g. 'PET.WCESTUS1.W' (commercial crude ex-SPR), "
        "'PET.WCSSTUS1.W' (SPR), 'PET.WGTSTUS1.W' (gasoline), "
        "'PET.WCRFPUS2.W' (US production), 'PET.WPULEUS3.W' (refinery "
        "utilization). Omit to get all five series.",
    ] = None,
    periods: Annotated[int, "Number of recent weeks to return (default 156)"] = 156,
) -> str:
    """
    Retrieve EIA weekly petroleum statistics: crude/gasoline/SPR inventories,
    US crude production and refinery utilization. Use it when analyzing the
    energy chain (oil & gas, oilfield services, petrochemicals).
    Uses the configured macro_data vendor (smartmoney_db local archive only).
    Args:
        series_id (str | None): Exact EIA series ID; omit for all series
        periods (int): Number of recent weeks to return (default 156)
    Returns:
        str: A formatted report of EIA weekly petroleum data
    """
    return route_to_vendor("get_eia_petroleum", series_id, periods)



@tool
def get_hk_tech_index(
    periods: Annotated[int, "Number of recent trading days to return (default 120)"] = 120,
) -> str:
    """
    Retrieve Hang Seng Tech Index (恒生科技指数) daily OHLCV bars from the
    local archive — the risk-appetite gauge for China tech, relevant when
    analyzing tech supply-chain or platform-economy names.
    Uses the configured macro_data vendor (smartmoney_db local archive only).
    Args:
        periods (int): Number of recent trading days to return (default 120)
    Returns:
        str: A CSV-formatted table of Hang Seng Tech daily bars
    """
    return route_to_vendor("get_hk_tech_index", periods)


@tool
def get_fx_rate(
    currency: Annotated[str, "Currency name in Chinese, e.g. '美元'"] = "美元",
    periods: Annotated[int, "Number of recent days to return (default 60)"] = 60,
) -> str:
    """
    Retrieve onshore CNY central-parity / BOC quotes (在岸人民币牌价):
    central parity, bank/cash buy and sell prices. RMB appreciation
    benefits importers and airlines; depreciation benefits exporters.
    Uses the configured macro_data vendor (smartmoney_db local archive only).
    Args:
        currency (str): Currency name in Chinese (default 美元)
        periods (int): Number of recent days to return (default 60)
    Returns:
        str: A formatted table of CNY quotes
    """
    return route_to_vendor("get_fx_rate", currency, periods)


@tool
def get_central_bank_balance(
    periods: Annotated[int, "Number of recent months to return (default 36)"] = 36,
) -> str:
    """
    Retrieve the PBOC balance sheet (央行资产负债表, monthly): total assets,
    reserve money, currency in circulation, claims on banks/government,
    government deposits, foreign assets and FX reserves. Liquidity-cycle
    context for the whole market.
    Uses the configured macro_data vendor (smartmoney_db local archive only).
    Args:
        periods (int): Number of recent months to return (default 36)
    Returns:
        str: A CSV-formatted table of the PBOC balance sheet
    """
    return route_to_vendor("get_central_bank_balance", periods)
