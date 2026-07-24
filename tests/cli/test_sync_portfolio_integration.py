"""Smoke / integration tests for the 'my' watchlist Google Sheet sync flow.

These tests exercise ``_sync_portfolio_for_my_list()`` end-to-end: the smoke
variant mocks the ``gws`` CLI, while the integration variant uses the real
``gws`` CLI and the sheet IDs configured via environment variables.
"""

from __future__ import annotations

import os
import shutil
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_portfolio_repo(tmp_path, monkeypatch):
    """Redirect the default portfolio repository path to a temp directory."""
    import tradingagents.portfolio.repository as repo_module

    monkeypatch.setattr(repo_module, "_DEFAULT_DATA_DIR", str(tmp_path / "quant_data"))


@pytest.fixture
def fake_sheet_config():
    """Return a config dict with fake sheet IDs for smoke tests."""
    return {
        "portfolio": {
            "sheet_id": "fake_holdings_sheet",
            "worksheet": "total",
            "transaction_sheet_id": "fake_tx_sheet",
            "transaction_worksheet": "stock transitions",
        }
    }


def _fake_gws_command_side_effect(holdings_rows, transaction_rows):
    """Build a side effect for ``_run_gws_command`` that returns fake rows.

    The side effect distinguishes holdings from transactions by the exact
    worksheet name in the requested range (e.g. ``total!A1:Z1000``).
    """

    def _side_effect(sheet_id, range_str):
        worksheet = range_str.split("!")[0]
        if worksheet == "total":
            return holdings_rows
        if worksheet == "stock transitions":
            return transaction_rows
        return []

    return _side_effect


@pytest.mark.smoke
class TestSyncPortfolioForMyListSmoke:
    """End-to-end smoke tests with the ``gws`` CLI mocked."""

    def test_happy_path_syncs_holdings_and_transactions(self, fake_sheet_config):
        """Mocked gws output is transformed and persisted to the local cache."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository

        holdings_rows = [
            [
                "代码",
                "资产名称",
                "持仓成本",
                "持仓数量",
                "现价",
                "投入本金 (元)",
                "盈亏率",
                "仓位占比",
                "网格策略",
            ],
            ["AAPL", "Apple Inc.", "150.00", "100", "160.00", "15000.00", "6.67%", "50.00%", ""],
        ]

        transaction_rows = [
            [
                "交易时间",
                "代码",
                "名称",
                "成本单价",
                "动作",
                "份额变动",
                "手续费",
                "资金变动",
                "网格建仓",
            ],
            ["2025-01-01", "AAPL", "Apple Inc.", "150.00", "买入", "10", "0", "-1500.00", ""],
        ]

        with (
            patch("cli.main.DEFAULT_CONFIG", fake_sheet_config),
            patch("tradingagents.portfolio.sync._run_gws_command") as mock_h,
            patch("tradingagents.portfolio.transaction_sync._run_gws_command") as mock_tx,
        ):
            mock_h.side_effect = _fake_gws_command_side_effect(holdings_rows, transaction_rows)
            mock_tx.side_effect = _fake_gws_command_side_effect(holdings_rows, transaction_rows)

            _sync_portfolio_for_my_list()

        repo = PortfolioRepository()
        saved = repo.load()

        assert "AAPL" in saved.holdings
        assert saved.holdings["AAPL"].shares == 100.0
        assert saved.holdings["AAPL"].avg_cost == 150.0
        assert saved.metadata.source_type == "google_sheet"
        assert saved.metadata.source_sheet_id == "fake_holdings_sheet"
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "AAPL"
        assert saved.transactions[0].action == "买入"

    def test_partial_failure_does_not_save_inconsistent_data(self, fake_sheet_config):
        """When the transaction sync fails, the local cache stays untouched."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        repo = PortfolioRepository()
        repo.save(
            Portfolio(
                holdings={"OLD": Holding(ticker="OLD", shares=5, avg_cost=10.0)},
                transactions=[Transaction(date="2024-01-01", ticker="OLD", action="买入", shares=5)],
            )
        )

        holdings_rows = [
            [
                "代码",
                "资产名称",
                "持仓成本",
                "持仓数量",
                "现价",
                "投入本金 (元)",
                "盈亏率",
                "仓位占比",
                "网格策略",
            ],
            ["AAPL", "Apple Inc.", "150.00", "100", "160.00", "15000.00", "6.67%", "50.00%", ""],
        ]

        with (
            patch("cli.main.DEFAULT_CONFIG", fake_sheet_config),
            patch("tradingagents.portfolio.sync._run_gws_command") as mock_h,
            patch(
                "tradingagents.portfolio.transaction_sync._run_gws_command",
                side_effect=RuntimeError("gws exploded"),
            ),
        ):
            mock_h.side_effect = _fake_gws_command_side_effect(holdings_rows, [])

            _sync_portfolio_for_my_list()

        saved = repo.load()
        # Old cache should be preserved because the transaction sync failed.
        assert "OLD" in saved.holdings
        assert saved.holdings["OLD"].shares == 5
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "OLD"

    def test_missing_gws_cli_leaves_cache_intact(self, fake_sheet_config):
        """Simulate a missing gws CLI: the helper warns and does not touch the cache."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        repo = PortfolioRepository()
        repo.save(
            Portfolio(
                holdings={"OLD": Holding(ticker="OLD", shares=5, avg_cost=10.0)},
                transactions=[Transaction(date="2024-01-01", ticker="OLD", action="买入", shares=5)],
            )
        )

        with (
            patch("cli.main.DEFAULT_CONFIG", fake_sheet_config),
            patch(
                "tradingagents.portfolio.sync._run_gws_command",
                side_effect=FileNotFoundError("gws not found"),
            ),
            patch(
                "tradingagents.portfolio.transaction_sync._run_gws_command",
                side_effect=FileNotFoundError("gws not found"),
            ),
        ):
            # Should not raise even though gws is unavailable.
            _sync_portfolio_for_my_list()

        saved = repo.load()
        # Cache must remain unchanged because both syncs failed.
        assert "OLD" in saved.holdings
        assert saved.holdings["OLD"].shares == 5
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "OLD"


@pytest.mark.integration
class TestSyncPortfolioForMyListIntegration:
    """Tests against the real Google Workspace ``gws`` CLI."""

    def test_real_google_sheet_sync(self):
        """Skip unless gws and sheet IDs are available; otherwise sync and verify."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository

        gws_path = shutil.which("gws")
        sheet_id = os.environ.get("PORTFOLIO_SHEET_ID")
        tx_sheet_id = os.environ.get("TRANSACTION_SHEET_ID")

        if not gws_path:
            pytest.skip("gws CLI not found in PATH")
        if not sheet_id:
            pytest.skip("PORTFOLIO_SHEET_ID not set")
        if not tx_sheet_id:
            pytest.skip("TRANSACTION_SHEET_ID not set")

        config = {
            "portfolio": {
                "sheet_id": sheet_id,
                "worksheet": "total",
                "transaction_sheet_id": tx_sheet_id,
                "transaction_worksheet": "stock transitions",
            }
        }

        with patch("cli.main.DEFAULT_CONFIG", config):
            _sync_portfolio_for_my_list()

        saved = PortfolioRepository().load()
        # We cannot assert exact contents without knowing the sheet, but we can
        # verify the sync ran and produced well-formed metadata.
        assert saved.metadata.source_type == "google_sheet"
        assert saved.metadata.source_sheet_id == sheet_id
        assert isinstance(saved.holdings, dict)
        assert isinstance(saved.transactions, list)
