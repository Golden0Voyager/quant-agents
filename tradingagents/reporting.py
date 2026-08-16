"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a run's per-section markdown (analysts, research, trading, risk,
portfolio) plus a consolidated ``complete_report.md`` under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

NON_DEGRADED_COVERAGE_STATUSES = frozenset(
    {"ok", "ok_fallback", "valid_empty", "not_applicable"}
)
DEGRADED_COVERAGE_STATUSES = frozenset(
    {"partial", "stale", "no_data", "unavailable", "failed"}
)
DATA_RELIABILITY_KEYS = (
    "confirmed",
    "fallback_success",
    "valid_empty",
    "not_applicable",
    "partial",
    "missing",
)
_RELIABILITY_STATUS_BUCKETS = {
    "ok": "confirmed",
    "ok_fallback": "fallback_success",
    "valid_empty": "valid_empty",
    "not_applicable": "not_applicable",
    "partial": "partial",
    "stale": "partial",
    "no_data": "missing",
    "unavailable": "missing",
    "failed": "missing",
}


def is_data_coverage_degraded(status: str) -> bool:
    """Return whether a route status represents degraded evidence."""
    return status in DEGRADED_COVERAGE_STATUSES


def _coverage_value(item, key: str, default=""):
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def aggregate_data_reliability(data_coverage) -> dict[str, int]:
    """Aggregate normalized logical route diagnostics into stable counts.

    The route collector has already collapsed equal normalized requests into
    one row and records repeated physical observations in ``call_count``.
    Therefore each input row contributes one logical request while call counts
    remain available for duplicate-call auditing.
    """
    result = dict.fromkeys(DATA_RELIABILITY_KEYS, 0)
    result.update(
        logical_requests=0,
        applicable_requests=0,
        call_count=0,
    )
    for item in data_coverage or ():
        status = str(_coverage_value(item, "status", "")).strip().lower()
        bucket = _RELIABILITY_STATUS_BUCKETS.get(status, "missing")
        result[bucket] += 1
        result["logical_requests"] += 1
        if status != "not_applicable":
            result["applicable_requests"] += 1
        try:
            call_count = int(_coverage_value(item, "call_count", 1))
        except (TypeError, ValueError):
            call_count = 1
        result["call_count"] += max(1, call_count)
    return result


def _coverage_impact(category: str, method: str) -> str:
    if category in {"macro_data", "research_opinion"} or "sentiment" in method:
        return "低"
    if category in {"news_data", "governance_risk"}:
        return "中"
    return "高"


def render_data_coverage_section(data_coverage) -> str:
    """Render route provenance and limitations as a stable report section."""
    lines = ["## 数据覆盖与限制", ""]
    if not data_coverage:
        lines.append("本次运行未记录数据源降级或无数据项；具体覆盖范围仍以各分析师原始数据为准。")
        return "\n".join(lines)

    reliability = aggregate_data_reliability(data_coverage)
    lines.extend(
        [
            (
                f"逻辑请求 {reliability['logical_requests']} 项（适用 "
                f"{reliability['applicable_requests']} 项），实际调用 "
                f"{reliability['call_count']} 次；缺失 {reliability['missing']} 项，"
                f"部分可用 {reliability['partial']} 项。"
            ),
            "",
        ]
    )

    lines.extend(
        [
            "| 数据项 | 状态 | 来源 | 截止日期 | 尝试数据源 | 原因 | 影响 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    status_labels = {
        "ok": "可用",
        "ok_fallback": "回退可用",
        "valid_empty": "确认无事件",
        "not_applicable": "不适用",
        "partial": "部分可用",
        "no_data": "无数据",
        "failed": "失败",
        "stale": "过期",
        "unavailable": "未配置/不可用",
    }
    for item in data_coverage:
        method = str(_coverage_value(item, "method", "unknown"))
        category = str(_coverage_value(item, "category", "unknown"))
        status = str(_coverage_value(item, "status", "unknown"))
        source = _coverage_value(item, "selected_vendor") or "—"
        as_of = _coverage_value(item, "as_of") or "—"
        attempted = _coverage_value(item, "attempted_vendors", ())
        attempted_text = (
            attempted
            if isinstance(attempted, str)
            else " → ".join(str(v) for v in attempted) or "—"
        )
        reason = str(_coverage_value(item, "reason", "未提供原因"))
        cells = [
            method,
            f"{status_labels.get(status, status)} ({status})",
            str(source),
            str(as_of),
            attempted_text,
            reason,
            _coverage_impact(category, method),
        ]
        lines.append("| " + " | ".join(value.replace("|", "\\|") for value in cells) + " |")
    lines.append("")
    lines.append("影响等级：高=核心价格/基本面输入，中=新闻/治理结论，低=可选增强信息。")
    return "\n".join(lines)


def write_report_tree(final_state: dict, ticker: str, save_path) -> Path:
    """Save a completed run's reports to ``save_path``; return the complete-report path."""
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)

    # Global company-name sanitization: correct LLM hallucinations before
    # any report text hits the disk.  Operates on final_state in-place.
    # Imported here (not at module level) to avoid circular imports between
    # reporting.py and agent_utils (agent_utils -> trading_graph -> reporting).
    from tradingagents.agents.utils.agent_utils import sanitize_company_name_in_report

    company_name = final_state.get("company_name", "")
    _report_keys = [
        "market_report", "sentiment_report", "news_report", "fundamentals_report",
        "governance_report", "industry_report",
        "trader_investment_plan", "final_trade_decision",
    ]
    for key in _report_keys:
        if isinstance(final_state.get(key), str):
            final_state[key] = sanitize_company_name_in_report(
                final_state[key], ticker, company_name
            )
    for debate_key in ["investment_debate_state", "risk_debate_state"]:
        debate = final_state.get(debate_key)
        if isinstance(debate, dict):
            for sub_key in debate:
                if isinstance(debate.get(sub_key), str):
                    debate[sub_key] = sanitize_company_name_in_report(
                        debate[sub_key], ticker, company_name
                    )

    sections = []

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if final_state.get("governance_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "governance.md").write_text(final_state["governance_report"], encoding="utf-8")
        analyst_parts.append(("Governance Analyst", final_state["governance_report"]))
    if final_state.get("industry_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "industry.md").write_text(final_state["industry_report"], encoding="utf-8")
        analyst_parts.append(("Industry Analyst", final_state["industry_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # 6. Data coverage and limitations.  This section is intentionally always
    # present so a reader can distinguish "no degradation was recorded" from
    # an older report format that had no provenance field at all.
    sections.append(render_data_coverage_section(final_state.get("data_coverage", [])))

    # Write consolidated report
    trade_date = final_state.get("trade_date", "")
    trade_date_line = f"\nAnalysis Date: {trade_date}" if trade_date else ""
    header = (
        f"# Trading Analysis Report: {ticker}\n\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        f"{trade_date_line}\n\n"
    )
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections), encoding="utf-8")
    return save_path / "complete_report.md"
