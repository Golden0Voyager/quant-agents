"""Tests for the HiThink (同花顺) vendor implementations and chain registration.

All HTTP is mocked at ``hithink_vendor.hithink_get``; fixtures follow the
documented Financial-API contract (docs/tonghuashun_api.md): statement rows in
``data.item[]`` (yuan, null = undisclosed), indicator rows in
``data.abilities[]``, hot rank as a market-wide Top30 list.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from tradingagents.dataflows import hithink_vendor
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
)

# Noon local time so the derived date is stable in any plausible test-runner tz.
_PERIOD_END_MS = int(datetime(2025, 3, 31, 12, 0, 0).timestamp() * 1000)

_INCOME_ITEM = {
    "fiscal_year": 2025,
    "fiscal_period": 1,
    "period_end_ms": _PERIOD_END_MS,
    "report_date_ms": _PERIOD_END_MS,
    "currency": "CNY",
    "operating_income": 41_580_000_000.0,
    "operating_costs": 4_158_000_000.0,
    "sales_fee": 1_000_000_000.0,
    "manage_fee": 2_000_000_000.0,
    "research_and_development_expenses": None,  # 未披露 → 跳过
    "operating_profit": 26_530_000_000.0,
    "profit_total": 26_600_000_000.0,
    "income_tax_expense": 6_600_000_000.0,
    "net_profit": 20_000_000_000.0,
    "parent_holder_net_profit": 19_840_000_000.0,
    "basic_eps": 15.78,
}

_BALANCE_ITEM = {
    "fiscal_year": 2025,
    "fiscal_period": 1,
    "period_end_ms": _PERIOD_END_MS,
    "currency": "CNY",
    "assets_total": 554_600_000_000.0,
    "total_current_assets": 442_000_000_000.0,
    "cash": 178_000_000_000.0,
    "accounts_receivable": None,
    "total_debt": 102_590_000_000.0,
    "holder_equity_total": 452_010_000_000.0,
}

_CASHFLOW_ITEM = {
    "fiscal_year": 2025,
    "fiscal_period": 1,
    "period_end_ms": _PERIOD_END_MS,
    "currency": "CNY",
    "act_cash_flow_net": 24_000_000_000.0,
    "invest_cash_flow_net": -8_000_000_000.0,
    "financing_cash_flow_net": -15_000_000_000.0,
    "pay_fixed_assets_etc_cash": 1_200_000_000.0,
    "pay_dividends_profits_interest_cash": 14_000_000_000.0,
    "cash_equivalents_net_addition": 1_000_000_000.0,
}

# Real payload shape verified live 2026-08-26 (600519.SH):
# abilities[] groups by ability, indicators[] carry index_id + string value.
_ABILITIES = {
    "abilities": [
        {
            "ability": "profitability",
            "indicators": [
                {"index_id": "index_weighted_avg_roe", "value": "15.23"},
                {"index_id": "sale_gross_margin", "value": "91.5"},
            ],
        },
        {
            "ability": "solvency",
            "indicators": [
                {"index_id": "assets_debt_ratio", "value": "18.5"},
                {"index_id": "earned_interest_multiple", "value": None},
            ],
        },
    ]
}

_HOT_LIST = {
    "item": [
        {
            "rank": 1,
            "thscode": "600519.SH",
            "ticker": "600519",
            "name": "贵州茅台",
            "heat": "987654",
            "rank_change": 3,
            "rank_trend": "up",
        },
        {
            "rank": 2,
            "thscode": "000001.SZ",
            "ticker": "000001",
            "name": "平安银行",
            "heat": "12345",
            "rank_change": 0,
            "rank_trend": "flat",
        },
    ]
}


def _patch_get(return_value):
    return patch(
        "tradingagents.dataflows.hithink_vendor.hithink_get",
        return_value=return_value,
    )


class TestIncomeStatement:
    def test_formats_latest_period_like_akshare(self):
        with _patch_get({"item": [_INCOME_ITEM]}) as mock_get:
            result = hithink_vendor.get_income_statement("600519.SS")

        mock_get.assert_called_once_with(
            "/api/a-share/financials/income-statements",
            {"thscode": "600519.SH", "period": "quarterly", "limit": 4},
        )
        assert "# Income Statement for 600519.SS (2025-03-31)" in result
        assert "hithink" in result
        assert "营业总收入: 415.80亿" in result
        assert "归母净利润: 198.40亿" in result
        assert "基本每股收益: 15.7800" in result
        # null (未披露) fields are passed through — skipped, never zero-filled
        assert "研发费用" not in result

    def test_annual_freq_maps_to_period_annual(self):
        with _patch_get({"item": [_INCOME_ITEM]}) as mock_get:
            hithink_vendor.get_income_statement("600519.SS", freq="annual")
        assert mock_get.call_args.args[1]["period"] == "annual"

    def test_empty_item_raises_no_market_data(self):
        with (
            _patch_get({"item": []}),
            pytest.raises(NoMarketDataError, match="600519.SS"),
        ):
            hithink_vendor.get_income_statement("600519.SS")

    def test_non_dict_rows_raise_no_market_data(self):
        with (
            _patch_get({"item": ["bogus"]}),
            pytest.raises(NoMarketDataError),
        ):
            hithink_vendor.get_income_statement("600519.SS")


class TestBalanceSheet:
    def test_basic(self):
        with _patch_get({"item": [_BALANCE_ITEM]}):
            result = hithink_vendor.get_balance_sheet("600519.SS")
        assert "# Balance Sheet for 600519.SS (2025-03-31)" in result
        assert "总资产: 5546.00亿" in result
        assert "货币资金: 1780.00亿" in result
        assert "应收账款" not in result  # null skipped

    def test_empty_raises_no_market_data(self):
        with (
            _patch_get({}),
            pytest.raises(NoMarketDataError, match="balance sheet"),
        ):
            hithink_vendor.get_balance_sheet("600519.SS")


class TestCashflow:
    def test_basic(self):
        with _patch_get({"item": [_CASHFLOW_ITEM]}):
            result = hithink_vendor.get_cashflow("000001.SZ")
        assert "# Cash Flow Statement for 000001.SZ (2025-03-31)" in result
        assert "经营活动现金流净额: 240.00亿" in result
        assert "投资活动现金流净额: -80.00亿" in result
        assert "现金及等价物净增加额: 10.00亿" in result


class TestNonAShare:
    @pytest.mark.parametrize(
        "call",
        [
            lambda: hithink_vendor.get_income_statement("AAPL"),
            lambda: hithink_vendor.get_balance_sheet("1810.HK"),
            lambda: hithink_vendor.get_cashflow("0700.HK"),
            lambda: hithink_vendor.get_indicators("AAPL", "roe", "2026-08-26"),
            lambda: hithink_vendor.get_hot_rank("AAPL"),
        ],
    )
    def test_fast_fail_without_http(self, call):
        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get"
        ) as mock_get, pytest.raises(NoMarketDataError, match="A-shares only"):
            call()
        mock_get.assert_not_called()


class TestKeyNotConfigured:
    def test_vendor_not_configured_propagates(self, monkeypatch):
        monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
        with pytest.raises(VendorNotConfiguredError):
            hithink_vendor.get_income_statement("600519.SS")


class TestFinancialIndicators:
    def test_roe_lookup_from_abilities(self):
        with _patch_get(_ABILITIES) as mock_get:
            result = hithink_vendor.get_indicators(
                "600519.SS", "roe", "2026-08-26", 30
            )
        # 2026-08 → latest published period is Q2 → report=2026-2
        mock_get.assert_called_once_with(
            "/api/a-share/financials/indicators",
            {"thscode": "600519.SH", "report": "2026-2"},
        )
        assert "roe values for 600519.SS" in result
        assert "2026-2" in result
        assert "净资产收益率(ROE,加权) = 15.23" in result

    def test_chinese_alias(self):
        with _patch_get(_ABILITIES):
            result = hithink_vendor.get_indicators(
                "600519.SS", "毛利率", "2026-08-26"
            )
        assert "毛利率 = 91.5" in result

    def test_technical_indicator_fast_fails_without_http(self):
        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get"
        ) as mock_get, pytest.raises(NoMarketDataError, match="rsi_14"):
            hithink_vendor.get_indicators("600519.SS", "rsi_14", "2026-08-26")
        mock_get.assert_not_called()

    def test_empty_abilities_raise_no_market_data(self):
        with (
            _patch_get({"abilities": []}),
            pytest.raises(NoMarketDataError, match="financial indicators"),
        ):
            hithink_vendor.get_indicators("600519.SS", "roe", "2026-08-26")

    def test_indicator_missing_from_payload_raises_no_market_data(self):
        with (
            _patch_get(_ABILITIES),
            pytest.raises(NoMarketDataError, match="not found"),
        ):
            hithink_vendor.get_indicators("600519.SS", "quick_ratio", "2026-08-26")

    @pytest.mark.parametrize(
        ("curr_date", "expected"),
        [
            ("2026-01-15", "2025-3"),
            ("2026-05-10", "2026-1"),
            ("2026-08-26", "2026-2"),
            ("2026-11-05", "2026-3"),
        ],
    )
    def test_report_period_lag(self, curr_date, expected):
        assert hithink_vendor._report_period_for(curr_date) == expected


class TestHotRank:
    def test_found_in_top30(self):
        with _patch_get(_HOT_LIST) as mock_get:
            result = hithink_vendor.get_hot_rank("600519.SS")
        mock_get.assert_called_once_with("/api/a-share/special-data/hot-stock-list")
        assert "同花顺热榜 — 600519.SS" in result
        assert "整体热度排名: #1" in result
        assert "贵州茅台(600519.SH)" in result
        assert "热度: 987654" in result
        assert "排名变动: 3（上升）" in result

    def test_flat_trend_without_rank_change(self):
        rows = [dict(r) for r in _HOT_LIST["item"]]
        rows[0].pop("rank_change")
        with _patch_get({"item": rows}):
            result = hithink_vendor.get_hot_rank("600519.SS")
        assert "排名趋势: 上升" in result

    def test_not_in_top30_falls_back(self):
        with (
            _patch_get(_HOT_LIST),
            pytest.raises(NoMarketDataError, match="not in hithink Top30"),
        ):
            hithink_vendor.get_hot_rank("002241.SZ")

    def test_empty_payload_raises_no_market_data(self):
        with (
            _patch_get({}),
            pytest.raises(NoMarketDataError, match="empty hot-stock-list"),
        ):
            hithink_vendor.get_hot_rank("600519.SS")

    def test_alternate_list_key_tolerated(self):
        with _patch_get({"list": _HOT_LIST["item"]}):
            result = hithink_vendor.get_hot_rank("600519.SS")
        assert "贵州茅台" in result


class TestChainRegistration:
    """hithink sits between smartmoney_db and akshare on A-share default chains."""

    @pytest.mark.parametrize(
        "method",
        ["get_income_statement", "get_balance_sheet", "get_cashflow", "get_indicators"],
    )
    def test_hithink_between_local_db_and_akshare(self, method):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain(method, "default", "600519.SS")
        assert chain[:3] == ["smartmoney_db", "hithink", "akshare"]

    def test_unrelated_method_chain_unchanged(self):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain("get_fundamentals", "default", "600519.SS")
        assert chain[:2] == ["smartmoney_db", "akshare"]
        assert "hithink" not in chain

    def test_explicit_chain_respected_verbatim(self):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain(
            "get_income_statement", "akshare,hithink", "600519.SS"
        )
        assert chain == ["akshare", "hithink"]

    def test_hot_rank_chain_prefers_hithink(self):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain(
            "fetch_eastmoney_hot_rank", "default", "600519.SS"
        )
        assert chain == ["hithink", "eastmoney"]

    def test_default_config_wires_hithink_after_local_db(self):
        from tradingagents.default_config import default_config

        vendors = default_config()["data_vendors"]
        for category in ("fundamental_data", "technical_indicators"):
            chain = vendors[category].split(",")
            assert chain.index("hithink") == chain.index("smartmoney_db") + 1
            assert chain.index("hithink") < chain.index("akshare")


class TestHotRankRouting:
    def test_hithink_success_short_circuits_eastmoney(self):
        from tradingagents.dataflows import interface

        with (
            _patch_get(_HOT_LIST),
            patch(
                "tradingagents.dataflows.interface.fetch_eastmoney_hot_rank"
            ) as mock_em,
        ):
            result = interface.route_to_vendor_with_source(
                "fetch_eastmoney_hot_rank", "600519.SS"
            )
        assert result.vendor == "hithink"
        assert "同花顺热榜" in result.data
        mock_em.assert_not_called()

    def test_hithink_failure_falls_back_to_eastmoney(self):
        from tradingagents.dataflows import interface

        with (
            patch(
                "tradingagents.dataflows.hithink_vendor.hithink_get",
                side_effect=VendorNotConfiguredError("no key"),
            ),
            patch(
                "tradingagents.dataflows.interface.fetch_eastmoney_hot_rank",
                return_value="EASTMONEY_OK",
            ) as mock_em,
        ):
            result = interface.route_to_vendor_with_source(
                "fetch_eastmoney_hot_rank", "600519.SS"
            )
        assert result.vendor == "eastmoney"
        assert result.data == "EASTMONEY_OK"
        mock_em.assert_called_once()


# ---------------------------------------------------------------------------
# Fixtures below mirror real payloads captured live on 2026-08-26.
# ---------------------------------------------------------------------------

# dragon-tiger-list (2026-08-25): stock-level aggregated rows; the same stock
# can appear as 当日榜 (range_days=1) and 3日榜 (range_days=3).
_DT_ROW_1 = {
    "thscode": "600487.SH",
    "ticker": "600487",
    "name": "亨通光电",
    "concept_list": [{"name": "F5G概念"}, {"name": "共封装光学(CPO)"}],
    "change": 0.069005,
    "net_value": 1_849_593_215.8,
    "net_rate": 0.11365869,
    "hot_rank": 2,
    "buy_value": 2_663_983_257.26,
    "sell_value": 814_390_041.46,
    "range_days": 1,
    "org_net_value": 656_306_893.06,
    "hot_money_net_value": 347_970_669.12,
}
_DT_ROW_3D = {**_DT_ROW_1, "range_days": 3, "net_value": 2_000_000_000.0}
_DT_OTHER = {"thscode": "002407.SZ", "ticker": "002407", "name": "多氟多", "range_days": 1}

_DRAGON_TIGER = {
    "board_type": "all",
    "trade_date": "2026-08-25",
    "count": 3,
    "stock_count": 2,
    "stock_items": [_DT_ROW_1, _DT_ROW_3D, _DT_OTHER],
    "hot_money_items": [],
}

_CALENDAR = {
    "item": [
        {"date_ms": 1_787_500_800_000, "date": "20260821"},
        {"date_ms": 1_787_587_200_000, "date": "20260825"},
        {"date_ms": 1_787_673_600_000, "date": "20260826"},
    ]
}

# limit-up-pool (2026-08-25, sorted by continue_day_cnt desc)
_LIMIT_UP = {
    "pagination": {"total": 3, "pages": 1, "size": 200, "page": 1},
    "item": [
        {
            "thscode": "002412.SZ",
            "ticker": "002412",
            "name": "汉森制药",
            "is_st": False,
            "is_new": False,
            "last_price": 12.33,
            "price_change_ratio_pct": 9.9911,
            "limit_up_time": "09:45",
            "limit_up_reason": "中药+净利增长+集采中选",
            "continue_day_text": "5连板",
            "continue_day_cnt": 5,
            "seal_money": 117_349_789,
            "max_seal_money": 325_976_594.4,
        },
        {
            "thscode": "603986.SH",
            "ticker": "603986",
            "name": "兆易创新",
            "continue_day_text": "2连板",
            "continue_day_cnt": 2,
            "seal_money": 123_456_789.12,
            "limit_up_reason": "存储芯片",
        },
        {
            "thscode": "600519.SH",
            "ticker": "600519",
            "name": "贵州茅台",
            "continue_day_text": "首板",
            "continue_day_cnt": 1,
            "limit_up_reason": None,
        },
    ],
}

_LIMIT_DOWN = {
    "pagination": {"total": 2, "pages": 1, "size": 200, "page": 1},
    "item": [
        {
            "thscode": "603156.SH",
            "ticker": "603156",
            "name": "养元饮品",
            "last_price": 42.96,
            "price_change_ratio_pct": -9.9937,
            "first_limit_time": "09:32",
            "last_limit_time": "13:47",
            "turnover_ratio_pct": 1.7417,
        },
        {"thscode": "000001.SZ", "ticker": "000001", "name": "平安银行",
         "price_change_ratio_pct": -10.0},
    ],
}

_EMPTY_POOL = {"pagination": {"total": 0, "pages": 0, "size": 200, "page": 1}, "item": []}


def _pool_dispatch(up_payload, down_payload):
    def fake_get(path, params=None):
        if path.endswith("limit-up-pool"):
            return up_payload
        if path.endswith("limit-down-pool"):
            return down_payload
        raise AssertionError(f"unexpected path: {path}")

    return fake_get


class TestDragonTiger:
    def test_filters_board_to_symbol_and_formats(self):
        with _patch_get(_DRAGON_TIGER) as mock_get:
            result = hithink_vendor.get_dragon_tiger("600487.SS")

        mock_get.assert_called_once_with(
            "/api/a-share/special-data/dragon-tiger-list", {"board_type": "all"}
        )
        assert "## 600487.SS Dragon Tiger Board (龙虎榜)" in result
        assert "hithink" in result
        assert "Date: 2026-08-25" in result
        assert "Total records: 2 entries" in result
        assert "**当日榜** (亨通光电)" in result
        assert "**3日榜**" in result
        assert "涨跌幅: +6.90%" in result
        assert "龙虎榜净买入: 18.50亿 (占成交 11.37%)" in result
        assert "买入/卖出: 26.64亿 / 8.14亿" in result
        assert "机构净买入: 6.56亿" in result
        assert "游资净买入: 3.48亿" in result
        assert "同花顺人气排名: #2" in result
        assert "多氟多" not in result  # other stocks filtered out

    def test_curr_date_snapped_to_trading_day_via_calendar(self):
        calls = []

        def fake_get(path, params=None):
            calls.append((path, params))
            if path.endswith("calendar/trading-days"):
                return _CALENDAR
            return _DRAGON_TIGER

        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get",
            side_effect=fake_get,
        ):
            # 2026-08-23 is a Sunday → snaps to Friday 2026-08-21
            hithink_vendor.get_dragon_tiger("600487.SS", "2026-08-23")

        assert calls[0][0].endswith("calendar/trading-days")
        assert calls[1][1]["date"] == "2026-08-21"

    def test_not_on_board_raises_no_market_data(self):
        with (
            _patch_get(_DRAGON_TIGER),
            pytest.raises(NoMarketDataError, match="not on dragon-tiger board"),
        ):
            hithink_vendor.get_dragon_tiger("600519.SS")

    def test_non_a_share_fast_fails_without_http(self):
        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get"
        ) as mock_get, pytest.raises(NoMarketDataError, match="A-shares only"):
            hithink_vendor.get_dragon_tiger("AAPL")
        mock_get.assert_not_called()


class TestLimitUpDown:
    def test_formats_pools_like_smartmoney(self):
        expected_ms = int(
            datetime(2026, 8, 25, tzinfo=hithink_vendor._SH_TZ).timestamp() * 1000
        )
        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get",
            side_effect=_pool_dispatch(_LIMIT_UP, _LIMIT_DOWN),
        ) as mock_get:
            result = hithink_vendor.get_limit_up_down("2026-08-25")

        up_call = mock_get.call_args_list[0]
        assert up_call.args[0].endswith("limit-up-pool")
        assert up_call.args[1]["date_ms"] == expected_ms
        assert up_call.args[1]["size"] == 200

        assert "## A-Share Limit-Up / Limit-Down Stats for 2026-08-25" in result
        assert "hithink" in result
        assert "- **Limit-up stocks (涨停)**: 3" in result
        assert "- **Limit-down stocks (跌停)**: 2" in result
        assert "- **Up/Down ratio**: 3:2" in result
        assert "5连板: 1 只 (汉森制药)" in result
        assert "2连板: 1 只 (兆易创新)" in result
        assert "首板: 1 只 (贵州茅台)" in result
        assert "汉森制药 (5连板) [中药+净利增长+集采中选]" in result
        assert "养元饮品 (-9.99%)" in result

    def test_paginates_until_all_pages_fetched(self):
        page1 = {**_LIMIT_UP, "pagination": {"total": 3, "pages": 2, "size": 2, "page": 1},
                 "item": _LIMIT_UP["item"][:2]}
        page2 = {**_LIMIT_UP, "pagination": {"total": 3, "pages": 2, "size": 2, "page": 2},
                 "item": _LIMIT_UP["item"][2:]}
        calls = []

        def fake_get(path, params=None):
            calls.append(params)
            if path.endswith("limit-up-pool"):
                return page1 if params["page"] == 1 else page2
            return _EMPTY_POOL

        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get",
            side_effect=fake_get,
        ):
            result = hithink_vendor.get_limit_up_down("2026-08-25")

        assert "- **Limit-up stocks (涨停)**: 3" in result
        assert [c["page"] for c in calls if "page" in c][:2] == [1, 2]

    def test_both_pools_empty_raise_no_market_data(self):
        with (
            patch(
                "tradingagents.dataflows.hithink_vendor.hithink_get",
                side_effect=_pool_dispatch(_EMPTY_POOL, _EMPTY_POOL),
            ),
            pytest.raises(NoMarketDataError, match="no limit-up/limit-down data"),
        ):
            hithink_vendor.get_limit_up_down("2026-08-23")  # Sunday → empty pools

    def test_unparseable_date_raises_no_market_data_without_http(self):
        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get"
        ) as mock_get, pytest.raises(NoMarketDataError, match="unparseable"):
            hithink_vendor.get_limit_up_down("not-a-date")
        mock_get.assert_not_called()


class TestP1ChainRegistration:
    def test_dragon_tiger_chain_order(self):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain("get_dragon_tiger", "default", "600519.SS")
        assert chain[:3] == ["smartmoney_db", "hithink", "akshare"]

    def test_limit_up_down_chain_order(self):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain("get_limit_up_down", "default", "2026-08-25")
        assert chain == ["smartmoney_db", "hithink"]

    def test_default_config_tool_chain_wires_dragon_tiger(self):
        from tradingagents.default_config import default_config

        chain = default_config()["tool_vendors"]["get_dragon_tiger"].split(",")
        assert chain == ["smartmoney_db", "hithink", "akshare"]


# Real payload shape verified live 2026-08-26 (002731.SZ, *ST萃华 跌停):
# batch param thscodes; item[] rows carry thscode/stock_name/tag_name/
# keyword_list/analysis_content (always ending with a fixed disclaimer).
_ANOMALY = {
    "item": [
        {
            "thscode": "002731.SZ",
            "stock_name": "萃华珠宝",
            "tag_name": "跌停",
            "keyword_list": ["ST板块", "退市风险", "资金出逃"],
            "analysis_content": (
                "萃华珠宝今日跌停。市场担忧其退市风险，资金持续流出。\n"
                "短线情绪偏弱，注意风险控制。"
                "（免责声明：本内容由AI生成，仅供参考，不构成投资建议。）"
            ),
        }
    ]
}


class TestAnomalyReason:
    def test_formats_tag_keywords_and_strips_disclaimer(self):
        with _patch_get(_ANOMALY) as mock_get:
            result = hithink_vendor.get_anomaly_reason("002731.SZ")

        mock_get.assert_called_once_with(
            "/api/a-share/special-data/anomaly-analysis-stock",
            {"thscodes": "002731.SZ"},
        )
        assert "同花顺异动解读 — 002731.SZ" in result
        assert "标签: 跌停" in result
        assert "关键词: ST板块、退市风险、资金出逃" in result
        assert "退市风险" in result
        assert "免责声明" not in result

    def test_empty_item_raises_no_market_data(self):
        with (
            _patch_get({"item": []}),
            pytest.raises(NoMarketDataError, match="no anomaly"),
        ):
            hithink_vendor.get_anomaly_reason("600519.SS")

    def test_missing_ticker_row_raises(self):
        payload = {"item": [{**_ANOMALY["item"][0], "thscode": "000001.SZ"}]}
        with (
            _patch_get(payload),
            pytest.raises(NoMarketDataError, match="missing ticker row"),
        ):
            hithink_vendor.get_anomaly_reason("002731.SZ")

    def test_non_a_share_fails_fast_without_http(self):
        with patch(
            "tradingagents.dataflows.hithink_vendor.hithink_get"
        ) as mock_get, pytest.raises(NoMarketDataError, match="A-shares only"):
            hithink_vendor.get_anomaly_reason("AAPL")
        mock_get.assert_not_called()

    def test_overlong_content_is_truncated(self):
        long_row = {
            **_ANOMALY["item"][0],
            "analysis_content": "很长的解读" * 400,
        }
        with _patch_get({"item": [long_row]}):
            result = hithink_vendor.get_anomaly_reason("002731.SZ")

        assert "…" in result
        assert len(result) < 1200

    def test_chain_is_hithink_only(self):
        from tradingagents.dataflows import interface

        chain = interface._build_vendor_chain(
            "get_anomaly_reason", "default", "600519.SS"
        )
        assert chain == ["hithink"]
