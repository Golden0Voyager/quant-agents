"""Tests for core_stock_tools module.

The ``@tool``-decorated function ``get_stock_data`` now reads from the same
``load_ohlcv`` cache used by the verified market snapshot so that analysts and
downstream agents see consistent prices.
"""

from unittest.mock import patch

import pandas as pd
import pytest


def _make_mock_load_ohlcv():
    dates = pd.bdate_range("2026-06-01", "2026-06-30")
    return pd.DataFrame({
        "Date": dates,
        "Open": [100.0 + i for i in range(len(dates))],
        "High": [101.0 + i for i in range(len(dates))],
        "Low": [99.0 + i for i in range(len(dates))],
        "Close": [100.5 + i for i in range(len(dates))],
        "Volume": [1_000_000 + i for i in range(len(dates))],
    })


@pytest.mark.unit
class TestGetStockData:
    def test_uses_load_ohlcv_and_filters_date_range(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        mock_df = _make_mock_load_ohlcv()
        with patch(
            "tradingagents.agents.utils.core_stock_tools.load_ohlcv",
            return_value=mock_df,
        ) as mock_load:
            result = get_stock_data.invoke({
                "symbol": "AAPL",
                "start_date": "2026-06-10",
                "end_date": "2026-06-15",
            })

        mock_load.assert_called_once_with("AAPL", "2026-06-15")
        assert "Date,Open,High,Low,Close,Volume" in result
        assert "2026-06-10" in result
        assert "2026-06-15" in result
        assert "2026-06-09" not in result
        assert "2026-06-16" not in result

    def test_different_symbol_and_dates(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        mock_df = _make_mock_load_ohlcv()
        with patch(
            "tradingagents.agents.utils.core_stock_tools.load_ohlcv",
            return_value=mock_df,
        ) as mock_load:
            get_stock_data.invoke({
                "symbol": "TSLA",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
            })

        mock_load.assert_called_once_with("TSLA", "2026-06-30")

    def test_error_propagates(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        with (
            patch(
                "tradingagents.agents.utils.core_stock_tools.load_ohlcv",
                side_effect=RuntimeError("vendor error"),
            ),
            pytest.raises(RuntimeError, match="vendor error"),
        ):
            get_stock_data.invoke({
                "symbol": "AAPL",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
            })

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.core_stock_tools import get_stock_data

        assert hasattr(get_stock_data, "name")
        assert hasattr(get_stock_data, "args")
        assert hasattr(get_stock_data, "invoke")
        assert get_stock_data.name == "get_stock_data"


@pytest.mark.unit
class TestGetChipDistribution:
    def test_returns_chip_distribution(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_chip_distribution",
            return_value="chip data: 70% concentrated at 85",
        ) as mock_inner:
            result = get_chip_distribution.invoke({
                "symbol": "600519.SS",
            })
        mock_inner.assert_called_once_with("600519.SS", None)
        assert "chip data" in result

    def test_returns_chip_distribution_with_curr_date(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_chip_distribution",
            return_value="chip data with date",
        ) as mock_inner:
            result = get_chip_distribution.invoke({
                "symbol": "000001.SZ",
                "curr_date": "2026-07-03",
            })
        mock_inner.assert_called_once_with("000001.SZ", "2026-07-03")
        assert "chip data" in result

    def test_error_returns_no_data_message(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        with patch(
            "tradingagents.dataflows.smartmoney_vendor.get_chip_distribution",
            side_effect=ValueError("API error"),
        ):
            result = get_chip_distribution.invoke({
                "symbol": "600519.SS",
            })
        assert "NO_DATA_AVAILABLE" in result
        assert "600519.SS" in result
        assert "API error" in result

    def test_is_structured_tool(self):
        from tradingagents.agents.utils.core_stock_tools import get_chip_distribution

        assert hasattr(get_chip_distribution, "name")
        assert hasattr(get_chip_distribution, "args")
        assert hasattr(get_chip_distribution, "invoke")
        assert get_chip_distribution.name == "get_chip_distribution"
