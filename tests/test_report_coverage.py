"""Coverage provenance rendered into completed Markdown reports."""

import pytest

from tradingagents.dataflows.interface import VendorRouteDiagnostic
from tradingagents.reporting import (
    aggregate_data_reliability,
    render_data_coverage_section,
    write_report_tree,
)


@pytest.mark.unit
def test_aggregate_data_reliability_counts_logical_requests_and_calls():
    coverage = [
        {"method": "confirmed", "status": "ok", "call_count": 3},
        {"method": "fallback", "status": "ok_fallback", "call_count": 2},
        {"method": "empty", "status": "valid_empty", "call_count": 1},
        {"method": "hk_only", "status": "not_applicable", "call_count": 4},
        {"method": "partial", "status": "partial", "call_count": 1},
        {"method": "stale", "status": "stale", "call_count": 1},
        {"method": "no_data", "status": "no_data", "call_count": 1},
        {"method": "unavailable", "status": "unavailable", "call_count": 1},
        {"method": "failed", "status": "failed", "call_count": 1},
    ]

    assert aggregate_data_reliability(coverage) == {
        "confirmed": 1,
        "fallback_success": 1,
        "valid_empty": 1,
        "not_applicable": 1,
        "partial": 2,
        "missing": 3,
        "logical_requests": 9,
        "applicable_requests": 8,
        "call_count": 15,
    }


@pytest.mark.unit
def test_aggregate_data_reliability_excludes_neutral_hk_policy_from_missing():
    reliability = aggregate_data_reliability(
        [
            {"method": "get_news", "status": "valid_empty"},
            {"method": "get_pledge_ratio", "status": "not_applicable"},
        ]
    )

    assert reliability["missing"] == 0
    assert reliability["applicable_requests"] == 1


@pytest.mark.unit
def test_aggregate_data_reliability_consumes_task4_collector_diagnostic():
    diagnostic = VendorRouteDiagnostic(
        method="get_news",
        category="news_data",
        status="ok_fallback",
        attempted_vendors=("yfinance", "akshare"),
        selected_vendor="akshare",
        as_of="2026-08-14",
        reason="fallback selected",
        call_count=3,
    )

    reliability = aggregate_data_reliability([diagnostic])

    assert reliability["fallback_success"] == 1
    assert reliability["logical_requests"] == 1
    assert reliability["call_count"] == 3


@pytest.mark.unit
def test_report_renders_data_coverage_and_impact_levels(tmp_path):
    state = {
        "market_report": "Market report",
        "trade_date": "2026-08-11",
        "data_coverage": [
            {
                "method": "get_stock_data",
                "category": "core_stock_apis",
                "status": "ok",
                "attempted_vendors": ["smartmoney_db", "akshare"],
                "selected_vendor": "akshare",
                "as_of": "2026-08-11",
                "reason": "selected after fallback",
            },
            {
                "method": "get_news",
                "category": "news_data",
                "status": "failed",
                "attempted_vendors": ["yfinance", "akshare"],
                "selected_vendor": None,
                "as_of": "2026-08-11",
                "reason": "JSONDecodeError: empty response",
            },
            {
                "method": "get_sentiment",
                "category": "macro_data",
                "status": "no_data",
                "attempted_vendors": ["smartmoney_db"],
                "selected_vendor": None,
                "as_of": None,
                "reason": "no rows in local archive",
            },
        ],
    }

    report = write_report_tree(state, "002241.SZ", tmp_path).read_text(encoding="utf-8")

    assert "## 数据覆盖与限制" in report
    assert "get_stock_data" in report
    assert "akshare" in report
    assert "JSONDecodeError: empty response" in report
    assert "高" in report
    assert "中" in report
    assert "低" in report
    assert "逻辑请求 3 项" in report
    assert "实际调用 3 次" in report


@pytest.mark.unit
def test_report_without_diagnostics_is_explicit_about_coverage(tmp_path):
    state = {"market_report": "Market report"}

    report = write_report_tree(state, "AAPL", tmp_path).read_text(encoding="utf-8")

    assert "## 数据覆盖与限制" in report
    assert "未记录" in report


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("ok", "可用"),
        ("ok_fallback", "回退可用"),
        ("valid_empty", "确认无事件"),
        ("not_applicable", "不适用"),
        ("partial", "部分可用"),
        ("stale", "过期"),
        ("no_data", "无数据"),
        ("unavailable", "未配置/不可用"),
        ("failed", "失败"),
    ],
)
def test_report_renders_precise_coverage_status_labels(status, label):
    report = render_data_coverage_section(
        [
            {
                "method": "get_news",
                "category": "news_data",
                "status": status,
                "reason": "matrix case",
            }
        ]
    )

    assert f"{label} ({status})" in report
