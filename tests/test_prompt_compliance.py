"""Prompt compliance tests — the analyst-layer contract in code.

TradingAgents-CN parity (tests/test_*_prompt_compliance.py): prompt quality
is enforced by tests, not by memory of who wrote what. These guards pin the
batch-1 prompt upgrades:

- every analyst system message carries the shared observation-mode boundary
  (no trade directives / position sizing / price targets at the analyst layer)
- the fundamentals analyst carries the A-share accounting caliber rules
  (cumulative reporting periods, snapshot-date discipline)
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit

from tradingagents.agents.analysts import (
    fundamentals_analyst,
    governance_analyst,
    industry_analyst,
    market_analyst,
    news_analyst,
    sentiment_analyst,
)
from tradingagents.agents.utils.agent_utils import (
    observation_mode_instruction,
)

ANALYST_MODULES = [
    market_analyst,
    sentiment_analyst,
    news_analyst,
    fundamentals_analyst,
    governance_analyst,
    industry_analyst,
]


# --- observation-mode shared block ------------------------------------------

def test_observation_mode_forbids_decision_vocabulary():
    block = observation_mode_instruction()
    for phrase in (
        "Do NOT output buy/sell/hold",
        "position",
        "price targets",
        "stop-losses",
        "take-profits",
        "expected-return",
    ):
        assert phrase in block


def test_observation_mode_allows_evidence_based_observations():
    block = observation_mode_instruction()
    assert "You MAY state valuation levels" in block
    assert "never from model memory" in block


@pytest.mark.parametrize("module", ANALYST_MODULES, ids=lambda m: m.__name__)
def test_every_analyst_injects_observation_mode(module):
    src = inspect.getsource(module)
    assert "observation_mode_instruction()" in src, (
        f"{module.__name__} does not inject the observation-mode boundary"
    )


def test_sentiment_system_message_contains_observation_mode():
    msg = sentiment_analyst._build_system_message(
        ticker="AAPL",
        company_name="Apple",
        start_date="2026-09-22",
        end_date="2026-09-29",
        news_block="n",
        hot_rank_block="h",
        guba_block="g",
        hot_keywords_block="k",
        anomaly_block="a",
        stocktwits_block="s",
        reddit_block="r",
    )
    assert "## Observation Mode (analyst boundary)" in msg


# --- fundamentals A-share accounting caliber ---------------------------------

def test_fundamentals_carries_accounting_caliber_rules():
    src = inspect.getsource(fundamentals_analyst)
    for phrase in (
        "A-Share Accounting Caliber",
        "CUMULATIVE year-to-date",
        "报告期累计口径",
        "single-quarter",
        "ROE",
        "SNAPSHOT",
        "snapshot date",
        "previous trading day",
        "not applicable",
        "fair-value",
        "price targets",
    ):
        assert phrase in src, f"missing caliber rule: {phrase}"


def test_fundamentals_duplicate_tool_registration_removed():
    """A stray duplicate get_dividend_summary entry once sat in the tools list."""
    src = inspect.getsource(fundamentals_analyst.create_fundamentals_analyst)
    assert src.count("        get_dividend_summary,\n") == 1


# --- decision-layer contracts ------------------------------------------------

from tradingagents.agents.managers import portfolio_manager, research_manager
from tradingagents.agents.researchers import bear_researcher, bull_researcher
from tradingagents.agents.trader import trader as trader_module


def test_research_manager_contract_pinned():
    """The RM prompt must keep: temporal integrity, rating scale, the bearish
    calibration note, and explicit signal-weight accounting."""
    src = inspect.getsource(research_manager)
    for phrase in (
        "Temporal integrity",
        "Rating Scale",
        "Calibration note",
        "signal_weights",
        "key_assumptions",
    ):
        assert phrase in src, f"research_manager lost contract phrase: {phrase}"


def test_trader_contract_pinned():
    """The Trader prompt/code must keep: verified-snapshot grounding, the
    null-instead-of-guessing rule, and the long-only stop validation."""
    src = inspect.getsource(trader_module)
    for phrase in (
        "do not estimate or recall a price",
        "validate_trader_proposal",
        "long-only",
        "entry_price and stop_loss to null",
    ):
        assert phrase in src, f"trader lost contract phrase: {phrase}"


def test_trader_rejects_non_protective_stop():
    """A stop at/above entry offers no protection for a long position and
    must be cleared to null (deterministic post-check)."""
    from tradingagents.agents.schemas import TraderAction, TraderProposal

    proposal = TraderProposal(
        action=TraderAction.BUY,
        reasoning="test",
        entry_price=10.0,
        stop_loss=11.5,  # above entry — a take-profit level, not a stop
    )
    fixed, note = trader_module.validate_trader_proposal(proposal)
    assert fixed.stop_loss is None
    assert note is not None and "rejected" in note


def test_portfolio_manager_contract_pinned():
    """The PM prompt must keep: rating scale, verified-snapshot grounding
    with the tolerance rule, and honest data-source accounting."""
    src = inspect.getsource(portfolio_manager)
    for phrase in (
        "Rating Scale",
        "Verified Market Snapshot",
        "25%",
        "data_sources",
        "Do not claim a source you did not consult",
    ):
        assert phrase in src, f"portfolio_manager lost contract phrase: {phrase}"


# --- bull/bear argument-quality rules (batch 2) -------------------------------

@pytest.mark.parametrize(
    "module", [bull_researcher, bear_researcher], ids=lambda m: m.__name__
)
def test_researchers_carry_argument_quality_rules(module):
    """Bull and bear must carry the good/bad example pairs and the four-layer
    valuation framework injected in batch 2."""
    src = inspect.getsource(module)
    for phrase in (
        "Argument quality rules",
        "Scenario framing, not prophecy",
        "You may write",
        "Do not write",
        "never to model memory",
        "absolute (earnings/cash-flow based)",
        "relative (vs peers)",
        "historical percentile",
        "market-implied expectations",
    ):
        assert phrase in src, f"{module.__name__} lost argument-quality phrase: {phrase}"


def test_bull_and_bar_examples_are_distinct():
    """The two sides must keep their own tailored examples (a copy-paste
    accident would give both sides the same 'You may write' lines)."""
    bull_src = inspect.getsource(bull_researcher)
    bear_src = inspect.getsource(bear_researcher)
    assert "order backlog grew 34% YoY" in bull_src
    assert "backlog conversion slowed for two consecutive quarters" in bear_src
    assert "order backlog grew 34% YoY" not in bear_src
    assert "backlog conversion slowed for two consecutive quarters" not in bull_src
