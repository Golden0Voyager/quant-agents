"""Tests for the report_auditor cross-validation feature."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "report_auditor.py"


def _load_auditor_module():
    """Load scripts/report_auditor.py as a module (path not on sys.path)."""
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


@pytest.mark.unit
class TestCrossValidationRules:
    def test_flags_pe_mismatch(self, auditor):
        metrics = auditor.FinancialMetrics(
            ticker="600519", file_path="dummy.md", pe_ttm=51.29
        )
        snapshot = {"ticker": "600519.SS", "pe_ttm": 34.88}
        issue = auditor.ValidationRules.check_realtime_pe(metrics, snapshot)
        assert issue is not None
        assert issue.severity == "ERROR"
        assert issue.rule_id == "REALTIME-PE"

    def test_no_issue_when_within_tolerance(self, auditor):
        metrics = auditor.FinancialMetrics(
            ticker="600519", file_path="d.md", pe_ttm=35.0
        )
        snapshot = {"pe_ttm": 34.88}
        assert auditor.ValidationRules.check_realtime_pe(metrics, snapshot) is None

    def test_no_issue_when_truth_missing(self, auditor):
        metrics = auditor.FinancialMetrics(
            ticker="600519", file_path="d.md", pe_ttm=35.0
        )
        assert auditor.ValidationRules.check_realtime_pe(metrics, {}) is None
        assert auditor.ValidationRules.check_realtime_pe(metrics, None) is None

    def test_flags_market_cap_mismatch(self, auditor):
        metrics = auditor.FinancialMetrics(
            ticker="603893", file_path="d.md", market_cap=574.58
        )
        snapshot = {"market_cap_yi": 57.46}
        issue = auditor.ValidationRules.check_realtime_market_cap(metrics, snapshot)
        assert issue is not None
        assert issue.severity == "ERROR"
        assert issue.rule_id == "REALTIME-MCAP"
        assert "10" in (issue.suggestion or "")

    def test_market_cap_within_tolerance(self, auditor):
        metrics = auditor.FinancialMetrics(
            ticker="603893", file_path="d.md", market_cap=57.46
        )
        snapshot = {"market_cap_yi": 58.0}
        assert auditor.ValidationRules.check_realtime_market_cap(metrics, snapshot) is None


@pytest.mark.unit
class TestTickerDirDiscovery:
    """批次目录发现：兼容 纯代码 与 名称_代码 两种命名。"""

    def test_discovers_name_code_dir(self, auditor, tmp_path):
        ticker_dir = tmp_path / "TCL科技_000100"
        ticker_dir.mkdir()
        (ticker_dir / "complete_report.md").write_text(
            "# TCL科技(000100) 分析\nROE:15.2%\n", encoding="utf-8"
        )
        results = auditor.ReportAuditor(str(tmp_path)).audit()
        assert "000100" in results
        assert results["000100"].files_audited == 1
        assert results["000100"].metrics[
            str(ticker_dir / "complete_report.md")
        ].roe == 15.2

    def test_discovers_pure_code_dir(self, auditor, tmp_path):
        ticker_dir = tmp_path / "600050"
        ticker_dir.mkdir()
        (ticker_dir / "complete_report.md").write_text(
            "# 中国联通(600050) 分析\n毛利率: 25.0%\n", encoding="utf-8"
        )
        results = auditor.ReportAuditor(str(tmp_path)).audit()
        assert "600050" in results
        assert results["600050"].files_audited == 1

    def test_ignores_non_ticker_dirs(self, auditor, tmp_path):
        (tmp_path / "batch_summary").mkdir()
        results = auditor.ReportAuditor(str(tmp_path)).audit()
        assert results == {}


@pytest.mark.unit
class TestPmSummaryConsistency:
    """PM 最终决策 (5_portfolio/decision.md) 与 batch_summary 的一致性规则。"""

    def _make_batch(self, tmp_path, summary_rows, decision_text):
        """构造 batch 目录: batch_summary.json + 比亚迪_002594/5_portfolio/decision.md"""
        (tmp_path / "batch_summary.json").write_text(
            json.dumps({"rows": summary_rows}, ensure_ascii=False), encoding="utf-8"
        )
        pm_dir = tmp_path / "比亚迪_002594" / "5_portfolio"
        pm_dir.mkdir(parents=True)
        (pm_dir / "decision.md").write_text(decision_text, encoding="utf-8")
        return tmp_path

    def test_flags_rating_mismatch(self, auditor, tmp_path):
        """summary=Hold vs PM=Underweight → PMSUM-001 ERROR"""
        batch = self._make_batch(
            tmp_path,
            [{"ticker": "002594", "rating": "Hold", "stop": "88.57"}],
            "**评级：** `Underweight`\n**止损位：** `88.57`\n",
        )
        issues = auditor.check_pm_summary_consistency(batch)
        assert any(i.rule_id == "PMSUM-001" and i.severity == "ERROR" for i in issues)

    def test_flags_stop_mismatch(self, auditor, tmp_path):
        """summary stop=100.23 vs PM stop=88.57 → PMSUM-002 ERROR"""
        batch = self._make_batch(
            tmp_path,
            [{"ticker": "002594", "rating": "Underweight", "stop": "100.23"}],
            "**评级：** `Underweight`\n**止损位：** `88.57`\n",
        )
        issues = auditor.check_pm_summary_consistency(batch)
        assert any(i.rule_id == "PMSUM-002" and i.severity == "ERROR" for i in issues)

    def test_pm_explicit_no_stop_kept(self, auditor, tmp_path):
        """PM 明确 止损价：不适用（清仓操作无需设置止损），summary 却给了 trader 的
        21.04 → PMSUM-002（PM 显式拒绝不应被 trader 数值覆盖）"""
        batch = self._make_batch(
            tmp_path,
            [{"ticker": "002594", "rating": "Sell", "stop": "21.04"}],
            "**评级：** `Sell`\n**止损价：** 不适用（清仓操作无需设置止损）\n",
        )
        issues = auditor.check_pm_summary_consistency(batch)
        assert any(i.rule_id == "PMSUM-002" and i.severity == "ERROR" for i in issues)

    def test_no_issue_when_consistent(self, auditor, tmp_path):
        batch = self._make_batch(
            tmp_path,
            [{"ticker": "002594", "rating": "Underweight", "stop": "88.57"}],
            "**评级：** `Underweight`\n**止损位：** `88.57`\n",
        )
        assert auditor.check_pm_summary_consistency(batch) == []

    def test_skips_when_no_decision_file(self, auditor, tmp_path):
        """缺少 5_portfolio/decision.md 时跳过，不产生误报"""
        (tmp_path / "batch_summary.json").write_text(
            json.dumps({"rows": [{"ticker": "002594", "rating": "Hold"}]}),
            encoding="utf-8",
        )
        assert auditor.check_pm_summary_consistency(tmp_path) == []

    def test_skips_when_no_summary_json(self, auditor, tmp_path):
        pm_dir = tmp_path / "比亚迪_002594" / "5_portfolio"
        pm_dir.mkdir(parents=True)
        (pm_dir / "decision.md").write_text("**评级：** `Underweight`\n", encoding="utf-8")
        assert auditor.check_pm_summary_consistency(tmp_path) == []


@pytest.mark.unit
class TestExtractorRegexFixes:
    """未分组 alternation 修复后，ROE:15% / ROE 15% 等写法都能提取。"""

    def test_roe_colon_no_space(self, auditor):
        metrics, _ = auditor.MetricsExtractor().extract("ROE:15.2%", "000100", "x.md")
        assert metrics.roe == 15.2

    def test_roe_space_separated(self, auditor):
        metrics, _ = auditor.MetricsExtractor().extract("ROE 15.2%", "000100", "x.md")
        assert metrics.roe == 15.2

    def test_free_cash_flow_alias(self, auditor):
        metrics, _ = auditor.MetricsExtractor().extract("FCF: -3.2亿", "000100", "x.md")
        assert metrics.free_cash_flow == -3.2

    def test_multiple_occurrences_take_first_and_warn(self, auditor):
        metrics, issues = auditor.MetricsExtractor().extract(
            "ROE:15%\n...(后文转述)...\nROE:18%", "000100", "x.md"
        )
        assert metrics.roe == 15.0
        assert any(
            i.rule_id == "EXTRACT-002" and i.severity == "WARNING" for i in issues
        )


@pytest.mark.unit
class TestZeroExtractionWarning:
    """一个指标都没提取到的文件应产生 WARNING，杜绝 vacuous green。"""

    def test_zero_extraction_file_warns(self, auditor, tmp_path):
        ticker_dir = tmp_path / "600050"
        ticker_dir.mkdir()
        (ticker_dir / "complete_report.md").write_text(
            "这份报告没有任何可提取的财务指标。", encoding="utf-8"
        )
        results = auditor.ReportAuditor(str(tmp_path)).audit()
        issues = results["600050"].issues
        assert any(
            i.rule_id == "EXTRACT-001" and i.severity == "WARNING" for i in issues
        )

    def test_no_warning_when_metrics_extracted(self, auditor, tmp_path):
        ticker_dir = tmp_path / "600050"
        ticker_dir.mkdir()
        (ticker_dir / "complete_report.md").write_text(
            "毛利率: 25.0%", encoding="utf-8"
        )
        results = auditor.ReportAuditor(str(tmp_path)).audit()
        issues = results["600050"].issues
        assert not any(i.rule_id == "EXTRACT-001" for i in issues)


@pytest.mark.unit
class TestDataReliabilityAudit:
    def _write_ticker(self, batch, ticker):
        ticker_dir = batch / ticker
        ticker_dir.mkdir()
        (ticker_dir / "complete_report.md").write_text(
            "ROE: 15.0%", encoding="utf-8"
        )

    def test_auditor_reports_missing_without_flagging_neutral_states(
        self, auditor, tmp_path
    ):
        self._write_ticker(tmp_path, "600519")
        self._write_ticker(tmp_path, "1810.HK")
        rows = [
            {
                "ticker": "600519",
                "data_reliability": {
                    "confirmed": 2,
                    "fallback_success": 1,
                    "valid_empty": 0,
                    "not_applicable": 0,
                    "partial": 1,
                    "missing": 1,
                    "logical_requests": 5,
                    "applicable_requests": 5,
                    "call_count": 7,
                },
            },
            {
                "ticker": "1810.HK",
                "data_reliability": {
                    "confirmed": 1,
                    "fallback_success": 0,
                    "valid_empty": 1,
                    "not_applicable": 3,
                    "partial": 0,
                    "missing": 0,
                    "logical_requests": 5,
                    "applicable_requests": 2,
                    "call_count": 5,
                },
            },
        ]
        (tmp_path / "batch_summary.json").write_text(
            json.dumps({"rows": rows}), encoding="utf-8"
        )

        report_auditor = auditor.ReportAuditor(str(tmp_path))
        results = report_auditor.audit()

        assert results["600519"].data_reliability["missing"] == 1
        assert any(
            issue.rule_id == "DATA-RELIABILITY-MISSING"
            for issue in results["600519"].issues
        )
        assert not any(
            issue.rule_id.startswith("DATA-RELIABILITY")
            for issue in results["1810.HK"].issues
        )

        md_path = Path(report_auditor.generate_report(str(tmp_path / "audit")))
        md = md_path.read_text(encoding="utf-8")
        json_path = next((tmp_path / "audit").glob("audit_report_*.json"))
        data = json.loads(json_path.read_text(encoding="utf-8"))

        assert "## 数据可靠性" in md
        assert "DATA-RELIABILITY-PARTIAL" in md
        assert "[600519]" in md
        assert data["results"]["1810.HK"]["data_reliability"]["not_applicable"] == 3
        assert data["summary"]["data_reliability"]["missing"] == 1

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"rows": None},
            {"rows": [None, "bad row"]},
        ],
    )
    def test_auditor_ignores_legacy_or_malformed_summary_shapes(
        self, auditor, tmp_path, payload
    ):
        self._write_ticker(tmp_path, "600519")
        (tmp_path / "batch_summary.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        results = auditor.ReportAuditor(str(tmp_path)).audit()

        assert results["600519"].files_audited == 1
        assert results["600519"].data_reliability == {}

    def test_auditor_reads_legacy_list_summary_format(self, auditor, tmp_path):
        self._write_ticker(tmp_path, "600519")
        (tmp_path / "batch_summary.json").write_text(
            json.dumps(
                [
                    {
                        "ticker": "600519",
                        "data_reliability": {
                            "confirmed": 1,
                            "logical_requests": 1,
                            "applicable_requests": 1,
                            "call_count": 2,
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        results = auditor.ReportAuditor(str(tmp_path)).audit()

        assert results["600519"].data_reliability["confirmed"] == 1
        assert results["600519"].data_reliability["call_count"] == 2

    def test_auditor_recomputes_inconsistent_summary_denominators(
        self, auditor, tmp_path
    ):
        self._write_ticker(tmp_path, "600519")
        reliability = {
            "confirmed": 2,
            "fallback_success": 0,
            "valid_empty": 0,
            "not_applicable": 1,
            "partial": 0,
            "missing": 1,
            "logical_requests": 999,
            "applicable_requests": 999,
            "call_count": 1,
        }
        (tmp_path / "batch_summary.json").write_text(
            json.dumps({"rows": [{"ticker": "600519", "data_reliability": reliability}]}),
            encoding="utf-8",
        )

        result = auditor.ReportAuditor(str(tmp_path)).audit()["600519"]

        assert result.data_reliability["logical_requests"] == 4
        assert result.data_reliability["applicable_requests"] == 3
        assert result.data_reliability["call_count"] == 4
