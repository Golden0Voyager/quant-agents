"""Smoke tests for the single-stock CLI analysis flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from cli.main import run_analysis
from cli.models import AnalystType


@pytest.fixture(autouse=True)
def _mock_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


@pytest.mark.unit
class TestRunAnalysis:
    """Smoke tests for the unified-dashboard run_analysis path."""

    def _make_selections(self, analysts=None):
        return {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "analysis_date": "2026-07-08",
            "asset_type": "stock",
            "analysts": analysts or [AnalystType.MARKET, AnalystType.NEWS],
            "research_depth": 1,
            "shallow_thinker": "qwen3.7-plus",
            "deep_thinker": "qwen3.7-max",
            "backend_url": None,
            "llm_provider": "openai",
            "google_thinking_level": None,
            "openai_reasoning_effort": None,
            "anthropic_effort": None,
            "output_language": "English",
            "force_regenerate": True,
        }

    def _make_fake_graph(self, chunks):
        fake_graph = MagicMock()
        fake_graph.propagator.create_initial_state.return_value = {"messages": []}
        fake_graph.propagator.get_graph_args.return_value = {}
        fake_graph.resolve_instrument_context.return_value = {}
        fake_graph.graph.stream.return_value = iter(chunks)
        fake_graph.process_signal.return_value = None
        return fake_graph

    @patch("typer.prompt", return_value="Y")
    @patch("cli.main.display_complete_report")
    @patch("cli.main.save_report_to_disk")
    @patch("cli.main.console")
    @patch("cli.main.BatchRunner._is_outside_trading_hours", return_value=False)
    @patch("cli.main.Path.mkdir")
    @patch("cli.main.Path.touch")
    @patch("cli.main.StatsCallbackHandler")
    @patch("cli.main.TradingAgentsGraph")
    @patch("cli.main.process_stream_chunk")
    def test_run_analysis_processes_stream_with_dashboard(
        self,
        mock_process_stream_chunk,
        mock_trading_graph,
        mock_stats_handler_cls,
        mock_touch,
        mock_mkdir,
        mock_is_outside,
        mock_console,
        mock_save_report,
        mock_display_report,
        mock_typer_prompt,
    ):
        selections = self._make_selections(
            analysts=[AnalystType.MARKET, AnalystType.NEWS, AnalystType.GOVERNANCE]
        )
        chunks = [
            {"market_report": "Market analysis content"},
            {"news_report": "News analysis content"},
            {
                "investment_debate_state": {
                    "bull_history": "Bull",
                    "bear_history": "Bear",
                    "judge_decision": "Hold",
                }
            },
            {"trader_investment_plan": "Buy at 200"},
            {
                "risk_debate_state": {
                    "aggressive_history": "Agg",
                    "conservative_history": "Cons",
                    "neutral_history": "Neu",
                    "judge_decision": "Overweight",
                }
            },
            {"final_trade_decision": "Buy"},
        ]
        fake_graph = self._make_fake_graph(chunks)
        mock_trading_graph.return_value = fake_graph

        mock_stats_handler = MagicMock()
        mock_stats_handler.get_stats.return_value = {
            "llm_calls": 3,
            "tool_calls": 2,
            "tokens_in": 1000,
            "tokens_out": 500,
        }
        mock_stats_handler_cls.return_value = mock_stats_handler

        with patch("builtins.open", mock_open()):
            run_analysis(checkpoint=False, selections=selections)

        # process_stream_chunk called once per chunk
        assert mock_process_stream_chunk.call_count == len(chunks)

        # save_report_to_disk and display_complete_report are invoked
        mock_save_report.assert_called_once()
        mock_display_report.assert_called_once()


@pytest.mark.unit
class TestRunBatchAnalysisMyWatchlist:
    """Tests for the automatic Google Sheet sync when running the 'my' watchlist."""

    def test_my_watchlist_triggers_sync_and_reloads_holdings(self, tmp_path):
        """When watchlist_name='my', sync helper is called and holdings reset to None."""
        from cli.main import run_batch_analysis

        with patch("cli.main._sync_portfolio_for_my_list") as mock_sync, \
             patch("cli.main.BatchRunner") as mock_runner_class:
            mock_runner = MagicMock()
            mock_runner.summaries = {}
            mock_runner.failures = {}
            mock_runner.completed_tickers = {"AAPL"}
            mock_runner.batch_stats = {}
            mock_runner.generate_summary.return_value = tmp_path / "batch_summary.md"
            mock_runner_class.return_value = mock_runner

            run_batch_analysis(
                tickers=["AAPL"],
                profile_config={"llm_provider": "openai", "output_language": "English"},
                output_dir=tmp_path,
                watchlist_name="my",
                holdings={"AAPL": {"shares": 10}},
                headless=True,
            )

            mock_sync.assert_called_once()
            # Holdings should be reset so BatchRunner reloads from freshly synced cache.
            assert mock_runner_class.call_args[1]["holdings"] is None

    def test_other_watchlist_does_not_trigger_sync(self, tmp_path):
        """Non-'my' watchlists should not trigger automatic Google Sheet sync."""
        from cli.main import run_batch_analysis

        with patch("cli.main._sync_portfolio_for_my_list") as mock_sync, \
             patch("cli.main.BatchRunner") as mock_runner_class:
            mock_runner = MagicMock()
            mock_runner.summaries = {}
            mock_runner.failures = {}
            mock_runner.completed_tickers = {"AAPL"}
            mock_runner.batch_stats = {}
            mock_runner.generate_summary.return_value = tmp_path / "batch_summary.md"
            mock_runner_class.return_value = mock_runner

            run_batch_analysis(
                tickers=["AAPL"],
                profile_config={"llm_provider": "openai", "output_language": "English"},
                output_dir=tmp_path,
                watchlist_name="tech",
                holdings={"AAPL": {"shares": 10}},
                headless=True,
            )

            mock_sync.assert_not_called()
            # Explicitly provided holdings should be preserved.
            assert mock_runner_class.call_args[1]["holdings"] == {"AAPL": {"shares": 10}}

    def test_my_watchlist_overrides_explicit_holdings(self, tmp_path):
        """When watchlist_name='my', any explicitly-passed holdings dict is ignored."""
        from cli.main import run_batch_analysis

        with patch("cli.main._sync_portfolio_for_my_list") as mock_sync, \
             patch("cli.main.BatchRunner") as mock_runner_class:
            mock_runner = MagicMock()
            mock_runner.summaries = {}
            mock_runner.failures = {}
            mock_runner.completed_tickers = {"AAPL"}
            mock_runner.batch_stats = {}
            mock_runner.generate_summary.return_value = tmp_path / "batch_summary.md"
            mock_runner_class.return_value = mock_runner

            run_batch_analysis(
                tickers=["AAPL"],
                profile_config={"llm_provider": "openai", "output_language": "English"},
                output_dir=tmp_path,
                watchlist_name="my",
                holdings={"AAPL": {"shares": 10}},
                headless=True,
            )

            mock_sync.assert_called_once()
            # The explicit holdings dict is intentionally discarded so the runner reloads
            # from the freshly synced local cache.
            assert mock_runner_class.call_args[1]["holdings"] is None


@pytest.mark.unit
class TestSyncPortfolioForMyList:
    """Tests for the _sync_portfolio_for_my_list helper."""

    def test_syncs_holdings_and_transactions_to_local_cache(self, tmp_path, monkeypatch):
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        portfolio = Portfolio(
            holdings={
                "AAPL": Holding(ticker="AAPL", shares=100, avg_cost=150.0)
            }
        )
        transactions = [Transaction(date="2025-01-01", ticker="AAPL", action="买入", shares=10)]

        with patch(
            "cli.main.DEFAULT_CONFIG",
            {
                "portfolio": {
                    "sheet_id": "sheet123",
                    "worksheet": "total",
                    "transaction_sheet_id": "tx_sheet123",
                    "transaction_worksheet": "trades",
                }
            },
        ), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync, \
             patch("tradingagents.portfolio.TransactionSyncService") as MockTxSync:
            MockHoldingSync.return_value.sync.return_value = portfolio
            MockTxSync.return_value.sync.return_value = transactions

            _sync_portfolio_for_my_list()

            MockHoldingSync.assert_called_once_with(sheet_id="sheet123", worksheet="total")
            MockTxSync.assert_called_once_with(sheet_id="tx_sheet123", worksheet="trades")

        # Verify saved data by loading the repository cache.
        from tradingagents.portfolio import PortfolioRepository

        repo = PortfolioRepository()
        saved = repo.load()
        assert "AAPL" in saved.holdings
        assert saved.holdings["AAPL"].shares == 100
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "AAPL"

    def test_skips_sync_when_no_sheet_id_configured(self, tmp_path, monkeypatch):
        from cli.main import _sync_portfolio_for_my_list

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch("cli.main.DEFAULT_CONFIG", {"portfolio": {}}), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync, \
             patch("tradingagents.portfolio.TransactionSyncService") as MockTxSync:
            _sync_portfolio_for_my_list()

            MockHoldingSync.assert_not_called()
            MockTxSync.assert_not_called()

    def test_sync_failure_does_not_raise(self, tmp_path, monkeypatch):
        """A failing Google Sheet sync should warn, not crash the batch."""
        from cli.main import _sync_portfolio_for_my_list

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch(
            "cli.main.DEFAULT_CONFIG",
            {
                "portfolio": {
                    "sheet_id": "sheet123",
                    "worksheet": "total",
                }
            },
        ), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync:
            MockHoldingSync.return_value.sync.side_effect = RuntimeError("gws failed")

            # Should not raise even though the sync failed.
            _sync_portfolio_for_my_list()

    def test_holdings_sync_preserves_existing_transactions(self, tmp_path, monkeypatch):
        """Holdings sync should not wipe transactions already in the local cache."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Pre-populate local cache with holdings and transactions.
        existing = Portfolio(
            holdings={"OLD": Holding(ticker="OLD", shares=1, avg_cost=1.0)},
            transactions=[Transaction(date="2024-01-01", ticker="OLD", action="买入", shares=1)],
        )
        repo = PortfolioRepository()
        repo.save(existing)

        # Holdings sheet returns a fresh portfolio without transactions.
        synced_holdings = Portfolio(
            holdings={"AAPL": Holding(ticker="AAPL", shares=100, avg_cost=150.0)}
        )

        with patch(
            "cli.main.DEFAULT_CONFIG",
            {
                "portfolio": {
                    "sheet_id": "sheet123",
                    "worksheet": "total",
                    # transaction_sheet_id intentionally omitted.
                }
            },
        ), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync, \
             patch("tradingagents.portfolio.TransactionSyncService") as MockTxSync:
            MockHoldingSync.return_value.sync.return_value = synced_holdings

            _sync_portfolio_for_my_list()

            MockHoldingSync.assert_called_once()
            MockTxSync.assert_not_called()

        saved = repo.load()
        assert "AAPL" in saved.holdings
        assert saved.holdings["AAPL"].shares == 100
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "OLD"

    def test_transaction_sync_failure_preserves_local_cache(self, tmp_path, monkeypatch):
        """If transaction sync fails, the old local cache must remain unchanged."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        existing = Portfolio(
            holdings={"OLD": Holding(ticker="OLD", shares=1, avg_cost=1.0)},
            transactions=[Transaction(date="2024-01-01", ticker="OLD", action="买入", shares=1)],
        )
        repo = PortfolioRepository()
        repo.save(existing)

        synced_holdings = Portfolio(
            holdings={"AAPL": Holding(ticker="AAPL", shares=100, avg_cost=150.0)}
        )

        with patch(
            "cli.main.DEFAULT_CONFIG",
            {
                "portfolio": {
                    "sheet_id": "sheet123",
                    "worksheet": "total",
                    "transaction_sheet_id": "tx_sheet123",
                    "transaction_worksheet": "trades",
                }
            },
        ), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync, \
             patch("tradingagents.portfolio.TransactionSyncService") as MockTxSync:
            MockHoldingSync.return_value.sync.return_value = synced_holdings
            MockTxSync.return_value.sync.side_effect = RuntimeError("gws failed")

            _sync_portfolio_for_my_list()

            MockHoldingSync.assert_called_once()
            MockTxSync.assert_called_once()

        saved = repo.load()
        # Old cache is preserved because one of the configured syncs failed.
        assert "OLD" in saved.holdings
        assert saved.holdings["OLD"].shares == 1
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "OLD"

    def test_both_syncs_fail_preserves_local_cache(self, tmp_path, monkeypatch):
        """If all syncs fail, the local cache must remain unchanged."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        existing = Portfolio(
            holdings={"OLD": Holding(ticker="OLD", shares=5, avg_cost=10.0)},
            transactions=[Transaction(date="2024-01-01", ticker="OLD", action="买入", shares=5)],
        )
        repo = PortfolioRepository()
        repo.save(existing)

        with patch(
            "cli.main.DEFAULT_CONFIG",
            {
                "portfolio": {
                    "sheet_id": "sheet123",
                    "worksheet": "total",
                    "transaction_sheet_id": "tx_sheet123",
                    "transaction_worksheet": "trades",
                }
            },
        ), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync, \
             patch("tradingagents.portfolio.TransactionSyncService") as MockTxSync:
            MockHoldingSync.return_value.sync.side_effect = RuntimeError("gws failed")
            MockTxSync.return_value.sync.side_effect = RuntimeError("gws failed")

            _sync_portfolio_for_my_list()

            MockHoldingSync.assert_called_once()
            MockTxSync.assert_called_once()

        saved = repo.load()
        assert "OLD" in saved.holdings
        assert saved.holdings["OLD"].shares == 5
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "OLD"

    def test_holdings_sync_failure_preserves_local_cache(self, tmp_path, monkeypatch):
        """If holdings sync fails, the old local cache must remain unchanged."""
        from cli.main import _sync_portfolio_for_my_list
        from tradingagents.portfolio import PortfolioRepository
        from tradingagents.portfolio.models import Holding, Portfolio, Transaction

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        existing = Portfolio(
            holdings={"OLD": Holding(ticker="OLD", shares=5, avg_cost=10.0)},
            transactions=[Transaction(date="2024-01-01", ticker="OLD", action="买入", shares=5)],
        )
        repo = PortfolioRepository()
        repo.save(existing)

        new_transactions = [Transaction(date="2025-01-01", ticker="NEW", action="买入", shares=20)]

        with patch(
            "cli.main.DEFAULT_CONFIG",
            {
                "portfolio": {
                    "sheet_id": "sheet123",
                    "worksheet": "total",
                    "transaction_sheet_id": "tx_sheet123",
                    "transaction_worksheet": "trades",
                }
            },
        ), \
             patch("tradingagents.portfolio.PortfolioSyncService") as MockHoldingSync, \
             patch("tradingagents.portfolio.TransactionSyncService") as MockTxSync:
            MockHoldingSync.return_value.sync.side_effect = RuntimeError("gws failed")
            MockTxSync.return_value.sync.return_value = new_transactions

            _sync_portfolio_for_my_list()

            MockHoldingSync.assert_called_once()
            MockTxSync.assert_called_once()

        saved = repo.load()
        # Old cache is preserved because one of the configured syncs failed.
        assert "OLD" in saved.holdings
        assert saved.holdings["OLD"].shares == 5
        assert len(saved.transactions) == 1
        assert saved.transactions[0].ticker == "OLD"

