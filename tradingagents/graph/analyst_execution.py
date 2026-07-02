from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any


@dataclass(frozen=True)
class AnalystNodeSpec:
    key: str
    agent_node: str
    clear_node: str
    tool_node: str
    report_key: str


@dataclass(frozen=True)
class AnalystExecutionPlan:
    specs: list[AnalystNodeSpec]
    concurrency_limit: int


ANALYST_NODE_SPECS: dict[str, AnalystNodeSpec] = {
    "market": AnalystNodeSpec(
        key="market",
        agent_node="Market Analyst",
        clear_node="Msg Clear Market",
        tool_node="tools_market",
        report_key="market_report",
    ),
    "social": AnalystNodeSpec(
        # Wire key stays "social" for saved-config back-compat; the
        # user-facing label is "Sentiment Analyst" to match the rename
        # that landed in v0.2.5 (sentiment_analyst now ingests news +
        # StockTwits + Reddit, not just social media).
        key="social",
        agent_node="Sentiment Analyst",
        clear_node="Msg Clear Sentiment",
        tool_node="tools_social",
        report_key="sentiment_report",
    ),
    "news": AnalystNodeSpec(
        key="news",
        agent_node="News Analyst",
        clear_node="Msg Clear News",
        tool_node="tools_news",
        report_key="news_report",
    ),
    "fundamentals": AnalystNodeSpec(
        key="fundamentals",
        agent_node="Fundamentals Analyst",
        clear_node="Msg Clear Fundamentals",
        tool_node="tools_fundamentals",
        report_key="fundamentals_report",
    ),
    "governance": AnalystNodeSpec(
        key="governance",
        agent_node="Governance Analyst",
        clear_node="Msg Clear Governance",
        tool_node="tools_governance",
        report_key="governance_report",
    ),
    "industry": AnalystNodeSpec(
        key="industry",
        agent_node="Industry Analyst",
        clear_node="Msg Clear Industry",
        tool_node="tools_industry",
        report_key="industry_report",
    ),
}


def build_analyst_execution_plan(
    selected_analysts: Iterable[str],
    concurrency_limit: int = 1,
) -> AnalystExecutionPlan:
    if concurrency_limit < 1:
        raise ValueError("analyst concurrency limit must be >= 1")

    specs: list[AnalystNodeSpec] = []
    for analyst_key in selected_analysts:
        spec = ANALYST_NODE_SPECS.get(analyst_key)
        if spec is None:
            raise ValueError(f"unknown analyst key: {analyst_key}")
        specs.append(spec)

    if not specs:
        raise ValueError("at least one analyst must be selected")

    return AnalystExecutionPlan(specs=specs, concurrency_limit=concurrency_limit)


def get_initial_analyst_node(plan: AnalystExecutionPlan) -> str:
    return plan.specs[0].agent_node


class AnalystWallTimeTracker:
    def __init__(self, plan: AnalystExecutionPlan):
        self.plan = plan
        self._started_at: dict[str, float] = {}
        self._wall_times: dict[str, float] = {}

    def mark_started(self, analyst_key: str, started_at: float | None = None) -> None:
        if analyst_key not in ANALYST_NODE_SPECS:
            raise ValueError(f"unknown analyst key: {analyst_key}")
        self._started_at.setdefault(analyst_key, monotonic() if started_at is None else started_at)

    def mark_completed(
        self,
        analyst_key: str,
        completed_at: float | None = None,
    ) -> None:
        if analyst_key not in ANALYST_NODE_SPECS:
            raise ValueError(f"unknown analyst key: {analyst_key}")
        if analyst_key in self._wall_times:
            return
        started_at = self._started_at.get(analyst_key)
        if started_at is None:
            return
        finished_at = monotonic() if completed_at is None else completed_at
        self._wall_times[analyst_key] = max(0.0, finished_at - started_at)

    def get_wall_times(self) -> dict[str, float]:
        return dict(self._wall_times)

    def format_summary(self) -> str:
        parts = []
        for spec in self.plan.specs:
            duration = self._wall_times.get(spec.key)
            if duration is not None:
                label = spec.agent_node.removesuffix(" Analyst")
                parts.append(f"{label} {duration:.2f}s")
        if not parts:
            return "Analyst wall time: pending"
        return "Analyst wall time: " + " | ".join(parts)


def sync_analyst_tracker_from_chunk(
    tracker: AnalystWallTimeTracker,
    chunk: dict[str, str],
    now: float | None = None,
) -> None:
    current_time = monotonic() if now is None else now
    active_found = False

    for spec in tracker.plan.specs:
        has_report = bool(chunk.get(spec.report_key))

        if has_report:
            tracker.mark_started(spec.key, started_at=current_time)
            tracker.mark_completed(spec.key, completed_at=current_time)
            continue

        if not active_found:
            tracker.mark_started(spec.key, started_at=current_time)
            active_found = True


_REPORT_QUALITY_RELIABLE = "reliable"
_REPORT_QUALITY_NO_DATA = "no_data"
_REPORT_QUALITY_SPARSE = "sparse"


def validate_report_quality(report_key: str, report_text: str) -> str:
    """Classify an analyst report's data quality.

    Returns one of:
    - ``"reliable"`` — report has >= 50 words and no NO_DATA_AVAILABLE sentinel.
    - ``"no_data"`` — report is empty or contains the no-data sentinel.
    - ``"sparse"`` — report exists but has fewer than 50 words.
    """
    if not report_text or not report_text.strip():
        return _REPORT_QUALITY_NO_DATA

    if "NO_DATA_AVAILABLE" in report_text:
        return _REPORT_QUALITY_NO_DATA

    word_count = len(report_text.split())
    if word_count < 50:
        return _REPORT_QUALITY_SPARSE

    return _REPORT_QUALITY_RELIABLE


def build_data_quality_summary(
    state: Mapping[str, Any],
    specs: list[AnalystNodeSpec],
) -> str:
    """Build a data quality summary block from all analyst reports in state."""
    lines: list[str] = ["## Data Quality Summary", ""]
    lines.append("| Analyst | Quality |")
    lines.append("|---------|---------|")

    unreliable_count = 0
    for spec in specs:
        report_text = state.get(spec.report_key, "")
        quality = validate_report_quality(spec.report_key, report_text)
        label = spec.agent_node
        emoji = {"reliable": "✅", "no_data": "❌", "sparse": "⚠️"}.get(quality, "❓")
        lines.append(f"| {label} | {emoji} {quality} |")
        if quality != "reliable":
            unreliable_count += 1

    lines.append("")
    if unreliable_count > 0:
        lines.append(
            f"**{unreliable_count} of {len(specs)} analyst reports have data "
            "quality issues. Treat conclusions from affected reports with caution.**"
        )
    else:
        lines.append("**All analyst reports have reliable data.**")

    return "\n".join(lines)
