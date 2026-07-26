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
