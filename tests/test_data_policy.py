from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tradingagents.dataflows.data_policy import (
    ToolPolicy,
    UnknownToolPolicyError,
    is_applicable,
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
                allowed_vendors=("akshare",),
            ),
        ),
        (
            "get_stock_data",
            ToolPolicy(
                applicable_markets=frozenset(
                    {"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"}
                ),
                date_policy="calendar_window",
                empty_semantics="coverage_gap",
                impact="high",
                allowed_vendors=(
                    "smartmoney_db",
                    "quant_db_global",
                    "alpha_vantage",
                    "yfinance",
                    "akshare",
                ),
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
                applicable_markets=frozenset({"XSHG"}),
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
        *[
            (
                method,
                ToolPolicy(
                    applicable_markets=frozenset(
                        {"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"}
                    ),
                    date_policy="latest_snapshot",
                    empty_semantics="coverage_gap",
                    impact="high",
                    allowed_vendors=(
                        "smartmoney_db",
                        "alpha_vantage",
                        "yfinance",
                        "akshare",
                    ),
                ),
            )
            for method in (
                "get_fundamentals",
                "get_balance_sheet",
                "get_cashflow",
                "get_income_statement",
                "get_indicators",
            )
        ],
        (
            "get_global_news",
            ToolPolicy(
                applicable_markets=frozenset(
                    {"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"}
                ),
                date_policy="calendar_window",
                empty_semantics="coverage_gap",
                impact="high",
                allowed_vendors=("yfinance", "alpha_vantage"),
            ),
        ),
        (
            "get_insider_transactions",
            ToolPolicy(
                applicable_markets=frozenset(
                    {"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"}
                ),
                date_policy="latest_snapshot",
                empty_semantics="confirmed_empty",
                impact="medium",
                allowed_vendors=(
                    "smartmoney_db",
                    "alpha_vantage",
                    "yfinance",
                    "akshare",
                ),
            ),
        ),
        (
            "get_institutional_holdings",
            ToolPolicy(
                applicable_markets=frozenset({"XSHG"}),
                date_policy="latest_snapshot",
                empty_semantics="coverage_gap",
                impact="medium",
                allowed_vendors=("smartmoney_db", "akshare"),
            ),
        ),
        (
            "get_macro_indicators",
            ToolPolicy(
                applicable_markets=frozenset(
                    {"XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"}
                ),
                date_policy="latest_snapshot",
                empty_semantics="coverage_gap",
                impact="medium",
                allowed_vendors=("smartmoney_db", "akshare", "fred"),
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
def test_every_routed_method_has_a_registered_policy_with_implemented_vendors():
    """Guard against registry drift: every routed method must be registered,
    and a policy may only name vendors that actually implement the method."""
    from tradingagents.dataflows.interface import VENDOR_METHODS

    for method, vendors in VENDOR_METHODS.items():
        policy = policy_for(method)  # raises UnknownToolPolicyError if missing
        unimplemented = set(policy.allowed_vendors) - set(vendors)
        assert not unimplemented, (
            f"{method}: policy allows vendors with no implementation: "
            f"{sorted(unimplemented)}"
        )


@pytest.mark.unit
def test_company_announcements_is_inapplicable_to_hk_until_hkex_is_registered():
    assert is_applicable("get_company_announcements", "XSHG") is True
    assert is_applicable("get_company_announcements", "XHKG") is False


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
