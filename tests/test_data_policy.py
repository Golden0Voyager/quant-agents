from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tradingagents.dataflows.data_policy import (
    ToolPolicy,
    UnknownToolPolicyError,
    legacy_policy_for,
    normalized_dates,
    policy_for,
)
from tradingagents.dataflows.runtime_context import RuntimeDataContext
from tradingagents.market_context import AnalysisDates


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (
            "get_limit_up_down",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG"}),
                date_policy="market_session",
                empty_semantics="coverage_gap",
                impact="high",
                allowed_vendors=("smartmoney_db",),
            ),
        ),
        (
            "get_restricted_release",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG"}),
                date_policy="calendar_window",
                empty_semantics="confirmed_empty",
                impact="medium",
                allowed_vendors=("smartmoney_db", "akshare"),
            ),
        ),
        (
            "get_news",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG", "XHKG", "XNYS", "CRYPTO"}),
                date_policy="calendar_window",
                empty_semantics="coverage_gap",
                impact="high",
                allowed_vendors=("smartmoney_db", "alpha_vantage", "yfinance", "akshare"),
            ),
        ),
        (
            "get_company_announcements",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG", "XHKG"}),
                date_policy="calendar_window",
                empty_semantics="confirmed_empty",
                impact="high",
                allowed_vendors=("smartmoney_db", "cninfo", "akshare", "tushare"),
            ),
        ),
        (
            "get_northbound_hold",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG"}),
                date_policy="latest_snapshot",
                empty_semantics="coverage_gap",
                impact="medium",
                allowed_vendors=("smartmoney_db", "akshare"),
            ),
        ),
        (
            "get_pledge_ratio",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG"}),
                date_policy="latest_snapshot",
                empty_semantics="coverage_gap",
                impact="medium",
                allowed_vendors=("smartmoney_db", "akshare", "tushare"),
            ),
        ),
    ],
)
def test_policy_for_returns_the_centralized_immutable_policy(method, expected):
    actual = policy_for(method)

    assert actual == expected
    with pytest.raises(FrozenInstanceError):
        actual.impact = "low"


@pytest.mark.unit
def test_policy_for_unknown_method_is_strict():
    with pytest.raises(UnknownToolPolicyError, match="missing_tool"):
        policy_for("missing_tool")


@pytest.mark.unit
def test_legacy_policy_is_an_explicit_warned_compatibility_opt_in(caplog):
    with caplog.at_level("WARNING"):
        policy = legacy_policy_for("missing_tool")

    assert policy == ToolPolicy(
        applicable_markets=frozenset({"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"}),
        date_policy="calendar_window",
        empty_semantics="coverage_gap",
        impact="low",
        allowed_vendors=(),
    )
    assert "legacy data policy" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("get_limit_up_down", ("2026-08-14", None)),
        ("get_northbound_hold", ("2026-08-14", None)),
        ("get_news", ("2026-08-16", "2026-08-16")),
    ],
)
def test_normalized_dates_returns_policy_anchors_not_rewritten_arguments(method, expected):
    context = RuntimeDataContext(
        ticker="600519.SS",
        market="XSHG",
        dates=AnalysisDates("2026-08-16", "2026-08-14", "2026-08-16"),
        policy_version="v1",
    )

    assert normalized_dates(method, context) == expected
