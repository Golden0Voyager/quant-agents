"""Tests for portfolio/validators module.

Pure functions for portfolio data validation and normalization:
``_parse_number``, ``normalize_ticker``, ``validate_holding``,
``deduplicate_holdings``.
"""

import pytest

from tradingagents.portfolio.models import Holding
from tradingagents.portfolio.validators import (
    _parse_number,
    canonical_ticker,
    deduplicate_holdings,
    normalize_ticker,
    ticker_matches,
    validate_holding,
)

# ===================================================================
# _parse_number
# ===================================================================


@pytest.mark.unit
class TestParseNumber:
    def test_parses_int(self):
        assert _parse_number(42) == 42.0

    def test_parses_float(self):
        assert _parse_number(3.14) == 3.14

    def test_parses_simple_string(self):
        assert _parse_number("123.45") == 123.45

    def test_strips_thousand_separators(self):
        assert _parse_number("1,234,567.89") == 1_234_567.89

    def test_strips_chinese_thousand_separator(self):
        assert _parse_number("1，234") == 1234.0

    def test_strips_dollar_sign(self):
        assert _parse_number("$99.99") == 99.99

    def test_strips_yen_sign(self):
        assert _parse_number("¥1500") == 1500.0

    def test_raises_on_non_numeric_string(self):
        with pytest.raises(ValueError):
            _parse_number("not a number")

    def test_raises_on_invalid_type(self):
        with pytest.raises(ValueError, match="Cannot parse number"):
            _parse_number(None)


# ===================================================================
# normalize_ticker
# ===================================================================


@pytest.mark.unit
class TestNormalizeTicker:
    def test_shanghai_6xxxxx(self):
        assert normalize_ticker("600519") == "600519.SS"

    def test_shenzhen_0xxxxx(self):
        assert normalize_ticker("000001") == "000001.SZ"

    def test_shenzhen_3xxxxx(self):
        assert normalize_ticker("300750") == "300750.SZ"

    def test_us_ticker_passed_through(self):
        assert normalize_ticker("AAPL") == "AAPL"

    def test_beijing_exchange_ticker(self):
        assert normalize_ticker("830946") == "830946.BJ"

    def test_etf_tickers(self):
        assert normalize_ticker("159888") == "159888.SZ"
        assert normalize_ticker("510300") == "510300.SS"

    def test_hk_ticker_passed_through(self):
        assert normalize_ticker("HK1810") == "HK1810"

    def test_empty_string_returns_none(self):
        assert normalize_ticker("") is None

    def test_dash_returns_none(self):
        assert normalize_ticker("-") is None

    def test_em_dash_returns_none(self):
        assert normalize_ticker("—") is None

    def test_chinese_header_returns_none(self):
        assert normalize_ticker("合计") is None

    def test_available_cash_returns_none(self):
        assert normalize_ticker("可用现金") is None

    def test_na_returns_none(self):
        assert normalize_ticker("N/A") is None

    def test_lowercase_na_returns_none(self):
        assert normalize_ticker("n/a") is None


# ===================================================================
# canonical_ticker & ticker_matches
# ===================================================================


@pytest.mark.unit
class TestCanonicalTicker:
    def test_bare_ashare_code(self):
        assert canonical_ticker("000603") == "000603.SZ"
        assert canonical_ticker("603893") == "603893.SS"
        assert canonical_ticker("301031") == "301031.SZ"

    def test_hk_formats(self):
        assert canonical_ticker("HK1810") == "1810.HK"
        assert canonical_ticker("1810.HK") == "1810.HK"
        assert canonical_ticker("01810.HK") == "1810.HK"

    def test_already_qualified_ashare(self):
        assert canonical_ticker("000603.SZ") == "000603.SZ"
        assert canonical_ticker("603893.SS") == "603893.SS"

    def test_us_and_other_tickers(self):
        assert canonical_ticker("AAPL") == "AAPL"
        assert canonical_ticker("TSLA") == "TSLA"


@pytest.mark.unit
class TestTickerMatches:
    def test_exact_matches(self):
        assert ticker_matches("AAPL", "AAPL") is True
        assert ticker_matches("000603.SZ", "000603.SZ") is True
        assert ticker_matches("1810.HK", "1810.HK") is True

    def test_case_insensitive(self):
        assert ticker_matches("aapl", "AAPL") is True
        assert ticker_matches("000603.sz", "000603.SZ") is True

    def test_bare_vs_suffixed_ashare(self):
        assert ticker_matches("000603", "000603.SZ") is True
        assert ticker_matches("000603.SZ", "000603") is True
        assert ticker_matches("603893", "603893.SS") is True
        assert ticker_matches("301031", "301031.SZ") is True

    def test_hk_cross_matching(self):
        assert ticker_matches("1810.HK", "HK1810") is True
        assert ticker_matches("HK1810", "1810.HK") is True
        assert ticker_matches("01810.HK", "HK1810") is True

    def test_mismatched_tickers(self):
        assert ticker_matches("000603", "000604") is False
        assert ticker_matches("000603.SZ", "603893.SS") is False
        assert ticker_matches("AAPL", "MSFT") is False
        assert ticker_matches("000603.SZ", "000603.SS") is False
        assert ticker_matches("", "000603.SZ") is False
        assert ticker_matches("000603.SZ", "") is False


# ===================================================================
# validate_holding
# ===================================================================


@pytest.mark.unit
class TestValidateHolding:
    def test_valid_holding(self):
        h = Holding(ticker="AAPL", shares=100, avg_cost=150.0, market_price=155.0)
        result = validate_holding(h)
        assert result is not None
        assert result.ticker == "AAPL"

    def test_empty_ticker(self):
        h = Holding(ticker="", shares=100, avg_cost=150.0, market_price=155.0)
        assert validate_holding(h) is None

    def test_zero_shares(self):
        h = Holding(ticker="AAPL", shares=0, avg_cost=150.0, market_price=155.0)
        assert validate_holding(h) is None

    def test_negative_shares(self):
        h = Holding(ticker="AAPL", shares=-10, avg_cost=150.0, market_price=155.0)
        assert validate_holding(h) is None

    def test_negative_avg_cost(self):
        h = Holding(ticker="AAPL", shares=100, avg_cost=-1.0, market_price=155.0)
        assert validate_holding(h) is None

    def test_zero_avg_cost(self):
        """Zero avg_cost is valid (free shares from spin-offs)."""
        h = Holding(ticker="AAPL", shares=100, avg_cost=0.0, market_price=155.0)
        result = validate_holding(h)
        assert result is not None


# ===================================================================
# deduplicate_holdings
# ===================================================================


@pytest.mark.unit
class TestDeduplicateHoldings:
    def test_deduplicates_by_ticker_keeps_last(self):
        h1 = Holding(ticker="AAPL", shares=100, avg_cost=150.0, market_price=155.0)
        h2 = Holding(ticker="AAPL", shares=200, avg_cost=160.0, market_price=165.0)
        result = deduplicate_holdings([h1, h2])
        assert len(result) == 1
        assert result["AAPL"].shares == 200  # last wins

    def test_preserves_different_tickers(self):
        h1 = Holding(ticker="AAPL", shares=100, avg_cost=150.0, market_price=155.0)
        h2 = Holding(ticker="MSFT", shares=50, avg_cost=300.0, market_price=310.0)
        result = deduplicate_holdings([h1, h2])
        assert len(result) == 2

    def test_skips_empty_ticker(self):
        h1 = Holding(ticker="", shares=100, avg_cost=150.0, market_price=155.0)
        h2 = Holding(ticker="AAPL", shares=50, avg_cost=300.0, market_price=310.0)
        result = deduplicate_holdings([h1, h2])
        assert len(result) == 1
        assert "AAPL" in result

    def test_empty_list(self):
        result = deduplicate_holdings([])
        assert result == {}
