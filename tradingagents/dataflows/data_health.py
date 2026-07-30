"""Batch pre-flight data health gate.

The 2026-07 incident showed the pipeline can silently serve corrupted data
(chip distribution all-zero for 5 days) while batches keep consuming it and
burning LLM cost on garbage-in reports. The pipeline side now has its own
health_check task, but the consumer must not rely on it having run: this
module re-asserts the same field-level invariants against quant_core.db right
before a batch starts.

Severity model:
- BLOCK  — data is unusable at scale (e.g. chip all-zero majority, bars very
           stale); the batch should not run.
- WARN   — degraded but analyzable (e.g. dividend-yield fill dip); surfaced in
           the dashboard and batch_summary.md header.
- OK     — checks passed.

The gate is best-effort: a missing/unreadable DB downgrades to WARN (non
A-share setups may not have quant_core.db at all).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field

DEFAULT_DB_PATH = os.path.expanduser("~/Code/quant_data/quant_core.db")

# Thresholds mirror quant_pipeline tasks/utility.py health_check so both ends
# alarm on the same conditions.
TURNOVER_FILL_WARN = 60.0   # % non-null turnover_rate on the latest bar date
TURNOVER_FILL_BLOCK = 10.0
CHIP_ZERO_WARN = 5.0        # % all-zero chip rows on the latest chip date
CHIP_ZERO_BLOCK = 50.0
DIVIDEND_FILL_WARN = 40.0   # % non-null dividend_yield on the latest date
BARS_STALE_WARN_DAYS = 3    # calendar days between analysis date and MAX(trade_date)
BARS_STALE_BLOCK_DAYS = 7
CHIP_STALE_WARN_DAYS = 4


@dataclass
class HealthReport:
    level: str = "ok"  # ok | warn | block
    issues: list[str] = field(default_factory=list)      # WARN-level findings
    blockers: list[str] = field(default_factory=list)    # BLOCK-level findings
    details: list[str] = field(default_factory=list)     # informational lines

    def escalate(self, level: str, message: str) -> None:
        if level == "block":
            self.blockers.append(message)
            self.level = "block"
        elif level == "warn":
            self.issues.append(message)
            if self.level != "block":
                self.level = "warn"

    def summary_lines(self) -> list[str]:
        """Markdown lines for the batch_summary.md header."""
        if self.level == "ok":
            return []
        lines = ["> [!WARNING]", "> **数据健康预检发现异常**，以下维度的分析结论可信度受影响："]
        for msg in self.blockers:
            lines.append(f"> - 🛑 {msg}")
        for msg in self.issues:
            lines.append(f"> - ⚠️ {msg}")
        lines.append("")
        return lines


def _calendar_gap_days(analysis_date: str, latest: str | None) -> int | None:
    from datetime import datetime

    if not latest:
        return None
    try:
        a = datetime.strptime(analysis_date[:10], "%Y-%m-%d")
        b = datetime.strptime(str(latest)[:10], "%Y-%m-%d")
        return (a - b).days
    except ValueError:
        return None


def check_data_health(
    analysis_date: str,
    db_path: str | None = None,
) -> HealthReport:
    """Assert field-level data invariants against quant_core.db."""
    report = HealthReport()
    path = db_path or os.getenv("QUANT_DB_PATH", DEFAULT_DB_PATH)

    if not os.path.exists(path):
        report.escalate("warn", f"quant_core.db 不存在 ({path})，跳过健康预检")
        return report

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error as exc:
        report.escalate("warn", f"quant_core.db 无法打开: {exc}")
        return report

    try:
        _check_daily_bars(conn, analysis_date, report)
        _check_chip_distribution(conn, analysis_date, report)
        _check_dividend_yield(conn, report)
    except sqlite3.Error as exc:  # missing tables etc. — degrade, don't crash
        report.escalate("warn", f"健康预检查询失败（表/列缺失?）: {exc}")
    finally:
        conn.close()

    return report


def _check_daily_bars(conn: sqlite3.Connection, analysis_date: str, report: HealthReport) -> None:
    latest = conn.execute("SELECT MAX(trade_date) FROM daily_bars").fetchone()[0]
    gap = _calendar_gap_days(analysis_date, latest)
    report.details.append(f"daily_bars 最新日期: {latest} (距分析日 {gap} 天)")
    if gap is None:
        report.escalate("warn", "daily_bars 无数据或日期不可解析")
        return
    if gap >= BARS_STALE_BLOCK_DAYS:
        report.escalate("block", f"日线数据严重滞后: 最新 {latest}，距分析日 {gap} 天 (≥{BARS_STALE_BLOCK_DAYS})")
    elif gap >= BARS_STALE_WARN_DAYS:
        report.escalate("warn", f"日线数据滞后: 最新 {latest}，距分析日 {gap} 天")

    total, valid = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN turnover_rate IS NOT NULL AND turnover_rate > 0"
        " THEN 1 ELSE 0 END) FROM daily_bars WHERE trade_date = ?",
        (latest,),
    ).fetchone()
    if total:
        pct = 100.0 * (valid or 0) / total
        report.details.append(f"当日换手率非空率: {pct:.1f}% ({valid}/{total})")
        if pct < TURNOVER_FILL_BLOCK:
            report.escalate("block", f"换手率当日非空率仅 {pct:.1f}% (<{TURNOVER_FILL_BLOCK}%)，筹码等衍生数据不可信")
        elif pct < TURNOVER_FILL_WARN:
            report.escalate("warn", f"换手率当日非空率 {pct:.1f}% (<{TURNOVER_FILL_WARN}%)")


def _check_chip_distribution(conn: sqlite3.Connection, analysis_date: str, report: HealthReport) -> None:
    latest = conn.execute("SELECT MAX(trade_date) FROM chip_distribution_em").fetchone()[0]
    gap = _calendar_gap_days(analysis_date, latest)
    report.details.append(f"chip_distribution_em 最新日期: {latest} (距分析日 {gap} 天)")
    if gap is not None and gap >= CHIP_STALE_WARN_DAYS:
        report.escalate("warn", f"筹码分布滞后: 最新 {latest}，距分析日 {gap} 天")

    total, zero = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN profit_ratio = 0 AND avg_cost = 0 THEN 1 ELSE 0 END)"
        " FROM chip_distribution_em WHERE trade_date = ?",
        (latest,),
    ).fetchone()
    if total:
        pct = 100.0 * (zero or 0) / total
        report.details.append(f"当日筹码全零率: {pct:.1f}% ({zero}/{total})")
        if pct > CHIP_ZERO_BLOCK:
            report.escalate("block", f"筹码分布当日全零率 {pct:.1f}% (>{CHIP_ZERO_BLOCK}%)，采集端疑似故障")
        elif pct > CHIP_ZERO_WARN:
            report.escalate("warn", f"筹码分布当日全零率 {pct:.1f}% (>{CHIP_ZERO_WARN}%)")


def _check_dividend_yield(conn: sqlite3.Connection, report: HealthReport) -> None:
    latest = conn.execute("SELECT MAX(trade_date) FROM fundamentals").fetchone()[0]
    total, valid = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN dividend_yield IS NOT NULL THEN 1 ELSE 0 END)"
        " FROM fundamentals WHERE trade_date = ?",
        (latest,),
    ).fetchone()
    if total:
        pct = 100.0 * (valid or 0) / total
        report.details.append(f"当日股息率非空率: {pct:.1f}% ({valid}/{total})")
        if pct < DIVIDEND_FILL_WARN:
            report.escalate("warn", f"股息率当日非空率 {pct:.1f}% (<{DIVIDEND_FILL_WARN}%)，股东回报维度受限")
