"""Tests for the per-stock full-table readers in smartmoney_vendor.

Covers get_placement_announcements / get_stock_repurchase /
get_dividend_summary / get_ah_premium against a temporary SQLite DB with
patched ``smartmoney_vendor._DB_PATH``.
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
"""

_DATA = """
    INSERT INTO placement_announcements
        (source_record_key, ts_code, symbol, name, issue_method, issue_date)
    VALUES
        ('k1', '600519', 'sh600519', '贵州茅台', '定向增发', '2024-05-01'),
        ('k2', '600519', 'sh600519', '贵州茅台', '定向增发', '2026-06-15');
    INSERT INTO stock_repurchase
        (trade_date, stock_code, stock_name, repurchase_amount, repurchase_price,
         repurchase_price_lower, repurchase_price_upper, repurchase_quantity,
         progress_status, source_record_key)
    VALUES
        ('2026-06-01', '600519', '贵州茅台', 10000.0, 1500.0, 1400.0, 1600.0, 50000.0, '董事会预案', 'r1'),
        ('2026-09-01', '600519', '贵州茅台', 20000.0, 1520.0, 1450.0, 1650.0, 80000.0, '实施中', 'r2');
    INSERT INTO dividend_summary
        (ts_code, name, list_date, cumulative_dividend, avg_annual_dividend,
         dividend_count, total_raise_amount, raise_count)
    VALUES
        ('600519', '贵州茅台', '2001-08-27', 1500.0, 60.0, 25, 22.4, 1);
    INSERT INTO ah_premium
        (trade_date, ts_code, h_code, name, a_price, h_price, premium)
    VALUES
        ('2026-09-17', '600519', '80999', '贵州茅台', 1500.0, 1450.0, 3.45),
        ('2026-09-18', '600519', '80999', '贵州茅台', 1520.0, 1460.0, 4.11);
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


@pytest.mark.unit
class TestGetPlacementAnnouncements:
    def test_returns_table(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_placement_announcements

        with _PatchedVendor(full_db):
            result = get_placement_announcements("600519.SS")

        assert "Placement Announcements" in result
        assert "Total records: 2 announcements" in result
        assert "2026-06-15" in result

    def test_respects_periods(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_placement_announcements

        with _PatchedVendor(full_db):
            result = get_placement_announcements("600519.SS", periods=1)

        assert "2024-05-01" not in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_placement_announcements

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_placement_announcements("600519.SS")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_placement_announcements

        conn = sqlite3.connect(empty_db)
        conn.execute("DROP TABLE placement_announcements")
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_placement_announcements("600519.SS")


@pytest.mark.unit
class TestGetStockRepurchase:
    def test_returns_blocks(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_stock_repurchase

        with _PatchedVendor(full_db):
            result = get_stock_repurchase("600519.SS")

        assert "Share Repurchase" in result
        assert "实施中" in result
        assert "1,520.00" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_stock_repurchase

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_stock_repurchase("600519.SS")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_stock_repurchase

        conn = sqlite3.connect(empty_db)
        conn.execute("DROP TABLE stock_repurchase")
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_stock_repurchase("600519.SS")


@pytest.mark.unit
class TestGetDividendSummary:
    def test_returns_single_row(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_dividend_summary

        with _PatchedVendor(full_db):
            result = get_dividend_summary("600519.SS")

        assert "Dividend & Fundraising Summary" in result
        assert "1,500.00" in result
        assert "贵州茅台" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_dividend_summary

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_dividend_summary("600519.SS")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_dividend_summary

        conn = sqlite3.connect(empty_db)
        conn.execute("DROP TABLE dividend_summary")
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_dividend_summary("600519.SS")


@pytest.mark.unit
class TestGetAhPremium:
    def test_returns_table_oldest_first(self, full_db):
        from tradingagents.dataflows.smartmoney_vendor import get_ah_premium

        with _PatchedVendor(full_db):
            result = get_ah_premium("600519.SS")

        assert "A/H Premium" in result
        assert result.index("2026-09-17") < result.index("2026-09-18")
        assert "4.11" in result

    def test_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_ah_premium

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_ah_premium("600519.SS")

    def test_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_ah_premium

        conn = sqlite3.connect(empty_db)
        conn.execute("DROP TABLE ah_premium")
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_ah_premium("600519.SS")
