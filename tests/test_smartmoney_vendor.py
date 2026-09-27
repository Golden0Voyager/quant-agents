import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pytest


def _create_test_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
        INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
        CREATE TABLE daily_bars (
            ts_code TEXT, trade_date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO daily_bars VALUES ('600519','2026-06-15',1500.0,1520.0,1490.0,1510.0,50000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-16',1510.0,1530.0,1500.0,1525.0,55000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-17',1525.0,1540.0,1510.0,1535.0,48000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-18',1535.0,1550.0,1525.0,1540.0,52000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-19',1540.0,1560.0,1530.0,1550.0,60000);
        CREATE TABLE indicators (
            ts_code TEXT, trade_date TEXT, close REAL, volume REAL,
            ma5 REAL, ma10 REAL, rsi6 REAL, macd_hist REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO indicators VALUES ('600519','2026-06-15',1510.0,50000,1510.0,1500.0,55.0,1.2);
        INSERT INTO indicators VALUES ('600519','2026-06-19',1550.0,60000,1540.0,1520.0,65.0,2.2);
        CREATE TABLE fundamentals (
            ts_code TEXT, trade_date TEXT, pe REAL, pb REAL, roe REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO fundamentals VALUES ('600519','2026-06-19',25.0,8.0,32.0);
    """)
    conn.commit()
    conn.close()


def _create_full_test_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
        INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');

        CREATE TABLE daily_bars (
            ts_code TEXT, trade_date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO daily_bars VALUES ('600519','2026-06-15',1500.0,1520.0,1490.0,1510.0,50000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-16',1510.0,1530.0,1500.0,1525.0,55000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-17',1525.0,1540.0,1510.0,1535.0,48000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-18',1535.0,1550.0,1525.0,1540.0,52000);
        INSERT INTO daily_bars VALUES ('600519','2026-06-19',1540.0,1560.0,1530.0,1550.0,60000);

        CREATE TABLE indicators (
            ts_code TEXT, trade_date TEXT, close REAL, volume REAL,
            ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
            vol_ma5 REAL, vol_ma50 REAL, vol_ma60 REAL,
            boll_upper REAL, boll_mid REAL, boll_lower REAL, boll_bandwidth REAL,
            cyc60 REAL, chip_concentration REAL,
            macd_dif REAL, macd_dea REAL, macd_hist REAL,
            kdj_k REAL, kdj_d REAL, kdj_j REAL,
            rsi6 REAL, rsi12 REAL, rsi24 REAL, cci REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO indicators VALUES ('600519','2026-06-15',1510.0,50000,1510.0,1500.0,1490.0,1480.0,50000,48000,47000,1520.0,1500.0,1480.0,2.5,1500.0,0.85,1.0,0.5,1.2,55.0,50.0,45.0,55.0,50.0,45.0,100.0);
        INSERT INTO indicators VALUES ('600519','2026-06-19',1550.0,60000,1540.0,1520.0,1510.0,1500.0,55000,50000,49000,1560.0,1540.0,1520.0,2.8,1520.0,0.80,2.0,1.0,2.2,65.0,60.0,55.0,65.0,60.0,55.0,120.0);

        CREATE TABLE fundamentals (
            ts_code TEXT, trade_date TEXT, pe_ttm REAL, pb REAL, roe REAL,
            market_cap REAL, ps_ttm REAL, dividend_yield REAL,
            roa REAL, gross_margin REAL, net_margin REAL,
            revenue_growth REAL, profit_growth REAL, eps_growth REAL, peg REAL,
            debt_ratio REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO fundamentals VALUES ('600519','2026-06-19',25.0,8.0,32.0,2.0e11,10.0,0.02,15.0,90.0,50.0,20.0,25.0,18.0,2.0,35.0);

        CREATE TABLE fund_flow (
            ts_code TEXT, trade_date TEXT,
            main_net_inflow REAL, main_net_inflow_pct REAL,
            super_large_net_inflow REAL, super_large_net_inflow_pct REAL,
            large_net_inflow REAL, large_net_inflow_pct REAL,
            is_simulated INTEGER,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO fund_flow VALUES ('600519','2026-06-19',1.0e8,0.05,5.0e7,0.03,3.0e7,0.02,0);
        INSERT INTO fund_flow VALUES ('600519','2026-06-18',-5.0e7,-0.02,-2.0e7,-0.01,-1.0e7,-0.005,1);

        CREATE TABLE margin_trading (
            ts_code TEXT, trade_date TEXT,
            margin_balance REAL, margin_buy REAL, margin_repay REAL,
            short_balance REAL, short_sell REAL, short_repay REAL, total_balance REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO margin_trading VALUES ('600519','2026-06-19',1.0e10,5.0e8,4.0e8,1.0e6,2.0e5,1.0e5,1.001e10);

        CREATE TABLE dragon_tiger (
            ts_code TEXT, trade_date TEXT,
            close_price REAL, pct_change REAL,
            net_buy_amount REAL, buy_amount REAL, sell_amount REAL,
            turnover_rate REAL, market_cap REAL, reason TEXT,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO dragon_tiger VALUES ('600519','2026-06-19',1550.0,2.5,1.0e7,5.0e7,4.0e7,0.01,2.0e11,'日涨幅偏离值达7%');

        CREATE TABLE block_trade (
            ts_code TEXT, trade_date TEXT,
            deal_price REAL, close_price REAL, discount_rate REAL,
            volume REAL, amount REAL,
            buyer_branch TEXT, seller_branch TEXT,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO block_trade VALUES ('600519','2026-06-19',1500.0,1550.0,3.2,100000,1.5e8,'中信证券','华泰证券');

        CREATE TABLE sector_fund_flow (
            trade_date TEXT, sector_name TEXT,
            main_net_inflow REAL, main_net_inflow_pct REAL,
            super_large_net_inflow REAL, large_net_inflow REAL,
            medium_net_inflow REAL, small_net_inflow REAL,
            PRIMARY KEY (trade_date, sector_name)
        );
        INSERT INTO sector_fund_flow VALUES ('2026-06-19','白酒',2.0e8,0.03,1.0e8,5.0e7,2.0e7,-1.0e7);
        INSERT INTO sector_fund_flow VALUES ('2026-06-19','军工电子',1.0e8,0.02,5.0e7,3.0e7,1.0e7,-5.0e6);
        INSERT INTO sector_fund_flow VALUES ('2026-06-19','军工装备',1.5e8,0.02,6.0e7,4.0e7,1.0e7,-6.0e6);
        INSERT INTO sector_fund_flow VALUES ('2026-06-19','元件',3.0e8,0.04,1.5e8,8.0e7,3.0e7,-2.0e7);

        CREATE TABLE shareholder_count (
            ts_code TEXT, report_date TEXT,
            holder_count INTEGER, holder_count_change_pct REAL, avg_shares_per_holder REAL,
            PRIMARY KEY (ts_code, report_date)
        );
        INSERT INTO shareholder_count VALUES ('600519','2026-06-19',100000,-2.5,5000);

        CREATE TABLE quarterly_financials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            report_period TEXT NOT NULL,
            revenue REAL,
            net_profit REAL,
            operating_cashflow REAL,
            roe REAL,
            gross_margin REAL,
            net_margin REAL,
            revenue_growth REAL,
            profit_growth REAL,
            debt_ratio REAL,
            eps REAL,
            bps REAL,
            UNIQUE(ts_code, report_period)
        );
        INSERT INTO quarterly_financials
            (id, ts_code, report_period, revenue, net_profit, operating_cashflow,
             roe, gross_margin, net_margin, revenue_growth, profit_growth,
             debt_ratio, eps, bps)
        VALUES
            (1,'600519','2026-03-31',5.47e10,2.72e10,2.69e10,
             10.57,89.76,52.22,6.34,1.47,
             12.12,21.76,216.32);

        CREATE TABLE IF NOT EXISTS chip_distribution_em (
            id INTEGER PRIMARY KEY, ts_code TEXT, trade_date TEXT,
            profit_ratio REAL, avg_cost REAL, cost_90_low REAL, cost_90_high REAL,
            concentration_90 REAL, cost_70_low REAL, cost_70_high REAL, concentration_70 REAL,
            chip_concentration REAL
        );
        INSERT INTO chip_distribution_em VALUES (1, '600519', '2026-06-19', 0.85, 1500.0, 1450.0, 1550.0, 0.08, 1480.0, 1520.0, 0.05, 0.08);

        CREATE TABLE institution_survey (
            id INTEGER PRIMARY KEY, trade_date TEXT, stock_code TEXT,
            stock_name TEXT, survey_org TEXT, survey_type TEXT, survey_count INTEGER
        );
        INSERT INTO institution_survey VALUES (1, '2026-06-19', '600519', '贵州茅台', '易方达基金', '现场调研', 1);

        CREATE TABLE earnings_forecast (
            id INTEGER PRIMARY KEY, ts_code TEXT, name TEXT, end_date TEXT,
            forecast_type TEXT, net_profit_change REAL, previous_profit REAL, data_source TEXT
        );
        INSERT INTO earnings_forecast VALUES (1, '600519', '贵州茅台', '2026-06-30', '预增', 25.5, 1.5e10, 'akshare');

        CREATE TABLE concept_member (
            concept_code TEXT, concept_name TEXT, ts_code TEXT, updated_at TEXT
        );
        INSERT INTO concept_member VALUES ('BK0447', '白酒概念', '600519', '2026-06-19');


        CREATE TABLE sector_industry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            industry_name TEXT NOT NULL,
            trade_date DATE NOT NULL,
            avg_pe REAL,
            avg_pb REAL,
            avg_ps REAL,
            avg_roe REAL,
            avg_revenue_growth REAL,
            avg_profit_growth REAL,
            total_market_cap REAL,
            fund_inflow_rank INTEGER,
            UNIQUE(industry_name, trade_date)
        );
        INSERT INTO sector_industry
            (id, industry_name, trade_date, avg_pe, avg_pb, avg_ps, total_market_cap)
        VALUES
            (1,'白酒','2026-07-01',22.15,2.74,4.43,3.13e11);

        CREATE TABLE historical_valuation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code TEXT NOT NULL,
            trade_date DATE NOT NULL,
            pe_ttm REAL,
            pb REAL,
            ps_ttm REAL,
            dividend_yield REAL,
            UNIQUE(ts_code, trade_date)
        );
        INSERT INTO historical_valuation VALUES (1,'600519','2026-07-01',18.03,5.51,8.51,NULL);
        INSERT INTO historical_valuation VALUES (2,'600519','2026-06-30',17.92,5.47,8.45,4.39);

        CREATE TABLE institutional_holdings (
            ts_code TEXT NOT NULL,
            report_date INTEGER NOT NULL,
            institution_count INTEGER,
            type_counts TEXT,
            PRIMARY KEY (ts_code, report_date)
        );
        INSERT INTO institutional_holdings VALUES ('600519',20260331,1372,'{"基金持仓": 1352, "券商持仓": 20}');
        INSERT INTO institutional_holdings VALUES ('600519',20251231,18,'{"券商持仓": 18}');

        CREATE TABLE north_hold (
            ts_code TEXT NOT NULL,
            security_name TEXT,
            trade_date DATE NOT NULL,
            close_price REAL,
            hold_shares REAL,
            hold_market_cap REAL,
            hold_shares_ratio REAL,
            free_shares_ratio REAL,
            total_shares_ratio REAL,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO north_hold VALUES ('600519','贵州茅台','2026-06-30',1185.49,53711656,63674631071,4.29,4.30,4.30);
        INSERT INTO north_hold VALUES ('600519','贵州茅台','2026-03-31',1420.0,52000000,73840000000,4.16,4.16,4.16);

        CREATE TABLE limit_up_down (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            name TEXT,
            pct_change REAL,
            close_price REAL,
            turnover_rate REAL,
            limit_type TEXT NOT NULL,
            board_count INTEGER,
            industry TEXT,
            UNIQUE(trade_date, ts_code, limit_type)
        );
        INSERT INTO limit_up_down (trade_date, ts_code, name, pct_change, close_price, turnover_rate, limit_type, board_count, industry) VALUES ('2026-06-19', '600519', '贵州茅台', 10.0, 1550.0, 0.5, '涨停', 2, '白酒');
        INSERT INTO limit_up_down (trade_date, ts_code, name, pct_change, close_price, turnover_rate, limit_type, board_count, industry) VALUES ('2026-06-19', '000858', '五粮液', 10.0, 180.0, 1.2, '涨停', 1, '白酒');
        INSERT INTO limit_up_down (trade_date, ts_code, name, pct_change, close_price, turnover_rate, limit_type, board_count, industry) VALUES ('2026-06-19', '002594', '比亚迪', 10.0, 250.0, 0.8, '涨停', 1, '汽车');
        INSERT INTO limit_up_down (trade_date, ts_code, name, pct_change, close_price, turnover_rate, limit_type, board_count, industry) VALUES ('2026-06-19', '000001', '平安银行', -10.0, 10.0, 2.0, '跌停', NULL, '银行');
        INSERT INTO limit_up_down (trade_date, ts_code, name, pct_change, close_price, turnover_rate, limit_type, board_count, industry) VALUES ('2026-06-19', '000002', '万科A', -10.0, 8.0, 1.5, '跌停', NULL, '房地产');

        CREATE TABLE index_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            UNIQUE(index_code, trade_date)
        );
        INSERT INTO index_daily (index_code, index_name, trade_date, open, high, low, close, volume) VALUES ('sh000001', '上证指数', '2026-06-15', 3050.0, 3060.0, 3045.0, 3055.0, 2.5e9);
        INSERT INTO index_daily (index_code, index_name, trade_date, open, high, low, close, volume) VALUES ('sh000001', '上证指数', '2026-06-16', 3055.0, 3070.0, 3050.0, 3065.0, 2.6e9);
        INSERT INTO index_daily (index_code, index_name, trade_date, open, high, low, close, volume) VALUES ('sh000001', '上证指数', '2026-06-17', 3065.0, 3080.0, 3060.0, 3075.0, 2.7e9);
        INSERT INTO index_daily (index_code, index_name, trade_date, open, high, low, close, volume) VALUES ('sh000001', '上证指数', '2026-06-18', 3075.0, 3090.0, 3070.0, 3085.0, 2.8e9);
        INSERT INTO index_daily (index_code, index_name, trade_date, open, high, low, close, volume) VALUES ('sh000001', '上证指数', '2026-06-19', 3085.0, 3100.0, 3080.0, 3095.0, 2.9e9);
        INSERT INTO index_daily (index_code, index_name, trade_date, open, high, low, close, volume) VALUES ('sz399001', '深证成指', '2026-06-19', 9850.0, 9900.0, 9820.0, 9880.0, 3.1e9);

        CREATE TABLE research_report (
            ts_code TEXT NOT NULL,
            report_date TEXT NOT NULL,
            org_name TEXT,
            rating TEXT,
            target_price REAL,
            title TEXT,
            PRIMARY KEY (ts_code, report_date, org_name)
        );
        INSERT INTO research_report VALUES ('600519','2026-06-15','中信证券','买入',1800.0,'贵州茅台深度研究：高端白酒龙头估值重构');
        INSERT INTO research_report VALUES ('600519','2026-06-10','华泰证券','增持',1750.0,'茅台提价周期开启，业绩确定性强');
    """)
    conn.commit()
    conn.close()


def _insert_null_quarterly_row(db_path, ts_code="000603"):
    """Insert a quarterly_financials row whose metric columns are all NULL —
    mimics the stale local rows that used to block the fallback chain."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO quarterly_financials (ts_code, report_period) VALUES (?, ?)",
        (ts_code, "2026-03-31"),
    )
    conn.commit()
    conn.close()


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


@pytest.mark.unit
class ToSmartmoneySymbolTests(unittest.TestCase):
    def test_strips_ss_suffix(self):
        from tradingagents.dataflows.smartmoney_vendor import _to_smartmoney_symbol
        self.assertEqual(_to_smartmoney_symbol("600519.SS"), "600519")


@pytest.mark.unit
class GetConnectionTests(unittest.TestCase):
    @patch("tradingagents.dataflows.smartmoney_vendor._DB_PATH", "/nonexistent/db.db")
    def test_raises_on_missing_db(self):
        from tradingagents.dataflows.smartmoney_vendor import _get_connection
        with self.assertRaises(FileNotFoundError):
            _get_connection()


@pytest.mark.unit
class DfFromSqlTests(unittest.TestCase):
    def test_returns_none_on_error(self):
        from tradingagents.dataflows.smartmoney_vendor import _df_from_sql
        with patch("tradingagents.dataflows.smartmoney_vendor._get_connection") as mock_conn:
            mock_conn.side_effect = Exception("db error")
            self.assertIsNone(_df_from_sql("SELECT 1"))


@pytest.mark.unit
class DfFromSqlEmptyTests(unittest.TestCase):
    def test_returns_none_on_empty_result(self):
        from tradingagents.dataflows.smartmoney_vendor import _df_from_sql

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (x int)")
            conn.close()
            with _PatchedVendor(db_path):
                df = _df_from_sql("SELECT * FROM t WHERE x = 999")
                self.assertIsNotNone(df)
                self.assertTrue(df.empty)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetStockDataTests(unittest.TestCase):
    def test_returns_csv(self):
        from tradingagents.dataflows.smartmoney_vendor import get_stock_data

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_stock_data("600519.SS", "2026-06-15", "2026-06-19")
                self.assertIn("600519", result)
                self.assertIn("Close", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_stock_data

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_stock_data("999999.SS", "2026-06-15", "2026-06-19")
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetFundamentalsTests(unittest.TestCase):
    def test_returns_fundamentals(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
                self.assertIn("600519", result)
                self.assertIn("贵州茅台", result)
        finally:
            os.unlink(db_path)

    def test_dividend_yield_not_scaled_up(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fundamentals

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE stock_list (code TEXT PRIMARY KEY, name TEXT, industry TEXT);
                INSERT INTO stock_list VALUES ('600519', '贵州茅台', '白酒');
                CREATE TABLE fundamentals (
                    ts_code TEXT, trade_date TEXT, dividend_yield REAL,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO fundamentals VALUES ('600519','2026-06-19',5.458);
            """)
            conn.commit()
            conn.close()
            with _PatchedVendor(db_path):
                result = get_fundamentals("600519.SS", "2026-06-19")
                # dividend_yield in quant_core.db is already a percent number
                self.assertIn("股息率: 5.46%", result)
                self.assertNotIn("545.80%", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetFundFlowTests(unittest.TestCase):
    def test_returns_fund_flow_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fund_flow("600519.SS", curr_date="2026-06-19")
                self.assertIn("600519", result)
                self.assertIn("Main Force Net Inflow", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_fund_flow("999999.SS")
        finally:
            os.unlink(db_path)

    def test_shows_simulated_note(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE fund_flow (
                    ts_code TEXT, trade_date TEXT,
                    main_net_inflow REAL, main_net_inflow_pct REAL,
                    super_large_net_inflow REAL, super_large_net_inflow_pct REAL,
                    large_net_inflow REAL, large_net_inflow_pct REAL,
                    is_simulated INTEGER,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO fund_flow VALUES ('600519','2026-06-19',1e8,0.05,5e7,0.03,3e7,0.02,1);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_fund_flow("600519.SS", curr_date="2026-06-19")
                self.assertIn("simulated", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetMarginTradingTests(unittest.TestCase):
    def test_returns_margin_trading_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_margin_trading

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_margin_trading("600519.SS", curr_date="2026-06-19")
                self.assertIn("融资余额", result)
                self.assertIn("融券余量", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_margin_trading

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_margin_trading("999999.SS")
        finally:
            os.unlink(db_path)

    def test_handles_null_numeric_values(self):
        """get_margin_trading should return 'N/A' for NULL DB values, not crash."""
        from tradingagents.dataflows.smartmoney_vendor import get_margin_trading

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE margin_trading (
                    ts_code TEXT, trade_date TEXT,
                    margin_balance REAL, margin_buy REAL, margin_repay REAL,
                    short_balance REAL, short_sell REAL, short_repay REAL, total_balance REAL,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO margin_trading VALUES ('600519','2026-06-19',1.0e10,5.0e8,4.0e8,NULL,2.0e5,1.0e5,NULL);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_margin_trading("600519.SS", curr_date="2026-06-19")
                # short_balance and total_balance are NULL → should show N/A
                self.assertIn("N/A", result)
                # margin_balance has a value → should be formatted normally
                self.assertIn("10,000,000,000", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetDragonTigerTests(unittest.TestCase):
    def test_returns_dragon_tiger_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_dragon_tiger

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_dragon_tiger("600519.SS", curr_date="2026-06-19")
                self.assertIn("龙虎榜", result)
                self.assertIn("日涨幅偏离值达7%", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_dragon_tiger

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_dragon_tiger("999999.SS")
        finally:
            os.unlink(db_path)

    def test_handles_missing_reason_column(self):
        from tradingagents.dataflows.smartmoney_vendor import get_dragon_tiger

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE dragon_tiger (
                    ts_code TEXT, trade_date TEXT,
                    close_price REAL, pct_change REAL,
                    net_buy_amount REAL, buy_amount REAL, sell_amount REAL,
                    turnover_rate REAL, market_cap REAL, reason TEXT,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO dragon_tiger VALUES ('600519','2026-06-19',1550.0,2.5,1e7,5e7,4e7,0.01,2e11,NULL);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_dragon_tiger("600519.SS", curr_date="2026-06-19")
                self.assertIn("龙虎榜", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class LocalDataStalenessGuardTests(unittest.TestCase):
    """_assert_local_data_not_stale wired into high-frequency getters.

    Stale local rows must raise NoMarketDataError so route_to_vendor falls
    through to the online vendor instead of silently serving aged data.
    """

    def _make_db(self, table: str, ddl_and_rows: str) -> str:
        import sqlite3
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = sqlite3.connect(db_path)
        conn.executescript(ddl_and_rows)
        conn.close()
        self.addCleanup(os.unlink, db_path)
        return db_path

    def test_fund_flow_stale_raises(self):
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        db_path = self._make_db("fund_flow", """
            CREATE TABLE fund_flow (
                ts_code TEXT, trade_date TEXT,
                main_net_inflow REAL, main_net_inflow_pct REAL,
                super_large_net_inflow REAL, super_large_net_inflow_pct REAL,
                large_net_inflow REAL, large_net_inflow_pct REAL,
                is_simulated INTEGER
            );
            INSERT INTO fund_flow VALUES
                ('600519','2026-06-19',1e8,0.05,5e7,0.03,3e7,0.02,0),
                ('600519','2026-06-18',1e8,0.05,5e7,0.03,3e7,0.02,0);
        """)
        with _PatchedVendor(db_path), self.assertRaises(NoMarketDataError):
            # 9 days old > 7-day budget: must fall through to the online vendor
            get_fund_flow("600519.SS", curr_date="2026-06-27")

    def test_fund_flow_within_budget_returns(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        db_path = self._make_db("fund_flow", """
            CREATE TABLE fund_flow (
                ts_code TEXT, trade_date TEXT,
                main_net_inflow REAL, main_net_inflow_pct REAL,
                super_large_net_inflow REAL, super_large_net_inflow_pct REAL,
                large_net_inflow REAL, large_net_inflow_pct REAL,
                is_simulated INTEGER
            );
            INSERT INTO fund_flow VALUES
                ('600519','2026-06-25',1e8,0.05,5e7,0.03,3e7,0.02,0);
        """)
        with _PatchedVendor(db_path):
            result = get_fund_flow("600519.SS", curr_date="2026-06-27")
        self.assertIn("Main Force Net Inflow", result)

    def test_backtest_anchor_not_penalised(self):
        """Old data is fine when the request itself targets an old date."""
        from tradingagents.dataflows.smartmoney_vendor import (
            get_dragon_tiger,
            get_fund_flow,
            get_margin_trading,
        )

        db_path = self._make_db("mixed", """
            CREATE TABLE fund_flow (
                ts_code TEXT, trade_date TEXT,
                main_net_inflow REAL, main_net_inflow_pct REAL,
                super_large_net_inflow REAL, super_large_net_inflow_pct REAL,
                large_net_inflow REAL, large_net_inflow_pct REAL,
                is_simulated INTEGER
            );
            INSERT INTO fund_flow VALUES
                ('600519','2026-06-19',1e8,0.05,5e7,0.03,3e7,0.02,0);
            CREATE TABLE margin_trading (
                ts_code TEXT, trade_date TEXT,
                margin_balance REAL, margin_buy REAL, margin_repay REAL,
                short_balance REAL, short_sell REAL, short_repay REAL,
                total_balance REAL
            );
            INSERT INTO margin_trading VALUES
                ('600519','2026-06-19',1e10,5e8,4e8,1e6,2e5,1e5,1.001e10);
            CREATE TABLE dragon_tiger (
                ts_code TEXT, trade_date TEXT, close_price REAL,
                pct_change REAL, net_buy_amount REAL, buy_amount REAL,
                sell_amount REAL, turnover_rate REAL, market_cap REAL,
                reason TEXT
            );
            INSERT INTO dragon_tiger VALUES
                ('600519','2026-06-19',1550.0,2.5,1e7,5e7,4e7,0.01,2e11,'x');
        """)
        with _PatchedVendor(db_path):
            self.assertIn("Main Force", get_fund_flow("600519.SS", curr_date="2026-06-19"))
            self.assertIn("融资余额", get_margin_trading("600519.SS", curr_date="2026-06-19"))
            self.assertIn("龙虎榜", get_dragon_tiger("600519.SS", curr_date="2026-06-19"))

    def test_malformed_dates_never_block(self):
        """A freshness guard must not be the reason data becomes unavailable."""
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        db_path = self._make_db("fund_flow", """
            CREATE TABLE fund_flow (
                ts_code TEXT, trade_date TEXT,
                main_net_inflow REAL, main_net_inflow_pct REAL,
                super_large_net_inflow REAL, super_large_net_inflow_pct REAL,
                large_net_inflow REAL, large_net_inflow_pct REAL,
                is_simulated INTEGER
            );
            INSERT INTO fund_flow VALUES
                ('600519','not-a-date',1e8,0.05,5e7,0.03,3e7,0.02,0);
        """)
        with _PatchedVendor(db_path):
            # no curr_date: guard anchors at today; unparseable latest must be
            # ignored, not treated as infinitely stale
            result = get_fund_flow("600519.SS")
        self.assertIn("Main Force", result)


@pytest.mark.unit
class GetBlockTradeTests(unittest.TestCase):
    def test_returns_block_trade_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_block_trade

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_block_trade("600519.SS")
                self.assertIn("大宗交易", result)
                self.assertIn("中信证券", result)
                self.assertIn("华泰证券", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_block_trade

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_block_trade("999999.SS")
        finally:
            os.unlink(db_path)

    def test_handles_null_buyer_seller(self):
        from tradingagents.dataflows.smartmoney_vendor import get_block_trade

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE block_trade (
                    ts_code TEXT, trade_date TEXT,
                    deal_price REAL, close_price REAL, discount_rate REAL,
                    volume REAL, amount REAL,
                    buyer_branch TEXT, seller_branch TEXT,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO block_trade VALUES ('600519','2026-06-19',1500.0,1550.0,3.2,100000,1.5e8,NULL,NULL);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_block_trade("600519.SS")
                self.assertIn("None", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetSectorFundFlowTests(unittest.TestCase):
    def test_returns_sector_fund_flow_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_sector_fund_flow("白酒")
                self.assertIn("白酒", result)
                self.assertIn("Sector Fund Flow", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_sector_fund_flow("Nonexistent Sector")
        finally:
            os.unlink(db_path)

    def test_fuzzy_unique_match_auto_resolves(self):
        """唯一模糊命中自动采用："电子元件" → 板块 "元件"，并在输出中标注。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_sector_fund_flow("电子元件")
                self.assertIn("元件 Sector Fund Flow", result)
                self.assertIn("自动匹配到板块", result)
        finally:
            os.unlink(db_path)

    def test_fuzzy_ambiguous_raises_with_candidates(self):
        """歧义命中报错并列出候选："军工" → 军工电子/军工装备。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                with self.assertRaises(RuntimeError) as ctx:
                    get_sector_fund_flow("军工")
                self.assertIn("军工电子", str(ctx.exception))
                self.assertIn("军工装备", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_zero_match_lists_available_sectors(self):
        """零命中报错并回传可用板块名，供 LLM 重试。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                with self.assertRaises(RuntimeError) as ctx:
                    get_sector_fund_flow("元宇宙")
                self.assertIn("Available sectors", str(ctx.exception))
                self.assertIn("白酒", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_handles_null_numeric_columns(self):
        # Regression: a row with SQL NULL (None/NaN) numeric cells must render
        # "N/A" rather than crashing with
        # "unsupported format string passed to NoneType.__format__".
        import pandas as pd

        from tradingagents.dataflows import smartmoney_vendor

        df = pd.DataFrame(
            {
                "Date": ["2026-06-19"],
                "main_net_inflow": [None],
                "main_net_inflow_pct": [None],
                "super_large_net_inflow": [1.0e8],
                "large_net_inflow": [None],
                "medium_net_inflow": [2.0e7],
                "small_net_inflow": [None],
            }
        )
        # get_sector_fund_flow 现在会先查 DISTINCT sector_name 做模糊匹配，
        # mock 需按 SQL 分流返回
        names_df = pd.DataFrame({"sector_name": ["新能源"]})

        def _fake_df_from_sql(sql, params=()):
            return names_df if "DISTINCT sector_name" in sql else df

        with patch.object(smartmoney_vendor, "_df_from_sql", side_effect=_fake_df_from_sql):
            result = smartmoney_vendor.get_sector_fund_flow("新能源")
        self.assertIn("新能源", result)
        self.assertIn("N/A", result)

    def _db_with_stock(self, db_path, code, name, industry):
        """Create the full fixture DB and add one extra stock_list row."""
        _create_full_test_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO stock_list VALUES (?, ?, ?)", (code, name, industry)
        )
        conn.commit()
        conn.close()

    def test_ticker_fallback_resolves_registered_industry(self):
        """概念名零命中时按 ticker 注册行业解析："新能源" + 600519 → 白酒。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_sector_fund_flow("新能源汽车", ticker="600519.SS")
                self.assertIn("白酒 Sector Fund Flow", result)
                self.assertIn("注册行业 '白酒'", result)
        finally:
            os.unlink(db_path)

    def test_ticker_fallback_resolves_ambiguous_via_alias(self):
        """请求名歧义（军工→军工电子/军工装备）时按注册行业 + 别名定夺：
        600760 注册行业 "航空装备" → 别名 "军工装备"。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._db_with_stock(db_path, "600760", "中航沈飞", "航空装备")
            with _PatchedVendor(db_path):
                result = get_sector_fund_flow("军工", ticker="600760.SS")
                self.assertIn("军工装备 Sector Fund Flow", result)
                self.assertIn("注册行业 '航空装备'", result)
        finally:
            os.unlink(db_path)

    def test_ticker_fallback_unresolvable_industry_still_raises(self):
        """注册行业也无命中（如 "农牧饲渔"，故意不加别名）时仍报错并列出可用板块。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._db_with_stock(db_path, "300999", "金龙鱼", "农牧饲渔")
            with _PatchedVendor(db_path):
                with self.assertRaises(RuntimeError) as ctx:
                    get_sector_fund_flow("农业概念", ticker="300999.SZ")
                self.assertIn("Available sectors", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_ticker_unknown_code_raises_direct_error(self):
        """ticker 不在 stock_list 时保留原始报错（含候选/可用板块）。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                with self.assertRaises(RuntimeError) as ctx:
                    get_sector_fund_flow("元宇宙", ticker="000000.SZ")
                self.assertIn("Available sectors", str(ctx.exception))
                self.assertIn("元宇宙", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_suffix_stripped_unique_match(self):
        """后缀归一："元件行业" → 板块 "元件"。"""
        from tradingagents.dataflows.smartmoney_vendor import get_sector_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_sector_fund_flow("元件行业")
                self.assertIn("元件 Sector Fund Flow", result)
                self.assertIn("自动匹配到板块", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetShareholderCountTests(unittest.TestCase):
    def test_returns_shareholder_count_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_shareholder_count

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_shareholder_count("600519.SS")
                self.assertIn("股东户数", result)
                self.assertIn("100,000", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_shareholder_count

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_shareholder_count("999999.SS")
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class RuntimeErrorStubsTests(unittest.TestCase):
    def test_get_insider_transactions_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_insider_transactions
        with self.assertRaises(RuntimeError) as ctx:
            get_insider_transactions("600519.SS")
        self.assertIn("Insider transactions", str(ctx.exception))

    def test_get_company_announcements_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_company_announcements
        with self.assertRaises(RuntimeError) as ctx:
            get_company_announcements("600519.SS", "2026-06-01", "2026-06-19")
        self.assertIn("Company announcements", str(ctx.exception))

    def test_get_restricted_release_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_restricted_release
        with self.assertRaises(RuntimeError) as ctx:
            get_restricted_release("600519.SS")
        self.assertIn("Restricted release", str(ctx.exception))

    def test_get_institutional_holdings_from_db(self):
        from tradingagents.dataflows.smartmoney_vendor import (
            get_institutional_holdings,
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with patch(
                "tradingagents.dataflows.smartmoney_vendor._DB_PATH", db_path
            ):
                result = get_institutional_holdings("600519.SS")
            self.assertIn("600519.SS", result)
            self.assertIn("机构持股", result)
            self.assertIn("Institutional Holdings", result)
            self.assertIn("1,372", result)
            self.assertIn("基金持仓", result)
            self.assertIn("券商持仓", result)
            self.assertIn("20260331", result)
        finally:
            os.unlink(db_path)

    def test_get_northbound_hold_raises_no_data_when_db_missing(self):
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_northbound_hold
        with self.assertRaises(NoMarketDataError) as ctx:
            get_northbound_hold("999999.SS")
        self.assertIn("northbound", str(ctx.exception))

    def test_get_news_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_news
        with self.assertRaises(RuntimeError) as ctx:
            get_news("600519.SS", "2026-01-01", "2026-06-19")
        self.assertIn("News not available", str(ctx.exception))

    def test_get_earnings_estimates_reads_local_forecast_rows(self):
        from tradingagents.dataflows.smartmoney_vendor import get_earnings_estimates

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_earnings_estimates("600519.SS", curr_date="2026-08-11")
            self.assertIn("Earnings Forecast", result)
            self.assertIn("2026-06-30", result)
            self.assertIn("预增", result)
        finally:
            os.unlink(db_path)

    def test_get_pledge_ratio_reads_stock_pledge_schema(self):
        from tradingagents.dataflows.smartmoney_vendor import get_pledge_ratio

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE stock_pledge (
                    id INTEGER PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    pledger TEXT,
                    pledge_amount REAL,
                    pledge_ratio REAL,
                    pledge_org TEXT,
                    source_record_key TEXT NOT NULL
                );
                INSERT INTO stock_pledge VALUES
                    (1, '2026-08-07', '600519', '贵州茅台', '股东A',
                     1000000, 2.5, '机构A', '600519|2026-08-07|1');
            """)
            conn.commit()
            conn.close()
            with _PatchedVendor(db_path):
                result = get_pledge_ratio("600519.SS")
            self.assertIn("Pledge Ratio", result)
            self.assertIn("质押数量", result)
            self.assertIn("2.5", result)
        finally:
            os.unlink(db_path)

    def test_get_macro_indicators_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_macro_indicators
        with self.assertRaises(RuntimeError) as ctx:
            get_macro_indicators()
        self.assertIn("not available in quant_core.db", str(ctx.exception))


@pytest.mark.unit
class GetBalanceSheetFromDbTests(unittest.TestCase):
    """get_balance_sheet now reads from quarterly_financials table."""

    def test_returns_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_balance_sheet

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_balance_sheet("600519.SS")
                self.assertIn("资产负债率", result)
                self.assertIn("12.12", result)
                self.assertIn("每股净资产", result)
                self.assertIn("216.32", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_balance_sheet

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_balance_sheet("999999.SS")
            self.assertIn("Balance sheet", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_raises_on_null_metrics_row(self):
        """Row exists but all metric columns are NULL — must raise so the
        fallback chain (hithink/akshare) is not blocked by an empty shell."""
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_balance_sheet

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            _insert_null_quarterly_row(db_path)
            with (
                _PatchedVendor(db_path),
                self.assertRaises(NoMarketDataError) as ctx,
            ):
                get_balance_sheet("000603.SZ")
            self.assertIn("null", str(ctx.exception))
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetCashflowFromDbTests(unittest.TestCase):
    """get_cashflow now reads from quarterly_financials table."""

    def test_returns_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_cashflow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_cashflow("600519.SS")
                self.assertIn("经营活动现金流净额", result)
                self.assertIn("26,900,000,000", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_cashflow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_cashflow("999999.SS")
            self.assertIn("Cashflow", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_raises_on_null_metrics_row(self):
        """Row exists but operating_cashflow is NULL — must raise for fallback."""
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_cashflow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            _insert_null_quarterly_row(db_path)
            with (
                _PatchedVendor(db_path),
                self.assertRaises(NoMarketDataError) as ctx,
            ):
                get_cashflow("000603.SZ")
            self.assertIn("null", str(ctx.exception))
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetIncomeStatementFromDbTests(unittest.TestCase):
    """get_income_statement now reads from quarterly_financials table."""

    def test_returns_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_income_statement

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_income_statement("600519.SS")
                self.assertIn("营业总收入", result)
                self.assertIn("54,700,000,000", result)
                self.assertIn("毛利率", result)
                self.assertIn("89.76", result)
                self.assertIn("基本每股收益", result)
                self.assertIn("21.76", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_income_statement

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_income_statement("999999.SS")
            self.assertIn("Income statement", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_raises_on_null_metrics_row(self):
        """Row exists but all metric columns are NULL — must raise for fallback."""
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_income_statement

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            _insert_null_quarterly_row(db_path)
            with (
                _PatchedVendor(db_path),
                self.assertRaises(NoMarketDataError) as ctx,
            ):
                get_income_statement("000603.SZ")
            self.assertIn("null", str(ctx.exception))
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetIndustryValuationFromDbTests(unittest.TestCase):
    """get_industry_valuation now reads from sector_industry + historical_valuation."""

    def test_returns_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_industry_valuation("600519.SS")
                self.assertIn("白酒", result)
                self.assertIn("18.03", result)  # pe_ttm
                self.assertIn("5.51", result)  # pb
                self.assertIn("22.15", result)  # industry avg_pe
                self.assertIn("2.74", result)  # industry avg_pb
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_industry_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_industry_valuation("999999.SS")
            self.assertIn("Industry valuation", str(ctx.exception))
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetIndicatorsTests(unittest.TestCase):
    def test_unknown_indicator_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_indicators

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_indicators("600519.SS", "zzz_not_an_indicator", "2026-06-19", 5)
        finally:
            os.unlink(db_path)

    def test_no_ohlcv_data_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_indicators

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_indicators("999999.SS", "rsi6", "2026-06-19", 5)
        finally:
            os.unlink(db_path)

    def test_precomputed_indicator_returns_values(self):
        from tradingagents.dataflows.smartmoney_vendor import get_indicators

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_indicators("600519.SS", "ma5", "2026-06-19", 5)
                self.assertIn("ma5", result)
                self.assertIn("600519", result)
        finally:
            os.unlink(db_path)

    def test_precomputed_indicator_no_indicator_table(self):
        from tradingagents.dataflows.smartmoney_vendor import get_indicators

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE daily_bars (
                    ts_code TEXT, trade_date TEXT, open REAL, high REAL,
                    low REAL, close REAL, volume REAL,
                    PRIMARY KEY (ts_code, trade_date)
                );
                INSERT INTO daily_bars VALUES ('600519','2026-06-15',1500.0,1520.0,1490.0,1510.0,50000);
                INSERT INTO daily_bars VALUES ('600519','2026-06-16',1510.0,1530.0,1500.0,1525.0,55000);
                INSERT INTO daily_bars VALUES ('600519','2026-06-17',1525.0,1540.0,1510.0,1535.0,48000);
                INSERT INTO daily_bars VALUES ('600519','2026-06-18',1535.0,1550.0,1525.0,1540.0,52000);
                INSERT INTO daily_bars VALUES ('600519','2026-06-19',1540.0,1560.0,1530.0,1550.0,60000);
            """)
            conn.close()
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_indicators("600519.SS", "rsi6", "2026-06-19", 5)
        finally:
            os.unlink(db_path)



@pytest.mark.unit
class GetNorthboundHoldTests(unittest.TestCase):
    def test_returns_northbound_flow_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_northbound_hold

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_northbound_hold("600519.SS")
                self.assertIn("600519", result)
                self.assertIn("Northbound", result)
                self.assertIn("贵州茅台", result)
                self.assertIn("Hold shares", result)
                self.assertIn("Hold market cap", result)
                self.assertIn("QoQ change", result)
                self.assertIn("增持", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_northbound_hold

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(NoMarketDataError):
                get_northbound_hold("999999.SS")
        finally:
            os.unlink(db_path)

    def test_route_to_vendor_returns_no_data_available(self):
        from tradingagents.dataflows import interface
        from tradingagents.dataflows.errors import NoMarketDataError

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), patch(
                "tradingagents.dataflows.interface.VENDOR_METHODS",
                {
                    **interface.VENDOR_METHODS,
                    "get_northbound_hold": {
                        "smartmoney_db": interface.VENDOR_METHODS["get_northbound_hold"]["smartmoney_db"],
                        "akshare": lambda *a, **k: (_ for _ in ()).throw(
                            NoMarketDataError(a[0] if a else "", "", "No akshare data")
                        ),
                    },
                },
            ):
                result = interface.route_to_vendor("get_northbound_hold", "999999.SS")
            self.assertIn("NO_DATA_AVAILABLE", result)
            self.assertIn("999999.SS", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetLimitUpDownTests(unittest.TestCase):
    def test_returns_limit_up_down_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_limit_up_down

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_limit_up_down("2026-06-19")
            self.assertIn("Limit-Up / Limit-Down", result)
            self.assertIn("涨停", result)
            self.assertIn("跌停", result)
            self.assertIn("贵州茅台", result)
            self.assertIn("平安银行", result)
            self.assertIn("连板分布", result)
            self.assertIn("行业分布", result)
        finally:
            os.unlink(db_path)

    def test_missing_date_returns_no_data_available(self):
        from tradingagents.dataflows import interface

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = interface.route_to_vendor(
                    "get_limit_up_down", "2026-06-20"
                )
            self.assertIn("NO_DATA_AVAILABLE", result)
        finally:
            os.unlink(db_path)

    def test_db_error_returns_no_data_available(self):
        from tradingagents.dataflows import interface

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Empty DB file that exists but has no limit_up_down table
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE dummy (x int)")
            conn.close()
            with _PatchedVendor(db_path):
                result = interface.route_to_vendor(
                    "get_limit_up_down", "2026-06-19"
                )
            self.assertIn("NO_DATA_AVAILABLE", result)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetIndexDailyTests(unittest.TestCase):
    def test_returns_index_daily_data(self):
        from tradingagents.dataflows.smartmoney_vendor import get_index_daily

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_index_daily("000001.SS", "2026-06-15", "2026-06-19")
            self.assertIn("000001.SS", result)
            self.assertIn("Index data", result)
            self.assertIn("3055.0", result)
            self.assertIn("3095.0", result)
            self.assertIn("Open", result)
            self.assertIn("Close", result)
        finally:
            os.unlink(db_path)

    def test_raises_on_no_data(self):
        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_index_daily

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(NoMarketDataError):
                get_index_daily("999999.SS", "2026-06-15", "2026-06-19")
        finally:
            os.unlink(db_path)

    def test_empty_result_returns_no_data_available(self):
        from tradingagents.dataflows import interface

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                # Valid table, but no rows match the requested code/date range.
                result = interface.route_to_vendor(
                    "get_index_daily", "999999.SS", "2026-06-15", "2026-06-19"
                )
            self.assertIn("NO_DATA_AVAILABLE", result)
        finally:
            os.unlink(db_path)

    def test_schema_failure_returns_no_data_available(self):
        from tradingagents.dataflows import interface

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Empty DB file with no index_daily table simulates a schema/DB failure.
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE dummy (x int)")
            conn.close()
            with _PatchedVendor(db_path):
                result = interface.route_to_vendor(
                    "get_index_daily", "000001.SS", "2026-06-15", "2026-06-19"
                )
            self.assertIn("NO_DATA_AVAILABLE", result)
        finally:
            os.unlink(db_path)

    def test_missing_columns_raises_no_market_data_error(self):
        """Missing required columns (e.g. Date, Open) should raise NoMarketDataError."""
        import pandas as pd

        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_index_daily

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with (
                _PatchedVendor(db_path),
                patch(
                    "tradingagents.dataflows.smartmoney_vendor._df_from_sql",
                    return_value=pd.DataFrame({
                        "Date": ["2026-06-19"],
                        "Open": [3085.0],
                        "Low": [3080.0],
                        "Close": [3095.0],
                        "Volume": [2.9e9],
                        # Missing 'High' column
                    }),
                ) as mock_df,
                self.assertRaises(NoMarketDataError) as ctx,
            ):
                get_index_daily("000001.SS", "2026-06-15", "2026-06-19")
            self.assertIn("schema mismatch", str(ctx.exception))
            self.assertIn("missing columns", str(ctx.exception))
            self.assertIn("High", str(ctx.exception))
            mock_df.assert_called_once()
        finally:
            os.unlink(db_path)

    def test_non_numeric_column_raises_no_market_data_error(self):
        """A non-numeric column (e.g. 'Open' as string) should raise NoMarketDataError."""
        import pandas as pd

        from tradingagents.dataflows.errors import NoMarketDataError
        from tradingagents.dataflows.smartmoney_vendor import get_index_daily

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with (
                _PatchedVendor(db_path),
                patch(
                    "tradingagents.dataflows.smartmoney_vendor._df_from_sql",
                    return_value=pd.DataFrame({
                        "Date": ["2026-06-19"],
                        "Open": [3085.0],
                        "High": [3100.0],
                        "Low": [3080.0],
                        "Close": [3095.0],
                        "Volume": ["string_value"],  # Not numeric!
                    }),
                ) as mock_df,
                self.assertRaises(NoMarketDataError) as ctx,
            ):
                get_index_daily("000001.SS", "2026-06-15", "2026-06-19")
            self.assertIn("schema mismatch", str(ctx.exception))
            self.assertIn("is not numeric", str(ctx.exception))
            self.assertIn("Volume", str(ctx.exception))
            mock_df.assert_called_once()
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class GetInstitutionalHoldingsTests(unittest.TestCase):
    """Tests for get_institutional_holdings — empty data + JSON parse edge cases."""

    def test_raises_on_no_data(self):
        """Querying a ticker with no holdings data raises RuntimeError."""
        from tradingagents.dataflows.smartmoney_vendor import (
            get_institutional_holdings,
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_institutional_holdings("999999.SS")
            self.assertIn("institutional-holdings", str(ctx.exception))
            self.assertIn("999999.SS", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_json_decode_error_does_not_crash(self):
        """When type_counts contains invalid JSON, the try/except pass should swallow it."""
        from tradingagents.dataflows.smartmoney_vendor import (
            get_institutional_holdings,
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE institutional_holdings (
                    ts_code TEXT NOT NULL,
                    report_date INTEGER NOT NULL,
                    institution_count INTEGER,
                    type_counts TEXT,
                    PRIMARY KEY (ts_code, report_date)
                );
                INSERT INTO institutional_holdings VALUES ('600519',20260331,1372,'NOT_VALID_JSON');
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_institutional_holdings("600519.SS")
            self.assertIn("机构持股", result)
            self.assertIn("1,372", result)
            self.assertNotIn("NOT_VALID_JSON", result)  # invalid JSON is silently skipped
        finally:
            os.unlink(db_path)

    def test_type_error_on_non_dict_type_counts_does_not_crash(self):
        """When type_counts is a bare integer (not a dict), TypeError is caught silently."""
        from tradingagents.dataflows.smartmoney_vendor import (
            get_institutional_holdings,
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE institutional_holdings (
                    ts_code TEXT NOT NULL,
                    report_date INTEGER NOT NULL,
                    institution_count INTEGER,
                    type_counts TEXT,
                    PRIMARY KEY (ts_code, report_date)
                );
                INSERT INTO institutional_holdings VALUES ('600519',20260331,1372,12345);
            """)
            conn.close()
            with _PatchedVendor(db_path):
                result = get_institutional_holdings("600519.SS")
            self.assertIn("机构持股", result)
            self.assertIn("1,372", result)
        finally:
            os.unlink(db_path)


# ===========================================================================
# get_research_reports
# ===========================================================================


@pytest.mark.unit
class GetResearchReportsTests(unittest.TestCase):
    """Tests for smartmoney_vendor.get_research_reports."""

    def test_returns_data_when_db_has_reports(self):
        """Querying a ticker with research reports returns formatted data."""
        from tradingagents.dataflows.smartmoney_vendor import get_research_reports

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_research_reports("600519.SS")
            self.assertIn("Research Reports", result)
            self.assertIn("quant_core.db", result)
            self.assertIn("中信证券", result)
            self.assertIn("华泰证券", result)
            self.assertIn("买入", result)
            self.assertIn("1800.0", result)
            self.assertIn("贵州茅台深度研究", result)
        finally:
            os.unlink(db_path)

    def test_raises_when_table_missing(self):
        """When the research_report table does not exist, raises RuntimeError."""
        from tradingagents.dataflows.smartmoney_vendor import get_research_reports

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_test_db(db_path)  # No research_report table
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_research_reports("600519.SS")
            self.assertIn("No research reports", str(ctx.exception))
            self.assertIn("600519.SS", str(ctx.exception))
        finally:
            os.unlink(db_path)

    def test_raises_when_no_data_for_ticker(self):
        """Querying a ticker with no research report data raises RuntimeError."""
        from tradingagents.dataflows.smartmoney_vendor import get_research_reports

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError) as ctx:
                get_research_reports("999999.SS")
            self.assertIn("research reports", str(ctx.exception))
            self.assertIn("999999", str(ctx.exception))
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class CurrDateFilteringTests(unittest.TestCase):
    """P1-3: smartmoney functions must not return data after curr_date."""

    def test_get_fund_flow_excludes_future_dates(self):
        from tradingagents.dataflows.smartmoney_vendor import get_fund_flow

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_fund_flow("600519.SS", curr_date="2026-06-18")
            self.assertIn("2026-06-18", result)
            self.assertNotIn("2026-06-19", result)
        finally:
            os.unlink(db_path)

    def test_get_research_reports_excludes_future_dates(self):
        from tradingagents.dataflows.smartmoney_vendor import get_research_reports

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                result = get_research_reports("600519.SS", curr_date="2026-06-12")
            self.assertIn("2026-06-10", result)
            self.assertNotIn("2026-06-15", result)
        finally:
            os.unlink(db_path)

    def test_get_margin_trading_raises_when_all_future(self):
        from tradingagents.dataflows.smartmoney_vendor import get_margin_trading

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path), self.assertRaises(RuntimeError):
                get_margin_trading("600519.SS", curr_date="2026-06-18")
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class HighAlphaVendorTests(unittest.TestCase):
    """Unit tests for new high-alpha quant_data vendor functions."""

    def test_get_chip_distribution(self):
        from tradingagents.dataflows.smartmoney_vendor import get_chip_distribution

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                res = get_chip_distribution("600519.SS")
            self.assertIn("Chip Distribution", res)
            self.assertIn("获利盘比例", res)
            self.assertIn("85.00%", res)
        finally:
            os.unlink(db_path)

    def test_get_historical_valuation(self):
        from tradingagents.dataflows.smartmoney_vendor import get_historical_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                res = get_historical_valuation("600519.SS")
            self.assertIn("Historical Valuation Percentile", res)
            self.assertIn("PE (TTM)", res)
        finally:
            os.unlink(db_path)

    def test_get_institution_survey(self):
        from tradingagents.dataflows.smartmoney_vendor import get_institution_survey

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                res = get_institution_survey("600519.SS")
            self.assertIn("Institutional Intelligence", res)
            self.assertIn("易方达基金", res)
        finally:
            os.unlink(db_path)

    def test_get_earnings_forecast(self):
        from tradingagents.dataflows.smartmoney_vendor import get_earnings_forecast

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                res = get_earnings_forecast("600519.SS")
            self.assertIn("Earnings Forecast", res)
            self.assertIn("预增", res)
        finally:
            os.unlink(db_path)

    def test_get_concept_board(self):
        from tradingagents.dataflows.smartmoney_vendor import get_concept_board

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                res = get_concept_board("600519.SS")
            self.assertIn("Belonging Concept Boards", res)
            self.assertIn("白酒概念", res)
        finally:
            os.unlink(db_path)


@pytest.mark.unit
class RobustnessGuardsTests(unittest.TestCase):
    """Unit tests for v2.0 robustness & anti-hallucination guards."""

    def test_check_stale_warning_helper(self):
        from tradingagents.dataflows.smartmoney_vendor import _check_stale_warning

        # Lagging by 7 days -> trigger warning
        warn = _check_stale_warning("2026-07-15", "2026-07-22", max_days=2)
        self.assertIn("数据时效性预警", warn)
        self.assertIn("滞后 7 天", warn)

        # Lagging by 1 day -> no warning
        no_warn = _check_stale_warning("2026-07-21", "2026-07-22", max_days=2)
        self.assertEqual(no_warn, "")

    def test_chip_distribution_stale_warning(self):
        from tradingagents.dataflows.smartmoney_vendor import get_chip_distribution

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                # 600519 db trade_date is 2026-07-03; query with curr_date=2026-07-10 (7 days lag)
                res = get_chip_distribution("600519.SS", curr_date="2026-07-10")
            self.assertIn("数据时效性预警", res)
        finally:
            os.unlink(db_path)

    def test_cyclical_pe_trap_warning(self):
        from tradingagents.dataflows.smartmoney_vendor import get_historical_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            # Insert a cyclical industry stock with low PE (4.5)
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO stock_list (code, name, industry) VALUES ('600019', '宝钢股份', '钢铁')")
            conn.execute("INSERT INTO historical_valuation (ts_code, trade_date, pe_ttm, pb, dividend_yield) VALUES ('600019', '2026-07-03', 4.5, 0.7, 5.0)")
            conn.commit()
            conn.close()

            with _PatchedVendor(db_path):
                res = get_historical_valuation("600019.SS")
            self.assertIn("周期股景气顶点预警", res)
        finally:
            os.unlink(db_path)

    def test_sub_new_stock_sample_notice(self):
        from tradingagents.dataflows.smartmoney_vendor import get_historical_valuation

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_full_test_db(db_path)
            with _PatchedVendor(db_path):
                # 600519 has only 1 row in mock test db (<120 bars)
                res = get_historical_valuation("600519.SS")
            self.assertIn("次新股/小样本警示", res)
        finally:
            os.unlink(db_path)


def _create_global_assets_db(path):
    """Minimal quant_core.db fixture with a global_assets_bars table."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE global_assets_bars (
            ts_code TEXT, trade_date TEXT, open REAL, high REAL,
            low REAL, close REAL, adj_close REAL, volume REAL,
            data_source TEXT,
            PRIMARY KEY (ts_code, trade_date)
        );
        INSERT INTO global_assets_bars VALUES ('AAPL','2026-06-15',200.0,203.0,199.0,202.0,202.0,5.0e7,'yfinance');
        INSERT INTO global_assets_bars VALUES ('AAPL','2026-06-16',202.0,205.0,201.0,204.0,204.0,4.8e7,'yfinance');
        INSERT INTO global_assets_bars VALUES ('AAPL','2026-06-17',204.0,206.0,203.0,205.5,205.5,4.5e7,'yfinance');
        INSERT INTO global_assets_bars VALUES ('BTC-USD','2026-06-18',64000.0,65500.0,63800.0,65100.0,65100.0,2.1e10,'yfinance');
        INSERT INTO global_assets_bars VALUES ('BTC-USD','2026-06-19',65100.0,66000.0,64900.0,65800.0,65800.0,2.3e10,'yfinance');
    """)
    conn.commit()
    conn.close()


@pytest.mark.unit
class GetGlobalAssetDataTests(unittest.TestCase):
    """Tests for get_global_asset_data (global_assets_bars, quant_db_global vendor)."""

    def setUp(self):
        from tradingagents.dataflows.errors import NoMarketDataError
        self.NoMarketDataError = NoMarketDataError

    def _with_db(self, fn):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_global_assets_db(db_path)
            with _PatchedVendor(db_path):
                fn()
        finally:
            os.unlink(db_path)

    def test_returns_csv_for_fresh_us_ticker(self):
        from tradingagents.dataflows.smartmoney_vendor import get_global_asset_data

        def check():
            result = get_global_asset_data("AAPL", "2026-06-15", "2026-06-17")
            self.assertIn("# Stock data for AAPL", result)
            self.assertIn("global_assets_bars", result)
            self.assertIn("# Total records: 3", result)
            self.assertIn("2026-06-17", result)
            self.assertIn("205.5", result)

        self._with_db(check)

    def test_returns_csv_for_crypto(self):
        from tradingagents.dataflows.smartmoney_vendor import get_global_asset_data

        def check():
            result = get_global_asset_data("BTC-USD", "2026-06-18", "2026-06-19")
            self.assertIn("# Stock data for BTC-USD", result)
            self.assertIn("# Total records: 2", result)
            self.assertIn("65800.0", result)

        self._with_db(check)

    def test_a_share_ticker_rejected(self):
        from tradingagents.dataflows.smartmoney_vendor import get_global_asset_data

        def check():
            with self.assertRaises(self.NoMarketDataError):
                get_global_asset_data("600519.SS", "2026-06-15", "2026-06-17")

        self._with_db(check)

    def test_unknown_symbol_raises(self):
        from tradingagents.dataflows.smartmoney_vendor import get_global_asset_data

        def check():
            with self.assertRaises(self.NoMarketDataError):
                get_global_asset_data("NOTHING", "2026-06-15", "2026-06-17")

        self._with_db(check)

    def test_stale_data_raises_for_online_fallback(self):
        """Latest local row > MAX_OHLCV_STALE_DAYS before end_date → no-data error."""
        from tradingagents.dataflows.smartmoney_vendor import get_global_asset_data

        def check():
            # AAPL's latest row is 2026-06-17; requesting up to 2026-07-20
            # is 33 days stale and must fall back to the online vendor.
            with self.assertRaises(self.NoMarketDataError) as ctx:
                get_global_asset_data("AAPL", "2026-06-15", "2026-07-20")
            self.assertIn("stale", str(ctx.exception))

        self._with_db(check)

    def test_within_staleness_window_accepted(self):
        """A long weekend / holiday gap (≤ MAX_OHLCV_STALE_DAYS) is still served."""
        from tradingagents.dataflows.smartmoney_vendor import get_global_asset_data

        def check():
            result = get_global_asset_data("AAPL", "2026-06-15", "2026-06-25")
            self.assertIn("# Total records: 3", result)

        self._with_db(check)


if __name__ == "__main__":
    unittest.main()

