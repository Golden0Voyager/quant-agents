"""Test checkpoint resume: crash mid-analysis, re-run resumes from last node."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict

import pytest
from langgraph.graph import END, StateGraph

from tradingagents.graph.checkpointer import (
    _db_path,
    checkpoint_step,
    clear_all_checkpoints,
    clear_checkpoint,
    get_checkpointer,
    has_checkpoint,
    thread_id,
)


class _TempDirMixin:
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(str(self._tmp), ignore_errors=True)


# Mutable flag to simulate crash on first run
_should_crash = False


class _SimpleState(TypedDict):
    count: int


def _node_a(state: _SimpleState) -> dict:
    return {"count": state["count"] + 1}


def _node_b(state: _SimpleState) -> dict:
    if _should_crash:
        raise RuntimeError("simulated mid-analysis crash")
    return {"count": state["count"] + 10}


def _build_graph() -> StateGraph:
    builder = StateGraph(_SimpleState)
    builder.add_node("analyst", _node_a)
    builder.add_node("trader", _node_b)
    builder.set_entry_point("analyst")
    builder.add_edge("analyst", "trader")
    builder.add_edge("trader", END)
    return builder


@pytest.mark.unit
class TestCheckpointResume(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ticker = "TEST"
        self.date = "2026-04-20"

    def test_crash_and_resume(self):
        """Crash at 'trader' node, then resume from checkpoint."""
        global _should_crash
        builder = _build_graph()
        tid = thread_id(self.ticker, self.date)
        cfg = {"configurable": {"thread_id": tid}}

        # Run 1: crash at trader node
        _should_crash = True
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config=cfg)

        # Checkpoint should exist at step 1 (analyst completed)
        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))
        step = checkpoint_step(self.tmpdir, self.ticker, self.date)
        self.assertEqual(step, 1)

        # Run 2: resume — trader succeeds this time
        _should_crash = False
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke(None, config=cfg)

        # analyst added 1, trader added 10 → 11
        self.assertEqual(result["count"], 11)

    def test_clear_checkpoint_allows_fresh_start(self):
        """After clearing, the graph starts from scratch."""
        global _should_crash
        builder = _build_graph()
        tid = thread_id(self.ticker, self.date)
        cfg = {"configurable": {"thread_id": tid}}

        # Create a checkpoint by crashing
        _should_crash = True
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config=cfg)

        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))

        # Clear it
        clear_checkpoint(self.tmpdir, self.ticker, self.date)
        self.assertFalse(has_checkpoint(self.tmpdir, self.ticker, self.date))

        # Fresh run succeeds from scratch
        _should_crash = False
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke({"count": 0}, config=cfg)

        self.assertEqual(result["count"], 11)


    def test_different_date_starts_fresh(self):
        """A different date must NOT resume from an existing checkpoint."""
        global _should_crash
        builder = _build_graph()
        date2 = "2026-04-21"

        # Run with date1 — crash to leave a checkpoint
        _should_crash = True
        tid1 = thread_id(self.ticker, self.date)
        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid1}})

        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))

        # date2 should have no checkpoint
        self.assertFalse(has_checkpoint(self.tmpdir, self.ticker, date2))

        # Run with date2 — should start fresh and succeed
        _should_crash = False
        tid2 = thread_id(self.ticker, date2)
        self.assertNotEqual(tid1, tid2)

        with get_checkpointer(self.tmpdir, self.ticker) as saver:
            graph = builder.compile(checkpointer=saver)
            result = graph.invoke({"count": 0}, config={"configurable": {"thread_id": tid2}})

        # Fresh run: analyst +1, trader +10 = 11
        self.assertEqual(result["count"], 11)

        # Original date checkpoint still exists (untouched)
        self.assertTrue(has_checkpoint(self.tmpdir, self.ticker, self.date))


@pytest.mark.unit
class CheckpointerUtilityTests(unittest.TestCase):
    def test_db_path_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _db_path(tmpdir, "AAPL")
            self.assertTrue(str(path).endswith("AAPL.db"))
            self.assertTrue(Path(tmpdir, "checkpoints").exists())

    def test_db_path_safe_ticker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _db_path(tmpdir, "600519.SS")
            self.assertTrue(str(path).endswith("600519.SS.db"))

    def test_thread_id_deterministic(self):
        tid1 = thread_id("AAPL", "2026-06-20")
        tid2 = thread_id("AAPL", "2026-06-20")
        self.assertEqual(tid1, tid2)

    def test_thread_id_different_date(self):
        tid1 = thread_id("AAPL", "2026-06-20")
        tid2 = thread_id("AAPL", "2026-06-21")
        self.assertNotEqual(tid1, tid2)

    def test_thread_id_case_insensitive(self):
        tid1 = thread_id("AAPL", "2026-06-20")
        tid2 = thread_id("aapl", "2026-06-20")
        self.assertEqual(tid1, tid2)

    def test_checkpoint_step_returns_none_for_missing_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = checkpoint_step(tmpdir, "NONEXISTENT", "2026-06-20")
            self.assertIsNone(step)

    def test_has_checkpoint_returns_false_for_missing_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(has_checkpoint(tmpdir, "NONEXISTENT", "2026-06-20"))

    def test_clear_all_checkpoints_returns_zero_for_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            count = clear_all_checkpoints(tmpdir)
            self.assertEqual(count, 0)

    def test_clear_checkpoint_no_db_no_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clear_checkpoint(tmpdir, "NONEXISTENT", "2026-06-20")


# =========================================================================
# Edge-case tests merged from test_remaining_coverage.py
# =========================================================================


@pytest.mark.unit
class ClearAllCheckpointsTests(_TempDirMixin, unittest.TestCase):
    """Lines 117-120: clear_all with existing DBs."""

    def test_clears_existing_dbs(self):
        cp_dir = self._tmp / "checkpoints"
        cp_dir.mkdir(parents=True)
        (cp_dir / "AAPL.db").write_text("")
        (cp_dir / "MSFT.db").write_text("")
        (cp_dir / "other.txt").write_text("")

        result = clear_all_checkpoints(str(self._tmp))
        self.assertEqual(result, 2)
        self.assertFalse((cp_dir / "AAPL.db").exists())
        self.assertFalse((cp_dir / "MSFT.db").exists())
        self.assertTrue((cp_dir / "other.txt").exists())

    def test_nonexistent_dir_returns_zero(self):
        result = clear_all_checkpoints("/nonexistent")
        self.assertEqual(result, 0)


@pytest.mark.unit
class ClearCheckpointOperationalErrorTests(_TempDirMixin, unittest.TestCase):
    """Lines 133-135: clear_checkpoint happy path + error handling."""

    def test_clear_checkpoint_happy_path(self):
        """Line 133: conn.commit() reached with proper DB tables."""
        cp_dir = self._tmp / "checkpoints"
        cp_dir.mkdir(parents=True)
        db_path = _db_path(str(self._tmp), "AAPL")
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE writes (thread_id TEXT)")
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT)")
        conn.commit()
        conn.close()

        clear_checkpoint(str(self._tmp), "AAPL", "2026-01-03")

    def test_operational_error_swallowed(self):
        """Lines 134-135: DB exists but tables missing -> OperationalError -> pass."""
        cp_dir = self._tmp / "checkpoints"
        cp_dir.mkdir(parents=True)
        db_path = _db_path(str(self._tmp), "AAPL")
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id INT)")
        conn.close()

        clear_checkpoint(str(self._tmp), "AAPL", "2026-01-03")

    def test_nonexistent_db_returns_silently(self):
        clear_checkpoint(str(self._tmp), "NONEXISTENT", "2026-01-03")


# =========================================================================
# _run_graph resume semantics (P0): a crashed run must NOT re-run completed
# nodes when the same ticker+date is analyzed again.
# =========================================================================


class _ResumeState(TypedDict):
    count: int
    market_report: str
    final_trade_decision: str
    verified_market_snapshot: str


@pytest.mark.unit
class RunGraphResumeTests(unittest.TestCase):
    """TradingAgentsGraph._run_graph must resume via stream(None) when a
    checkpoint exists for the ticker+date thread."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_graph(self):
        from unittest.mock import MagicMock, patch

        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        g.config = {
            "data_cache_dir": self.tmpdir,
            "results_dir": self.tmpdir,
            "checkpoint_enabled": True,
        }
        g.callbacks = []
        g.debug = False
        g.memory_log = MagicMock()
        g.memory_log.get_past_context.return_value = ""
        g.reflector = MagicMock()
        g.log_states_dict = {}
        g.ticker = "TEST"
        g.node_timings = []
        g.selected_analysts = ["market"]
        g.signal_processor = MagicMock()
        g.signal_processor.process_signal.return_value = "Buy"
        g.resolve_instrument_context = MagicMock(return_value="ctx")
        g.propagator = MagicMock()
        g.propagator.create_initial_state.return_value = {
            "count": 0,
            "market_report": "",
            "final_trade_decision": "",
            "verified_market_snapshot": "snap",
        }
        g.propagator.get_graph_args.return_value = {"config": {}}
        g._log_state = MagicMock()
        g._checkpointer_ctx = None
        return g

    def test_resume_does_not_rerun_completed_nodes(self):
        """Crash at 'trader'; the re-run must skip the finished 'analyst' node
        and still merge its checkpointed output into the final state."""
        calls = {"analyst": 0, "trader": 0}
        crash = {"enabled": True}

        def analyst(state: _ResumeState) -> dict:
            calls["analyst"] += 1
            return {"count": state["count"] + 1, "market_report": "analyst-report"}

        def trader(state: _ResumeState) -> dict:
            calls["trader"] += 1
            if crash["enabled"]:
                raise RuntimeError("simulated mid-analysis crash")
            return {"count": state["count"] + 10, "final_trade_decision": "Buy"}

        builder = StateGraph(_ResumeState)
        builder.add_node("analyst", analyst)
        builder.add_node("trader", trader)
        builder.set_entry_point("analyst")
        builder.add_edge("analyst", "trader")
        builder.add_edge("trader", END)

        g = self._make_graph()

        # Run 1: trader crashes; analyst's checkpoint persists.
        with get_checkpointer(self.tmpdir, "TEST") as saver:
            g.graph = builder.compile(checkpointer=saver)
            with self.assertRaises(RuntimeError):
                g._run_graph("TEST", "2026-04-20")

        self.assertEqual(calls, {"analyst": 1, "trader": 1})
        self.assertTrue(has_checkpoint(self.tmpdir, "TEST", "2026-04-20"))

        # Run 2: no crash — must resume after 'analyst' instead of re-running it.
        crash["enabled"] = False
        with get_checkpointer(self.tmpdir, "TEST") as saver:
            g.graph = builder.compile(checkpointer=saver)
            final_state, signal = g._run_graph("TEST", "2026-04-20")

        # analyst ran exactly once across both runs; trader ran in each.
        self.assertEqual(calls["analyst"], 1)
        self.assertEqual(calls["trader"], 2)
        # The checkpointed analyst output survived into the merged final state.
        self.assertEqual(final_state["market_report"], "analyst-report")
        self.assertEqual(final_state["count"], 11)
        self.assertEqual(final_state["final_trade_decision"], "Buy")
        self.assertEqual(signal, "Buy")
        # Successful completion clears the checkpoint.
        self.assertFalse(has_checkpoint(self.tmpdir, "TEST", "2026-04-20"))


if __name__ == "__main__":
    unittest.main()
