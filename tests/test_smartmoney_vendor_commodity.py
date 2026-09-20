"""Tests for the commodity data readers in smartmoney_vendor.

get_lithium_spot / get_commodity_futures read the market-level
lithium_spot_daily and futures_daily tables from quant_core.db. Tests build a
temporary SQLite DB and patch ``smartmoney_vendor._DB_PATH``, mirroring the
pattern used for the other smartmoney vendor tests.
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


def _create_commodity_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE lithium_spot_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spot_date DATE NOT NULL,
            spot_price REAL,
            near_contract TEXT, near_contract_price REAL,
            dom_contract TEXT, dom_contract_price REAL,
            dom_basis REAL, dom_basis_rate REAL,
            data_source TEXT, updated_at DATETIME,
            UNIQUE(spot_date)
        );
        INSERT INTO lithium_spot_daily
            (spot_date, spot_price, near_contract, near_contract_price,
             dom_contract, dom_contract_price, dom_basis, dom_basis_rate, data_source)
        VALUES
            ('2026-09-16', 132000.0, 'LC2610', 131500.0, 'LC2701', 130800.0, 1200.0, 0.009, 'sunss'),
            ('2026-09-17', 131000.0, 'LC2610', 132280.0, 'LC2701', 131180.0, 180.0, 0.0014, 'sunss'),
            ('2026-09-18', 130000.0, 'LC2610', 128580.0, 'LC2701', 127160.0, -2840.0, -0.0218, 'sunss');

        CREATE TABLE futures_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date DATE NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT, open REAL, high REAL, low REAL, close REAL,
            volume BIGINT, hold BIGINT, change_pct REAL,
            data_source TEXT, updated_at DATETIME,
            UNIQUE(trade_date, symbol)
        );
        INSERT INTO futures_daily
            (trade_date, symbol, name, open, high, low, close, volume, hold, change_pct, data_source)
        VALUES
            ('2026-09-16', 'AG', '白银', 15580.0, 15883.0, 15436.0, 15832.0, 127900, 204304, 2.46, 'akshare_sina'),
            ('2026-09-17', 'AG', '白银', 15808.0, 15900.0, 15420.0, 15593.0, 165653, 206770, -1.51, 'akshare_sina'),
            ('2026-09-18', 'AG', '白银', 15970.0, 16321.0, 15940.0, 16304.0, 165934, 215014, 4.56, 'akshare_sina'),
            ('2026-09-17', 'LC', '碳酸锂', 128000.0, 132000.0, 127500.0, 131500.0, 210000, 150000, 1.2, 'akshare_sina');
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def commodity_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    _create_commodity_db(db_path)
    try:
        yield db_path
    finally:
        os.unlink(db_path)


@pytest.fixture()
def empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        yield db_path
    finally:
        os.unlink(db_path)


@pytest.mark.unit
class TestGetLithiumSpot:
    def test_returns_formatted_report(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_lithium_spot

        with _PatchedVendor(commodity_db):
            result = get_lithium_spot()

        assert "Lithium Carbonate Spot" in result
        assert "Total records: 3 days" in result
        assert "130,000.00" in result
        assert "LC2701" in result

    def test_rows_oldest_first(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_lithium_spot

        with _PatchedVendor(commodity_db):
            result = get_lithium_spot()

        assert result.index("2026-09-16") < result.index("2026-09-18")

    def test_respects_periods(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_lithium_spot

        with _PatchedVendor(commodity_db):
            result = get_lithium_spot(periods=2)

        assert "Total records: 2 days" in result
        assert "2026-09-16" not in result

    def test_raises_no_data_error_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_lithium_spot

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_lithium_spot()

    def test_raises_no_data_error_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_lithium_spot

        conn = sqlite3.connect(empty_db)
        conn.execute(
            "CREATE TABLE lithium_spot_daily (spot_date DATE, spot_price REAL)"
        )
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_lithium_spot()


@pytest.mark.unit
class TestGetCommodityFutures:
    def test_returns_formatted_report(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        with _PatchedVendor(commodity_db):
            result = get_commodity_futures("AG")

        assert "AG Futures Daily (白银)" in result
        assert "Total records: 3 trading days" in result
        assert "16,304.00 (+4.56%)" in result
        assert "Open Interest: 215,014" in result

    def test_variety_case_insensitive(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        with _PatchedVendor(commodity_db):
            result = get_commodity_futures("ag")

        assert "AG Futures Daily" in result

    def test_other_variety(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        with _PatchedVendor(commodity_db):
            result = get_commodity_futures("LC")

        assert "LC Futures Daily (碳酸锂)" in result
        assert "Total records: 1 trading days" in result

    def test_respects_periods(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        with _PatchedVendor(commodity_db):
            result = get_commodity_futures("AG", periods=2)

        assert "Total records: 2 trading days" in result
        assert "2026-09-16" not in result

    def test_unknown_variety_raises_no_data_error(self, commodity_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        with _PatchedVendor(commodity_db), pytest.raises(NoMarketDataError) as exc_info:
            get_commodity_futures("XX")

        assert "XX" in str(exc_info.value)

    def test_raises_no_data_error_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_commodity_futures("AG")

    def test_raises_no_data_error_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_commodity_futures

        conn = sqlite3.connect(empty_db)
        conn.execute(
            "CREATE TABLE futures_daily (trade_date DATE, symbol TEXT, close REAL)"
        )
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_commodity_futures("AG")
