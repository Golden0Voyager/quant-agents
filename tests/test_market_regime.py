"""Unit tests for the shared market regime digest/report."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from tradingagents.dataflows import market_regime

FAKE_DIGEST = {
    "trade_date": "2026-09-29",
    "indices": [
        {
            "name": "上证指数",
            "code": "sh000001",
            "available": True,
            "date": "2026-09-29",
            "close": 3850.12,
            "pct_change": 0.72,
            "pct_change_5d": 2.1,
            "range_position_20d": 0.85,
            "above_20d_high": True,
        },
        {
            "name": "科创50",
            "code": "sh000688",
            "available": False,
        },
    ],
    "breadth": {
        "date": "2026-09-29",
        "up_count": 3200,
        "down_count": 1800,
        "flat_count": 200,
        "limit_up": 85,
        "limit_down": 4,
        "real_limit_up": 60,
        "real_limit_down": 2,
        "high20": 450,
        "low20": 30,
        "high60": 300,
        "low60": 55,
        "high120": 180,
        "low120": 90,
        "below_net_asset_ratio": 8.5,
        "activity_ratio": 22.0,
    },
}


@pytest.fixture(autouse=True)
def _clear_cache():
    market_regime.clear_market_regime_cache()
    yield
    market_regime.clear_market_regime_cache()


def _digest_with_data_date(data_date: str) -> dict:
    """FAKE_DIGEST with the available index row moved to ``data_date``."""
    digest = copy.deepcopy(FAKE_DIGEST)
    digest["indices"][0]["date"] = data_date
    digest["breadth"]["date"] = data_date
    return digest


@pytest.mark.unit
class TestRenderDigest:
    def test_renders_index_rows_and_breadth(self):
        text = market_regime.render_market_regime_digest(FAKE_DIGEST)
        assert "上证指数" in text
        assert "3850.12" in text
        assert "+0.72%" in text
        assert "+2.10%" in text
        assert "85%" in text
        assert "3200" in text and "1800" in text
        assert "涨停 85" in text
        assert "20日新高 450" in text
        assert "8.5%" in text

    def test_missing_index_marked_unavailable(self):
        text = market_regime.render_market_regime_digest(FAKE_DIGEST)
        assert "科创50" in text
        assert "DATA_UNAVAILABLE" in text

    def test_missing_breadth_marked_unavailable(self):
        digest = {**FAKE_DIGEST, "breadth": None}
        text = market_regime.render_market_regime_digest(digest)
        assert "市场宽度数据 DATA_UNAVAILABLE" in text


@pytest.mark.unit
class TestExtractDataDate:
    def test_uses_newest_available_index_date(self):
        digest = copy.deepcopy(FAKE_DIGEST)
        digest["indices"][0]["date"] = "2026-09-29"
        assert market_regime.extract_data_date(digest) == "2026-09-29"

    def test_ignores_unavailable_rows(self):
        # the unavailable row has no date at all; available rows decide
        digest = copy.deepcopy(FAKE_DIGEST)
        digest["indices"][0]["date"] = "2026-09-26"
        assert market_regime.extract_data_date(digest) == "2026-09-26"

    def test_falls_back_to_trade_date_when_all_unavailable(self):
        digest = {
            "trade_date": "2026-09-29",
            "indices": [{"name": "上证指数", "code": "sh000001", "available": False}],
            "breadth": None,
        }
        assert market_regime.extract_data_date(digest) == "2026-09-29"


@pytest.mark.unit
class TestSynthesize:
    def _llm_returning(self, content: str):
        llm = MagicMock()
        response = MagicMock()
        response.content = content
        llm.invoke.return_value = response
        return llm

    def test_synthesis_returns_llm_content(self, monkeypatch):
        monkeypatch.setattr(
            "tradingagents.agents.utils.agent_utils.get_language_instruction",
            lambda: "",
        )
        llm = self._llm_returning("🟡 中等风险，➡️ 震荡。报告正文……")
        report, synthesized = market_regime.synthesize_market_regime_report("digest text", llm)
        assert report == "🟡 中等风险，➡️ 震荡。报告正文……"
        assert synthesized is True
        llm.invoke.assert_called_once()

    def test_synthesis_fails_open_to_digest(self, monkeypatch):
        monkeypatch.setattr(
            "tradingagents.agents.utils.agent_utils.get_language_instruction",
            lambda: "",
        )
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("provider down")
        report, synthesized = market_regime.synthesize_market_regime_report("digest text", llm)
        assert report == "digest text"
        assert synthesized is False

    def test_synthesis_fails_open_on_empty_content(self, monkeypatch):
        monkeypatch.setattr(
            "tradingagents.agents.utils.agent_utils.get_language_instruction",
            lambda: "",
        )
        report, synthesized = market_regime.synthesize_market_regime_report(
            "digest text", self._llm_returning("   ")
        )
        assert report == "digest text"
        assert synthesized is False


@pytest.mark.unit
class TestOrchestratorCache:
    def _patch_pipeline(self, monkeypatch):
        monkeypatch.setattr(
            market_regime, "build_market_regime_digest", lambda date: FAKE_DIGEST
        )
        monkeypatch.setattr(
            "tradingagents.agents.utils.agent_utils.get_language_instruction",
            lambda: "",
        )

    def _llm(self, content: str = "合成报告"):
        llm = MagicMock()
        llm.invoke.return_value.content = content
        return llm

    def test_process_cache_serves_second_call_without_llm(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch)
        llm = self._llm()
        first = market_regime.get_market_regime_report(
            "2026-09-29", llm=llm, data_cache_dir=str(tmp_path)
        )
        second = market_regime.get_market_regime_report(
            "2026-09-29", llm=llm, data_cache_dir=str(tmp_path)
        )
        assert first == second == "合成报告"
        assert llm.invoke.call_count == 1  # one synthesis for the whole batch

    def test_disk_cache_survives_process_cache_clear(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch)
        llm = self._llm()
        market_regime.get_market_regime_report(
            "2026-09-29", llm=llm, data_cache_dir=str(tmp_path)
        )
        market_regime.clear_market_regime_cache()
        report = market_regime.get_market_regime_report(
            "2026-09-29", llm=None, data_cache_dir=str(tmp_path)
        )
        assert report == "合成报告"
        assert llm.invoke.call_count == 1

    def test_without_llm_returns_raw_digest(self, monkeypatch):
        self._patch_pipeline(monkeypatch)
        report = market_regime.get_market_regime_report("2026-09-29", llm=None)
        assert "上证指数" in report
        assert "市场宽度" in report

    def test_disk_file_name_carries_data_date(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch)
        market_regime.get_market_regime_report(
            "2026-09-29", llm=self._llm(), data_cache_dir=str(tmp_path)
        )
        assert (tmp_path / "market_regime_2026-09-29_2026-09-29.md").exists()


@pytest.mark.unit
class TestDataDateInvalidation:
    """The pipeline refreshes quant_core.db intraday, so a second run on the
    same calendar date but newer data must not reuse the first summary."""

    def _patch_pipeline(self, monkeypatch, digests):
        queue = list(digests)

        def _next(date):
            return queue.pop(0) if len(queue) > 1 else queue[0]

        monkeypatch.setattr(market_regime, "build_market_regime_digest", _next)
        monkeypatch.setattr(
            "tradingagents.agents.utils.agent_utils.get_language_instruction",
            lambda: "",
        )

    def _llm(self, content: str = "合成报告"):
        llm = MagicMock()
        llm.invoke.return_value.content = content
        return llm

    def test_newer_data_date_resynthesizes(self, monkeypatch, tmp_path):
        self._patch_pipeline(
            monkeypatch,
            [_digest_with_data_date("2026-09-29"), _digest_with_data_date("2026-09-30")],
        )
        first_llm = self._llm("早盘摘要")
        second_llm = self._llm("收盘摘要")

        assert market_regime.get_market_regime_report(
            "2026-09-29", llm=first_llm, data_cache_dir=str(tmp_path)
        ) == "早盘摘要"
        assert market_regime.get_market_regime_report(
            "2026-09-29", llm=second_llm, data_cache_dir=str(tmp_path)
        ) == "收盘摘要"
        assert first_llm.invoke.call_count == 1
        assert second_llm.invoke.call_count == 1  # stale summary was not reused
        assert (tmp_path / "market_regime_2026-09-29_2026-09-30.md").exists()

    def test_same_data_date_hits_process_cache(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch, [_digest_with_data_date("2026-09-29")])
        llm = self._llm()
        market_regime.get_market_regime_report(
            "2026-09-29", llm=llm, data_cache_dir=str(tmp_path)
        )
        market_regime.get_market_regime_report(
            "2026-09-29", llm=llm, data_cache_dir=str(tmp_path)
        )
        assert llm.invoke.call_count == 1

    def test_newer_data_date_bypasses_stale_disk_cache(self, monkeypatch, tmp_path):
        """Old-naming files are ignored; same data_date still reads from disk."""
        self._patch_pipeline(monkeypatch, [_digest_with_data_date("2026-09-29")])
        market_regime.get_market_regime_report(
            "2026-09-29", llm=self._llm(), data_cache_dir=str(tmp_path)
        )
        market_regime.clear_market_regime_cache()
        llm = self._llm()
        report = market_regime.get_market_regime_report(
            "2026-09-29", llm=llm, data_cache_dir=str(tmp_path)
        )
        assert report == "合成报告"
        assert llm.invoke.call_count == 0  # served from disk


@pytest.mark.unit
class TestFailedSynthesisNotCached:
    """One provider outage must not cost the day its regime report."""

    def _patch_pipeline(self, monkeypatch):
        monkeypatch.setattr(
            market_regime, "build_market_regime_digest", lambda date: FAKE_DIGEST
        )
        monkeypatch.setattr(
            "tradingagents.agents.utils.agent_utils.get_language_instruction",
            lambda: "",
        )

    def test_failed_synthesis_writes_no_disk_cache(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch)
        broken = MagicMock()
        broken.invoke.side_effect = RuntimeError("provider down")

        report = market_regime.get_market_regime_report(
            "2026-09-29", llm=broken, data_cache_dir=str(tmp_path)
        )
        assert "上证指数" in report  # fail-open digest still returned
        assert list(tmp_path.iterdir()) == []  # nothing persisted

    def test_recovery_after_failure_synthesizes_and_persists(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch)
        broken = MagicMock()
        broken.invoke.side_effect = RuntimeError("provider down")
        market_regime.get_market_regime_report(
            "2026-09-29", llm=broken, data_cache_dir=str(tmp_path)
        )

        healthy = MagicMock()
        healthy.invoke.return_value.content = "收盘摘要"
        report = market_regime.get_market_regime_report(
            "2026-09-29", llm=healthy, data_cache_dir=str(tmp_path)
        )
        assert report == "收盘摘要"  # not poisoned by the earlier bare digest
        assert healthy.invoke.call_count == 1
        assert (tmp_path / "market_regime_2026-09-29_2026-09-29.md").read_text(
            encoding="utf-8"
        ) == "收盘摘要"

    def test_digest_only_path_does_not_poison_later_synthesis(self, monkeypatch, tmp_path):
        self._patch_pipeline(monkeypatch)
        digest_only = market_regime.get_market_regime_report(
            "2026-09-29", llm=None, data_cache_dir=str(tmp_path)
        )
        assert "上证指数" in digest_only
        assert list(tmp_path.iterdir()) == []  # digest-only never persisted

        healthy = MagicMock()
        healthy.invoke.return_value.content = "收盘摘要"
        report = market_regime.get_market_regime_report(
            "2026-09-29", llm=healthy, data_cache_dir=str(tmp_path)
        )
        assert report == "收盘摘要"
        assert healthy.invoke.call_count == 1


@pytest.mark.unit
class TestBreadthGuard:
    def test_all_zero_breadth_row_treated_as_unavailable(self, monkeypatch):
        zero_row = {
            "date": "2026-09-28",
            "up_count": 0,
            "down_count": 0,
            "flat_count": 0,
            "limit_up": 0,
            "limit_down": 0,
        }
        monkeypatch.setattr(
            "tradingagents.dataflows.smartmoney_vendor.get_market_breadth",
            lambda date: zero_row,
        )
        assert market_regime._fetch_breadth("2026-09-28") is None

    def test_real_breadth_row_passes_through(self, monkeypatch):
        row = {"date": "2026-09-28", "up_count": 100, "down_count": 50, "limit_up": 10}
        monkeypatch.setattr(
            "tradingagents.dataflows.smartmoney_vendor.get_market_breadth",
            lambda date: row,
        )
        assert market_regime._fetch_breadth("2026-09-28") == row
