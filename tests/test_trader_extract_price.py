"""Tests for _extract_market_analyst_price in tradingagents/agents/trader/trader.py."""

import pytest

from tradingagents.agents.trader.trader import _extract_market_analyst_price


@pytest.mark.unit
class TestExtractMarketAnalystPrice:
    """Unit tests for _extract_market_analyst_price (pure regex function)."""

    # --- Empty / None inputs ---

    def test_returns_none_for_empty_string(self):
        assert _extract_market_analyst_price("") is None

    def test_returns_none_for_none(self):
        assert _extract_market_analyst_price(None) is None  # type: ignore[arg-type]

    # --- Chinese patterns ---

    def test_chinese_xianjia(self):
        assert _extract_market_analyst_price("现价：160.51") == "160.51"

    def test_chinese_xianjia_colon(self):
        assert _extract_market_analyst_price("现价: 160.51") == "160.51"

    def test_chinese_shoupanjia(self):
        assert _extract_market_analyst_price("收盘价: 91.60") == "91.60"

    def test_chinese_zuixinjia(self):
        assert _extract_market_analyst_price("最新价：85.30") == "85.30"

    def test_chinese_jiage(self):
        assert _extract_market_analyst_price("价格: 50.00") == "50.00"

    # --- English patterns ---

    def test_english_close(self):
        assert _extract_market_analyst_price("Close: 91.60") == "91.60"

    def test_english_price(self):
        assert _extract_market_analyst_price("Price: 150.25") == "150.25"

    # --- "当前" prefix pattern ---

    def test_current_prefix(self):
        assert _extract_market_analyst_price("当前价格是120.50元") == "120.50"

    # --- Table row pattern ---

    def test_table_close_row(self):
        assert _extract_market_analyst_price("| Close | 91.60 |") == "91.60"

    # --- Priority: first pattern wins ---

    def test_first_pattern_wins(self):
        assert _extract_market_analyst_price("现价：100.50 Close: 200.75") == "100.50"

    # --- No match ---

    def test_no_price_in_text(self):
        assert _extract_market_analyst_price("No price data available") is None

    # --- Integer price (no decimal) ---

    def test_integer_price(self):
        assert _extract_market_analyst_price("现价：100") == "100"
