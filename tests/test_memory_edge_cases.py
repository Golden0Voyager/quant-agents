"""Targeted edge-case tests for uncovered code paths in TradingMemoryLog.

Existing tests in test_memory_log.py cover ~80%. This file fills the remaining
gaps: _parse_entry failures, _format_full without reflection,
_format_reflection_no_reflection truncation, batch_update edge cases,
update_with_outcome no-match, and _apply_rotation edge values.
"""

import pytest

from tradingagents.agents.utils.memory import TradingMemoryLog

_SEP = TradingMemoryLog._SEPARATOR
DECISION_BUY = "Rating: Buy\nEnter at $189-192, 6% portfolio cap."


# ---------------------------------------------------------------------------
# _parse_entry: edge cases for entries that fail parsing
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestParseEntryEdgeCases:
    """_parse_entry() returns None for malformed entries."""

    def test_parse_entry_empty_string(self):
        """Empty raw string → None."""
        log = TradingMemoryLog(config=None)
        assert log._parse_entry("") is None

    def test_parse_entry_whitespace_only(self):
        """Whitespace-only string → None (splitlines returns [])."""
        log = TradingMemoryLog(config=None)
        assert log._parse_entry("   \n  \n  ") is None

    def test_parse_entry_no_brackets(self):
        """Tag line missing leading [ → None."""
        log = TradingMemoryLog(config=None)
        raw = "2026-01-10 | NVDA | Buy | pending]\nDECISION:\nBuy."
        assert log._parse_entry(raw) is None

    def test_parse_entry_no_closing_bracket(self):
        """Tag line missing trailing ] → None."""
        log = TradingMemoryLog(config=None)
        raw = "[2026-01-10 | NVDA | Buy | pending\nDECISION:\nBuy."
        assert log._parse_entry(raw) is None

    def test_parse_entry_too_few_fields(self):
        """Tag with < 4 pipe-separated fields → None."""
        log = TradingMemoryLog(config=None)
        raw = "[2026-01-10 | NVDA | Buy]\nDECISION:\nBuy."
        assert log._parse_entry(raw) is None

    def test_parse_entry_no_decision_section(self):
        """Entry with a valid tag but no DECISION: section → decision field is empty string."""
        log = TradingMemoryLog(config=None)
        raw = "[2026-01-10 | NVDA | Buy | pending]"
        parsed = log._parse_entry(raw)
        assert parsed is not None
        assert parsed["decision"] == ""
        assert parsed["reflection"] == ""


# ---------------------------------------------------------------------------
# _format_full: without reflection
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFormatFullEdgeCases:

    def test_format_full_without_reflection(self):
        """Entry dict with empty reflection → no REFLECTION section in output."""
        log = TradingMemoryLog(config=None)
        entry = {
            "date": "2026-01-10",
            "ticker": "NVDA",
            "rating": "Buy",
            "raw": "+5.0%",
            "alpha": "+2.0%",
            "holding": "5d",
            "decision": "Buy NVDA at $190.",
            "reflection": "",
        }
        result = log._format_full(entry)
        assert "REFLECTION:" not in result
        assert "DECISION:\nBuy NVDA at $190." in result
        assert "[2026-01-10 | NVDA | Buy | +5.0% | +2.0% | 5d]" in result

    def test_format_full_with_reflection(self):
        """Entry dict with reflection → includes REFLECTION section."""
        log = TradingMemoryLog(config=None)
        entry = {
            "date": "2026-01-10",
            "ticker": "NVDA",
            "rating": "Buy",
            "raw": "+5.0%",
            "alpha": "+2.0%",
            "holding": "5d",
            "decision": "Buy NVDA at $190.",
            "reflection": "Correct call.",
        }
        result = log._format_full(entry)
        assert "REFLECTION:\nCorrect call." in result

    def test_format_full_raw_none_shows_n_a(self):
        """When raw/alpha/holding are None → shows 'n/a' in tag."""
        log = TradingMemoryLog(config=None)
        entry = {
            "date": "2026-01-10",
            "ticker": "NVDA",
            "rating": "Buy",
            "raw": None,
            "alpha": None,
            "holding": None,
            "decision": "Buy.",
            "reflection": "",
        }
        result = log._format_full(entry)
        assert "n/a" in result


# ---------------------------------------------------------------------------
# _format_reflection_only: paths without reflection + truncation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFormatReflectionOnlyEdgeCases:

    def test_reflection_only_no_reflection_short_decision(self):
        """Cross-ticker entry without reflection + short decision."""
        log = TradingMemoryLog(config=None)
        entry = {
            "date": "2026-01-10",
            "ticker": "AAPL",
            "rating": "Buy",
            "raw": "+3.0%",
            "alpha": None,
            "holding": None,
            "decision": "Buy AAPL on services growth.",
            "reflection": "",
        }
        result = log._format_reflection_only(entry)
        # Should show the decision text (not truncated)
        assert "Buy AAPL on services growth." in result
        assert "..." not in result

    def test_reflection_only_no_reflection_truncation(self):
        """Cross-ticker entry without reflection + decision > 300 chars → truncated with '...'."""
        log = TradingMemoryLog(config=None)
        long_decision = "Word. " * 80  # ~480 chars
        entry = {
            "date": "2026-01-10",
            "ticker": "AAPL",
            "rating": "Hold",
            "raw": "+0.5%",
            "alpha": None,
            "holding": None,
            "decision": long_decision,
            "reflection": "",
        }
        result = log._format_reflection_only(entry)
        assert result.endswith("...")
        assert len(result.split("\n")[1]) == 303  # 300 chars + "..."

    def test_reflection_only_with_reflection(self):
        """Cross-ticker entry with reflection → shows reflection text only."""
        log = TradingMemoryLog(config=None)
        entry = {
            "date": "2026-01-10",
            "ticker": "AAPL",
            "rating": "Sell",
            "raw": "-2.0%",
            "alpha": None,
            "holding": None,
            "decision": "Sell AAPL.",
            "reflection": "Correct move.",
        }
        result = log._format_reflection_only(entry)
        assert "Correct move." in result
        assert "Sell AAPL." not in result


# ---------------------------------------------------------------------------
# batch_update_with_outcomes: edge cases
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBatchUpdateEdgeCases:

    def test_batch_update_empty_updates(self, tmp_path):
        """Empty updates list → no-op (no file written, no crash)."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.batch_update_with_outcomes([])
        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["pending"] is True

    def test_batch_update_file_not_exists(self, tmp_path):
        """Log file doesn't exist → no-op, no crash."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "nonexistent.md")})
        log.batch_update_with_outcomes([
            {"ticker": "NVDA", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "Should not crash."},
        ])
        assert not (tmp_path / "nonexistent.md").exists()

    def test_batch_update_no_matching_pending(self, tmp_path):
        """No pending entry matches the update → no entries are modified."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.batch_update_with_outcomes([
            {"ticker": "AAPL", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "Should not match."},
        ])
        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["pending"] is True
        assert entries[0]["ticker"] == "NVDA"

    def test_batch_update_no_log_path(self):
        """No log path configured → no-op."""
        log = TradingMemoryLog(config=None)
        log.batch_update_with_outcomes([
            {"ticker": "NVDA", "trade_date": "2026-01-10",
             "raw_return": 0.05, "alpha_return": 0.02, "holding_days": 5,
             "reflection": "No-op."},
        ])
        assert log.load_entries() == []


# ---------------------------------------------------------------------------
# update_with_outcome: edge cases
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateWithOutcomeEdgeCases:

    def test_update_no_matching_pending(self, tmp_path):
        """No pending entry matches ticker/date → entry stays Pending."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-11", 0.05, 0.02, 5, "Wrong date.")
        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["pending"] is True

    def test_update_file_not_exists(self, tmp_path):
        """Log file doesn't exist → no-op, no crash."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "nonexistent.md")})
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "No crash.")
        assert not (tmp_path / "nonexistent.md").exists()


# ---------------------------------------------------------------------------
# _apply_rotation: edge values for max_entries
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestApplyRotationEdgeCases:

    def test_rotation_zero_max_entries(self, tmp_path):
        """max_entries=0 → rotation disabled (keeps all entries)."""
        log = TradingMemoryLog({
            "memory_log_path": str(tmp_path / "mem.md"),
            "memory_log_max_entries": 0,
        })
        for i in range(5):
            log.store_decision("NVDA", f"2026-01-{i+1:02d}", DECISION_BUY)
        entries = log.load_entries()
        assert len(entries) == 5

    def test_rotation_negative_max_entries(self, tmp_path):
        """max_entries=-1 → rotation disabled (keeps all entries)."""
        log = TradingMemoryLog({
            "memory_log_path": str(tmp_path / "mem.md"),
            "memory_log_max_entries": -1,
        })
        for i in range(5):
            log.store_decision("NVDA", f"2026-01-{i+1:02d}", DECISION_BUY)
        entries = log.load_entries()
        assert len(entries) == 5


# ---------------------------------------------------------------------------
# load_entries: silently filters parsing failures
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLoadEntriesEdgeCases:

    def test_load_entries_skips_parsing_failures(self, tmp_path):
        """Entries with malformed tags are silently skipped."""
        path = tmp_path / "mem.md"
        log = TradingMemoryLog({"memory_log_path": str(path)})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)

        # Append a malformed entry directly to the file (no tag brackets)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"Garbage entry with no brackets{_SEP}")

        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["ticker"] == "NVDA"

    def test_load_entries_skips_empty_blocks(self, tmp_path):
        """Empty blocks between separators are skipped during parse."""
        path = tmp_path / "mem.md"
        log = TradingMemoryLog({"memory_log_path": str(path)})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)

        # Append two consecutive separators creating an empty block
        with open(path, "a", encoding="utf-8") as f:
            f.write(_SEP)

        entries = log.load_entries()
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# get_past_context: same-ticker entry without reflection
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetPastContextEdgeCases:

    def test_same_ticker_no_reflection(self, tmp_path):
        """Same-ticker entry without reflection → only tag + DECISION shown."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.update_with_outcome("NVDA", "2026-01-10", 0.05, 0.02, 5, "")
        ctx = log.get_past_context("NVDA")
        assert "Past analyses of NVDA" in ctx
        assert "DECISION:" in ctx
        assert "REFLECTION:" not in ctx

    def test_get_past_context_all_pending(self, tmp_path):
        """All entries are pending → returns empty string."""
        log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
        log.store_decision("NVDA", "2026-01-10", DECISION_BUY)
        log.store_decision("NVDA", "2026-01-11", DECISION_BUY)
        ctx = log.get_past_context("NVDA")
        assert ctx == ""
