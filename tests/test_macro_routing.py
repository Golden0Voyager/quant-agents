"""Routing-layer tests for the macro archive methods.

Covers the registration contract (macro_data category membership,
smartmoney_db-only chain, tool-level config pinning, non-A-share filter
bypass) plus end-to-end route_to_vendor behaviour against a temporary
SQLite DB: smartmoney_db serves the data, and an empty result degrades to
the NO_DATA_AVAILABLE sentinel instead of raising.
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

_METHODS = ("get_us_macro", "get_cftc_cot", "get_eia_petroleum")


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


def _create_macro_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE us_macro_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date DATE NOT NULL,
            effr REAL, dgs3mo REAL, dgs2 REAL, dgs10 REAL,
            t10yie REAL, t5yie REAL, spread_10y_3m REAL, real_rate_10y REAL,
            icsa REAL, hy_oas REAL, ig_oas REAL, stlfi REAL,
            data_source TEXT, updated_at DATETIME,
            UNIQUE(trade_date)
        );
        INSERT INTO us_macro_daily
            (trade_date, effr, dgs3mo, dgs2, dgs10, t10yie, t5yie,
             spread_10y_3m, real_rate_10y, icsa, hy_oas, ig_oas, stlfi, data_source)
        VALUES
            ('2026-09-18', 3.63, 4.10, 3.55, 4.18, 2.28, 2.31, -0.7, 1.9, 219000, 2.72, 0.80, -0.3, 'fred');

        CREATE TABLE cftc_cot_weekly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date DATE NOT NULL,
            market TEXT NOT NULL,
            instrument TEXT NOT NULL,
            long_positions REAL, short_positions REAL, net_positions REAL,
            data_source TEXT, updated_at DATETIME,
            UNIQUE(trade_date, market, instrument)
        );
        INSERT INTO cftc_cot_weekly
            (trade_date, market, instrument, long_positions, short_positions, net_positions, data_source)
        VALUES
            ('2026-09-08', 'goods', '白银', 36245, 10196, 26049, 'cftc');

        CREATE TABLE eia_petroleum_weekly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_date DATE NOT NULL,
            series_id TEXT NOT NULL,
            series_name TEXT, value REAL, units TEXT,
            data_source TEXT, updated_at DATETIME,
            UNIQUE(week_date, series_id)
        );
        INSERT INTO eia_petroleum_weekly
            (week_date, series_id, series_name, value, units, data_source)
        VALUES
            ('2026-09-11', 'PET.WCESTUS1.W', '全美商业原油库存(除SPR)', 423429, 'MBBL', 'eia');
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def macro_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    _create_macro_db(db_path)
    try:
        yield db_path
    finally:
        os.unlink(db_path)


@pytest.mark.unit
class TestMacroArchiveRegistration:
    def test_methods_belong_to_macro_data_category(self):
        for method in _METHODS:
            assert get_category_for_method(method) == "macro_data"

    def test_only_smartmoney_db_registered(self):
        """Local archive is the sole source — no online fallback exists."""
        for method in _METHODS:
            assert list(interface.VENDOR_METHODS[method]) == ["smartmoney_db"]

    def test_configured_vendor_is_smartmoney_db(self):
        """The macro_data category chain is akshare,fred; tool-level config
        must override it, otherwise the chain builder raises ValueError."""
        for method in _METHODS:
            assert get_vendor("macro_data", method) == "smartmoney_db"

    def test_non_ashare_filter_bypassed(self):
        """These methods take no ticker; the local vendor must survive the
        non-A-share chain filter (macro_data is in the category whitelist)."""
        for method in _METHODS:
            assert _should_skip_ashare_filter("macro_data", method)


@pytest.mark.unit
class TestMacroArchiveRouting:
    def test_route_serves_us_macro(self, macro_db):
        with _PatchedVendor(macro_db):
            result = route_to_vendor("get_us_macro", 10)
        assert "US Macro Daily" in result

    def test_route_serves_cftc_cot(self, macro_db):
        with _PatchedVendor(macro_db):
            result = route_to_vendor("get_cftc_cot", "白银", 10)
        assert "CFTC COT — 白银" in result

    def test_route_serves_eia_petroleum(self, macro_db):
        with _PatchedVendor(macro_db):
            result = route_to_vendor("get_eia_petroleum", "PET.WCESTUS1.W", 10)
        assert "PET.WCESTUS1.W" in result

    def test_unknown_instrument_degrades_to_no_data_sentinel(self, macro_db):
        with _PatchedVendor(macro_db):
            result = route_to_vendor("get_cftc_cot", "不存在", 10)
        assert result.startswith("NO_DATA_AVAILABLE")

    def test_empty_table_degrades_to_no_data_sentinel(self, macro_db):
        """A crashed/missing table surfaces as a sentinel, not an exception."""
        conn = sqlite3.connect(macro_db)
        conn.execute("DROP TABLE us_macro_daily")
        conn.commit()
        conn.close()

        with _PatchedVendor(macro_db):
            result = route_to_vendor("get_us_macro", 10)
        assert result.startswith("NO_DATA_AVAILABLE")
