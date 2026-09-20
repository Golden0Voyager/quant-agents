"""Routing-layer tests for the full-table coverage methods.

Covers the registration contract (category membership, smartmoney_db-only
chain, tool-level config pinning, non-A-share filter bypass) plus
end-to-end route_to_vendor behaviour against a temporary SQLite DB.
"""

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.interface import (
    _should_skip_ashare_filter,
    get_category_for_method,
    get_vendor,
    route_to_vendor,
)

# method -> expected category
_METHODS = {
    "get_placement_announcements": "governance_risk",
    "get_stock_repurchase": "governance_risk",
    "get_dividend_summary": "shareholder_return",
    "get_ah_premium": "core_stock_apis",
    "get_etf_daily": "core_stock_apis",
    "get_cb_quotation": "core_stock_apis",
    "get_cb_redeem": "core_stock_apis",
    "get_cb_index": "core_stock_apis",
    "get_option_sentiment": "technical_indicators",
    "get_index_futures_basis": "technical_indicators",
    "get_sector_daily": "technical_indicators",
    "get_sector_valuation": "technical_indicators",
    "get_south_flow": "news_data",
    "get_gold_price": "commodity_data",
    "get_hk_tech_index": "macro_data",
    "get_fx_rate": "macro_data",
    "get_central_bank_balance": "macro_data",
}

# methods whose first argument is not a ticker (or none) — must survive the
# non-A-share chain filter.
_NON_TICKER_METHODS = {
    "get_gold_price",
    "get_hk_tech_index",
    "get_fx_rate",
    "get_cb_quotation",
    "get_cb_redeem",
    "get_cb_index",
    "get_option_sentiment",
    "get_south_flow",
    "get_index_futures_basis",
    "get_sector_daily",
    "get_sector_valuation",
    "get_central_bank_balance",
}


class _PatchedVendor:
    def __init__(self, db_path):
        self.db_path = db_path

    def __enter__(self):
        self.patcher = patch(
            "tradingagents.dataflows.smartmoney_vendor._DB_PATH", self.db_path
        )
        self.patcher.start()
        return self

    def __exit__(self, *args):
        self.patcher.stop()


_SCHEMA = """
    CREATE TABLE placement_announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_record_key TEXT UNIQUE,
        ts_code TEXT, symbol TEXT, name TEXT, issue_method TEXT,
        issue_date TEXT, data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE stock_repurchase (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT, stock_code TEXT, stock_name TEXT,
        repurchase_amount REAL, repurchase_price REAL,
        repurchase_price_lower REAL, repurchase_price_upper REAL,
        repurchase_quantity REAL, progress_status TEXT,
        source_record_key TEXT UNIQUE, data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE dividend_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT UNIQUE, name TEXT, list_date TEXT,
        cumulative_dividend REAL, avg_annual_dividend REAL,
        dividend_count INTEGER, total_raise_amount REAL, raise_count INTEGER,
        data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE ah_premium (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT, ts_code TEXT, h_code TEXT, name TEXT,
        a_price REAL, h_price REAL, premium REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(trade_date, ts_code)
    );
    CREATE TABLE gold_price (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE, trading_time TEXT,
        evening_price REAL, morning_price REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(trade_date, trading_time)
    );
    CREATE TABLE hk_tech_index_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE, open REAL, high REAL, low REAL, close REAL,
        change_pct REAL, volume REAL, amount REAL,
        data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE fx_rate (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE, currency TEXT,
        bank_buy_price REAL, cash_buy_price REAL, cash_sell_price REAL,
        central_parity_rate REAL, boc_convert_price REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(trade_date, currency)
    );
    CREATE TABLE cb_quotation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT UNIQUE, bond_name TEXT, price REAL, premium REAL,
        double_low REAL, expire_date TEXT,
        data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE cb_redeem (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT UNIQUE, bond_name TEXT, redeem_flag TEXT,
        redeem_price REAL, redeem_date TEXT,
        data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE cb_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE, index_code TEXT, index_name TEXT,
        open REAL, close REAL, high REAL, low REAL, volume REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(trade_date, index_code)
    );
    CREATE TABLE etf_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT, trade_date DATE,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL, adj_factor REAL, name TEXT,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(ts_code, trade_date)
    );
    CREATE TABLE option_sentiment (
        trade_date TEXT PRIMARY KEY,
        qvix REAL, pcr REAL, put_volume REAL, call_volume REAL,
        put_oi REAL, call_oi REAL, implied_vol_avg REAL,
        data_source TEXT, updated_at DATETIME
    );
    CREATE TABLE south_flow (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE, market TEXT,
        net_buy_amount REAL, buy_amount REAL, sell_amount REAL,
        cumulative_net_buy REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(trade_date, market)
    );
    CREATE TABLE index_futures_basis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date DATE, futures_code TEXT,
        futures_price REAL, index_price REAL, basis REAL, basis_pct REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(trade_date, futures_code)
    );
    CREATE TABLE sector_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sector_name TEXT, trade_date DATE,
        open REAL, close REAL, high REAL, low REAL,
        volume REAL, amount REAL, pct_change REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(sector_name, trade_date)
    );
    CREATE TABLE sector_valuation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sector_name TEXT, trade_date DATE,
        pe REAL, pb REAL, total_mv REAL,
        data_source TEXT, updated_at DATETIME,
        UNIQUE(sector_name, trade_date)
    );
    CREATE TABLE central_bank_balance (
        date TEXT PRIMARY KEY,
        total_assets REAL, reserve_money REAL, currency_issue REAL,
        claims_on_other_deposit REAL, claims_on_gov REAL,
        gov_deposits REAL, foreign_assets REAL, fx_reserve REAL,
        data_date TEXT, data_source TEXT, updated_at DATETIME
    );
"""

_DATA = """
    INSERT INTO placement_announcements (source_record_key, ts_code, symbol, name, issue_method, issue_date)
    VALUES ('k1', '600519', 'sh600519', '贵州茅台', '定向增发', '2026-06-15');
    INSERT INTO stock_repurchase (trade_date, stock_code, stock_name, repurchase_amount,
        repurchase_price, repurchase_price_lower, repurchase_price_upper,
        repurchase_quantity, progress_status, source_record_key)
    VALUES ('2026-09-01', '600519', '贵州茅台', 20000.0, 1520.0, 1450.0, 1650.0, 80000.0, '实施中', 'r1');
    INSERT INTO dividend_summary (ts_code, name, list_date, cumulative_dividend,
        avg_annual_dividend, dividend_count, total_raise_amount, raise_count)
    VALUES ('600519', '贵州茅台', '2001-08-27', 1500.0, 60.0, 25, 22.4, 1);
    INSERT INTO ah_premium (trade_date, ts_code, h_code, name, a_price, h_price, premium)
    VALUES ('2026-09-18', '600519', '80999', '贵州茅台', 1520.0, 1460.0, 4.11);
    INSERT INTO gold_price (trade_date, trading_time, evening_price, morning_price)
    VALUES ('2026-09-18', '2026-09-18', 935.0, 928.0);
    INSERT INTO hk_tech_index_daily (trade_date, open, high, low, close, change_pct, volume, amount)
    VALUES ('2026-09-18', 4390.0, 4420.0, 4380.0, 4405.0, 0.34, 1.6e9, 5.8e10);
    INSERT INTO fx_rate (trade_date, currency, bank_buy_price, cash_buy_price,
        cash_sell_price, central_parity_rate, boc_convert_price)
    VALUES ('2026-09-18', '美元', 7.09, 7.06, 7.13, 7.11, 7.11);
    INSERT INTO cb_quotation (ts_code, bond_name, price, premium, double_low, expire_date)
    VALUES ('110001', '测试转债甲', 105.0, 10.0, 115.0, '2029-01-01');
    INSERT INTO cb_redeem (ts_code, bond_name, redeem_flag, redeem_price, redeem_date)
    VALUES ('110001', '测试转债甲', '已公告强赎', 105.5, '2026-10-15');
    INSERT INTO cb_index (trade_date, index_code, index_name, open, close, high, low, volume)
    VALUES ('2026-09-18', 'JSL_EW', '集思录可转债等权指数', 1910.0, 1925.0, 1930.0, 1905.0, 1.1e8);
    INSERT INTO etf_daily (ts_code, trade_date, open, high, low, close, volume, amount, adj_factor, name)
    VALUES ('510300', '2026-09-18', 4.53, 4.60, 4.52, 4.58, 1.2e8, 5.0e8, 1.0, '沪深300ETF');
    INSERT INTO option_sentiment (trade_date, qvix, pcr, put_volume, call_volume, put_oi, call_oi, implied_vol_avg)
    VALUES ('2026-09-18', 14.25, 0.72, 311742, 433713, 554140, 885084, NULL);
    INSERT INTO south_flow (trade_date, market, net_buy_amount, buy_amount, sell_amount, cumulative_net_buy)
    VALUES ('2026-09-18', '南向', 11.93, 521.95, 510.02, 5.52);
    INSERT INTO index_futures_basis (trade_date, futures_code, futures_price, index_price, basis, basis_pct)
    VALUES ('2026-09-18', 'IF0', 4410.0, 4412.0, -2.0, -0.05);
    INSERT INTO sector_daily (sector_name, trade_date, open, close, high, low, volume, amount, pct_change)
    VALUES ('半导体', '2026-09-18', 16900.0, 17299.0, 17449.0, 16832.0, 4.1e9, 3.4e11, 4.31);
    INSERT INTO sector_valuation (sector_name, trade_date, pe, pb, total_mv)
    VALUES ('半导体', '2026-09-18', 56.0, 4.6, 3250.0);
    INSERT INTO central_bank_balance (date, total_assets, reserve_money, currency_issue,
        claims_on_other_deposit, claims_on_gov, gov_deposits, foreign_assets, fx_reserve, data_date)
    VALUES ('2026-08-01', 498567.0, 401814.0, 119000.0, 179000.0, 159000.0, 44000.0, 229000.0, 31900.0, '2026-08');
"""


@pytest.fixture()
def full_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.executescript(_DATA)
    conn.commit()
    conn.close()
    try:
        yield db_path
    finally:
        os.unlink(db_path)


@pytest.mark.unit
class TestFullCoverageRegistration:
    def test_methods_belong_to_expected_categories(self):
        for method, category in _METHODS.items():
            assert get_category_for_method(method) == category, method

    def test_only_smartmoney_db_registered(self):
        """Local archive is the sole source — no online fallback exists."""
        for method in _METHODS:
            assert list(interface.VENDOR_METHODS[method]) == ["smartmoney_db"], method

    def test_configured_vendor_resolves_to_smartmoney_db(self):
        """Every category chain must resolve to smartmoney_db for these
        methods — either natively, via 'default', or via the tool pin."""
        for method in _METHODS:
            vendor_config = get_vendor(_METHODS[method], method)
            chain = interface._build_vendor_chain(method, vendor_config, symbol=None)
            assert chain == ["smartmoney_db"], (method, vendor_config, chain)

    def test_non_ashare_filter_bypassed_for_non_ticker_methods(self):
        for method in _NON_TICKER_METHODS:
            assert _should_skip_ashare_filter(_METHODS[method], method), method


@pytest.mark.unit
class TestFullCoverageRouting:
    def test_route_serves_governance_events(self, full_db):
        with _PatchedVendor(full_db):
            assert "Placement Announcements" in route_to_vendor(
                "get_placement_announcements", "600519.SS", 5
            )
            assert "Share Repurchase" in route_to_vendor(
                "get_stock_repurchase", "600519.SS", 5
            )
            assert "Dividend & Fundraising Summary" in route_to_vendor(
                "get_dividend_summary", "600519.SS"
            )

    def test_route_serves_ah_premium_and_etf(self, full_db):
        with _PatchedVendor(full_db):
            assert "A/H Premium" in route_to_vendor("get_ah_premium", "600519.SS", 5)
            assert "ETF 510300" in route_to_vendor("get_etf_daily", "510300.SS", 5)

    def test_route_serves_cb_triple(self, full_db):
        with _PatchedVendor(full_db):
            assert "Convertible Bond Quotation" in route_to_vendor("get_cb_quotation")
            assert "Redemption Flags" in route_to_vendor("get_cb_redeem")
            assert "CB Index Daily" in route_to_vendor("get_cb_index")

    def test_route_serves_market_breadth(self, full_db):
        with _PatchedVendor(full_db):
            assert "Option Sentiment" in route_to_vendor("get_option_sentiment", 5)
            assert "Basis %" in route_to_vendor("get_index_futures_basis")
            assert "半导体 Daily" in route_to_vendor("get_sector_daily", "半导体", 5)
            assert "半导体 Valuation" in route_to_vendor("get_sector_valuation", "半导体", 5)

    def test_route_serves_macro_tables(self, full_db):
        with _PatchedVendor(full_db):
            assert "SGE Gold Price" in route_to_vendor("get_gold_price", 5)
            assert "Hang Seng Tech" in route_to_vendor("get_hk_tech_index", 5)
            assert "CNY FX Rate" in route_to_vendor("get_fx_rate", "美元", 5)
            assert "PBOC Balance Sheet" in route_to_vendor("get_central_bank_balance", 5)

    def test_route_serves_south_flow(self, full_db):
        with _PatchedVendor(full_db):
            assert "Southbound Flow" in route_to_vendor("get_south_flow")

    def test_unknown_param_degrades_to_no_data_sentinel(self, full_db):
        with _PatchedVendor(full_db):
            result = route_to_vendor("get_sector_daily", "不存在", 5)
        assert result.startswith("NO_DATA_AVAILABLE")

    def test_missing_table_degrades_to_no_data_sentinel(self, full_db):
        conn = sqlite3.connect(full_db)
        conn.execute("DROP TABLE gold_price")
        conn.commit()
        conn.close()

        with _PatchedVendor(full_db):
            result = route_to_vendor("get_gold_price", 5)
        assert result.startswith("NO_DATA_AVAILABLE")
