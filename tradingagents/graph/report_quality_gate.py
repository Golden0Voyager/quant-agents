"""Analyst report quality gate — post-generation checks with layered degrade.

Borrowed from TradingAgents-CN's ``core/agents/quality_gate.py`` idea and
adapted to this graph's clear-node architecture. Runs on every analyst
report right after the analyst node writes it into state, before the
workflow moves on to the next analyst.

Three checks, two severities:

- ``critical`` — the report is empty or matches the fallback/blank-input
  blacklist (the LLM silently degraded, refused, or improvised with empty
  inputs instead of analysing). Layered degrade: route back to the analyst
  for **one retry**, with the rejection reason injected into the retry's
  placeholder message so a deterministic failure is not re-rolled
  verbatim; if still critical, accept the report but prepend a warning
  banner so downstream agents and the audit trail see it.
- ``warning`` — the report is thin (< 50 words) or carries no conclusion
  keyword (pure data dump without any judgment). Accepted as-is with a
  warning banner; no retry (the content exists, it is just shallow).

The gate never blocks the workflow — every path terminates at the next
analyst. State fields ``report_quality_retries`` /
``report_quality_flags`` record what happened per analyst key and feed
the Data Quality Summary table.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .analyst_execution import AnalystNodeSpec

logger = logging.getLogger(__name__)

QUALITY_OK = "ok"
QUALITY_WARNING = "warning"
QUALITY_CRITICAL = "critical"

# Matches the threshold used by validate_report_quality in analyst_execution.
MIN_REPORT_WORDS = 50

# CJK scripts have no inter-word spaces, so a naive str.split() undercounts
# them by an order of magnitude. Count every CJK ideograph as one word plus
# every latin/digit run as one word.
_CJK_WORD_TOKEN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[A-Za-z0-9_]+")


def count_report_words(text: str) -> int:
    """Word count that treats each CJK character as one word."""
    return len(_CJK_WORD_TOKEN.findall(text))

# High-precision bilingual markers of a degraded/refused generation. Kept
# deliberately narrow so real reports cannot false-positive: every pattern
# here describes the *act of failing*, never legitimate analysis content.
_FALLBACK_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # blank-input improvisation (observed 20260928: the model discussed an
    # empty analyst input instead of analysing)
    re.compile(r"输入为空白|输入是空白|输入内容为空|未提供输入|没有提供输入|输入中为空白"),
    re.compile(r"input[s]? (is|are|was|were) blank", re.I),
    re.compile(r"no input (was )?provided", re.I),
    # explicit generation failure
    re.compile(r"分析未能完成|未能完成分析|无法完成本次分析|分析报告生成失败"),
    # refusals
    re.compile(r"as an ai language model", re.I),
    re.compile(r"i cannot (provide|offer|give) (investment|financial|trading|buy|sell)", re.I),
    re.compile(r"无法提供(投资|交易|买卖)建议"),
)

# Judgment markers — a report with none of these likely just lists numbers.
# Deliberately excludes near-universal words (风险/risk) so the check keeps
# signal; the bar is "at least one judgment", not "deep insight".
_CONCLUSION_KEYWORDS: tuple[str, ...] = (
    "结论", "观点", "判断", "展望", "信号", "警惕", "看好", "承压",
    "偏强", "偏弱", "上行", "下行", "乐观", "审慎", "谨慎",
    "conclusion", "outlook", "signal", "bullish", "bearish", "neutral",
    "overbought", "oversold", "uptrend", "downtrend", "watch",
)


@dataclass(frozen=True)
class ReportQualityVerdict:
    """Outcome of the quality gate for one report."""

    severity: str  # ok / warning / critical
    reason: str

    @property
    def should_retry(self) -> bool:
        return self.severity == QUALITY_CRITICAL


def classify_report(report_text: str | None) -> ReportQualityVerdict:
    """Classify a finished analyst report.

    Returns ok / warning / critical. Pure function — no state access, no
    logging side effects beyond nothing; callers decide what to do.
    """
    text = (report_text or "").strip()
    if not text:
        return ReportQualityVerdict(QUALITY_CRITICAL, "empty report")

    for pattern in _FALLBACK_TEXT_PATTERNS:
        if pattern.search(text):
            return ReportQualityVerdict(QUALITY_CRITICAL, f"fallback text: {pattern.pattern!r}")

    word_count = count_report_words(text)
    if word_count < MIN_REPORT_WORDS:
        return ReportQualityVerdict(QUALITY_WARNING, f"thin report ({word_count} words < {MIN_REPORT_WORDS})")

    lowered = text.lower()
    if not any(keyword in lowered for keyword in _CONCLUSION_KEYWORDS):
        return ReportQualityVerdict(QUALITY_WARNING, "no conclusion markers (data dump?)")

    return ReportQualityVerdict(QUALITY_OK, "")


def build_quality_banner(verdict: ReportQualityVerdict) -> str:
    icon = "❌" if verdict.severity == QUALITY_CRITICAL else "⚠️"
    return (
        f"> {icon} **Report quality: {verdict.severity}** — {verdict.reason}. "
        "Treat this analyst's findings with caution.\n\n"
    )


def build_retry_instruction(verdict: ReportQualityVerdict) -> str:
    """Text appended to the placeholder when a critical report is retried.

    Without it the retry sees the same generic "Proceed with your assigned
    analysis…" placeholder and re-rolls the dice: when the failure is
    deterministic (provider exception, refusal, empty output) the second
    attempt reproduces it and the whole analyst run — tools included — was
    wasted. Naming the reason and demanding a complete report gives the
    retry something to correct.
    """
    return (
        f"Note: your previous response was rejected and must be replaced "
        f"(reason: {verdict.reason}). "
        "You MUST now produce the complete, substantive analysis report as your "
        "final response — an empty, refused, or placeholder answer will be "
        "rejected again."
    )


def _append_retry_instruction(updates: dict[str, Any], verdict: ReportQualityVerdict) -> None:
    """Rewrite the trailing placeholder HumanMessage in ``updates['messages']``.

    ``create_msg_delete`` returns ``RemoveMessage`` ops followed by one
    context-anchored placeholder (see its docstring, #888) — the anchor must
    survive, so the rejection note is appended rather than substituted for
    the whole message. No-ops if the shape is not what we expect, which
    keeps the retry routing intact even if the clear node changes.
    """
    from langchain_core.messages import HumanMessage

    messages = list(updates.get("messages") or [])
    if not messages:
        return
    placeholder = messages[-1]
    if not isinstance(placeholder, HumanMessage):
        return
    messages[-1] = placeholder.model_copy(
        update={"content": f"{placeholder.content}\n\n{build_retry_instruction(verdict)}"}
    )
    updates["messages"] = messages


def create_quality_gate_clear_node(spec: AnalystNodeSpec) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Wrap the message-clear node with the report quality gate.

    Runs after the analyst node wrote its report into state. On the first
    critical verdict the node bumps the retry counter and appends the
    rejection reason to the placeholder message, so the retry has something
    to correct — the router then sends the workflow back to the analyst for
    one fresh attempt. On any later non-ok verdict the report is accepted
    with a warning banner prepended, so downstream agents and the saved
    artifacts see the flag.
    """
    from tradingagents.agents.utils.agent_utils import create_msg_delete

    clear_messages = create_msg_delete()

    def clear_and_check(state: Mapping[str, Any]) -> dict[str, Any]:
        updates = dict(clear_messages(state))  # type: ignore[arg-type]
        report = state.get(spec.report_key, "")
        verdict = classify_report(report)

        retries = dict(state.get("report_quality_retries") or {})
        flags = dict(state.get("report_quality_flags") or {})
        attempts = retries.get(spec.key, 0)

        if verdict.severity == QUALITY_CRITICAL and attempts < 1:
            retries[spec.key] = attempts + 1
            updates["report_quality_retries"] = retries
            _append_retry_instruction(updates, verdict)
            logger.warning(
                "Quality gate: %s report critical (%s) — scheduling one retry",
                spec.key,
                verdict.reason,
            )
            return updates

        flags[spec.key] = verdict.severity if verdict.severity != QUALITY_OK else "ok"
        updates["report_quality_flags"] = flags

        if verdict.severity != QUALITY_OK:
            if verdict.severity == QUALITY_CRITICAL:
                retries[spec.key] = attempts + 1
                updates["report_quality_retries"] = retries
            banner = build_quality_banner(verdict)
            updates[spec.report_key] = banner + (report or "")
            logger.warning(
                "Quality gate: %s report %s (%s) — accepting with banner",
                spec.key,
                verdict.severity,
                verdict.reason,
            )
        return updates

    return clear_and_check


def make_quality_gate_router(
    spec: AnalystNodeSpec,
    next_node: str,
) -> Callable[[Mapping[str, Any]], str]:
    """Build the conditional-edge router for an analyst's clear node.

    Re-classifies the report and the retry counter left by the clear node:
    critical on the *first* failure (retry counter just hit 1) routes back
    to the analyst node for one fresh attempt; everything else proceeds to
    ``next_node``. The gate cannot loop: the counter only reaches the retry
    threshold once.
    """
    def route(state: Mapping[str, Any]) -> str:
        retries = state.get("report_quality_retries") or {}
        attempts = retries.get(spec.key, 0)
        if attempts == 1 and classify_report(state.get(spec.report_key, "")).severity == QUALITY_CRITICAL:
            logger.warning(
                "Quality gate: %s report critical after first pass — retrying once",
                spec.key,
            )
            return spec.agent_node
        return next_node

    return route
