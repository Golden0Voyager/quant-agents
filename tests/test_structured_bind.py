"""Unit tests for tradingagents/agents/utils/structured.py."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
    parse_confidence,
)

pytestmark = pytest.mark.unit


class StructuredBindEdgeTests(unittest.TestCase):
    """Lines 50-56: AttributeError handling in bind_structured."""

    def test_bind_structured_attribute_error_fallback(self):
        llm = MagicMock()
        llm.with_structured_output.side_effect = AttributeError("no tool_choice")

        class TestSchema(BaseModel):
            field: str

        result = bind_structured(llm, TestSchema, "test_agent")
        self.assertIsNone(result)
        llm.with_structured_output.assert_called_once_with(TestSchema)

    def test_bind_structured_attribute_error_logged_at_warning(self):
        llm = MagicMock()
        llm.with_structured_output.side_effect = AttributeError("no tool_choice")

        class TestSchema(BaseModel):
            field: str

        with self.assertLogs(level="WARNING") as logs:
            result = bind_structured(llm, TestSchema, "test_agent")
        self.assertIsNone(result)
        self.assertTrue(any("test_agent" in m and "does not support" in m for m in logs.output))

    def test_invoke_structured_or_freetext_structured_fails_fallback(self):
        class TestSchema(BaseModel):
            field: str

        structured_llm = MagicMock()
        structured_llm.invoke.side_effect = ValueError("structured call failed")
        plain_llm = MagicMock()
        plain_response = MagicMock()
        plain_response.content = "free text fallback"
        plain_llm.invoke.return_value = plain_response

        result = invoke_structured_or_freetext(
            structured_llm, plain_llm,
            "test prompt",
            lambda x: x.field,
            "test_agent",
        )
        self.assertIn("STRUCTURED_FALLBACK", result)
        self.assertIn("free text fallback", result)

    def test_invoke_structured_or_freetext_structured_success(self):
        class TestSchema(BaseModel):
            field: str

        structured_llm = MagicMock()
        structured_llm.invoke.return_value = TestSchema(field="structured result")
        plain_llm = MagicMock()

        result = invoke_structured_or_freetext(
            structured_llm, plain_llm,
            "test prompt",
            lambda x: x.field,
            "test_agent",
        )
        self.assertEqual(result, "structured result")

    def test_invoke_structured_or_freetext_no_structured(self):
        plain_llm = MagicMock()
        plain_response = MagicMock()
        plain_response.content = "free text"
        plain_llm.invoke.return_value = plain_response

        result = invoke_structured_or_freetext(
            None, plain_llm,
            "test prompt",
            lambda x: "should not matter",
            "test_agent",
        )
        self.assertIn("STRUCTURED_FALLBACK", result)
        self.assertIn("free text", result)


class TestStructuredFallback(unittest.TestCase):
    """Cover remaining lines in invoke_structured_or_freetext."""

    def test_structured_llm_is_none_uses_plain(self):
        plain = MagicMock()
        plain.invoke.return_value = MagicMock(content="free-text response")
        result = invoke_structured_or_freetext(
            structured_llm=None,
            plain_llm=plain,
            prompt="test",
            render=lambda x: str(x),
            agent_name="test_agent",
        )
        self.assertIn("STRUCTURED_FALLBACK", result)
        self.assertIn("free-text response", result)
        plain.invoke.assert_called_once_with("test")

    def test_fallback_injects_marker_without_mutating_state(self):
        class TestSchema(BaseModel):
            field: str

        structured_llm = MagicMock()
        structured_llm.invoke.side_effect = ValueError("structured call failed")
        plain_llm = MagicMock()
        plain_llm.invoke.return_value = MagicMock(content="free text fallback")

        state = {"_structured_fallback": False}
        result = invoke_structured_or_freetext(
            structured_llm,
            plain_llm,
            "test prompt",
            lambda x: x.field,
            "test_agent",
            state=state,
        )
        self.assertIn("STRUCTURED_FALLBACK", result)
        self.assertFalse(state["_structured_fallback"])

    def test_fallback_marker_without_state_does_not_raise(self):
        class TestSchema(BaseModel):
            field: str

        structured_llm = MagicMock()
        structured_llm.invoke.side_effect = ValueError("structured call failed")
        plain_llm = MagicMock()
        plain_llm.invoke.return_value = MagicMock(content="free text fallback")

        result = invoke_structured_or_freetext(
            structured_llm,
            plain_llm,
            "test prompt",
            lambda x: x.field,
            "test_agent",
        )
        self.assertIn("STRUCTURED_FALLBACK", result)


class TestParseConfidence(unittest.TestCase):
    """parse_confidence heuristic over English and Chinese phrasings."""

    def test_english_labels(self):
        self.assertEqual(parse_confidence("**Confidence**: high"), "high")
        self.assertEqual(parse_confidence("**Confidence:** Low"), "low")
        self.assertEqual(parse_confidence("Confidence: MEDIUM"), "medium")

    def test_chinese_labels(self):
        self.assertEqual(parse_confidence("置信度：中等"), "medium")
        self.assertEqual(parse_confidence("本报告置信度较低，建议谨慎"), "low")
        self.assertEqual(parse_confidence("整体置信度：**中等偏高**"), "medium")
        self.assertEqual(parse_confidence("置信度为高"), "high")

    def test_markdown_table_cell(self):
        self.assertEqual(parse_confidence("| 置信度 | 低 |"), "low")

    def test_inline_with_reason(self):
        self.assertEqual(
            parse_confidence("**Confidence**: low （源于基本面核心矛盾待解）"), "low"
        )

    def test_first_label_wins(self):
        self.assertEqual(
            parse_confidence("Confidence: high\nlater note Confidence: low"), "high"
        )

    def test_returns_none_when_absent(self):
        self.assertIsNone(parse_confidence(""))
        self.assertIsNone(parse_confidence("no confidence word anywhere here"))
        # Prose that mentions confidence but states no level must not misparse.
        self.assertIsNone(parse_confidence("Confidence in the thesis is genuinely strong"))


class TestFallbackConfidenceRecovery(unittest.TestCase):
    """invoke_structured_or_freetext re-surfaces a **Confidence** line lost in
    the free-text fallback path."""

    @staticmethod
    def _fallback(content: str) -> str:
        structured_llm = MagicMock()
        structured_llm.invoke.side_effect = ValueError("structured call failed")
        plain_llm = MagicMock()
        plain_llm.invoke.return_value = MagicMock(content=content)
        return invoke_structured_or_freetext(
            structured_llm, plain_llm, "prompt", lambda x: x.field, "Portfolio Manager"
        )

    def test_appends_canonical_confidence_from_chinese_prose(self):
        result = self._fallback("经权衡，本报告置信度较低，维持持有。")
        self.assertIn("**Confidence**: low", result)

    def test_appends_canonical_confidence_from_english_prose(self):
        result = self._fallback("Overall the thesis is well supported. Confidence: high.")
        self.assertIn("**Confidence**: high", result)

    def test_does_not_double_append_when_already_present(self):
        result = self._fallback("Decision text.\n**Confidence**: medium\n")
        self.assertEqual(result.count("**Confidence**"), 1)

    def test_no_append_when_no_confidence_stated(self):
        result = self._fallback("A decision with no stated confidence at all.")
        self.assertNotIn("**Confidence**", result)

