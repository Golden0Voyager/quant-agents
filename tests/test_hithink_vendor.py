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

_ABILITIES = {
    "abilities": [
        {"category": "盈利能力", "items": {"roe": 15.23, "gross_margin": 91.5}},
        {"category": "偿债能力", "items": {"debt_ratio": 18.5}},
    ]
}

_HOT_LIST = {
    "item": [
        {
            "rank": 1,
            "thscode": "600519.SH",
            "name": "贵州茅台",
            "heat": 987654,
            "price": 1680.5,
            "change_pct": 2.35,
        },
        {"rank": 2, "thscode": "000001.SZ", "name": "平安银行", "heat": 12345},
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
        assert "净资产收益率(ROE) = 15.23" in result

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
        assert "最新价: 1680.5" in result
        assert "涨跌幅: 2.35%" in result

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
