"""Tests for the portfolio holdings system.

Covers:
1. Sync from Google Sheet (requires gws auth)
2. Local JSON repository read/write
3. Data validation and normalization
4. Prompt generation for each agent type
5. Backward compatibility with legacy holdings dict
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tradingagents.portfolio import (
    Holding,
    Portfolio,
    PortfolioMetadata,
    PortfolioRepository,
    Transaction,
    build_pm_prompt,
    build_risk_prompt,
    build_trader_prompt,
    normalize_ticker,
    validate_holding,
)
from tradingagents.portfolio.validators import _parse_number

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestHolding:
    def test_to_dict_excludes_none(self):
        h = Holding(ticker="002241.SZ", shares=100, avg_cost=10.0)
        d = h.to_dict()
        assert d == {"ticker": "002241.SZ", "shares": 100, "avg_cost": 10.0}
        assert "market_price" not in d

    def test_from_dict_roundtrip(self):
        h = Holding(
            ticker="002241.SZ",
            name="歌尔股份",
            shares=4500,
            avg_cost=28.41,
            market_price=25.37,
            pnl_pct=-0.107,
        )
        d = h.to_dict()
        h2 = Holding.from_dict(d)
        assert h2.ticker == h.ticker
        assert h2.shares == h.shares
        assert h2.pnl_pct == h.pnl_pct


class TestTransaction:
    def test_to_dict(self):
        t = Transaction(
            date="2026-01-15",
            ticker="600519.SS",
            name="贵州茅台",
            price=1500.0,
            action="买入",
            shares=100.0,
        )
        d = t.to_dict()
        assert d["date"] == "2026-01-15"
        assert d["ticker"] == "600519.SS"
        assert d["price"] == 1500.0
        assert d["shares"] == 100.0

    def test_to_dict_excludes_none_and_empty(self):
        t = Transaction(
            date="2026-01-15",
            ticker="600519.SS",
            price=1500.0,
            action="买入",
            shares=100.0,
        )
        d = t.to_dict()
        assert "fee" not in d
        assert "cash_change" not in d
        assert "tag" not in d
        assert "name" not in d

    def test_from_dict(self):
        data = {
            "date": "2026-01-15",
            "ticker": "600519.SS",
            "price": 1500.0,
            "action": "买入",
            "shares": 100.0,
            "fee": 5.0,
            "cash_change": -150005.0,
            "tag": "手动建仓",
        }
        t = Transaction.from_dict(data)
        assert t.date == "2026-01-15"
        assert t.ticker == "600519.SS"
        assert t.price == 1500.0
        assert t.fee == 5.0
        assert t.cash_change == -150005.0
        assert t.tag == "手动建仓"

    def test_from_dict_filters_invalid_keys(self):
        data = {"date": "2026-01-15", "ticker": "A", "price": 10.0, "action": "买入", "shares": 1.0, "nonexistent": "should be ignored"}
        t = Transaction.from_dict(data)
        assert not hasattr(t, "nonexistent")


class TestPortfolio:
    def test_total_invested(self):
        p = Portfolio(
            holdings={
                "A": Holding(ticker="A", shares=100, avg_cost=10.0),
                "B": Holding(ticker="B", shares=200, avg_cost=5.0),
            }
        )
        assert p.total_invested() == 2000.0

    def test_total_market_value_with_market_price(self):
        p = Portfolio(
            holdings={
                "A": Holding(ticker="A", shares=100, avg_cost=10.0, market_price=12.0),
                "B": Holding(ticker="B", shares=200, avg_cost=5.0, market_price=6.0),
            }
        )
        assert p.total_market_value() == 2400.0

    def test_total_market_value_falls_back_to_avg_cost(self):
        p = Portfolio(
            holdings={
                "A": Holding(ticker="A", shares=100, avg_cost=10.0),
            }
        )
        assert p.total_market_value() == 1000.0

    def test_total_pnl(self):
        p = Portfolio(
            holdings={
                "A": Holding(ticker="A", shares=100, avg_cost=10.0, market_price=12.0),
            }
        )
        # invested = 1000, market_value = 1200, pnl = 200
        assert p.total_pnl() == 200.0

    def test_to_dict_roundtrip(self):
        p = Portfolio(
            holdings={
                "002241.SZ": Holding(ticker="002241.SZ", shares=100, avg_cost=10.0),
            },
            metadata=PortfolioMetadata(updated_at="2026-01-01T00:00:00+00:00"),
            summary={"total_holdings": 1},
        )
        d = p.to_dict()
        p2 = Portfolio.from_dict(d)
        assert len(p2.holdings) == 1
        assert p2.summary["total_holdings"] == 1


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class TestNormalizeTicker:
    def test_a_share_6_prefix(self):
        assert normalize_ticker("600519") == "600519.SS"

    def test_a_share_0_prefix(self):
        assert normalize_ticker("002241") == "002241.SZ"

    def test_a_share_3_prefix(self):
        assert normalize_ticker("300002") == "300002.SZ"

    def test_hk_unchanged(self):
        assert normalize_ticker("HK1810") == "HK1810"

    def test_skip_headers(self):
        assert normalize_ticker("合计") is None
        assert normalize_ticker("可用现金") is None
        assert normalize_ticker("-") is None


class TestParseNumber:
    def test_int_input(self):
        assert _parse_number(42) == 42.0

    def test_float_input(self):
        assert _parse_number(3.14) == 3.14

    def test_cleans_thousand_separators(self):
        assert _parse_number("1,234.56") == 1234.56

    def test_cleans_chinese_comma(self):
        assert _parse_number("1，234.56") == 1234.56

    def test_cleans_currency_symbols(self):
        assert _parse_number("$100.50") == 100.50
        assert _parse_number("¥200.00") == 200.00

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Cannot parse number"):
            _parse_number([1, 2, 3])


class TestValidateHolding:
    def test_valid_holding(self):
        h = Holding(ticker="A", shares=100, avg_cost=10.0)
        assert validate_holding(h) is h

    def test_empty_ticker(self):
        h = Holding(ticker="", shares=100, avg_cost=10.0)
        assert validate_holding(h) is None

    def test_invalid_shares(self):
        h = Holding(ticker="A", shares=0, avg_cost=10.0)
        assert validate_holding(h) is None

    def test_invalid_cost(self):
        h = Holding(ticker="A", shares=100, avg_cost=-1.0)
        assert validate_holding(h) is None


class TestDeduplicateHoldings:
    def test_deduplicates_by_ticker_keeping_last(self):
        from tradingagents.portfolio.validators import deduplicate_holdings

        holdings = [
            Holding(ticker="A", shares=100, avg_cost=10.0),
            Holding(ticker="B", shares=200, avg_cost=5.0),
            Holding(ticker="A", shares=150, avg_cost=12.0),  # replaces first A
        ]
        result = deduplicate_holdings(holdings)
        assert len(result) == 2
        assert result["A"].shares == 150.0
        assert result["B"].shares == 200.0

    def test_skips_holdings_with_empty_ticker(self):
        from tradingagents.portfolio.validators import deduplicate_holdings

        holdings = [
            Holding(ticker="A", shares=100, avg_cost=10.0),
            Holding(ticker="", shares=0, avg_cost=0.0),
        ]
        result = deduplicate_holdings(holdings)
        assert len(result) == 1
        assert "A" in result


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class TestPortfolioRepository:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = PortfolioRepository(data_path=os.path.join(tmpdir, "portfolio.json"))
            p = Portfolio(
                holdings={
                    "002241.SZ": Holding(ticker="002241.SZ", shares=100, avg_cost=10.0),
                },
                metadata=PortfolioMetadata(updated_at="2026-01-01T00:00:00+00:00"),
            )
            repo.save(p)
            assert repo.exists()

            p2 = repo.load()
            assert p2.holdings["002241.SZ"].shares == 100

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = PortfolioRepository(data_path=os.path.join(tmpdir, "missing.json"))
            with pytest.raises(FileNotFoundError):
                repo.load()

    def test_corrupted_json_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "portfolio.json"
            path.write_text("not json", encoding="utf-8")
            repo = PortfolioRepository(data_path=str(path))
            with pytest.raises(ValueError) as exc_info:
                repo.load()
            assert ".bak" in str(exc_info.value)
            assert (path.with_suffix(".json.bak")).exists()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

class TestPrompts:
    def test_build_pm_prompt_with_holding(self):
        p = Portfolio(
            holdings={
                "002241.SZ": Holding(
                    ticker="002241.SZ",
                    name="歌尔股份",
                    shares=4500,
                    avg_cost=28.41,
                    market_price=25.37,
                    pnl_pct=-0.107,
                    weight=0.0959,
                ),
            }
        )
        prompt = build_pm_prompt("002241.SZ", p)
        assert "歌尔股份" in prompt
        assert "28.41" in prompt
        assert "-10.70%" in prompt
        assert "9.59%" in prompt

    def test_build_pm_prompt_no_holding(self):
        p = Portfolio()
        assert build_pm_prompt("UNKNOWN", p) == ""

    def test_build_risk_prompt_concentration_warning(self):
        p = Portfolio(
            holdings={
                "A": Holding(ticker="A", shares=100, avg_cost=10.0, weight=0.20, pnl_pct=-0.25),
            }
        )
        prompt = build_risk_prompt("A", p)
        assert "20.00%" in prompt
        assert "集中持仓" in prompt
        assert "亏损超过 20%" in prompt

    def test_build_trader_prompt_with_grid(self):
        p = Portfolio(
            holdings={
                "A": Holding(
                    ticker="A",
                    shares=100,
                    avg_cost=10.0,
                    market_price=12.0,
                    grid_strategy="网格宽度: +3%/-3%",
                ),
            }
        )
        prompt = build_trader_prompt("A", p)
        assert "网格策略" in prompt
        assert "+20.00%" in prompt  # price gap


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_legacy_holdings_dict(self):
        """Ensure legacy flat dict format still works via Holding.from_dict."""
        legacy = {
            "002241.SZ": {
                "shares": 4500.0,
                "avg_cost": 28.41,
                "market_price": 25.37,
                "pnl_pct": -0.107,
                "weight": 0.0959,
                "grid_strategy": None,
                "name": "",
            }
        }
        portfolio = Portfolio(
            holdings={t: Holding.from_dict(d, ticker=t) for t, d in legacy.items()}
        )
        assert portfolio.has_holding("002241.SZ")
        h = portfolio.get_holding("002241.SZ")
        assert h.shares == 4500.0
        assert h.pnl_pct == -0.107


# ---------------------------------------------------------------------------
# Sync integration (requires gws auth — marked as integration)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPortfolioSyncIntegration:
    def test_sync_from_gsheet(self):
        """Sync from a mocked Google Sheet response."""
        from tradingagents.portfolio import PortfolioSyncService

        sheet_id = "1g8EqjG8dVVVmH9Tq7Wq8UkXP72T7hoXZVP1cvqZz9g4"
        sync = PortfolioSyncService(sheet_id=sheet_id, worksheet="total")

        mock_rows = [
            ["代码", "资产名称", "持仓成本", "持仓数量", "现价", "投入本金 (元)", "盈亏率", "仓位占比", "网格策略"],
            ["600519", "贵州茅台", "1500.00", "100", "1600.00", "150000.00", "6.67%", "50.00%", ""],
            ["002241", "歌尔股份", "28.41", "4500", "25.37", "127845.00", "-10.70%", "9.59%", "网格宽度: +3%/-3%"],
            ["合计", "", "", "", "", "", "", "", ""],
        ]

        with patch.object(
            sync, "_fetch_from_gsheet", return_value=mock_rows
        ):
            portfolio = sync.sync()

        assert len(portfolio.holdings) == 2
        assert portfolio.metadata.source_type == "google_sheet"
        assert portfolio.summary["total_holdings"] == 2

        # Verify A-share suffix normalization
        assert "600519.SS" in portfolio.holdings
        assert "002241.SZ" in portfolio.holdings

        # Verify numeric parsing (no commas left)
        h1 = portfolio.holdings["600519.SS"]
        assert isinstance(h1.shares, float)
        assert h1.shares == 100.0
        assert isinstance(h1.avg_cost, float)
        assert h1.avg_cost == 1500.0
        assert h1.market_price == 1600.0
        assert h1.pnl_pct == 0.0667
        assert h1.weight == 0.5

        h2 = portfolio.holdings["002241.SZ"]
        assert h2.shares == 4500.0
        assert h2.avg_cost == 28.41
        assert h2.grid_strategy == "网格宽度: +3%/-3%"
