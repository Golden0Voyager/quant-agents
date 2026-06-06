"""Tests for the portfolio holdings system.

Covers:
1. Sync from Google Sheet (requires gws auth)
2. Local JSON repository read/write
3. Data validation and normalization
4. Prompt generation for each agent type
5. Backward compatibility with legacy holdings dict
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tradingagents.portfolio import (
    Holding,
    Portfolio,
    PortfolioMetadata,
    PortfolioRepository,
    normalize_ticker,
    validate_holding,
    build_pm_prompt,
    build_risk_prompt,
    build_trader_prompt,
)


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


class TestPortfolio:
    def test_total_invested(self):
        p = Portfolio(
            holdings={
                "A": Holding(ticker="A", shares=100, avg_cost=10.0),
                "B": Holding(ticker="B", shares=200, avg_cost=5.0),
            }
        )
        assert p.total_invested() == 2000.0

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


class TestValidateHolding:
    def test_valid_holding(self):
        h = Holding(ticker="A", shares=100, avg_cost=10.0)
        assert validate_holding(h) is h

    def test_invalid_shares(self):
        h = Holding(ticker="A", shares=0, avg_cost=10.0)
        assert validate_holding(h) is None

    def test_invalid_cost(self):
        h = Holding(ticker="A", shares=100, avg_cost=-1.0)
        assert validate_holding(h) is None


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
