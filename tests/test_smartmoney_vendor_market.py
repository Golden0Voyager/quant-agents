"""Tests for the market-level full-table readers in smartmoney_vendor.

Covers the 13 market-level readers (gold, HK tech, FX, CB triple, ETF,
option sentiment, south flow, index-futures basis, sector daily/valuation,
PBOC balance sheet) against a temporary SQLite DB with patched
``smartmoney_vendor._DB_PATH``.
"""

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from tradingagents.dataflows.errors import NoMarketDataError


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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT PRIMARY KEY,
        total_assets REAL, reserve_money REAL, currency_issue REAL,
        claims_on_other_deposit REAL, claims_on_gov REAL,
        gov_deposits REAL, foreign_assets REAL, fx_reserve REAL,
        data_date TEXT, data_source TEXT, updated_at DATETIME
    );
""".replace("id INTEGER PRIMARY KEY AUTOINCREMENT,\n        date TEXT PRIMARY KEY,", "date TEXT PRIMARY KEY,")

_DATA = """
    INSERT INTO gold_price (trade_date, trading_time, evening_price, morning_price)
    VALUES ('2026-09-17', '2026-09-17', 930.0, 925.0), ('2026-09-18', '2026-09-18', 935.0, 928.0);
    INSERT INTO hk_tech_index_daily (trade_date, open, high, low, close, change_pct, volume, amount)
    VALUES ('2026-09-17', 4300.0, 4400.0, 4290.0, 4390.0, 1.5, 1.5e9, 5.5e10),
           ('2026-09-18', 4390.0, 4420.0, 4380.0, 4405.0, 0.34, 1.6e9, 5.8e10);
    INSERT INTO fx_rate (trade_date, currency, bank_buy_price, cash_buy_price,
                         cash_sell_price, central_parity_rate, boc_convert_price)
    VALUES ('2026-09-17', '美元', 7.08, 7.05, 7.12, 7.10, 7.10),
           ('2026-09-18', '美元', 7.09, 7.06, 7.13, 7.11, 7.11);
    INSERT INTO cb_quotation (ts_code, bond_name, price, premium, double_low, expire_date)
    VALUES ('110001', '测试转债甲', 105.0, 10.0, 115.0, '2029-01-01'),
           ('110002', '测试转债乙', 130.0, 40.0, 170.0, '2028-06-01'),
           ('110003', '退市转债', 30.0, -20.0, 10.0, '2025-01-01');
    INSERT INTO cb_redeem (ts_code, bond_name, redeem_flag, redeem_price, redeem_date)
    VALUES ('110001', '测试转债甲', '已公告强赎', 105.5, '2026-10-15'),
           ('110002', '测试转债乙', '', NULL, NULL),
           ('110004', '测试转债丙', '公告不强赎', NULL, NULL);
    INSERT INTO cb_index (trade_date, index_code, index_name, open, close, high, low, volume)
    VALUES ('2026-09-17', 'JSL_EW', '集思录可转债等权指数', 1900.0, 1910.0, 1920.0, 1890.0, 1.0e8),
           ('2026-09-18', 'JSL_EW', '集思录可转债等权指数', 1910.0, 1925.0, 1930.0, 1905.0, 1.1e8);
    INSERT INTO etf_daily (ts_code, trade_date, open, high, low, close, volume, amount, adj_factor, name)
    VALUES ('510300', '2026-09-17', 4.50, 4.55, 4.48, 4.53, 1.0e8, 4.5e8, 1.0, '沪深300ETF'),
           ('510300', '2026-09-18', 4.53, 4.60, 4.52, 4.58, 1.2e8, 5.0e8, 1.0, '沪深300ETF');
    INSERT INTO option_sentiment (trade_date, qvix, pcr, put_volume, call_volume, put_oi, call_oi, implied_vol_avg)
    VALUES ('2026-09-17', 15.5, 0.85, 300000, 400000, 500000, 800000, 18.2),
           ('2026-09-18', 14.25, 0.72, 311742, 433713, 554140, 885084, NULL);
    INSERT INTO south_flow (trade_date, market, net_buy_amount, buy_amount, sell_amount, cumulative_net_buy)
    VALUES ('2026-09-17', '南向', 33.63, 406.87, 373.23, 5.51),
           ('2026-09-18', '南向', 11.93, 521.95, 510.02, 5.52);
    INSERT INTO index_futures_basis (trade_date, futures_code, futures_price, index_price, basis, basis_pct)
    VALUES ('2026-09-17', 'IF0', 4400.0, 4405.0, -5.0, -0.11),
           ('2026-09-18', 'IF0', 4410.0, 4412.0, -2.0, -0.05),
           ('2026-09-18', 'IC0', 6200.0, 6210.0, -10.0, -0.16);
    INSERT INTO sector_daily (sector_name, trade_date, open, close, high, low, volume, amount, pct_change)
    VALUES ('半导体', '2026-09-17', 16800.0, 16900.0, 17000.0, 16700.0, 4.0e9, 3.3e11, 1.2),
           ('半导体', '2026-09-18', 16900.0, 17299.0, 17449.0, 16832.0, 4.1e9, 3.4e11, 4.31);
    INSERT INTO sector_valuation (sector_name, trade_date, pe, pb, total_mv)
    VALUES ('半导体', '2026-09-17', 55.0, 4.5, 3200.0),
           ('半导体', '2026-09-18', 56.0, 4.6, 3250.0);
    INSERT INTO central_bank_balance (date, total_assets, reserve_money, currency_issue,
                                      claims_on_other_deposit, claims_on_gov, gov_deposits,
                                      foreign_assets, fx_reserve, data_date)
    VALUES ('2026-07-01', 502068.0, 404903.0, 120000.0, 180000.0, 160000.0, 45000.0, 230000.0, 32000.0, '2026-07'),
           ('2026-08-01', 498567.0, 401814.0, 119000.0, 179000.0, 159000.0, 44000.0, 229000.0, 31900.0, '2026-08');
"""


def _create_db(path, populate: bool):
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    if populate:
        conn.executescript(_DATA)
    conn.commit()
    conn.close()


@pytest.fixture()
def full_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    _create_db(db_path, populate=True)
    try:
        yield db_path
    finally:
        os.unlink(db_path)


@pytest.fixture()
def empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    _create_db(db_path, populate=False)
    try:
        yield db_path
    finally:
        os.unlink(db_path)


def _drop(db_path: str, table: str):
    conn = sqlite3.connect(db_path)
    conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()


@pytest.mark.unit
class TestGetGoldPrice:
    def test_returns_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_gold_price

        with _PatchedVendor(full_db):
            result = get_gold_price()
        assert "SGE Gold Price" in result
        assert "935.00" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_gold_price

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_gold_price()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_gold_price

        _drop(empty_db, "gold_price")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_gold_price()


@pytest.mark.unit
class TestGetHkTechIndex:
    def test_returns_csv(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_hk_tech_index

        with _PatchedVendor(full_db):
            result = get_hk_tech_index()
        assert "Hang Seng Tech" in result
        assert "4,405.0" in result or "4405.0" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_hk_tech_index

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_hk_tech_index()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_hk_tech_index

        _drop(empty_db, "hk_tech_index_daily")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_hk_tech_index()


@pytest.mark.unit
class TestGetFxRate:
    def test_returns_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_fx_rate

        with _PatchedVendor(full_db):
            result = get_fx_rate("美元")
        assert "CNY FX Rate" in result
        assert "7.1100" in result

    def test_unknown_currency_raises_with_available_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_fx_rate

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_fx_rate("欧元")
        assert "美元" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_fx_rate

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_fx_rate("美元")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_fx_rate

        _drop(empty_db, "fx_rate")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_fx_rate("美元")


@pytest.mark.unit
class TestGetCbQuotation:
    def test_top_by_double_low_excludes_junk(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_quotation

        with _PatchedVendor(full_db):
            result = get_cb_quotation()
        assert "Convertible Bond Quotation" in result
        assert "测试转债甲" in result
        # price < 50 junk bond excluded
        assert "退市转债" not in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_quotation

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_quotation()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_quotation

        _drop(empty_db, "cb_quotation")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_quotation()


@pytest.mark.unit
class TestGetCbRedeem:
    def test_only_flagged_bonds(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_redeem

        with _PatchedVendor(full_db):
            result = get_cb_redeem()
        assert "Redemption Flags" in result
        assert "已公告强赎" in result
        # blank flag excluded
        assert "测试转债乙" not in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_redeem

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_redeem()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_redeem

        _drop(empty_db, "cb_redeem")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_redeem()


@pytest.mark.unit
class TestGetCbIndex:
    def test_single_index_csv(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        with _PatchedVendor(full_db):
            result = get_cb_index("JSL_EW")
        assert "CB Index JSL_EW" in result
        assert "1,925.0" in result or "1925.0" in result

    def test_wide_table_all_indices(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        with _PatchedVendor(full_db):
            result = get_cb_index()
        assert "CB Index Daily" in result
        assert "JSL_EW" in result

    def test_unknown_index_raises_with_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_cb_index("XX")
        assert "JSL_EW" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_index()

    def test_raises_when_empty_single(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_index("JSL_EW")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        _drop(empty_db, "cb_index")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_index()

    def test_raises_when_table_missing_single(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cb_index

        _drop(empty_db, "cb_index")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cb_index("JSL_EW")


@pytest.mark.unit
class TestGetEtfDaily:
    def test_returns_csv(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_etf_daily

        with _PatchedVendor(full_db):
            result = get_etf_daily("510300.SS")
        assert "ETF 510300" in result
        assert "沪深300ETF" in result

    def test_unknown_etf_raises_with_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_etf_daily

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_etf_daily("999999.SS")
        assert "510300" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_etf_daily

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_etf_daily("510300.SS")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_etf_daily

        _drop(empty_db, "etf_daily")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_etf_daily("510300.SS")


@pytest.mark.unit
class TestGetOptionSentiment:
    def test_returns_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_option_sentiment

        with _PatchedVendor(full_db):
            result = get_option_sentiment()
        assert "Option Sentiment" in result
        assert "14.25" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_option_sentiment

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_option_sentiment()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_option_sentiment

        _drop(empty_db, "option_sentiment")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_option_sentiment()


@pytest.mark.unit
class TestGetSouthFlow:
    def test_wide_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_south_flow

        with _PatchedVendor(full_db):
            result = get_south_flow()
        assert "Southbound Flow" in result
        assert "南向" in result

    def test_single_market(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_south_flow

        with _PatchedVendor(full_db):
            result = get_south_flow("南向", 1)
        assert "Total records: 1 trading days" in result
        assert "2026-09-17" not in result

    def test_unknown_market_raises_with_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_south_flow

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_south_flow("北向")
        assert "南向" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_south_flow

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_south_flow()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_south_flow

        _drop(empty_db, "south_flow")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_south_flow()

    def test_raises_when_table_missing_single(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_south_flow

        _drop(empty_db, "south_flow")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_south_flow("南向")


@pytest.mark.unit
class TestGetIndexFuturesBasis:
    def test_single_code_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_index_futures_basis

        with _PatchedVendor(full_db):
            result = get_index_futures_basis("IF0")
        assert "IF0" in result
        assert "-0.05" in result

    def test_wide_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_index_futures_basis

        with _PatchedVendor(full_db):
            result = get_index_futures_basis(periods=1)
        assert "Basis %" in result
        assert "IC0" in result

    def test_unknown_code_raises_with_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_index_futures_basis

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_index_futures_basis("ZZ0")
        assert "IF0" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_index_futures_basis

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_index_futures_basis()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_index_futures_basis

        _drop(empty_db, "index_futures_basis")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_index_futures_basis()

    def test_raises_when_table_missing_single(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_index_futures_basis

        _drop(empty_db, "index_futures_basis")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_index_futures_basis("IF0")


@pytest.mark.unit
class TestGetSectorDaily:
    def test_returns_csv(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_daily

        with _PatchedVendor(full_db):
            result = get_sector_daily("半导体")
        assert "半导体 Daily" in result
        assert "17,299.0" in result or "17299.0" in result

    def test_unknown_sector_raises_with_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_daily

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_sector_daily("不存在")
        assert "半导体" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_daily

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_sector_daily("半导体")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_daily

        _drop(empty_db, "sector_daily")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_sector_daily("半导体")


@pytest.mark.unit
class TestGetSectorValuation:
    def test_returns_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_valuation

        with _PatchedVendor(full_db):
            result = get_sector_valuation("半导体")
        assert "半导体 Valuation" in result
        assert "56.00" in result

    def test_unknown_sector_raises_with_list(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_valuation

        with _PatchedVendor(full_db), pytest.raises(NoMarketDataError) as exc_info:
            get_sector_valuation("不存在")
        assert "半导体" in str(exc_info.value)

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_valuation

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_sector_valuation("半导体")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_valuation

        _drop(empty_db, "sector_valuation")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_sector_valuation("半导体")


@pytest.mark.unit
class TestGetCentralBankBalance:
    def test_returns_csv(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_central_bank_balance

        with _PatchedVendor(full_db):
            result = get_central_bank_balance()
        assert "PBOC Balance Sheet" in result
        assert "reserve_money" in result
        assert "498,567.0" in result or "498567.0" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_central_bank_balance

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_central_bank_balance()

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_central_bank_balance

        _drop(empty_db, "central_bank_balance")
        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_central_bank_balance()
