from unittest.mock import patch

import pandas as pd
import pytest


@pytest.mark.unit
class TestAkshareStockData:
    def test_basic_ohlcv_returned_as_csv(self):
        from tradingagents.dataflows import akshare_vendor

        fake_df = pd.DataFrame({
            "日期": ["2026-05-10", "2026-05-13", "2026-05-14"],
            "开盘": [1670.0, 1675.5, 1682.0],
            "收盘": [1680.5, 1681.0, 1683.5],
            "最高": [1690.0, 1685.0, 1690.0],
            "最低": [1665.0, 1670.0, 1678.0],
            "成交量": [120000, 135000, 110000],
        })
        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = fake_df
            result = akshare_vendor.get_stock_data(
                "600519.SS", "2026-05-10", "2026-05-14"
            )

        mock_ak.stock_zh_a_hist.assert_called_once()
        call_kwargs = mock_ak.stock_zh_a_hist.call_args.kwargs
        assert call_kwargs["symbol"] == "600519"
        assert call_kwargs["period"] == "daily"
        assert call_kwargs["adjust"] == "qfq"
        assert "Stock data for 600519.SS" in result
        assert "1680.5" in result

    def test_empty_df_returns_no_data_message(self):
        from tradingagents.dataflows import akshare_vendor

        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
            result = akshare_vendor.get_stock_data(
                "600519.SS", "2026-05-10", "2026-05-14"
            )
        assert "No data" in result


@pytest.mark.unit
class TestAkshareIncomeStatement:
    def test_returns_latest_period_summary(self):
        from tradingagents.dataflows import akshare_vendor

        fake_df = pd.DataFrame([{
            "REPORT_DATE": "2026-03-31",
            "TOTAL_OPERATE_INCOME": 41_580_000_000.0,
            "OPERATE_INCOME": 41_580_000_000.0,
            "OPERATE_COST": 4_158_000_000.0,
            "OPERATE_PROFIT": 26_530_000_000.0,
            "PARENT_NETPROFIT": 19_840_000_000.0,
            "BASIC_EPS": 15.78,
        }])
        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_profit_sheet_by_report_em.return_value = fake_df
            result = akshare_vendor.get_income_statement("600519.SS")

        call_args = mock_ak.stock_profit_sheet_by_report_em.call_args
        assert call_args.kwargs["symbol"] == "SH600519"
        assert "Income Statement for 600519.SS" in result
        assert "415.80亿" in result
        assert "198.40亿" in result
        assert "15.7800" in result or "15.78" in result
        assert "akshare" in result.lower()

    def test_empty_returns_warning(self):
        from tradingagents.dataflows import akshare_vendor

        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_profit_sheet_by_report_em.return_value = pd.DataFrame()
            result = akshare_vendor.get_income_statement("600519.SS")
        assert "No income statement" in result


@pytest.mark.unit
class TestAkshareBalanceSheet:
    def test_basic(self):
        from tradingagents.dataflows import akshare_vendor

        fake_df = pd.DataFrame([{
            "REPORT_DATE": "2026-03-31",
            "TOTAL_ASSETS": 554_600_000_000.0,
            "TOTAL_CURRENT_ASSETS": 442_000_000_000.0,
            "MONETARYFUNDS": 178_000_000_000.0,
            "INVENTORY": 49_000_000_000.0,
            "TOTAL_LIABILITIES": 102_590_000_000.0,
            "TOTAL_EQUITY": 452_010_000_000.0,
        }])
        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_balance_sheet_by_report_em.return_value = fake_df
            result = akshare_vendor.get_balance_sheet("600519.SS")

        assert mock_ak.stock_balance_sheet_by_report_em.call_args.kwargs["symbol"] == "SH600519"
        assert "5546.00亿" in result
        assert "1780.00亿" in result
        assert "Balance Sheet" in result

    def test_empty_returns_warning(self):
        from tradingagents.dataflows import akshare_vendor

        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_balance_sheet_by_report_em.return_value = pd.DataFrame()
            result = akshare_vendor.get_balance_sheet("600519.SS")
        assert "No balance sheet" in result


@pytest.mark.unit
class TestAkshareCashflow:
    def test_basic(self):
        from tradingagents.dataflows import akshare_vendor

        fake_df = pd.DataFrame([{
            "REPORT_DATE": "2026-03-31",
            "NETCASH_OPERATE": 23_000_000_000.0,
            "NETCASH_INVEST": -5_000_000_000.0,
            "NETCASH_FINANCE": -8_000_000_000.0,
            "CCE_ADD": 10_000_000_000.0,
        }])
        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = fake_df
            result = akshare_vendor.get_cashflow("600519.SS")
        assert "230.00亿" in result
        assert "-50.00亿" in result
        assert "Cash Flow" in result

    def test_empty_returns_warning(self):
        from tradingagents.dataflows import akshare_vendor

        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_cash_flow_sheet_by_report_em.return_value = pd.DataFrame()
            result = akshare_vendor.get_cashflow("600519.SS")
        assert "No cash flow" in result


@pytest.mark.unit
class TestAkshareFundamentals:
    def test_combines_info_and_yjbb(self):
        from tradingagents.dataflows import akshare_vendor

        info_df = pd.DataFrame({
            "item": ["股票简称", "行业", "总市值", "流通市值"],
            "value": ["贵州茅台", "白酒", 2_108_000_000_000.0, 2_108_000_000_000.0],
        })
        yjbb_df = pd.DataFrame([{
            "股票代码": "600519",
            "营业总收入-同比增长": 12.5,
            "净利润-同比增长": 15.2,
            "销售毛利率": 91.5,
            "净资产收益率": 18.0,
            "每股收益": 15.78,
        }])
        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_individual_info_em.return_value = info_df
            mock_ak.stock_yjbb_em.return_value = yjbb_df
            result = akshare_vendor.get_fundamentals("600519.SS", "2026-05-14")

        assert "贵州茅台" in result
        assert "白酒" in result
        assert "91.50%" in result
        assert "ROE" in result or "净资产收益率" in result

    def test_empty_returns_warning(self):
        from tradingagents.dataflows import akshare_vendor

        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_individual_info_em.return_value = pd.DataFrame()
            mock_ak.stock_yjbb_em.return_value = pd.DataFrame()
            result = akshare_vendor.get_fundamentals("600519.SS", "2026-05-14")
        assert "No fundamentals" in result


@pytest.mark.unit
class TestAkshareIndicators:
    def test_uses_akshare_kline_and_stockstats(self):
        """get_indicators should pull K-line from akshare and run stockstats."""
        from tradingagents.dataflows import akshare_vendor

        fake_df = pd.DataFrame({
            "日期": pd.date_range("2026-04-01", periods=30, freq="D"),
            "开盘": [1500.0 + i for i in range(30)],
            "收盘": [1510.0 + i for i in range(30)],
            "最高": [1520.0 + i for i in range(30)],
            "最低": [1490.0 + i for i in range(30)],
            "成交量": [100_000 + i * 1000 for i in range(30)],
        })
        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_zh_a_hist.return_value = fake_df
            result = akshare_vendor.get_indicators(
                "600519.SS", "rsi_14", "2026-04-30", look_back_days=20
            )
        assert "rsi_14" in result.lower() or "RSI" in result
        assert "600519.SS" in result
        assert mock_ak.stock_zh_a_hist.call_args.kwargs["symbol"] == "600519"

    def test_propagates_network_exceptions(self):
        """Network errors should bubble up so route_to_vendor can fall back to yfinance."""
        from tradingagents.dataflows import akshare_vendor

        with patch("tradingagents.dataflows.akshare_vendor.ak") as mock_ak:
            mock_ak.stock_zh_a_hist.side_effect = ConnectionError("network down")
            with pytest.raises(ConnectionError, match="network down"):
                akshare_vendor.get_indicators(
                    "600519.SS", "rsi_14", "2026-04-30", look_back_days=20
                )
