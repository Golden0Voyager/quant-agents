"""Coverage provenance rendered into completed Markdown reports."""

import pytest

from tradingagents.reporting import render_data_coverage_section, write_report_tree


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
