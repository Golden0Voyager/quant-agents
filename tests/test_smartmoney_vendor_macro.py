"""Tests for the macro archive readers in smartmoney_vendor.

get_us_macro / get_cftc_cot / get_eia_petroleum read the us_macro_daily,
cftc_cot_weekly and eia_petroleum_weekly tables from quant_core.db. Tests
build a temporary SQLite DB and patch ``smartmoney_vendor._DB_PATH``,
mirroring the pattern used for the other smartmoney vendor tests.
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
            ('2026-09-16', 3.63, 4.14, 3.60, 4.25, 2.33, 2.35, -0.5, 1.9, 220000, 2.70, 0.78, -0.5, 'fred'),
            ('2026-09-17', 3.63, 4.12, 3.58, 4.20, 2.30, 2.33, -0.6, 1.9, 218000, 2.71, 0.79, -0.4, 'fred'),
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
            ('2026-09-01', 'goods', '白银', 35403, 8664, 26739, 'cftc'),
            ('2026-09-08', 'goods', '白银', 36245, 10196, 26049, 'cftc'),
            ('2026-09-01', 'goods', '黄金', 210000, 45000, 165000, 'cftc'),
            ('2026-09-08', 'goods', '黄金', 212000, 46000, 166000, 'cftc'),
            ('2026-09-01', 'fx', '欧元', 80000, 60000, 20000, 'cftc');

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
            ('2026-09-04', 'PET.WCESTUS1.W', '全美商业原油库存(除SPR)', 424069, 'MBBL', 'eia'),
            ('2026-09-11', 'PET.WCESTUS1.W', '全美商业原油库存(除SPR)', 423429, 'MBBL', 'eia'),
            ('2026-09-04', 'PET.WGTSTUS1.W', '车用汽油总库存', 210000, 'MBBL', 'eia'),
            ('2026-09-11', 'PET.WGTSTUS1.W', '车用汽油总库存', 209000, 'MBBL', 'eia');
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


@pytest.fixture()
def empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        yield db_path
    finally:
        os.unlink(db_path)


@pytest.mark.unit
class TestGetUsMacro:
    def test_returns_csv_report(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_us_macro

        with _PatchedVendor(macro_db):
            result = get_us_macro()

        assert "US Macro Daily" in result
        assert "effr,dgs3mo" in result
        assert "stlfi" in result
        assert "3.63" in result

    def test_rows_oldest_first_and_limited(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_us_macro

        with _PatchedVendor(macro_db):
            result = get_us_macro(periods=2)

        assert "Total records: 2 days" in result
        assert result.index("2026-09-17") < result.index("2026-09-18")
        assert "2026-09-16," not in result

    def test_raises_no_data_error_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_us_macro

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_us_macro()

    def test_raises_no_data_error_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_us_macro

        conn = sqlite3.connect(empty_db)
        conn.execute(
            """
            CREATE TABLE us_macro_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE NOT NULL,
                effr REAL, dgs3mo REAL, dgs2 REAL, dgs10 REAL,
                t10yie REAL, t5yie REAL, spread_10y_3m REAL, real_rate_10y REAL,
                icsa REAL, hy_oas REAL, ig_oas REAL, stlfi REAL,
                data_source TEXT, updated_at DATETIME,
                UNIQUE(trade_date)
            )
            """
        )
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_us_macro()


@pytest.mark.unit
class TestGetCftcCot:
    def test_instrument_series(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        with _PatchedVendor(macro_db):
            result = get_cftc_cot("白银")

        assert "CFTC COT — 白银" in result
        assert "Total records: 2 weeks" in result
        assert "26,739" in result
        assert "| 2026-09-01 |" in result

    def test_instrument_oldest_first(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        with _PatchedVendor(macro_db):
            result = get_cftc_cot("黄金")

        assert result.index("2026-09-01") < result.index("2026-09-08")

    def test_goods_complex_wide_table(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        with _PatchedVendor(macro_db):
            result = get_cftc_cot()

        assert "Goods complex" in result
        # fx instruments must be excluded from the goods overview
        assert "欧元" not in result
        assert "白银" in result
        assert "黄金" in result
        assert "26,739" in result

    def test_unknown_instrument_raises_no_data_error(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        with _PatchedVendor(macro_db), pytest.raises(NoMarketDataError) as exc_info:
            get_cftc_cot("不存在")

        message = str(exc_info.value)
        assert "不存在" in message
        assert "白银" in message  # available instruments are listed

    def test_raises_no_data_error_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cftc_cot("白银")

    def test_raises_no_data_error_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        conn = sqlite3.connect(empty_db)
        conn.execute(
            """
            CREATE TABLE cftc_cot_weekly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE NOT NULL,
                market TEXT NOT NULL,
                instrument TEXT NOT NULL,
                long_positions REAL, short_positions REAL, net_positions REAL,
                data_source TEXT, updated_at DATETIME,
                UNIQUE(trade_date, market, instrument)
            )
            """
        )
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cftc_cot("白银")

    def test_goods_overview_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cftc_cot()

    def test_goods_overview_raises_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_cftc_cot

        conn = sqlite3.connect(empty_db)
        conn.execute(
            """
            CREATE TABLE cftc_cot_weekly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATE NOT NULL,
                market TEXT NOT NULL,
                instrument TEXT NOT NULL,
                long_positions REAL, short_positions REAL, net_positions REAL,
                data_source TEXT, updated_at DATETIME,
                UNIQUE(trade_date, market, instrument)
            )
            """
        )
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_cftc_cot()


@pytest.mark.unit
class TestGetEiaPetroleum:
    def test_single_series(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_eia_petroleum

        with _PatchedVendor(macro_db):
            result = get_eia_petroleum("PET.WCESTUS1.W")

        assert "PET.WCESTUS1.W" in result
        assert "Total records: 2 weeks" in result
        assert "424,069.00" in result

    def test_all_series_wide_table(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_eia_petroleum

        with _PatchedVendor(macro_db):
            result = get_eia_petroleum()

        assert "EIA Petroleum Weekly" in result
        assert "车用汽油总库存" in result
        assert "423,429.00" in result

    def test_unknown_series_raises_no_data_error(self, macro_db):
        from tradingagents.dataflows.smartmoney_vendor import get_eia_petroleum

        with _PatchedVendor(macro_db), pytest.raises(NoMarketDataError) as exc_info:
            get_eia_petroleum("PET.XXXX.W")

        message = str(exc_info.value)
        assert "PET.XXXX.W" in message
        assert "PET.WCESTUS1.W" in message  # available series are listed

    def test_raises_no_data_error_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_eia_petroleum

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_eia_petroleum("PET.WCESTUS1.W")

    def test_raises_no_data_error_when_empty(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_eia_petroleum

        conn = sqlite3.connect(empty_db)
        conn.execute(
            """
            CREATE TABLE eia_petroleum_weekly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_date DATE NOT NULL,
                series_id TEXT NOT NULL,
                series_name TEXT, value REAL, units TEXT,
                data_source TEXT, updated_at DATETIME,
                UNIQUE(week_date, series_id)
            )
            """
        )
        conn.commit()
        conn.close()

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_eia_petroleum()

    def test_all_series_raises_when_table_missing(self, empty_db):
        from tradingagents.dataflows.smartmoney_vendor import get_eia_petroleum

        with _PatchedVendor(empty_db), pytest.raises(NoMarketDataError):
            get_eia_petroleum()
