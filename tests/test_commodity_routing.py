"""Routing-layer tests for the commodity_data category.

Covers the registration contract (category membership, vendor chain, policy,
non-A-share filter bypass) plus end-to-end route_to_vendor behaviour against a
temporary SQLite DB: smartmoney_db serves the data, and an empty result
degrades to the NO_DATA_AVAILABLE sentinel instead of raising.
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
            ('2026-09-17', 131000.0, 'LC2610', 132280.0, 'LC2701', 131180.0, 180.0, 0.0014, 'sunss');

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
            ('2026-09-18', 'AG', '白银', 15970.0, 16321.0, 15940.0, 16304.0, 165934, 215014, 4.56, 'akshare_sina');
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


@pytest.mark.unit
class TestCommodityRegistration:
    def test_methods_belong_to_commodity_data_category(self):
        assert get_category_for_method("get_lithium_spot") == "commodity_data"
        assert get_category_for_method("get_commodity_futures") == "commodity_data"

    def test_only_smartmoney_db_registered(self):
        """Local archive is the sole source — no online fallback exists."""
        assert list(interface.VENDOR_METHODS["get_lithium_spot"]) == ["smartmoney_db"]
        assert list(interface.VENDOR_METHODS["get_commodity_futures"]) == ["smartmoney_db"]

    def test_configured_vendor_is_smartmoney_db(self):
        assert get_vendor("commodity_data", "get_lithium_spot") == "smartmoney_db"
        assert get_vendor("commodity_data", "get_commodity_futures") == "smartmoney_db"

    def test_non_ashare_filter_bypassed(self):
        """Variety codes are not tickers; the local vendor must survive the
        non-A-share chain filter (same treatment as macro_data)."""
        assert _should_skip_ashare_filter("commodity_data", "get_lithium_spot")
        assert _should_skip_ashare_filter("commodity_data", "get_commodity_futures")


@pytest.mark.unit
class TestCommodityRouting:
    def test_route_serves_lithium_spot(self, commodity_db):
        with _PatchedVendor(commodity_db):
            result = route_to_vendor("get_lithium_spot", 5)
        assert "Lithium Carbonate Spot" in result

    def test_route_serves_commodity_futures(self, commodity_db):
        with _PatchedVendor(commodity_db):
            result = route_to_vendor("get_commodity_futures", "AG")
        assert "AG Futures Daily" in result

    def test_unknown_variety_degrades_to_no_data_sentinel(self, commodity_db):
        with _PatchedVendor(commodity_db):
            result = route_to_vendor("get_commodity_futures", "XX")
        assert result.startswith("NO_DATA_AVAILABLE")

    def test_empty_table_degrades_to_no_data_sentinel(self, commodity_db):
        """A crashed/missing table surfaces as a sentinel, not an exception."""
        conn = sqlite3.connect(commodity_db)
        conn.execute("DROP TABLE lithium_spot_daily")
        conn.commit()
        conn.close()

        with _PatchedVendor(commodity_db):
            result = route_to_vendor("get_lithium_spot", 5)
        assert result.startswith("NO_DATA_AVAILABLE")
