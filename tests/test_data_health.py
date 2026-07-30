"""Unit tests for the batch pre-flight data health gate (dataflows/data_health.py)."""

from __future__ import annotations

import sqlite3

import pytest

from tradingagents.dataflows.data_health import HealthReport, check_data_health

pytestmark = pytest.mark.unit

ANALYSIS_DATE = "2026-07-30"


def _make_db(
    path,
    *,
    bar_date: str = "2026-07-30",
    turnover_fill: float = 1.0,
    chip_date: str = "2026-07-30",
    chip_zero_ratio: float = 0.0,
    dividend_fill: float = 1.0,
    n: int = 100,
) -> str:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE daily_bars (ts_code TEXT, trade_date TEXT, turnover_rate REAL);
        CREATE TABLE chip_distribution_em (ts_code TEXT, trade_date TEXT, profit_ratio REAL, avg_cost REAL);
        CREATE TABLE fundamentals (ts_code TEXT, trade_date TEXT, dividend_yield REAL);
        """
    )
    for i in range(n):
        code = f"{i:06d}"
        conn.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?)",
            (code, bar_date, 2.5 if i < n * turnover_fill else None),
        )
        is_zero = i < n * chip_zero_ratio
        conn.execute(
            "INSERT INTO chip_distribution_em VALUES (?, ?, ?, ?)",
            (code, chip_date, 0.0 if is_zero else 0.5, 0.0 if is_zero else 10.0),
        )
        conn.execute(
            "INSERT INTO fundamentals VALUES (?, ?, ?)",
            (code, bar_date, 2.1 if i < n * dividend_fill else None),
        )
    conn.commit()
    conn.close()
    return str(path)


class TestHealthyDb:
    def test_all_ok(self, tmp_path):
        db = _make_db(tmp_path / "ok.db")
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        assert r.level == "ok"
        assert r.issues == []
        assert r.blockers == []
        assert r.summary_lines() == []


class TestWarnConditions:
    def test_low_turnover_fill_warns(self, tmp_path):
        db = _make_db(tmp_path / "w1.db", turnover_fill=0.5)  # 50% < 60%
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        assert r.level == "warn"
        assert any("换手率" in m for m in r.issues)

    def test_chip_zero_rate_warns(self, tmp_path):
        db = _make_db(tmp_path / "w2.db", chip_zero_ratio=0.10)  # 10% > 5%
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        assert r.level == "warn"
        assert any("全零率" in m for m in r.issues)

    def test_low_dividend_fill_warns(self, tmp_path):
        db = _make_db(tmp_path / "w3.db", dividend_fill=0.2)  # 20% < 40%
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        assert r.level == "warn"
        assert any("股息率" in m for m in r.issues)

    def test_stale_bars_warns(self, tmp_path):
        db = _make_db(tmp_path / "w4.db", bar_date="2026-07-26", chip_date="2026-07-26")
        r = check_data_health(ANALYSIS_DATE, db_path=db)  # gap=4 天
        assert r.level == "warn"
        assert any("滞后" in m for m in r.issues)

    def test_missing_db_downgrades_to_warn(self, tmp_path):
        r = check_data_health(ANALYSIS_DATE, db_path=str(tmp_path / "nope.db"))
        assert r.level == "warn"
        assert any("不存在" in m for m in r.issues)

    def test_missing_table_downgrades_to_warn(self, tmp_path):
        db_path = tmp_path / "empty.db"
        sqlite3.connect(str(db_path)).close()
        r = check_data_health(ANALYSIS_DATE, db_path=str(db_path))
        assert r.level == "warn"


class TestBlockConditions:
    def test_chip_majority_zero_blocks(self, tmp_path):
        """2026-07 事故形态：筹码大面积全零 → 必须 BLOCK。"""
        db = _make_db(tmp_path / "b1.db", chip_zero_ratio=0.9)
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        assert r.level == "block"
        assert any("全零率" in m for m in r.blockers)

    def test_very_stale_bars_blocks(self, tmp_path):
        db = _make_db(tmp_path / "b2.db", bar_date="2026-07-20", chip_date="2026-07-20")
        r = check_data_health(ANALYSIS_DATE, db_path=db)  # gap=10 天 ≥ 7
        assert r.level == "block"

    def test_turnover_near_zero_blocks(self, tmp_path):
        """换手率整列丢失（重复列 bug 形态）→ BLOCK。"""
        db = _make_db(tmp_path / "b3.db", turnover_fill=0.05)
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        assert r.level == "block"

    def test_summary_lines_render_blockers_first(self, tmp_path):
        db = _make_db(tmp_path / "b4.db", chip_zero_ratio=0.9, dividend_fill=0.1)
        r = check_data_health(ANALYSIS_DATE, db_path=db)
        lines = r.summary_lines()
        assert lines[0] == "> [!WARNING]"
        joined = "\n".join(lines)
        assert "🛑" in joined
        assert "⚠️" in joined
        # blocker 行必须出现在 warn 行之前
        assert joined.index("🛑") < joined.index("⚠️")


class TestHealthReportEscalate:
    def test_warn_does_not_downgrade_block(self):
        r = HealthReport()
        r.escalate("block", "b")
        r.escalate("warn", "w")
        assert r.level == "block"
