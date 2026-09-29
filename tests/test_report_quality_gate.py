"""Unit tests for the analyst report quality gate."""

from __future__ import annotations

import unittest

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.graph.analyst_execution import (
    ANALYST_NODE_SPECS,
    AnalystNodeSpec,
    build_data_quality_summary,
)
from tradingagents.graph.report_quality_gate import (
    QUALITY_CRITICAL,
    QUALITY_OK,
    QUALITY_WARNING,
    classify_report,
    count_report_words,
    create_quality_gate_clear_node,
    make_quality_gate_router,
)

MARKET_SPEC = ANALYST_NODE_SPECS["market"]

GOOD_ZH_REPORT = (
    "基于技术面与竞价数据的多维度分析，我们形成以下研究结论：该股短期均线呈多头排列，"
    "成交量温和放大，资金呈净流入态势。RSI 指标处于强势区间但未达超买，后续观察信号为 "
    "量能能否持续。主要风险在于大盘波动与板块轮动，整体观点偏审慎乐观，建议投资者密切关注 "
    "关键支撑位的承接力度与北向资金流向变化。" * 4
)

GOOD_EN_REPORT = (
    "Based on the indicator suite the trend remains constructive. Our conclusion: "
    "momentum is bullish with RSI confirming the uptrend, while volume supports the "
    "breakout. Key risks are sector rotation and macro data. The outlook is neutral "
    "to bullish; we would watch the 50-day average as the signal level." * 4
)

DATA_DUMP_REPORT = (
    "Open 12.50 High 12.80 Low 12.30 Close 12.66 Volume 1234567 MA5 12.40 MA10 12.35 "
    "MA20 12.20 MACD 0.12 RSI 58 BOLL upper 13.00 lower 11.80 turnover 2.3 pct 1.25 " * 12
)


@pytest.mark.unit
class ClassifyReportTests(unittest.TestCase):
    def test_empty_report_is_critical(self):
        self.assertEqual(classify_report("").severity, QUALITY_CRITICAL)
        self.assertEqual(classify_report(None).severity, QUALITY_CRITICAL)
        self.assertEqual(classify_report("   \n  ").severity, QUALITY_CRITICAL)

    def test_blank_input_improvisation_is_critical(self):
        verdict = classify_report(
            "既然刚才没有看到空头分析师的具体论述（输入中为空白），我们从其他角度分析。" + GOOD_ZH_REPORT
        )
        self.assertEqual(verdict.severity, QUALITY_CRITICAL)
        self.assertIn("fallback", verdict.reason)

    def test_english_blank_input_is_critical(self):
        verdict = classify_report(
            "Since no input was provided, I will improvise an analysis. " + GOOD_EN_REPORT
        )
        self.assertEqual(verdict.severity, QUALITY_CRITICAL)

    def test_refusal_is_critical(self):
        verdict = classify_report(
            "As an AI language model, I cannot provide investment advice. " + GOOD_EN_REPORT
        )
        self.assertEqual(verdict.severity, QUALITY_CRITICAL)

    def test_good_zh_report_is_ok(self):
        self.assertEqual(classify_report(GOOD_ZH_REPORT).severity, QUALITY_OK)

    def test_good_en_report_is_ok(self):
        self.assertEqual(classify_report(GOOD_EN_REPORT).severity, QUALITY_OK)

    def test_data_dump_without_judgment_is_warning(self):
        verdict = classify_report(DATA_DUMP_REPORT)
        self.assertEqual(verdict.severity, QUALITY_WARNING)
        self.assertIn("conclusion", verdict.reason)

    def test_thin_report_is_warning(self):
        verdict = classify_report("结论：短期偏强。")
        self.assertEqual(verdict.severity, QUALITY_WARNING)
        self.assertIn("thin", verdict.reason)

    def test_cjk_characters_count_as_words(self):
        # Chinese reports have no spaces; a naive split() would count this
        # dense paragraph as a handful of tokens and false-flag it as thin.
        self.assertGreater(count_report_words(GOOD_ZH_REPORT), 50)


@pytest.mark.unit
class QualityGateClearNodeTests(unittest.TestCase):
    # what create_msg_delete() produces for the state built by _state()
    _UNMODIFIED_PLACEHOLDER = (
        "Proceed with your assigned analysis for this workflow. "
        "test context The analysis date is 2026-09-29."
    )

    def _state(self, report: str, retries: dict | None = None, flags: dict | None = None):
        return {
            "messages": [HumanMessage(content="q"), AIMessage(content=report)],
            "market_report": report,
            "instrument_context": "test context",
            "trade_date": "2026-09-29",
            "report_quality_retries": retries or {},
            "report_quality_flags": flags or {},
        }

    @staticmethod
    def _placeholder(updates) -> HumanMessage:
        messages = updates["messages"]
        assert isinstance(messages[-1], HumanMessage), "last message must be the placeholder"
        return messages[-1]

    def test_first_critical_bumps_retry_without_banner(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state("输入为空白，无法分析。"))
        self.assertEqual(updates["report_quality_retries"], {"market": 1})
        self.assertNotIn("market", updates.get("report_quality_flags", {}))
        assert "market_report" not in updates  # no banner yet — retry comes first
        # messages still cleared
        self.assertTrue(updates["messages"])

    def test_retry_placeholder_carries_reason_and_instruction(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state("输入为空白，无法分析。"))
        content = self._placeholder(updates).content
        self.assertIn("rejected", content)
        self.assertIn("fallback text", content)  # the verdict reason is quoted
        self.assertIn("complete, substantive analysis report", content)

    def test_retry_placeholder_keeps_instrument_anchor(self):
        """#888: the anchor must survive, we only append to it."""
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state("输入为空白，无法分析。"))
        content = self._placeholder(updates).content
        self.assertIn("test context", content)
        self.assertIn("2026-09-29", content)
        self.assertTrue(
            content.startswith("Proceed with your assigned analysis"),
            content,
        )

    def test_empty_report_reason_reaches_the_retry(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state(""))
        content = self._placeholder(updates).content
        self.assertIn("empty report", content)

    def test_second_critical_accepts_with_banner(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state("输入为空白，无法分析。", retries={"market": 1}))
        self.assertEqual(updates["report_quality_retries"], {"market": 2})
        self.assertEqual(updates["report_quality_flags"]["market"], QUALITY_CRITICAL)
        self.assertIn("Report quality: critical", updates["market_report"])

    def test_non_retry_paths_leave_placeholder_untouched(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        plain = self._placeholder(node(self._state(GOOD_ZH_REPORT))).content
        self.assertEqual(plain, self._UNMODIFIED_PLACEHOLDER)
        accepted = self._placeholder(
            node(self._state("输入为空白。", retries={"market": 1}))
        ).content
        self.assertEqual(accepted, self._UNMODIFIED_PLACEHOLDER)
        warned = self._placeholder(node(self._state("结论：偏强。"))).content
        self.assertEqual(warned, self._UNMODIFIED_PLACEHOLDER)

    def test_warning_accepts_with_banner_and_no_retry(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state("结论：偏强。"))
        self.assertNotIn("report_quality_retries", updates)
        self.assertEqual(updates["report_quality_flags"]["market"], QUALITY_WARNING)
        self.assertIn("Report quality: warning", updates["market_report"])

    def test_ok_marks_flag_without_touching_report(self):
        node = create_quality_gate_clear_node(MARKET_SPEC)
        updates = node(self._state(GOOD_ZH_REPORT))
        self.assertEqual(updates["report_quality_flags"]["market"], "ok")
        assert "market_report" not in updates


@pytest.mark.unit
class QualityGateRouterTests(unittest.TestCase):
    def _state(self, report: str, retries: dict):
        return {"market_report": report, "report_quality_retries": retries}

    def test_first_critical_routes_back_to_analyst(self):
        route = make_quality_gate_router(MARKET_SPEC, "Sentiment Analyst")
        result = route(self._state("输入为空白。", {"market": 1}))
        self.assertEqual(result, "Market Analyst")

    def test_persistent_critical_proceeds(self):
        route = make_quality_gate_router(MARKET_SPEC, "Sentiment Analyst")
        self.assertEqual(
            route(self._state("输入为空白。", {"market": 2})),
            "Sentiment Analyst",
        )

    def test_ok_proceeds(self):
        route = make_quality_gate_router(MARKET_SPEC, "Sentiment Analyst")
        self.assertEqual(route(self._state(GOOD_ZH_REPORT, {"market": 1})), "Sentiment Analyst")

    def test_warning_proceeds(self):
        route = make_quality_gate_router(MARKET_SPEC, "Sentiment Analyst")
        self.assertEqual(route(self._state("结论：偏强。", {})), "Sentiment Analyst")


@pytest.mark.unit
class DataQualitySummaryTests(unittest.TestCase):
    def test_summary_shows_generation_column_when_flags_present(self):
        specs = list(ANALYST_NODE_SPECS.values())[:2]
        state = {
            "market_report": GOOD_ZH_REPORT,
            "sentiment_report": GOOD_EN_REPORT,
            "report_quality_flags": {"market": "warning", "social": "ok"},
        }
        summary = build_data_quality_summary(state, specs)
        self.assertIn("| Generation |", summary)
        self.assertIn("⚠️ warning", summary)
        self.assertIn("✅ ok", summary)

    def test_summary_keeps_two_columns_without_flags(self):
        specs = [AnalystNodeSpec(
            key="market", agent_node="Market Analyst", clear_node="c",
            tool_node="t", report_key="market_report",
        )]
        state = {"market_report": GOOD_ZH_REPORT}
        summary = build_data_quality_summary(state, specs)
        self.assertIn("| Analyst | Quality |", summary)
        self.assertNotIn("Generation", summary)


if __name__ == "__main__":
    unittest.main()
