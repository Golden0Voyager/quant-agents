"""Tests for BatchRunner concurrency — proves ``workers`` param is honored.

Test 1 passes with current sequential-only implementation.
Tests 2 and 3 verify the concurrent path with a real ThreadPoolExecutor
but mocked ``_run_single`` so they run fast with zero LLM / vendor calls.
"""

from pathlib import Path
from unittest.mock import call, patch

import pytest

from cli.batch_runner import BatchRunner

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_home(tmp_path, monkeypatch):
    """Isolate home-directory caches to a temporary directory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


class TestWorkersOne:
    """workers=1 must keep the existing sequential loop — no ThreadPoolExecutor."""

    def test_workers_one_runs_sequentially(self, tmp_path):
        """workers=1 → sequential for-loop, ThreadPoolExecutor NOT instantiated."""
        runner = BatchRunner(
            tickers=["AAPL", "MSFT"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=tmp_path / "reports",
            workers=1,
        )
        with (
            patch.object(runner, "_run_single") as mock_run,
            patch("cli.batch_runner.ThreadPoolExecutor", create=True) as mock_executor,
        ):
            runner.run()

        # ── Sequential path — ThreadPoolExecutor NOT used ──
        mock_executor.assert_not_called()

        # workers=1 → batch_mode is False (keep detailed dashboard)
        assert runner._batch_mode is False

        # Tickers processed in order via the existing for loop
        assert mock_run.call_count == 2
        mock_run.assert_has_calls([call("AAPL"), call("MSFT")])
        assert runner.completed_tickers == {"AAPL", "MSFT"}


class TestWorkersGreaterThanOne:
    """workers > 1 uses ThreadPoolExecutor internally.

    Uses a real executor (no mock) so ``_run_single`` actually executes
    and shared state (``completed_tickers``, ``failures``, ``summaries``)
    is populated correctly.
    """

    def test_workers_greater_than_one_uses_thread_pool(self, tmp_path):
        """workers=2 → each ticker submitted via executor, all completed."""
        runner = BatchRunner(
            tickers=["AAPL", "MSFT", "GOOGL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=tmp_path / "reports",
            workers=2,
        )
        with patch.object(runner, "_run_single") as mock_run:
            runner.run()

        # ── Concurrent path — each ticker submitted to the executor ──
        assert mock_run.call_count == 3
        mock_run.assert_has_calls(
            [call("AAPL"), call("MSFT"), call("GOOGL")],
            any_order=True,
        )

        # workers>1 → batch_mode is True (batch summary dashboard)
        assert runner._batch_mode is True

        # All tickers eventually completed
        assert len(runner.completed_tickers) == 3

    def test_concurrent_run_records_failures_and_summaries(self, tmp_path):
        """workers=2: one ticker fails → failures, all in completed_tickers, successful in summaries."""
        runner = BatchRunner(
            tickers=["AAPL", "MSFT", "GOOGL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=tmp_path / "reports",
            workers=2,
        )

        def side_effect(ticker):
            if ticker == "MSFT":
                raise RuntimeError("API error")
            # Populate summaries for successful tickers as _run_single normally does
            runner.summaries[ticker] = {
                "company": ticker,
                "rating": "Buy",
                "entry": "100",
                "stop": "90",
                "size": "5%",
            }

        with patch.object(runner, "_run_single", side_effect=side_effect):
            runner.run()

        # Only the failing ticker in failures
        assert runner.failures == {"MSFT": "API error"}

        # All tickers eventually completed (including the failed one)
        assert runner.completed_tickers == {"AAPL", "MSFT", "GOOGL"}

        # Successful tickers appear in summaries
        assert "AAPL" in runner.summaries
        assert "GOOGL" in runner.summaries
        assert "MSFT" not in runner.summaries
