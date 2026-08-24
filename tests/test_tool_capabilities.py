"""Market capability filtering for analyst-bound tools."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)
from tradingagents.agents.analysts.governance_analyst import create_governance_analyst
from tradingagents.agents.analysts.industry_analyst import create_industry_analyst
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.utils.agent_utils import (
    get_block_trade,
    get_cailianpress_telegrams,
    get_chip_distribution,
    get_company_announcements,
    get_concept_board,
    get_dividend_history,
    get_dragon_tiger,
    get_earnings_estimates,
    get_earnings_forecast,
    get_fund_flow,
    get_historical_valuation,
    get_index_daily,
    get_industry_valuation,
    get_institutional_intelligence,
    get_limit_up_down,
    get_margin_trading,
    get_news,
    get_northbound_hold,
    get_pledge_ratio,
    get_research_reports,
    get_restricted_release,
    get_sector_fund_flow,
    get_shareholder_count,
)
from tradingagents.agents.utils.tool_capabilities import (
    BoundToolsByMarket,
    tools_for_market,
)

_A_SHARE_ONLY_TOOLS = [
    get_block_trade,
    get_cailianpress_telegrams,
    get_chip_distribution,
    get_company_announcements,
    get_concept_board,
    get_dividend_history,
    get_dragon_tiger,
    get_earnings_estimates,
    get_earnings_forecast,
    get_fund_flow,
    get_historical_valuation,
    get_index_daily,
    get_industry_valuation,
    get_institutional_intelligence,
    get_limit_up_down,
    get_margin_trading,
    get_northbound_hold,
    get_pledge_ratio,
    get_research_reports,
    get_restricted_release,
    get_sector_fund_flow,
    get_shareholder_count,
]

_HK_STATE = {
    "company_of_interest": "1810.HK",
    "company_name": "Xiaomi Corporation",
    "trade_date": "2026-08-17",
    "market": "XHKG",
    "asset_type": "stock",
    "instrument_context": "Company: Xiaomi Corporation; Ticker: 1810.HK",
    "messages": [],
}


def _capturing_llm(
    bound_tool_sets: list[set[str]], prompts: list[str] | None = None
) -> MagicMock:
    llm = MagicMock()

    def bind_tools(tools):
        bound_tool_sets.append({item.name for item in tools})
        def invoke(prompt_value):
            if prompts is not None:
                prompts.append(prompt_value.to_messages()[0].content)
            return AIMessage(content="report")

        return RunnableLambda(invoke)

    llm.bind_tools.side_effect = bind_tools
    return llm


@pytest.mark.unit
def test_tools_for_market_excludes_a_share_only_tools_from_hk_and_keeps_xshg():
    hk_names = {tool.name for tool in tools_for_market(_A_SHARE_ONLY_TOOLS, "XHKG")}
    a_share_names = {
        tool.name for tool in tools_for_market(_A_SHARE_ONLY_TOOLS, "XSHG")
    }

    assert hk_names == set()
    assert a_share_names == {tool.name for tool in _A_SHARE_ONLY_TOOLS}


@pytest.mark.unit
def test_tools_without_policy_use_explicit_warned_legacy_compatibility(caplog):
    @tool
    def legacy_tool(value: str) -> str:
        """Return a legacy value."""
        return value

    with caplog.at_level("WARNING"):
        selected = tools_for_market([legacy_tool], "XHKG")

    assert selected == [legacy_tool]
    assert "legacy data policy" in caplog.text
    assert "legacy_tool" in caplog.text


@pytest.mark.unit
def test_bound_tools_cache_single_flights_concurrent_market_binding():
    entered = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()
    llm = MagicMock()

    def bind_tools(tools):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return object()

    llm.bind_tools.side_effect = bind_tools
    cache = BoundToolsByMarket(llm, [get_news, get_research_reports])

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get, "XHKG")
        assert entered.wait(timeout=2)
        second = executor.submit(cache.get, "XHKG")
        release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert calls == 1
    assert first_result is second_result


@pytest.mark.unit
def test_bound_tool_sets_are_immutable_and_isolated_between_instances():
    llm = MagicMock()
    llm.bind_tools.side_effect = lambda tools: object()
    first = BoundToolsByMarket(llm, [get_news, get_research_reports])
    second = BoundToolsByMarket(llm, [get_news, get_research_reports])

    first_tools, _ = first.get("XSHG")
    second_tools, _ = second.get("XSHG")

    assert isinstance(first_tools, tuple)
    assert isinstance(second_tools, tuple)
    assert first_tools == second_tools
    assert first_tools is not second_tools
    assert llm.bind_tools.call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "excluded_names"),
    [
        (
            create_market_analyst,
            {
                "get_chip_distribution",
                "get_fund_flow",
                "get_sector_fund_flow",
                "get_limit_up_down",
                "get_index_daily",
            },
        ),
        (
            create_news_analyst,
            {"get_research_reports", "get_cailianpress_telegrams"},
        ),
        (
            create_governance_analyst,
            {
                "get_company_announcements",
                "get_restricted_release",
                "get_institutional_intelligence",
                "get_northbound_hold",
                "get_margin_trading",
                "get_pledge_ratio",
                "get_dragon_tiger",
                "get_block_trade",
            },
        ),
        (
            create_industry_analyst,
            {
                "get_industry_valuation",
                "get_concept_board",
                "get_sector_fund_flow",
            },
        ),
        (
            create_fundamentals_analyst,
            {
                "get_historical_valuation",
                "get_earnings_forecast",
                "get_earnings_estimates",
                "get_shareholder_count",
                "get_dividend_history",
            },
        ),
    ],
)
def test_hk_analyst_nodes_never_bind_or_call_policy_excluded_tools(
    factory, excluded_names, monkeypatch
):
    bound_tool_sets: list[set[str]] = []
    call_spies = []
    for excluded_tool in _A_SHARE_ONLY_TOOLS:
        if excluded_tool.name not in excluded_names:
            continue
        spy = MagicMock(name=excluded_tool.name)
        monkeypatch.setattr(excluded_tool, "func", spy)
        call_spies.append(spy)

    factory(_capturing_llm(bound_tool_sets))(dict(_HK_STATE))

    assert len(bound_tool_sets) == 1
    assert bound_tool_sets[0].isdisjoint(excluded_names)
    for spy in call_spies:
        spy.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "excluded_names", "retained_name"),
    [
        (
            create_market_analyst,
            {
                "get_chip_distribution",
                "get_fund_flow",
                "get_sector_fund_flow",
                "get_limit_up_down",
                "get_index_daily",
            },
            "get_stock_data",
        ),
        (
            create_news_analyst,
            {"get_research_reports", "get_cailianpress_telegrams"},
            "get_news",
        ),
        (
            create_governance_analyst,
            {
                "get_company_announcements",
                "get_restricted_release",
                "get_institutional_intelligence",
                "get_northbound_hold",
                "get_margin_trading",
                "get_pledge_ratio",
                "get_dragon_tiger",
                "get_block_trade",
            },
            "get_insider_transactions",
        ),
        (
            create_industry_analyst,
            {
                "get_industry_valuation",
                "get_concept_board",
                "get_sector_fund_flow",
            },
            "get_macro_indicators",
        ),
        (
            create_fundamentals_analyst,
            {
                "get_historical_valuation",
                "get_earnings_forecast",
                "get_earnings_estimates",
                "get_shareholder_count",
                "get_dividend_history",
            },
            "get_fundamentals",
        ),
    ],
)
def test_analyst_prompt_guidance_matches_bound_tools_for_hk_and_xshg(
    factory, excluded_names, retained_name
):
    bound_tool_sets: list[set[str]] = []
    prompts: list[str] = []
    node = factory(_capturing_llm(bound_tool_sets, prompts))
    xshg_state = {
        **_HK_STATE,
        "company_of_interest": "600519.SS",
        "company_name": "Kweichow Moutai",
        "market": "XSHG",
        "instrument_context": "Company: Kweichow Moutai; Ticker: 600519.SS",
    }

    node(dict(_HK_STATE))
    node(xshg_state)

    assert len(prompts) == 2
    hk_prompt, xshg_prompt = prompts
    assert retained_name in hk_prompt
    assert retained_name in xshg_prompt
    for tool_name in excluded_names:
        assert tool_name not in hk_prompt
        assert tool_name in xshg_prompt


@pytest.mark.unit
def test_bound_llms_are_cached_per_state_market_without_cross_market_leakage():
    bound_tool_sets: list[set[str]] = []
    node = create_governance_analyst(_capturing_llm(bound_tool_sets))

    node(dict(_HK_STATE))
    xshg_state = {
        **_HK_STATE,
        "company_of_interest": "600519.SS",
        "market": "XSHG",
    }
    node(xshg_state)
    node(dict(_HK_STATE))

    assert len(bound_tool_sets) == 2
    assert "get_northbound_hold" not in bound_tool_sets[0]
    assert "get_northbound_hold" in bound_tool_sets[1]


@pytest.mark.unit
def test_state_market_identity_wins_over_ticker_inference_for_tool_binding():
    bound_tool_sets: list[set[str]] = []
    conflicting_state = {
        **_HK_STATE,
        "company_of_interest": "600519.SS",
        "market": "XHKG",
    }

    create_market_analyst(_capturing_llm(bound_tool_sets))(conflicting_state)

    assert "get_limit_up_down" not in bound_tool_sets[0]
