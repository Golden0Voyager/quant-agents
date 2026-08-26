"""Tests for scripts/rating_backtest.py calibration analysis (单调性/完整度切片)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "rating_backtest.py"


def _load_module():
    """Load scripts/rating_backtest.py as a module (scripts/ not on sys.path)."""
    if "rating_backtest" in sys.modules:
        return sys.modules["rating_backtest"]
    spec = importlib.util.spec_from_file_location("rating_backtest", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rating_backtest"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rb():
    return _load_module()


def _outcome(rating, excess_5, ret_5=None, **kw):
    o = {
        "batch_date": "2026-08-25",
        "ticker": "600519",
        "rating": rating,
        "confidence": kw.pop("confidence", "medium"),
        "excess_5": excess_5,
        "ret_5": excess_5 if ret_5 is None else ret_5,
    }
    o.update(kw)
    return o


class TestRanks:
    def test_simple_ordering(self, rb):
        assert rb._ranks([30.0, 10.0, 20.0]) == [3.0, 1.0, 2.0]

    def test_ties_share_average_rank(self, rb):
        assert rb._ranks([1.0, 2.0, 2.0, 4.0]) == [1.0, 2.5, 2.5, 4.0]


class TestSpearman:
    def test_perfect_monotone(self, rb):
        assert rb.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)

    def test_perfect_reverse(self, rb):
        assert rb.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_too_few_points_returns_none(self, rb):
        assert rb.spearman([1, 2], [1, 2]) is None

    def test_zero_variance_returns_none(self, rb):
        assert rb.spearman([1, 1, 1], [1, 2, 3]) is None


class TestMonotonicity:
    def test_well_calibrated_system_scores_high_rho(self, rb):
        outcomes = [
            _outcome("Buy", 8.0), _outcome("Buy", 6.0),
            _outcome("Overweight", 3.0),
            _outcome("Hold", 0.5),
            _outcome("Underweight", -2.0),
            _outcome("Sell", -6.0), _outcome("Sell", -4.0),
        ]
        stats = rb.monotonicity_by_horizon(outcomes, horizons=(5,))[5]
        assert stats["rho"] == pytest.approx(1.0)
        assert stats["means"]["Buy"] == pytest.approx(7.0)
        assert stats["counts"]["Sell"] == 2

    def test_inverted_system_scores_negative(self, rb):
        outcomes = [
            _outcome("Buy", -5.0),
            _outcome("Hold", 0.0),
            _outcome("Sell", 5.0),
        ]
        rho = rb.monotonicity_by_horizon(outcomes, horizons=(5,))[5]["rho"]
        assert rho == pytest.approx(-1.0)

    def test_missing_buckets_reduces_points(self, rb):
        outcomes = [_outcome("Buy", 5.0), _outcome("Sell", -5.0)]
        stats = rb.monotonicity_by_horizon(outcomes, horizons=(5,))[5]
        # 两个分桶 → 不足 3 点 → None
        assert stats["rho"] is None
        assert set(stats["means"]) == {"Buy", "Sell"}

    def test_rows_without_horizon_data_excluded(self, rb):
        outcomes = [
            _outcome("Buy", 5.0),
            {"batch_date": "2026-08-25", "ticker": "000001", "rating": "Sell",
             "excess_5": None, "ret_5": None},
        ]
        stats = rb.monotonicity_by_horizon(outcomes, horizons=(5,))[5]
        assert "Sell" not in stats["means"]


class TestCompletenessSlice:
    def test_degraded_flag_from_fallback_or_coverage(self, rb):
        outcomes = [
            _outcome("Sell", -3.0, fallback=1),
            _outcome("Sell", -2.0, coverage_degraded=2),
            _outcome("Buy", 1.0, fallback=0, coverage_degraded=0),
            _outcome("Buy", 2.0),
        ]
        buckets = rb.completeness_by_horizon(outcomes, horizons=(5,))[5]
        assert len(buckets["degraded"]) == 2
        assert len(buckets["clean"]) == 2

    def test_hold_excluded_from_slice(self, rb):
        outcomes = [_outcome("Hold", 0.1, fallback=1)]
        buckets = rb.completeness_by_horizon(outcomes, horizons=(5,))[5]
        assert not buckets["degraded"] and not buckets["clean"]


class TestLoadRatingsSlices:
    def test_captures_fallback_and_coverage(self, rb, tmp_path):
        batch = tmp_path / "20260825_batch_x"
        batch.mkdir()
        (batch / "batch_summary.json").write_text(
            json.dumps({"rows": [
                {"ticker": "600519.SS", "rating": "Buy", "confidence": "high",
                 "entry": "1300", "stop": "1250", "fallback": False,
                 "coverage_degraded": 0},
                {"ticker": "000603", "rating": "Sell", "confidence": "low",
                 "entry": "—", "stop": None, "fallback": True,
                 "coverage_degraded": 3},
            ]}),
            encoding="utf-8",
        )
        ratings = rb.load_ratings(tmp_path)
        assert len(ratings) == 2
        clean, degraded = ratings
        assert clean["fallback"] == 0 and clean["coverage_degraded"] == 0
        assert degraded["fallback"] == 1 and degraded["coverage_degraded"] == 3

    def test_missing_slice_fields_default(self, rb, tmp_path):
        batch = tmp_path / "20260820_batch_y"
        batch.mkdir()
        (batch / "batch_summary.json").write_text(
            json.dumps([{"ticker": "600519", "rating": "Hold"}]),
            encoding="utf-8",
        )
        (rating,) = rb.load_ratings(tmp_path)
        assert rating["fallback"] == 0
        assert rating["coverage_degraded"] is None


class TestRenderSections:
    def test_report_contains_new_sections(self, rb):
        outcomes = [
            _outcome("Buy", 6.0, confidence="high"),
            _outcome("Hold", 1.0),
            _outcome("Sell", -4.0, fallback=1),
        ]
        report = rb.render_report(outcomes)
        assert "单调性检验" in report
        assert "数据完整度 × 方向命中率" in report
        assert "置信度 × 方向命中率" in report
        # 5 日窗口行应有实际数据
        assert "| 5日 | " in report
