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
