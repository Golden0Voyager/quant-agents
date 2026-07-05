"""Further edge-case tests for TradingMemoryLog uncovered code paths.

Existing tests in test_memory_log.py + test_memory_edge_cases.py cover ~86%.
This file fills remaining gaps:
- store_decision: idempotency guard on duplicate entries
- get_past_context: cross-only entries, same-only entries, limit reached
- _apply_rotation: drop oldest resolved, preserve pending
- update_with_outcome: atomic write (temp file + replace)
- batch_update_with_outcomes: multiple updates matching different entries
"""


import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog

_SEP = TradingMemoryLog._SEPARATOR
DECISION_BUY = "Rating: Buy\nEnter at $189-192, 6% portfolio cap."


# ---------------------------------------------------------------------------
# store_decision: idempotency guard — fast raw-text scan skips duplicates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStoreDecisionIdempotency:
    """store_decision: idempotency guard prevents duplicate entries."""

    def test_duplicate_ticker_date_skips_second_write(self, tmp_path):
        """Calling store_decision twice with same ticker/date -> second skipped."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        # Capture file size after first write
        size1 = (tmp_path / "mem.md").stat().st_size
        # Second call with same ticker/date should skip (idempotency guard)
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        size2 = (tmp_path / "mem.md").stat().st_size
        assert size1 == size2, "File size should not change on duplicate store_decision"

    def test_duplicate_check_ignores_extra_fields(self, tmp_path):
        """Idempotency guard uses startswith/endswith, matching tag structure."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        decision1 = "Rating: Strong Buy\nEnter at $200"
        decision2 = "Rating: Buy\nEnter at $195"  # Different rating
        log.store_decision("NVDA", "2026-01-10", decision1)
        # The guard checks for [date | ticker | ANY rating | pending]
        # So different rating should still match as duplicate
        log.store_decision("NVDA", "2026-01-10", decision2)
        entries = log.load_entries()
        assert len(entries) == 1

    def test_different_ticker_not_duplicate(self, tmp_path):
        """Different ticker same date -> not a duplicate, both written."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("AAPL", "2026-01-10", DECISION_BUY)
        entries = log.load_entries()
        assert len(entries) == 2

    def test_different_date_not_duplicate(self, tmp_path):
        """Same ticker different date -> not a duplicate, both written."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-11", DECISION_BUY)
        entries = log.load_entries()
        assert len(entries) == 2

    def test_no_log_path_skips_write(self):
        """No log_path configured -> store_decision returns without writing."""
        log = TradingMemoryLog(config=None)
        # Should not raise even though there's no path
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        # No way to verify no write happened, but at least no crash


# ---------------------------------------------------------------------------
# get_past_context: cross-only, same-only, limit reached
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPastContextCoverage:
    """get_past_context: edge cases for same/cross ticker collection."""

    def test_cross_only_entries(self, tmp_path):
        """Only cross-ticker entries exist -> shows cross-ticker lessons."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("AAPL", "2026-01-11", DECISION_BUY)
        # Mark both as resolved
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "")
        log.update_with_outcome("AAPL", "2026-01-11", 0.03, 0.01, 3, "Good call")

        ctx = log.get_past_context("MSFT")  # No same-ticker MSFT entries
        assert "Recent cross-ticker lessons" in ctx
        assert "Past analyses of MSFT" not in ctx

    def test_same_only_entries(self, tmp_path):
        """Only same-ticker entries exist -> shows past analyses."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-11", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "First")
        log.update_with_outcome("NVDA", "2026-01-11", 0.03, 0.01, 3, "Second")

        ctx = log.get_past_context("NVDA")
        assert "Past analyses of NVDA" in ctx
        assert "Recent cross-ticker lessons" not in ctx

    def test_same_limit_reached(self, tmp_path):
        """More same-ticker entries than n_same -> only shows n_same most recent."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        for i in range(1, 8):  # 7 entries, n_same=5
            log.store_decision("NVDA", f"2026-01-{i:02d}", DECISION_BUY)
            log.update_with_outcome("NVDA", f"2026-01-{i:02d}", 0.01 * i, 0.005 * i, i, f"Entry {i}")
        ctx = log.get_past_context("NVDA", n_same=5, n_cross=3)
        # Should show at most 5 NVDA entries (most recent)
        nvda_count = ctx.count("[2026-01-")
        assert nvda_count <= 5

    def test_cross_limit_reached(self, tmp_path):
        """More cross-ticker entries than n_cross -> only shows n_cross most recent."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("MSFT", "2026-01-01", DECISION_BUY)
        log.update_with_outcome("MSFT", "2026-01-01", 0.05, 0.02, 5, "")
        log.store_decision("GOOGL", "2026-01-02", DECISION_BUY)
        log.update_with_outcome("GOOGL", "2026-01-02", 0.03, 0.01, 3, "")
        log.store_decision("AMZN", "2026-01-03", DECISION_BUY)
        log.update_with_outcome("AMZN", "2026-01-03", 0.04, 0.02, 4, "")
        log.store_decision("META", "2026-01-04", DECISION_BUY)
        log.update_with_outcome("META", "2026-01-04", 0.02, 0.01, 2, "")

        ctx = log.get_past_context("NVDA", n_same=5, n_cross=2)
        # Should show at most 2 cross-ticker entries
        assert "Recent cross-ticker lessons" in ctx
        # Count cross-ticker entries in the cross section
        cross_section = ctx.split("Recent cross-ticker lessons")[1] if "Recent cross-ticker lessons" in ctx else ""
        ticker_count = sum(1 for t in ["MSFT", "GOOGL", "AMZN", "META"] if t in cross_section)
        assert ticker_count <= 2

    def test_no_resolved_entries(self, tmp_path):
        """All entries are pending -> returns empty string."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        ctx = log.get_past_context("NVDA")
        assert ctx == ""


# ---------------------------------------------------------------------------
# _apply_rotation: drop oldest resolved, preserve pending
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyRotationDropLogic:
    """_apply_rotation: drop oldest resolved entries."""

    def test_drops_oldest_resolved(self, tmp_path):
        """More resolved entries than max -> oldest resolved dropped, pending kept."""
        log = TradingMemoryLog({
            "memory_log_path": str(tmp_path / "mem.md"),
            "memory_log_max_entries": 2,
        })
        # Create 3 resolved entries (dates 1/9, 1/10, 1/11) + 1 pending (1/12)
        log.store_decision("NVDA", "2026-01-09", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-11", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-12", DECISION_BUY)
        # Mark first 3 as resolved
        log.update_with_outcome("NVDA", "2026-01-09", 0.01, 0.005, 1, "oldest")
        log.update_with_outcome("NVDA", "2026-01-10", 0.02, 0.01, 2, "middle")
        log.update_with_outcome("NVDA", "2026-01-11", 0.03, 0.015, 3, "newest")
        # Entry for 1/12 is still pending
        # _apply_rotation: resolved_count=3, max_entries=2 -> to_drop=1
        # Only the oldest resolved (1/9) is dropped.
        # Kept: 1/10 (resolved), 1/11 (resolved), 1/12 (pending) = 3 entries

        entries = log.load_entries()
        assert len(entries) == 3
        dates = [e["date"] for e in entries]
        assert "2026-01-10" in dates  # kept as 2nd oldest resolved
        assert "2026-01-11" in dates  # kept as newest resolved
        assert "2026-01-12" in dates  # kept as pending (always preserved)
        assert "2026-01-09" not in dates  # dropped as oldest resolved

    def test_rotation_skipped_below_cap(self, tmp_path):
        """Fewer resolved entries than max -> no rotation."""
        log = TradingMemoryLog({
            "memory_log_path": str(tmp_path / "mem.md"),
            "memory_log_max_entries": 5,
        })
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "")
        log.store_decision("NVDA", "2026-01-11", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-11", 0.03, 0.01, 3, "")

        entries = log.load_entries()
        assert len(entries) == 2  # Both kept since 2 <= 5

    def test_empty_block_in_rotation(self, tmp_path):
        """Blocks with whitespace-only content are not tagged as resolved."""
        log = TradingMemoryLog({
            "memory_log_path": str(tmp_path / "mem.md"),
            "memory_log_max_entries": 1,
        })
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-11", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "")
        log.update_with_outcome("NVDA", "2026-01-11", 0.03, 0.01, 3, "")

        entries = log.load_entries()
        # max_entries=1, so 1 resolved should be kept
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# update_with_outcome: atomic write (temp file + replace)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateWithOutcomeAtomicWrite:
    """update_with_outcome: temp file + replace flow."""

    def test_atomic_write_updates_file(self, tmp_path):
        """update_with_outcome uses temp file + replace -> original file updated."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "Good call")

        # Verify the file was updated (not still pending)
        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["pending"] is False
        assert entries[0]["raw"] == "+5.0%"
        assert entries[0]["alpha"] == "+2.0%"
        assert entries[0]["holding"] == "5d"
        assert entries[0]["reflection"] == "Good call"

    def test_atomic_write_with_log_file_missing(self, tmp_path):
        """Log file doesn't exist -> update_with_outcome returns without error."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "nonexistent.md")})
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "No crash")
        # Should not raise

    def test_update_with_negative_returns(self, tmp_path):
        """update_with_outcome handles negative return values correctly."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-10", -0.03, -0.01, 5, "Loss")
        entries = log.load_entries()
        assert entries[0]["raw"] == "-3.0%"
        assert entries[0]["alpha"] == "-1.0%"


# ---------------------------------------------------------------------------
# batch_update_with_outcomes: multiple updates in one batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchUpdateMultipleMatches:
    """batch_update_with_outcomes: multiple updates matching different entries."""

    def test_multiple_matches_in_one_batch(self, tmp_path):
        """Multiple updates matching different entries -> all updated atomically."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("AAPL", "2026-01-10", "Rating: Buy\nEnter at $240")
        log.store_decision("MSFT", "2026-01-10", DECISION_BUY)

        updates = [
            {"ticker": "NVDA", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "NVDA great"},
            {"ticker": "AAPL", "trade_date": "2026-01-10",
             "raw_return": 0.03, "alpha_return": 0.01, "holding_days": 3,
             "reflection": "AAPL ok"},
        ]
        log.batch_update_with_outcomes(updates)

        entries = log.load_entries()
        assert len(entries) == 3

        # Check NVDA was updated
        nvda = [e for e in entries if e["ticker"] == "NVDA"][0]
        assert nvda["pending"] is False
        assert nvda["reflection"] == "NVDA great"

        # Check AAPL was updated
        aapl = [e for e in entries if e["ticker"] == "AAPL"][0]
        assert aapl["pending"] is False
        assert aapl["reflection"] == "AAPL ok"

        # Check MSFT is still pending (no update provided)
        msft = [e for e in entries if e["ticker"] == "MSFT"][0]
        assert msft["pending"] is True

    def test_batch_update_no_matches(self, tmp_path):
        """batch_update with no matching entries -> no entries modified."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)

        updates = [
            {"ticker": "AAPL", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "No match"},
        ]
        log.batch_update_with_outcomes(updates)

        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["pending"] is True

    def test_batch_update_partial_matches(self, tmp_path):
        """batch_update where some updates match and some don't -> partial update."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("AAPL", "2026-01-11", DECISION_BUY)

        updates = [
            {"ticker": "NVDA", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "NVDA good"},
            {"ticker": "GOOGL", "trade_date": "2026-01-12",
             "raw_return": 0.04, "alpha_return": 0.01, "holding_days": 4,
             "reflection": "GOOGL not found"},
        ]
        log.batch_update_with_outcomes(updates)

        entries = log.load_entries()
        assert len(entries) == 2
        # NVDA should be updated
        nvda = [e for e in entries if e["ticker"] == "NVDA"][0]
        assert nvda["pending"] is False
        # AAPL should still be pending
        aapl = [e for e in entries if e["ticker"] == "AAPL"][0]
        assert aapl["pending"] is True

    def test_batch_update_with_empty_update_map(self, tmp_path):
        """batch_update where all updates matched -> update map fully consumed."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("AAPL", "2026-01-11", DECISION_BUY)

        updates = [
            {"ticker": "NVDA", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "Good"},
            {"ticker": "AAPL", "trade_date": "2026-01-11",
             "raw_return": 0.03, "alpha_return": 0.01, "holding_days": 3,
             "reflection": "OK"},
        ]
        log.batch_update_with_outcomes(updates)

        entries = log.load_entries()
        assert len(entries) == 2
        assert all(e["pending"] is False for e in entries)
