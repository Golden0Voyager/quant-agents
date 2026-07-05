"""Edge-case tests for smartmoney_vendor.py uncovered code paths.

Targets:
- get_fundamentals: full-schema formatting (market_cap, dividend_yield,
  peg, debt_ratio) and no-data error path
- get_industry_valuation: no-valuation-data error, dividend_yield formatting,
  avg_roe formatting, below/above-average verdict branches
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pytest


def _rich_db(path: str) -> None:
    """Create a DB with the full fundamentals and valuation schemas."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
        INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
        INSERT INTO stock_list VALUES ('000858', '五粮液', '白酒');

        CREATE TABLE fundamentals (
            ts_code TEXT, trade_date TEXT,
            pe_ttm REAL, pb REAL, ps_ttm REAL,
            dividend_yield REAL, market_cap REAL,
            roe REAL, roa REAL, gross_margin REAL, net_margin REAL,
            revenue_growth REAL, profit_growth REAL, eps_growth REAL, peg REAL,
            debt_ratio REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO fundamentals VALUES (
            '600519','2026-06-19',
            25.0,8.0,10.0,
            0.02,2.0e11,
            32.0,15.0,90.0,50.0,
            20.0,25.0,18.0,2.0,
            35.0
        );

        CREATE TABLE sector_industry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            industry_name TEXT NOT NULL,
            trade_date DATE NOT NULL,
            avg_pe REAL, avg_pb REAL, avg_ps REAL,
            avg_roe REAL, avg_revenue_growth REAL, avg_profit_growth REAL,
            total_market_cap REAL,
            UNIQUE(industry_name, trade_date)
        );
        INSERT INTO sector_industry
            (industry_name, trade_date, avg_pe, avg_pb, avg_ps, avg_roe, total_market_cap)
        VALUES ('白酒','2026-07-01',22.15,2.74,4.43,12.5,3.13e11);

        CREATE TABLE historical_valuation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            trade_date DATE NOT NULL,
            pe_ttm REAL, pb REAL, ps_ttm REAL, dividend_yield REAL,
            UNIQUE(ts_code, trade_date)
        );
        INSERT INTO historical_valuation VALUES (1,'600519','2026-07-01',18.03,5.51,8.51,4.39);
    """)
    conn.commit()
    conn.close()


class _PatchedVendor:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def __enter__(self):
        self.patcher = patch(
            "tradingagents.dataflows.smartmoney_vendor._DB_PATH", self.db_path
        )
        self.patcher.start()
        return self

    def __exit__(self, *args):
        self.patcher.stop()


@pytest.mark.unit
class GetFundamentalsFullSchemaTests(unittest.TestCase):
    """Covers get_fundamentals formatting branches (lines 281, 283, 311-314, 319-321)."""

    def test_market_cap_formatting(self) -> None:
        """market_cap renders in 亿/billion format."""
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
            self.assertIn("总市值", result)
            self.assertIn("亿", result)
            self.assertIn("billion", result)
        finally:
            os.unlink(db_path)

    def test_dividend_yield_formatting(self) -> None:
        """dividend_yield renders as percentage."""
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
            self.assertIn("股息率", result)
            self.assertIn("2.00%", result)  # 0.02 * 100 = 2.00%
        finally:
            os.unlink(db_path)

    def test_peg_formatting(self) -> None:
        """PEG renders without percentage sign."""
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
            self.assertIn("PEG", result)
            self.assertIn("2.00", result)  # peg = 2.0, not 2.00%
        finally:
            os.unlink(db_path)

    def test_debt_ratio_section(self) -> None:
        """debt_ratio renders under 偿债能力 section."""
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
            self.assertIn("偿债能力", result)
            self.assertIn("资产负债率", result)
            self.assertIn("35.00%", result)
        finally:
            os.unlink(db_path)

    def test_roe_and_roa_formatting(self) -> None:
        """ROE and ROA render under 盈利能力."""
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
            self.assertIn("盈利能力", result)
            self.assertIn("ROE", result)
            self.assertIn("32.00%", result)
            self.assertIn("ROA", result)
            self.assertIn("15.00%", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetFundamentalsNoDataTests(unittest.TestCase):
    """Covers get_fundamentals error path (line 259)."""

    def test_raises_when_no_fundamentals(self) -> None:
        """When fundamentals table has no data for the code, raise RuntimeError."""
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
                INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
                CREATE TABLE fundamentals (
                    ts_code TEXT, trade_date TEXT, pe REAL, pb REAL, roe REAL,
                    PRIMARY KEY (ts_code, trade_date)
                );
                -- Insert for a different code, not 600519
                INSERT INTO fundamentals VALUES ('000858','2026-06-19',20.0,5.0,25.0);
            """)
            conn.close()
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_fundamentals("600519.SS", "2026-06-19")
            self.assertIn("No fundamentals", str(ctx.exception))
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetIndustryValuationEdgeTests(unittest.TestCase):
    """Covers get_industry_valuation uncovered paths (lines 610, 636, 652, 671, 673)."""

    def test_raises_when_both_data_sources_empty(self) -> None:
        """stock_list lookup succeeds but both sector_industry and
        historical_valuation have no data -> RuntimeError (line 610)."""
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
                INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
            """)
            conn.close()
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_industry_valuation("600519.SS")
            self.assertIn("Industry valuation", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_dividend_yield_rendered(self) -> None:
        """When historical_valuation has dividend_yield, it is rendered (line 636)."""
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_industry_valuation("600519.SS")
            self.assertIn("股息率", result)
            self.assertIn("4.39%", result)  # from historical_valuation
        finally:
            os.unlink(db_path)

    def test_avg_roe_rendered(self) -> None:
        """When sector_industry has avg_roe, it is rendered (line 652)."""
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _rich_db(db_path)
            with _PatchedVendor(db_path):
                result = get_industry_valuation("600519.SS")
            self.assertIn("平均 ROE", result)
            self.assertIn("12.50%", result)
        finally:
            os.unlink(db_path)

    def test_verdict_below_average(self) -> None:
        """pe_ttm / avg_pe < 0.8 -> 'below industry average' (line 671)."""
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
                INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
                CREATE TABLE sector_industry (
                    industry_name TEXT, trade_date DATE,
                    avg_pe REAL, avg_pb REAL, avg_ps REAL,
                    avg_roe REAL, avg_revenue_growth REAL,
                    avg_profit_growth REAL, total_market_cap REAL,
                    PRIMARY KEY (industry_name, trade_date)
                );
                INSERT INTO sector_industry VALUES ('白酒','2026-07-01',30.0,5.0,10.0,NULL,NULL,NULL,NULL);
                CREATE TABLE historical_valuation (
                    ts_code TEXT, trade_date DATE,
                    pe_ttm REAL, pb REAL, ps_ttm REAL, dividend_yield REAL,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO historical_valuation VALUES ('600519','2026-07-01',15.0,5.0,8.0,NULL);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_industry_valuation("600519.SS")
            # 15.0 / 30.0 = 0.5 < 0.8 -> below average
            self.assertIn("可能低估", result)
            self.assertIn("below industry average", result)
        finally:
            os.unlink(db_path)

    def test_verdict_above_average(self) -> None:
        """pe_ttm / avg_pe > 1.2 -> 'above industry average' (line 673)."""
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
                INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
                CREATE TABLE sector_industry (
                    industry_name TEXT, trade_date DATE,
                    avg_pe REAL, avg_pb REAL, avg_ps REAL,
                    avg_roe REAL, avg_revenue_growth REAL,
                    avg_profit_growth REAL, total_market_cap REAL,
                    PRIMARY KEY (industry_name, trade_date)
                );
                INSERT INTO sector_industry VALUES ('白酒','2026-07-01',10.0,5.0,10.0,NULL,NULL,NULL,NULL);
                CREATE TABLE historical_valuation (
                    ts_code TEXT, trade_date DATE,
                    pe_ttm REAL, pb REAL, ps_ttm REAL, dividend_yield REAL,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO historical_valuation VALUES ('600519','2026-07-01',25.0,5.0,8.0,NULL);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_industry_valuation("600519.SS")
            # 25.0 / 10.0 = 2.5 > 1.2 -> above average
            self.assertIn("可能高估", result)
            self.assertIn("above industry average", result)
        finally:
            os.unlink(db_path)
