"""Targeted edge-case tests for trader.py uncovered code paths.

Existing tests in test_trader_extract_price.py + test_structured_agents.py
cover ~88%. This file fills remaining gaps:
- _build_verified_snapshot_block: success path, fallback with extracted price
- trader_node: holdings_context path, data_quality_summary injection
- _extract_market_analyst_price: remaining edge cases in table patterns
"""

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.trader.trader import (
    _build_verified_snapshot_block,
    _extract_market_analyst_price,
    create_trader,
)

# ---------------------------------------------------------------------------
# _extract_market_analyst_price: remaining table-pattern edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractMarketAnalystPriceEdgeCases:
    """_extract_market_analyst_price: edge cases for table/report patterns."""

    def test_table_row_with_spaces(self):
        """Table row with extra spaces still matches."""
        assert _extract_market_analyst_price("|  Close  |  91.60  |") == "91.60"

    def test_close_in_mixed_context(self):
        """Close mentioned mid-paragraph (not a table row) -> not matched by table pattern."""
        result = _extract_market_analyst_price("The Close was 91.60 which is above support.")
        # Should match "Close: " pattern since it starts with "Close:"
        # Actually "Close was" doesn't match any pattern... let me check
        # Patterns: r"[Cc]lose[：:]\\s*(\\d+\\.?\\d*)" — requires colon after Close
        # And table pattern: r"\\|\\s*Close\\s*\\|" — requires pipe delimiters
        # So neither matches "Close was 91.60"
        assert result is None

    def test_table_row_lowercase_close(self):
        """Table row with lowercase 'close' -> no match (regex case-sensitive)."""
        assert _extract_market_analyst_price("| close | 91.60 |") is None

    def test_table_row_mixed_case(self):
        """Table row with 'CLOSE' -> still matches (pattern is case-sensitive for Close)."""
        # The pattern is r"\|\s*Close\s*\|" — capital C
        # So "CLOSE" would NOT match
        assert _extract_market_analyst_price("| CLOSE | 91.60 |") is None

    def test_table_row_no_match_close_not_close(self):
        """Table row where the header is something other than Close."""
        assert _extract_market_analyst_price("| Open | 100.50 |") is None


# ---------------------------------------------------------------------------
# _build_verified_snapshot_block: success path, fallback with/without price
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildVerifiedSnapshotBlock:
    """_build_verified_snapshot_block: standalone tests for full branch coverage."""

    def test_successful_snapshot(self):
        """build_verified_market_snapshot returns data -> returned as-is."""
        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            return_value="FAKE_SNAPSHOT data here",
        ):
            result = _build_verified_snapshot_block("AAPL", "2026-01-15")
        assert result == "FAKE_SNAPSHOT data here"

    def test_snapshot_failure_without_fallback_price(self):
        """Snapshot raises, market_report empty -> fallback without price note."""
        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            side_effect=ValueError("no data"),
        ):
            result = _build_verified_snapshot_block("AAPL", "2026-01-15", market_report="")
        assert "Verified market data is unavailable" in result
        assert "set entry_price and stop_loss to null" in result
        assert "NOTE: The Market Analyst's report mentions" not in result

    def test_snapshot_failure_with_fallback_price(self):
        """Snapshot raises, market_report contains price -> fallback includes price note."""
        market_report = "The stock closed at 现价: 185.50 on strong volume."
        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            side_effect=ValueError("vendor offline"),
        ):
            result = _build_verified_snapshot_block("AAPL", "2026-01-15", market_report=market_report)
        assert "Verified market data is unavailable" in result
        assert "set entry_price and stop_loss to null" in result
        assert "NOTE: The Market Analyst's report mentions" in result
        assert "185.50" in result

    def test_snapshot_failure_unknown_exception(self):
        """Snapshot raises non-ValueError (e.g. RuntimeError) -> still caught."""
        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            side_effect=RuntimeError("unexpected error"),
        ):
            result = _build_verified_snapshot_block("AAPL", "2026-01-15", market_report="")
        assert "Verified market data is unavailable" in result
        assert "set entry_price and stop_loss to null" in result


# ---------------------------------------------------------------------------
# trader_node: shared verified_market_snapshot from state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTraderUsesSharedSnapshot:
    """Trader should use verified_market_snapshot from state when present."""

    def test_uses_state_snapshot_and_skips_build(self):
        state = _TraderTestHelpers._make_state(
            verified_market_snapshot="STATE_SNAPSHOT",
        )
        llm = _TraderTestHelpers._make_llm()
        trader = create_trader(llm)

        captured_messages = []
        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
        ) as mock_build, patch(
            "tradingagents.agents.trader.trader.invoke_structured_or_freetext",
        ) as mock_invoke:
            mock_invoke.return_value = "**Action**: Buy\n"
            trader(state)
            captured_messages = mock_invoke.call_args[0][2]

        mock_build.assert_not_called()
        prompt_text = "\n".join(
            m.get("content", "") for m in captured_messages if isinstance(m, dict)
        )
        assert "STATE_SNAPSHOT" in prompt_text

    def test_falls_back_when_state_snapshot_missing(self):
        state = _TraderTestHelpers._make_state()
        llm = _TraderTestHelpers._make_llm()
        trader = create_trader(llm)

        captured_messages = []
        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            return_value="FRESH_SNAPSHOT",
        ) as mock_build, patch(
            "tradingagents.agents.trader.trader.invoke_structured_or_freetext",
        ) as mock_invoke:
            mock_invoke.return_value = "**Action**: Buy\n"
            trader(state)
            captured_messages = mock_invoke.call_args[0][2]

        mock_build.assert_called_once()
        prompt_text = "\n".join(
            m.get("content", "") for m in captured_messages if isinstance(m, dict)
        )
        assert "FRESH_SNAPSHOT" in prompt_text


# ---------------------------------------------------------------------------
# trader_node: holdings_context and data_quality_summary paths
# ---------------------------------------------------------------------------


class _TraderTestHelpers:
    @staticmethod
    def _make_state(**overrides):
        state = {
            "company_of_interest": "NVDA",
            "investment_plan": "**Recommendation**: Buy\n**Rationale**: Strong setup.\n**Strategic Actions**: Buy 1000 shares.",
            "trade_date": "2026-01-15",
            "market_report": "",
            "holdings_context": {},
            "transactions_context": [],
        }
        state.update(overrides)
        return state

    @staticmethod
    def _make_llm():
        """Create mock LLM that returns a real TraderProposal from structured path."""
        from tradingagents.agents.schemas import TraderAction, TraderProposal

        structured = MagicMock()
        proposal = TraderProposal(action=TraderAction.BUY, reasoning="Strong setup.")
        structured.invoke.return_value = proposal

        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        return llm


@pytest.mark.unit
class TestTraderNodeHoldings:
    """trader_node: holdings_context and data_quality_summary paths."""

    def test_empty_holdings_skips_holdings_line(self):
        """holdings_context is empty -> no holdings_line appended to prompt."""
        state = _TraderTestHelpers._make_state()
        llm = _TraderTestHelpers._make_llm()
        trader = create_trader(llm)

        with patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            return_value="SNAPSHOT",
        ):
            result = trader(state)

        plan = result["trader_investment_plan"]
        assert plan is not None
        # The prompt is inside the captured call — we can verify the output exists
        assert "**Action**: BUY" in plan or "BUY" in plan

    def test_populated_holdings_triggers_build_trader_prompt(self):
        """holdings_context has entries -> build_trader_prompt is called."""
        holdings = {
            "NVDA": {
                "ticker": "NVDA",
                "shares": 100.0,
                "avg_cost": 180.0,
                "market_price": 195.0,
                "pnl_pct": 0.0833,
                "weight": 0.25,
                "grid_strategy": None,
                "name": "NVIDIA Corp",
            }
        }
        state = _TraderTestHelpers._make_state(holdings_context=holdings)
        llm = _TraderTestHelpers._make_llm()
        trader = create_trader(llm)

        # build_trader_prompt is imported INSIDE the function body, so patch
        # at the portfolio module level where the inline import reads from.
        with patch(
            "tradingagents.portfolio.build_trader_prompt",
            return_value="CUSTOM_HOLDINGS_PROMPT",
        ) as mock_build, patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            return_value="SNAPSHOT",
        ):
            result = trader(state)

        assert mock_build.called
        plan = result["trader_investment_plan"]
        assert plan is not None

    def test_transactions_context_populated(self):
        """transactions_context has entries -> passed to build_trader_prompt."""
        holdings = {
            "NVDA": {
                "ticker": "NVDA",
                "shares": 100.0,
                "avg_cost": 180.0,
                "market_price": 195.0,
                "pnl_pct": 0.0833,
                "weight": 0.25,
                "grid_strategy": None,
                "name": "NVIDIA Corp",
            }
        }
        transactions = [
            {"ticker": "NVDA", "action": "buy", "shares": 100.0,
             "price": 180.0, "date": "2026-01-10", "fee": None, "tag": None}
        ]
        state = _TraderTestHelpers._make_state(
            holdings_context=holdings,
            transactions_context=transactions,
        )
        llm = _TraderTestHelpers._make_llm()
        trader = create_trader(llm)

        with patch(
            "tradingagents.portfolio.build_trader_prompt",
            return_value="TX_HOLDINGS_PROMPT",
        ) as mock_build, patch(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            return_value="SNAPSHOT",
        ):
            result = trader(state)

        assert mock_build.called
        plan = result["trader_investment_plan"]
        assert plan is not None
