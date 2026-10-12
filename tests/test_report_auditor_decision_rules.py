"""Tests for the report_auditor decision-layer rules (ARITH/PERSIST/DECISION/TIME/...).

规则实现见 scripts/report_auditor.py 的 DecisionLayerRules，需求来源：
docs/decision_layer_quality_report.md 第六节。所有案例均取自
reports/20261011_batch_my 的真实缺陷。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "report_auditor.py"


def _load_auditor_module():
    if "report_auditor" in sys.modules:
        return sys.modules["report_auditor"]
    spec = importlib.util.spec_from_file_location("report_auditor", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["report_auditor"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def auditor():
    return _load_auditor_module()


@pytest.fixture(scope="module")
def rules(auditor):
    return auditor.DecisionLayerRules


# ---------------------------------------------------------------------------
# ARITH-001: 显式算式重算
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestArithDivisions:
    def test_flags_wrong_pb(self, rules):
        # 中国联通 600050 真实案例：1344/3820.76 = 0.352，报告写 0.78
        text = "净资产 3,820.76 亿 vs 总市值 1,344 亿 → PB = 1,344 / 3,820.76 ≈ 0.78，与快照一致"
        issues = rules.check_arith_divisions(text, "600050", "d.md")
        assert any(i.rule_id == "ARITH-001" and i.severity == "CRITICAL" for i in issues)

    def test_passes_correct_math(self, rules):
        text = "PB = 1,344 / 3,820.76 ≈ 0.35，处于破净状态"
        assert rules.check_arith_divisions(text, "600050", "d.md") == []

    def test_ignores_integer_fractions(self, rules):
        # 无小数点/千分位的整数比（如 1/3）不算算式声称
        text = "单次不超过计划仓位的 1/3 分批执行"
        assert rules.check_arith_divisions(text, "600050", "d.md") == []


# ---------------------------------------------------------------------------
# ARITH-002/003: PEG 算术
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestArithPeg:
    def test_flags_wrong_peg(self, rules):
        # 中熔电气 301031 真实案例：PE 24.34 / 增速 57.82% = 0.42，报告写 0.16
        text = (
            "当前 PE(TTM) 24.34 倍，净利润同比 +57.82%，PEG 仅 0.16，显著低估"
        )
        issues = rules.check_arith_peg(text, "301031", "d.md")
        assert any(i.rule_id == "ARITH-002" and i.severity == "CRITICAL" for i in issues)

    def test_passes_consistent_peg(self, rules):
        text = "PE(TTM) 24.34 倍，净利润同比 +57.82%，PEG 0.42，估值合理"
        assert rules.check_arith_peg(text, "301031", "d.md") == []

    def test_flags_negative_peg_with_positive_pe(self, rules):
        # 航宇科技 688239 真实案例：PEG -31.55 与 PE 43.88 同文出现
        text = "PE(TTM) 43.88 倍处于低位，但 PEG -31.55 提示盈利质量存疑"
        issues = rules.check_arith_peg(text, "688239", "d.md")
        assert any(i.rule_id == "ARITH-003" and i.severity == "WARNING" for i in issues)


# ---------------------------------------------------------------------------
# ARITH-004: 翻倍声称
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestArithDoubling:
    def test_flags_doubling_claim(self, rules):
        # 万华化学 600309 真实案例：70.20 → 107 仅 +52.4%，声称"翻倍级别"
        text = (
            "在70.20这个价格，如果放量突破73.75的密集阻力带，直接看向200日线的77.50，"
            "甚至卖方一致预期的107元（PE 15x），那上方的空间是翻倍级别的。"
        )
        issues = rules.check_arith_doubling(text, "600309", "d.md", base_price=70.20)
        assert any(i.rule_id == "ARITH-004" for i in issues)

    def test_passes_real_doubling(self, rules):
        text = "在35.50这个价格，空间看向68元，是翻倍级别的上行空间。"
        assert rules.check_arith_doubling(text, "600309", "d.md", base_price=35.50) == []

    def test_skips_without_base_price(self, rules):
        text = "上方空间是翻倍级别的。"
        assert rules.check_arith_doubling(text, "600309", "d.md", base_price=None) == []


# ---------------------------------------------------------------------------
# PERSIST-001/002: 持仓浮盈恒等式
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPersistPnl:
    def test_flags_wrong_unrealized_pnl(self, rules):
        # 歌尔股份 002241 真实案例：成本 26.669 / 现价 21.51 → -19.34%，报告写 -13.76%
        text = "维持现有 5,700 股（成本 26.669、现价 21.51、浮亏 -13.76%、仓位约 7.69%）"
        issues = rules.check_persist_pnl(text, "002241", "d.md")
        assert any(i.rule_id == "PERSIST-001" and i.severity == "CRITICAL" for i in issues)

    def test_passes_correct_pnl(self, rules):
        text = "维持现有 5,700 股（成本 26.669、现价 21.51、浮亏 -19.34%、仓位约 7.69%）"
        assert rules.check_persist_pnl(text, "002241", "d.md") == []

    def test_fu_ying_positive(self, rules):
        text = "当前持仓（成本 40.00、现价 44.00、浮盈 10.00%）"
        assert rules.check_persist_pnl(text, "000001", "d.md") == []

    def test_snapshot_misalignment(self, rules):
        # 通富微电案例：持仓页现价停在 09-29，验证快照是 10-09 收盘
        texts = {
            "/b/5_portfolio/decision.md": "当前持仓（成本 62.110、现价 60.360、浮亏 -2.83%）",
            "/b/3_trading/trader.md": "入场价取自验证市场快照的 2026-10-09 收盘价 55.55",
        }
        issues = rules.check_persist_snapshot(texts, "002156")
        assert any(i.rule_id == "PERSIST-002" for i in issues)


# ---------------------------------------------------------------------------
# DECISION-001/002: 字段关系
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDecisionFields:
    def _texts(self, decision: str, trader: str = "") -> dict:
        return {
            "/b/5_portfolio/decision.md": decision,
            "/b/3_trading/trader.md": trader,
        }

    def test_target_equals_stop(self, rules):
        # 上海瀚讯 300762 真实案例
        texts = self._texts("**Rating**: Underweight\n\n**Price Target**: 22.29\n\n**Stop**: 22.29\n")
        issues = rules.check_decision_fields(texts, "300762", None)
        assert any(i.rule_id == "DECISION-001" for i in issues)

    def test_target_below_stop(self, rules):
        # 朗源股份 300175 真实案例
        texts = self._texts("**Rating**: Underweight\n\n**Price Target**: 5.10\n\n**Stop**: 5.13\n")
        issues = rules.check_decision_fields(texts, "300175", None)
        assert any(i.rule_id == "DECISION-001" for i in issues)

    def test_valid_target_stop_passes(self, rules):
        texts = self._texts("**Rating**: Overweight\n\n**Price Target**: 23.02\n\n**Stop**: 20.22\n")
        assert rules.check_decision_fields(texts, "002241", None) == []

    def test_action_rating_opposite(self, rules):
        texts = self._texts(
            "**Rating**: Underweight\n",
            trader="FINAL TRANSACTION PROPOSAL: **SELL**",
        )
        issues = rules.check_action_rating(texts, "300762", {"rating": "Underweight"})
        assert any(i.rule_id == "DECISION-002" and i.severity == "CRITICAL" for i in issues)

    def test_action_sell_vs_underweight(self, rules):
        # 上海瀚讯：action=Sell（清仓）vs rating=Underweight（减半）——执行语义差一整级
        texts = self._texts(
            "**Rating**: Underweight\n",
            trader="FINAL TRANSACTION PROPOSAL: **SELL**",
        )
        issues = rules.check_action_rating(texts, "300762", None)
        assert any("清仓" in i.message for i in issues)

    def test_aligned_action_rating_passes(self, rules):
        texts = self._texts(
            "**Rating**: Hold\n",
            trader="FINAL TRANSACTION PROPOSAL: **HOLD**",
        )
        assert rules.check_action_rating(texts, "002241", None) == []

    def test_target_is_technical_level(self, rules):
        # 东方铁塔 002545 真实案例：目标价 15.87 = 布林中轨
        texts = self._texts(
            "**Rating**: Overweight\n\n**Price Target**: 15.87\n",
        )
        texts["/b/1_analysts/market.md"] = "| 指标 | 数值 |\n| 布林中轨 | 15.87 |\n| 50SMA | 16.20 |"
        issues = rules.check_target_anchor(texts, "002545")
        assert any(i.rule_id == "DECISION-003" for i in issues)


# ---------------------------------------------------------------------------
# SCOPE-001/002: 降权来源仍被使用
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScope:
    def test_no_data_report_contributes_stop(self, rules):
        # 航宇科技 688239 真实案例：market.md 被标 no_data，止损 44.90 出自该报告
        analyst_text = "## 技术面深度分析报告\n" + ("RSI 38.33，止损参考 44.90（90% 筹码下沿）\n" * 120)
        texts = {
            "/b/1_analysts/market.md": analyst_text,
            "/b/2_research/manager.md": "市场分析师报告为 no_data，技术破位仅作风控参考。",
            "/b/5_portfolio/decision.md": "**Stop**: 44.9\n",
        }
        issues = rules.check_scope(texts, "688239")
        assert any(i.rule_id == "SCOPE-001" and i.severity == "CRITICAL" for i in issues)
        assert any(i.rule_id == "SCOPE-002" for i in issues)

    def test_clean_case_passes(self, rules):
        texts = {
            "/b/1_analysts/market.md": "## 技术面报告\nRSI 38.33\n" * 3,
            "/b/5_portfolio/decision.md": "**Stop**: 44.9\n",
        }
        assert rules.check_scope(texts, "688239") == []


# ---------------------------------------------------------------------------
# TIME-001: 时间穿越
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTimeTravel:
    def test_flags_future_event(self, rules):
        # 山金国际 000975 真实案例：分析基准日 2026-10-09，新闻写入 10-10 事件
        news = (
            "- **2026-10-09**：正常交易日复盘\n"
            "- **2026-10-10**：沙特利雅得哈立德国王国际机场遭袭，12 死 309 伤\n"
            "- **2026-10-11**：联军确认袭击方为胡塞武装\n"
        )
        texts = {"/b/1_analysts/news.md": news}
        import datetime as dt
        issues = rules.check_time_travel(texts, "000975", dt.datetime(2026, 10, 9))
        assert len(issues) == 2
        assert all(i.rule_id == "TIME-001" and i.severity == "CRITICAL" for i in issues)

    def test_passes_within_analysis_date(self, rules):
        news = "- **2026-10-08**：公司发布三季度预告\n- **2026-10-09**：放量上涨\n"
        import datetime as dt
        issues = rules.check_time_travel(
            {"/b/1_analysts/news.md": news}, "000975", dt.datetime(2026, 10, 9)
        )
        assert issues == []

    def test_resolve_analysis_date_from_dir(self, rules, auditor, tmp_path):
        batch = tmp_path / "20261011_batch_my"
        batch.mkdir()
        assert rules._resolve_analysis_date(batch, {}) == auditor.datetime(2026, 10, 11)


# ---------------------------------------------------------------------------
# DERIVED-001: 估值分位失真
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDerivedEps:
    def test_flags_deteriorating_denominator(self, rules, auditor):
        # 华友钴业 603799 真实案例：35.60/9.76 → TTM EPS 3.65 vs 卖方预测 2.85
        metrics = {
            "f.md": auditor.FinancialMetrics(
                ticker="603799", file_path="f.md",
                price=35.60, pe_ttm=9.76, eps_expected=2.85,
            )
        }
        issues = rules.check_derived_eps(metrics, "603799")
        assert any(i.rule_id == "DERIVED-001" for i in issues)

    def test_passes_consistent_eps(self, rules, auditor):
        metrics = {
            "f.md": auditor.FinancialMetrics(
                ticker="603799", file_path="f.md",
                price=35.60, pe_ttm=12.5, eps_expected=2.85,
            )
        }
        assert rules.check_derived_eps(metrics, "603799") == []


# ---------------------------------------------------------------------------
# RATIO-001/002: 赔率-仓位联动
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRatio:
    def test_low_rr_with_full_size(self, rules):
        # 盛达资源 000603 真实案例：R/R 0.96，仓位标准配置 55%–65%
        decision = (
            "**Rating**: Overweight\n\n**Price Target**: 33.26\n\n**Entry**: 30.52\n\n"
            "**Stop**: 27.68\n\n**Size**: 仓位控制在标准配置的 55%–65%\n"
        )
        texts = {"/b/5_portfolio/decision.md": decision}
        issues = rules.check_ratio(texts, "000603", {"entry": "30.52", "stop": "27.68"})
        assert any(i.rule_id == "RATIO-001" for i in issues)

    def test_good_rr_passes(self, rules):
        decision = (
            "**Rating**: Overweight\n\n**Price Target**: 175\n\n**Entry**: 161.40\n\n"
            "**Stop**: 155.50\n\n**Size**: 标准配置的 20%\n"
        )
        texts = {"/b/5_portfolio/decision.md": decision}
        issues = rules.check_ratio(texts, "300760", None)
        assert not any(i.rule_id == "RATIO-001" for i in issues)

    def test_slippage_info(self, rules):
        # 朗源股份 300175：建议 5,000 股 vs 日均成交量 618 万股 → 0.08% (INFO 阈值 5%)
        decision = "**Rating**: Overweight\n\n**Size**: 建议买入 5,000 股\n"
        texts = {
            "/b/5_portfolio/decision.md": decision,
            "/b/1_analysts/market.md": "日均成交量：618万股，换手率偏低",
        }
        issues = rules.check_ratio(texts, "300175", None)
        assert not any(i.rule_id == "RATIO-002" for i in issues)
        # 小盘股：日均成交量 5 万股，5,000 股 = 10% > 5%
        texts["/b/1_analysts/market.md"] = "日均成交量：5.0万股"
        issues = rules.check_ratio(texts, "300175", None)
        assert any(i.rule_id == "RATIO-002" and i.severity == "INFO" for i in issues)


# ---------------------------------------------------------------------------
# SNAPSHOT-001: 结构化降级传播
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSnapshotFallback:
    def test_marker_without_summary_flag(self, rules):
        texts = {
            "/b/5_portfolio/decision.md": "<!--STRUCTURED_FALLBACK: schema validation failed-->\n**Rating**: Overweight",
        }
        issues = rules.check_snapshot_fallback(texts, "000807", {"fallback": False})
        assert any(i.rule_id == "SNAPSHOT-001" for i in issues)

    def test_flag_without_marker(self, rules):
        texts = {"/b/5_portfolio/decision.md": "**Rating**: Overweight"}
        issues = rules.check_snapshot_fallback(texts, "000807", {"fallback": True})
        assert any(i.rule_id == "SNAPSHOT-001" for i in issues)

    def test_consistent_passes(self, rules):
        texts = {"/b/5_portfolio/decision.md": "**Rating**: Overweight"}
        assert rules.check_snapshot_fallback(texts, "000807", {"fallback": False}) == []
