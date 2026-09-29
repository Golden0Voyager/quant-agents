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
