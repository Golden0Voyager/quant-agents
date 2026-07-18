"""Tests for the deterministic market-data verification snapshot (#830/#881)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import tradingagents.dataflows.market_data_validator as validator


def _sample_ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2026-04-01", "2026-05-20")
    closes = [100 + i for i in range(len(dates))]
    return pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i for i in range(len(dates))],
    })


@pytest.mark.unit
class TestVerifiedSnapshot:
    def test_excludes_future_rows(self, monkeypatch):
        data = pd.concat([
            _sample_ohlcv(),
            pd.DataFrame({"Date": [pd.Timestamp("2026-06-01")], "Open": [999.0],
                          "High": [999.0], "Low": [999.0], "Close": [999.0], "Volume": [999]}),
        ], ignore_index=True)
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, refresh=False: data)

        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Verified market data snapshot for COF" in snap
        assert "Requested analysis date: 2026-05-13" in snap
        assert "Latest trading row used: 2026-05-13" in snap
        assert "999.00" not in snap          # future row excluded
        assert "boll_lb" in snap             # indicators present

    def test_uses_previous_trading_day_when_date_is_weekend(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, refresh=False: _sample_ohlcv())
        # 2026-05-16 is a Saturday; latest row should be Fri 2026-05-15
        snap = validator.build_verified_market_snapshot("COF", "2026-05-16")
        assert "Latest trading row used: 2026-05-15" in snap
        assert "Recent verified closes" in snap

    def test_raises_when_no_rows_on_or_before_date(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, refresh=False: _sample_ohlcv())
        with pytest.raises(ValueError):
            validator.build_verified_market_snapshot("COF", "2020-01-01")

    def test_raises_on_empty_data(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, refresh=False: pd.DataFrame())
        with pytest.raises(ValueError):
            validator.build_verified_market_snapshot("COF", "2026-05-13")

    def test_look_back_window_capped_at_30(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, refresh=False: _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-20", look_back_days=999)
        # last-N closes table has at most 30 data rows
        close_rows = [ln for ln in snap.splitlines() if ln.startswith("| 2026-")]
        assert 0 < len(close_rows) <= 30

    def test_refresh_passed_to_load_ohlcv(self, monkeypatch):
        calls = []

        def mock_load(s, d, refresh=False):
            calls.append(refresh)
            return _sample_ohlcv()

        monkeypatch.setattr(validator, "load_ohlcv", mock_load)
        validator.build_verified_market_snapshot("COF", "2026-05-20", refresh=True)
        assert calls == [True]


@pytest.mark.unit
class TestTool:
    def test_tool_delegates_to_builder(self, monkeypatch):
        from tradingagents.agents.utils.market_data_validation_tools import (
            get_verified_market_snapshot,
        )
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, refresh=False: _sample_ohlcv())
        out = get_verified_market_snapshot.invoke(
            {"symbol": "COF", "curr_date": "2026-05-20"}
        )
        assert "Verified market data snapshot for COF" in out


# =========================================================================
# _fmt edge cases imported from test_final_push.py / test_llm_and_minor.py
# =========================================================================


@pytest.mark.unit
class MktDataValidatorFmtTests(unittest.TestCase):
    """_fmt edge cases (None, NaN, NA, bool, Timestamp, int, float)."""

    def test_fmt_none_returns_na(self):
        self.assertEqual(validator._fmt(None), "N/A")

    def test_fmt_nan_returns_na(self):
        self.assertEqual(validator._fmt(float("nan")), "N/A")

    def test_fmt_pd_na_returns_na(self):
        self.assertEqual(validator._fmt(pd.NA), "N/A")

    def test_fmt_bool_returns_str(self):
        self.assertEqual(validator._fmt(True), "True")
        self.assertEqual(validator._fmt(False), "False")

    def test_fmt_timestamp_returns_date_str(self):
        ts = pd.Timestamp("2026-05-20")
        self.assertEqual(validator._fmt(ts), "2026-05-20")

    def test_fmt_int_returns_str(self):
        self.assertEqual(validator._fmt(42), "42")

    def test_fmt_float_returns_two_decimals(self):
        self.assertEqual(validator._fmt(123.456), "123.46")


@pytest.mark.unit
class TestFormatEdgeCases(unittest.TestCase):
    """Cover _fmt with bool, int, float, Timestamp values."""

    def test_fmt_none(self):
        self.assertEqual(validator._fmt(None), "N/A")

    def test_fmt_na(self):
        self.assertEqual(validator._fmt(pd.NA), "N/A")

    def test_fmt_timestamp(self):
        ts = pd.Timestamp("2026-06-15")
        self.assertEqual(validator._fmt(ts), "2026-06-15")

    def test_fmt_bool(self):
        self.assertEqual(validator._fmt(True), "True")
        self.assertEqual(validator._fmt(False), "False")

    def test_fmt_int(self):
        self.assertEqual(validator._fmt(42), "42")
        self.assertEqual(validator._fmt(0), "0")

    def test_fmt_float(self):
        self.assertEqual(validator._fmt(3.14159), "3.14")
        self.assertEqual(validator._fmt(100.0), "100.00")

    def test_fmt_float_nan(self):
        self.assertEqual(validator._fmt(float("nan")), "N/A")


# =========================================================================
# Indicator exception tests imported from test_final_push.py
# =========================================================================


@pytest.mark.unit
class MktDataValidatorIndicatorExceptionTests(unittest.TestCase):
    """Indicator calculation exception handling."""

    def test_indicator_exception_returns_na_with_exception_name(self):
        df = pd.DataFrame({
            "Date": pd.to_datetime(["2026-05-19", "2026-05-20"]),
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [10000, 11000],
        })

        mock_stock_df = MagicMock()
        mock_iloc = MagicMock()
        mock_last_row = MagicMock()

        def _raise_on_rsi(key):
            if key == "rsi":
                raise RuntimeError("indicator calculation failed")
            return "some_value"

        mock_last_row.__getitem__.side_effect = _raise_on_rsi
        mock_iloc.__getitem__.return_value = mock_last_row
        mock_stock_df.iloc = mock_iloc
        mock_stock_df.__getitem__.return_value = None

        with patch("tradingagents.dataflows.market_data_validator.load_ohlcv", return_value=df), \
             patch("tradingagents.dataflows.market_data_validator.wrap", return_value=mock_stock_df):
            result = validator.build_verified_market_snapshot("AAPL", "2026-05-20", indicators=("rsi",))
        self.assertIn("N/A (RuntimeError)", result)

    def test_indicator_value_present(self):
        df = pd.DataFrame({
            "Date": pd.to_datetime(["2026-05-19", "2026-05-20"]),
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [10000, 11000],
        })

        with (
            patch("tradingagents.dataflows.market_data_validator.load_ohlcv", return_value=df),
            patch("tradingagents.dataflows.market_data_validator.wrap") as mock_wrap,
        ):
            mock_stock_df = df.copy()
            mock_stock_df["rsi"] = [55.0, 60.0]
            mock_wrap.return_value = mock_stock_df

            result = validator.build_verified_market_snapshot("AAPL", "2026-05-20", indicators=("rsi",))
        self.assertIn("| rsi | 60.00 |", result)


@pytest.mark.unit
class TestVerifiedFundamentalsSnapshot:
    """build_verified_fundamentals_snapshot extracts key fundamentals from vendor text."""

    SMARTMONEY_FUNDAMENTALS = """\
# Fundamentals for 600519.SS (Kweichow Moutai) as of 2026-06-20
# Source: quant_core.db (local SQLite)

- 股票简称: Kweichow Moutai
- 行业: 酿酒行业

- PE(TTM): 25.50
- PB: 8.20
- PS(TTM): 12.30
- 股息率: 1.50%
- 总市值: 21000.00 亿 (≈2100.00 billion CNY)

## 盈利能力
- ROE: 18.50%
- ROA: 12.30%
- 毛利率: 91.50%
- 净利率: 35.20%

## 成长性
- 营收同比增长: 15.20%
- 净利润同比增长: 18.30%
- EPS同比增长: 16.50%
- PEG: 1.40

## 偿债能力
- 资产负债率: 22.50%
"""

    AKSHARE_FUNDAMENTALS = """\
# Fundamentals for 600519.SS as of 2026-06-20
# Source: akshare (Eastmoney)

- 股票简称: 贵州茅台
- 行业: 酿酒行业
- 上市时间: 2001-08-27

## 业绩报表 (报告期 2026-03-31)
- 营收同比增长(YoY): 15.20%
- 净利润同比增长(YoY): 18.30%
- 销售毛利率: 91.50%
- 净资产收益率: 18.50%
- EPS: 12.50
- 每股经营现金流量: 10.20
"""

    def test_parses_pe_pb_from_smartmoney_output(self, monkeypatch):
        from tradingagents.dataflows.interface import VendorRouteResult

        monkeypatch.setattr(
            validator,
            "route_to_vendor_with_source",
            lambda method, *args, **kwargs: VendorRouteResult(
                self.SMARTMONEY_FUNDAMENTALS, "smartmoney_db"
            ),
        )
        snap = validator.build_verified_fundamentals_snapshot("600519.SS", "2026-06-20")
        assert snap["pe_ttm"] == 25.50
        assert snap["pb"] == 8.20
        assert snap["market_cap_billion_cny"] == 2100.00
        assert snap["source"] == "smartmoney_db"
        assert snap["as_of"] == "2026-06-20"

    def test_parses_eps_and_roe_from_akshare_output(self, monkeypatch):
        from tradingagents.dataflows.interface import VendorRouteResult

        monkeypatch.setattr(
            validator,
            "route_to_vendor_with_source",
            lambda method, *args, **kwargs: VendorRouteResult(
                self.AKSHARE_FUNDAMENTALS, "akshare"
            ),
        )
        snap = validator.build_verified_fundamentals_snapshot("600519.SS", "2026-06-20")
        assert snap["eps"] == 12.50
        assert snap["roe"] == 18.50

    def test_returns_empty_dict_when_no_data(self, monkeypatch):
        from tradingagents.dataflows.interface import VendorRouteResult

        monkeypatch.setattr(
            validator,
            "route_to_vendor_with_source",
            lambda method, *args, **kwargs: VendorRouteResult(
                "NO_DATA_AVAILABLE: No usable market data for '600519.SS' "
                "from any configured vendor. Do not estimate or fabricate values.",
                None,
            ),
        )
        snap = validator.build_verified_fundamentals_snapshot("600519.SS", "2026-06-20")
        assert snap.get("symbol") == "600519.SS"
        assert "error" in snap

    def test_render_fundamentals_snapshot_includes_source(self, monkeypatch):
        from tradingagents.dataflows.interface import VendorRouteResult

        monkeypatch.setattr(
            validator,
            "route_to_vendor_with_source",
            lambda method, *args, **kwargs: VendorRouteResult(
                self.SMARTMONEY_FUNDAMENTALS, "smartmoney_db"
            ),
        )
        snap = validator.build_verified_fundamentals_snapshot("600519.SS", "2026-06-20")
        md = validator.render_fundamentals_snapshot(snap)
        assert "PE(TTM)" in md
        assert "PB" in md
        assert "smartmoney_db" in md
        assert "2026-06-20" in md

    def test_preserves_signed_fundamental_values(self):
        text = """
- ROE: -434.20%
- 净利润同比增长: -18.30%
- EPS: -1.25
"""
        metrics = validator._extract_fundamental_metrics(text)
        assert metrics["roe"] == -434.20
        assert metrics["profit_growth"] == -18.30
        assert metrics["eps"] == -1.25

    def test_metric_free_snapshot_is_unavailable(self, monkeypatch):
        from tradingagents.dataflows.interface import VendorRouteResult

        monkeypatch.setattr(
            validator,
            "route_to_vendor_with_source",
            lambda method, *args, **kwargs: VendorRouteResult(
                "# Fundamentals\n- Company: Example Corp", "yfinance"
            ),
        )
        snap = validator.build_verified_fundamentals_snapshot("AAPL", "2026-06-20")
        md = validator.render_fundamentals_snapshot(snap)
        assert "error" in snap
        assert "Verified fundamentals snapshot" not in md
        assert "unavailable" in md.lower()
