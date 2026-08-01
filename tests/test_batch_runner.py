"""Tests for BatchRunner fallback detection and summary rendering."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.batch_runner import BatchRunner


@pytest.fixture(autouse=True)
def _mock_home(tmp_path, monkeypatch):
    """Isolate home-directory caches to a temporary directory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


@pytest.mark.unit
class TestBatchRunnerFallbackDetection:
    def test_extract_summary_flags_upstream_fallback_agents(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        final_state = {
            "final_trade_decision": "**Rating**: Buy\n**Confidence**: high\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
            "structured_fallback_agents": ["Research Manager"],
        }
        runner._extract_summary("AAPL", final_state)
        # An upstream (Research Manager) fallback flags the run as degraded but
        # must NOT drag the PM's own stated confidence down: the decision text
        # says "high", so "high" is what the summary records.
        assert runner.summaries["AAPL"]["fallback"] is True
        assert runner.summaries["AAPL"]["confidence"] == "high"

    def test_extract_summary_flags_fallback_from_state(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        final_state = {
            "final_trade_decision": "**Rating**: Buy\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
            "_structured_fallback": True,
        }
        runner._extract_summary("AAPL", final_state)
        # Degraded run with no confidence stated anywhere in the decision text:
        # record "—" (unknown), not an assumed "low".
        assert runner.summaries["AAPL"]["fallback"] is True
        assert runner.summaries["AAPL"]["confidence"] == "—"

    def test_extract_summary_flags_fallback_from_marker(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        marker = "<!--STRUCTURED_FALLBACK: schema validation failed, treat with low confidence-->"
        final_state = {
            "final_trade_decision": f"{marker}\n**Rating**: Buy\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
        }
        runner._extract_summary("AAPL", final_state)
        # The fallback marker flags the run as degraded, but with no confidence
        # word in the decision the summary records "—" rather than assuming low.
        assert runner.summaries["AAPL"]["fallback"] is True
        assert runner.summaries["AAPL"]["confidence"] == "—"

    def test_extract_summary_parses_confidence_from_decision(self):
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        final_state = {
            "final_trade_decision": "**Rating**: Buy\n**Confidence**: high\n",
            "trader_investment_plan": "",
            "company_name": "Apple",
        }
        runner._extract_summary("AAPL", final_state)
        assert runner.summaries["AAPL"]["fallback"] is False
        assert runner.summaries["AAPL"]["confidence"] == "high"

    def test_extract_summary_parses_chinese_confidence_under_fallback(self):
        """A degraded (fallback) decision that states its confidence in Chinese
        prose is recorded at that stated level, not force-downgraded to low."""
        runner = BatchRunner(
            tickers=["600901.SS"],
            profile_config={"llm_provider": "openai", "output_language": "Chinese"},
            output_dir=Path("/tmp/reports"),
            workers=1,
        )
        marker = "<!--STRUCTURED_FALLBACK: schema validation failed, treat with low confidence-->"
        final_state = {
            "final_trade_decision": (
                f"{marker}\n**评级**: 持有\n"
                "**Confidence**: low （源于基本面核心矛盾待解，且情绪数据缺失）\n"
            ),
            "trader_investment_plan": "",
            "company_name": "江苏金租",
        }
        runner._extract_summary("600901.SS", final_state)
        assert runner.summaries["600901.SS"]["fallback"] is True
        assert runner.summaries["600901.SS"]["confidence"] == "low"


@pytest.mark.unit
class TestBatchRunnerSummaryOutput:
    def test_generate_summary_appends_fallback_marker(self, tmp_path):
        output_dir = tmp_path / "reports" / "batch_20260101_000000"
        output_dir.mkdir(parents=True)
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=output_dir,
            workers=1,
        )
        runner.completed_tickers.add("AAPL")
        runner.summaries["AAPL"] = {
            "company": "Apple",
            "rating": "Buy",
            "entry": "150",
            "stop": "140",
            "size": "5%",
            "fallback": True,
            "confidence": "low",
        }

        md_path = runner.generate_summary()
        md = md_path.read_text(encoding="utf-8")
        assert "[fallback]" in md
        assert "low" in md

    def test_generate_summary_json_includes_fallback_and_low_confidence(self, tmp_path):
        output_dir = tmp_path / "reports" / "batch_20260101_000000"
        output_dir.mkdir(parents=True)
        runner = BatchRunner(
            tickers=["AAPL"],
            profile_config={"llm_provider": "openai", "output_language": "English"},
            output_dir=output_dir,
            workers=1,
        )
        runner.completed_tickers.add("AAPL")
        runner.summaries["AAPL"] = {
            "company": "Apple",
            "rating": "Buy",
            "entry": "150",
            "stop": "140",
            "size": "5%",
            "fallback": True,
            "confidence": "low",
        }

        runner.generate_summary()
        json_path = output_dir / "batch_summary.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))
        row = data["rows"][0]
        assert row["fallback"] is True
        assert row["confidence"] == "low"

@pytest.mark.unit
class TestParseSummaryFieldsFallbackFormats:
    """PM 结构化 fallback 降级为自由文本/表格时，字段仍应被抽出（方案 A）。"""

    def test_markdown_table_row_extraction(self):
        """降级路径把决策渲染成 ``| 字段 | 值 |`` 表格，需按表格行抽取。"""
        decision = (
            "### 最终交易决策\n"
            "| 项目 | 内容 |\n"
            "|------|------|\n"
            "| **评级** | **Hold** |\n"
            "| **入场价格** | 106.105 |\n"
            "| **止损价** | 100.00 |\n"
            "| **仓位规模** | 维持200股（约1.37%仓位） |\n"
        )
        fields = BatchRunner._parse_summary_fields(decision, "")
        assert fields["rating"] == "Hold"
        assert fields["entry"] == "106.105"
        assert fields["stop"] == "100.00"
        assert fields["size"] == "维持200股"

    def test_chinese_bullet_list_extraction(self):
        """列表项 ``- **入场价**：19.08（...）`` 前导标记与括号注释都要处理。"""
        decision = (
            "**评级：卖出**\n"
            "-   **入场价**：19.08（基于2026-07-29的收盘价）\n"
            "-   **止损价**：18.32（基于布林带中轨）\n"
        )
        fields = BatchRunner._parse_summary_fields(decision, "")
        assert fields["rating"] == "Sell"
        assert fields["entry"] == "19.08"
        assert fields["stop"] == "18.32"

    def test_clean_strips_trailing_unit_punctuation(self):
        """散文匹配残留的 ``元 。`` 等尾部应被清理，但合法值不受损。"""
        assert BatchRunner._parse_summary_fields("入场价：23.06元 。", "")["entry"] == "23.06"
        assert BatchRunner._parse_summary_fields("止损价：100.00", "")["stop"] == "100.00"

    def test_no_numeric_fields_stay_dash(self):
        """PM 仅给文字方向、无数值时字段应保持占位符，不误抓正文数字。"""
        decision = "**评级：减持**\n建议在反弹时积极减持，降低仓位，不设硬止损。\n"
        fields = BatchRunner._parse_summary_fields(decision, "")
        assert fields["rating"] == "Underweight"
        assert fields["entry"] == "—"
        assert fields["stop"] == "—"


@pytest.mark.unit
class TestParseSummaryFieldsPmAuthoritative:
    """PM 决策修正值应优先于 trader 回退；评级候选需合法性验证（方案 B）。"""

    def test_rating_ignores_ticker_code_in_heading(self):
        """002594 场景：标题行 ``### 最终交易决策：**002594.SZ (比亚迪)**`` 中的
        ``002594`` 不应被当作评级捕获；应取正文 ``**评级：** `Underweight```。"""
        decision = (
            "### 最终交易决策：**002594.SZ (比亚迪)**\n"
            "**评级：** `Underweight`\n"
        )
        fields = BatchRunner._parse_summary_fields(decision, "")
        assert fields["rating"] == "Underweight"

    def test_stop_prefers_pm_corrected_value_over_trader(self):
        """002594：PM 止损 ``88.57``（反引号 + ``止损位`` 字段名）应覆盖 trader 的
        ``100.23``（错误方向的 entry+1.5xATR）。"""
        decision = (
            "**评级：** `Underweight`\n"
            "**止损位：** `88.57`（引用自布林带中轨）\n"
        )
        trader = (
            "**Action**: Sell\n"
            "**Entry Price**: 95.77\n"
            "**Stop Loss**: 100.23\n"
            "**Position Sizing**: 全部700股清仓\n"
        )
        fields = BatchRunner._parse_summary_fields(decision, trader)
        assert fields["stop"] == "88.57"
        assert fields["stop_source"] == "pm"

    def test_size_prefers_pm_adjustment_over_trader(self):
        """002594：PM 仓位调整 ``卖出100股`` 应覆盖 trader 的 ``全部700股清仓``。"""
        decision = (
            "**评级：** `Underweight`\n"
            "**仓位调整：** 卖出100股（约占当前持仓的14%）\n"
        )
        trader = "**Action**: Sell\n**Position Sizing**: 全部700股清仓\n"
        fields = BatchRunner._parse_summary_fields(decision, trader)
        assert fields["size"] == "卖出100股"
        assert fields["size_source"] == "pm"

    def test_pm_explicit_no_stop_not_overridden_by_trader(self):
        """603993：PM 明确 ``不适用（清仓操作无需设置止损）`` 时，不应回退 trader 的
        ``21.04``，应保持占位符。"""
        decision = (
            "**评级：** 卖出\n"
            "**止损价：** 不适用（清仓操作无需设置止损）\n"
        )
        trader = "**Action**: Sell\n**Stop Loss**: 21.04\n**Position Sizing**: 清仓\n"
        fields = BatchRunner._parse_summary_fields(decision, trader)
        assert fields["stop"] == "—"
        assert fields["stop_source"] == "pm"

    def test_trader_fallback_still_works_with_source_marker(self):
        """PM 未提及止损时仍回退 trader，但标记来源为 trader。"""
        decision = "**评级：** Buy\n看多，建议分批建仓。\n"
        trader = "**Action**: Buy\n**Stop Loss**: 9.50\n"
        fields = BatchRunner._parse_summary_fields(decision, trader)
        assert fields["stop"] == "9.50"
        assert fields["stop_source"] == "trader"

    def test_table_pm_correction_prefers_pm(self):
        """688239：表格行 PM 止损 ``35.00`` 应覆盖 trader 的 ``45.43``。"""
        decision = (
            "| **评级** | **Sell** |\n"
            "| **止损价** | 35.00 |\n"
            "| **仓位规模** | 卖出600股（剩余600股） |\n"
        )
        trader = (
            "**Action**: Sell\n"
            "**Stop Loss**: 45.43\n"
            "**Position Sizing**: 清仓1200股\n"
        )
        fields = BatchRunner._parse_summary_fields(decision, trader)
        assert fields["stop"] == "35.00"
        assert fields["stop_source"] == "pm"
        assert fields["size"] == "卖出600股"
        assert fields["size_source"] == "pm"

    def test_rating_fallback_to_trader_label_still_works(self):
        """PM 无数值时仍可回退 trader 的评级标签，来源标记 trader。"""
        decision = "看多情绪占优，维持当前仓位。\n"
        trader = "FINAL TRANSACTION PROPOSAL: **Hold**\n"
        fields = BatchRunner._parse_summary_fields(decision, trader)
        assert fields["rating"] == "Hold"
        assert fields["rating_source"] == "trader"
