"""Tests for alpha_vantage_indicator module with mocked API calls."""

from unittest.mock import patch

import pytest

from tradingagents.dataflows.alpha_vantage_common import (
    AlphaVantageNotConfiguredError,
)
from tradingagents.dataflows.errors import NoMarketDataError


def _indicator_csv(header_col: str = "SMA") -> str:
    return (
        f"time,{header_col}\n"
        "2026-05-01,100.0\n"
        "2026-05-10,102.5\n"
        "2026-05-20,105.0\n"
    )


@pytest.mark.unit
class TestGetIndicatorSMATests:
    """Tests for SMA indicators (close_50_sma, close_200_sma)."""

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_sma_50(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("SMA")
        result = get_indicator("AAPL", "close_50_sma", "2026-05-20", 30)
        assert "CLOSE_50_SMA values" in result
        assert "100.0" in result
        assert "105.0" in result
        mock_api.assert_called_once_with("SMA", {
            "symbol": "AAPL", "interval": "daily", "time_period": "50",
            "series_type": "close", "datatype": "csv",
        })

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_sma_200(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("SMA")
        result = get_indicator("AAPL", "close_200_sma", "2026-05-20", 30)
        assert "CLOSE_200_SMA values" in result
        mock_api.assert_called_once()
        assert mock_api.call_args[0][1]["time_period"] == "200"


@pytest.mark.unit
class TestGetIndicatorEMATests:
    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_ema_10(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("EMA")
        result = get_indicator("AAPL", "close_10_ema", "2026-05-20", 30)
        assert "CLOSE_10_EMA values" in result
        mock_api.assert_called_once_with("EMA", {
            "symbol": "AAPL", "interval": "daily", "time_period": "10",
            "series_type": "close", "datatype": "csv",
        })


@pytest.mark.unit
class TestGetIndicatorMACDTests:
    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_macd(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("MACD")
        result = get_indicator("AAPL", "macd", "2026-05-20", 30)
        assert "MACD values" in result
        mock_api.assert_called_once()
        assert mock_api.call_args[0][0] == "MACD"

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_macds(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("MACD_Signal")
        result = get_indicator("AAPL", "macds", "2026-05-20", 30)
        assert "MACDS values" in result

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_macdh(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("MACD_Hist")
        result = get_indicator("AAPL", "macdh", "2026-05-20", 30)
        assert "MACDH values" in result


@pytest.mark.unit
class TestGetIndicatorRSITests:
    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_rsi(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("RSI")
        result = get_indicator("AAPL", "rsi", "2026-05-20", 30)
        assert "RSI values" in result
        mock_api.assert_called_once()
        # time_period defaults to 14 (function parameter), not look_back_days
        assert mock_api.call_args[0][1]["time_period"] == "14"


@pytest.mark.unit
class TestGetIndicatorBollingerTests:
    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_boll(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("Real Middle Band")
        result = get_indicator("AAPL", "boll", "2026-05-20", 30)
        assert "BOLL values" in result
        mock_api.assert_called_once_with("BBANDS", {
            "symbol": "AAPL", "interval": "daily", "time_period": "20",
            "series_type": "close", "datatype": "csv",
        })

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_boll_ub(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("Real Upper Band")
        result = get_indicator("AAPL", "boll_ub", "2026-05-20", 30)
        assert "BOLL_UB values" in result

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_boll_lb(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("Real Lower Band")
        result = get_indicator("AAPL", "boll_lb", "2026-05-20", 30)
        assert "BOLL_LB values" in result


@pytest.mark.unit
class TestGetIndicatorATRTests:
    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_get_atr(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = _indicator_csv("ATR")
        result = get_indicator("AAPL", "atr", "2026-05-20", 30)
        assert "ATR values" in result
        # time_period defaults to 14 (function parameter), not look_back_days
        mock_api.assert_called_once_with("ATR", {
            "symbol": "AAPL", "interval": "daily", "time_period": "14",
            "datatype": "csv",
        })


@pytest.mark.unit
class TestGetIndicatorVWMABranch:
    def test_vwma_returns_descriptive_message(self):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        result = get_indicator("AAPL", "vwma", "2026-05-20", 30)
        assert "VWMA" in result
        assert "Volume Weighted Moving Average" in result

    def test_unsupported_indicator_raises(self):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        with pytest.raises(ValueError, match="not supported"):
            get_indicator("AAPL", "nonexistent", "2026-05-20", 30)


@pytest.mark.unit
class TestGetIndicatorEdgeCases:
    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_empty_data(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = ""
        with pytest.raises(NoMarketDataError, match="No data returned"):
            get_indicator("AAPL", "rsi", "2026-05-20", 30)

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_missing_time_column(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = "date,RSI\n2026-05-01,50.0"
        with pytest.raises(NoMarketDataError, match="'time' column not found"):
            get_indicator("AAPL", "rsi", "2026-05-20", 30)

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_no_data_in_date_range(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = "time,RSI\n2025-01-01,50.0"
        with pytest.raises(NoMarketDataError, match="No data available"):
            get_indicator("AAPL", "rsi", "2026-05-20", 30)

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_general_exception_raises_no_market_data(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.side_effect = RuntimeError("API failure")
        with pytest.raises(NoMarketDataError, match="API failure"):
            get_indicator("AAPL", "rsi", "2026-05-20", 30)

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_not_configured_propagates(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.side_effect = AlphaVantageNotConfiguredError("no key")
        with pytest.raises(AlphaVantageNotConfiguredError):
            get_indicator("AAPL", "rsi", "2026-05-20", 30)

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_bad_value_column(self, mock_api):
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = "time,WrongCol\n2026-05-01,100.0"
        with pytest.raises(NoMarketDataError, match="Column 'SMA' not found"):
            get_indicator("AAPL", "close_50_sma", "2026-05-20", 30)

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_blank_lines_skipped(self, mock_api):
        """Blank/empty lines in CSV are skipped (covers continue at line 174)."""
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = (
            "time,RSI\n"
            "\n"  # blank line
            "2026-05-10,42.5\n"
            "   \n"  # whitespace-only line
            "2026-05-20,55.0\n"
        )
        result = get_indicator("AAPL", "rsi", "2026-05-20", 30)
        assert "42.5" in result
        assert "55.0" in result

    @patch("tradingagents.dataflows.alpha_vantage_indicator._make_api_request")
    def test_bad_date_line_skipped(self, mock_api):
        """Lines with unparseable dates are skipped (covers except at lines 186-187)."""
        from tradingagents.dataflows.alpha_vantage_indicator import get_indicator
        mock_api.return_value = (
            "time,RSI\n"
            "not-a-date,99.9\n"  # bad date format -> ValueError
            "2026-05-10,42.5\n"
            "2026-05-20,55.0\n"
        )
        result = get_indicator("AAPL", "rsi", "2026-05-20", 30)
        assert "not-a-date" not in result  # skipped
        assert "42.5" in result
        assert "55.0" in result


