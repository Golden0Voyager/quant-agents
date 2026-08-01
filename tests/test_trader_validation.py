"""Tests for the Trader's stop-loss direction validation.

A-share framework is long-only: a stop-loss must protect a long position,
so it has to sit BELOW the entry price. The model frequently emits
``entry + 1.5*ATR`` for Sell/Underweight proposals (a level ABOVE the
entry that offers no protection), and the Portfolio Manager then corrects
it downstream. This validation removes such misdirected stops at the
source so the wrong number never reaches the report or the batch summary.
"""

from __future__ import annotations

import pytest

from tradingagents.agents.schemas import TraderAction, TraderProposal
from tradingagents.agents.trader.trader import validate_trader_proposal


@pytest.mark.unit
class TestValidateTraderProposalStopDirection:
    def test_sell_stop_above_entry_removed(self):
        """Regression: 比亚迪 002594 — Trader gave stop=100.23 > entry=95.77
        for a Sell proposal. Such a stop is removed (nulled)."""
        p = TraderProposal(
            action=TraderAction.SELL, reasoning="x", entry_price=95.77, stop_loss=100.23
        )
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss is None
        assert note is not None

    def test_sell_stop_below_entry_kept(self):
        """A stop below the entry for a Sell proposal is a legitimate
        protective stop for the remaining position — kept untouched."""
        p = TraderProposal(
            action=TraderAction.SELL, reasoning="x", entry_price=95.77, stop_loss=88.57
        )
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss == 88.57
        assert note is None

    def test_buy_stop_above_entry_removed(self):
        p = TraderProposal(
            action=TraderAction.BUY, reasoning="x", entry_price=100.0, stop_loss=105.0
        )
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss is None
        assert note is not None

    def test_buy_stop_below_entry_kept(self):
        p = TraderProposal(
            action=TraderAction.BUY, reasoning="x", entry_price=100.0, stop_loss=95.0
        )
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss == 95.0
        assert note is None

    def test_hold_without_levels_untouched(self):
        p = TraderProposal(action=TraderAction.HOLD, reasoning="x")
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss is None
        assert note is None

    def test_entry_none_stop_untouched(self):
        """No entry price → cannot validate direction; leave the stop alone."""
        p = TraderProposal(action=TraderAction.SELL, reasoning="x", stop_loss=100.0)
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss == 100.0
        assert note is None

    def test_stop_never_below_zero(self):
        p = TraderProposal(
            action=TraderAction.SELL, reasoning="x", entry_price=5.0, stop_loss=7.0
        )
        fixed, note = validate_trader_proposal(p)
        assert fixed.stop_loss is None
        assert note is not None
