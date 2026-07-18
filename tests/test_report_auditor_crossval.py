"""Tests for the report_auditor cross-validation feature."""
import importlib.util
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
