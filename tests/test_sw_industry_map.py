"""申万行业归属缓存与板块名权威解析的单元测试。

覆盖三条判定规则（这是"不产生错误数据"的保证）：
1. 申万权威名精确/唯一命中本地板块 → 采用
2. 申万权威名无法唯一命中 → 降级（抛 NoMarketDataError），不猜
3. 缓存缺失 / 未收录该票 → 降级
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from tradingagents.dataflows import smartmoney_vendor as smv
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.sw_industry_map import (
    build_sw_industry_map,
    get_sw_industry,
    get_sw_industry_map_age_days,
    sw_industry_map_path,
)

SECOND_INFO = pd.DataFrame(
    [
        {"行业代码": "801016.SI", "行业名称": "种植业", "上级行业": "农林牧渔"},
        {"行业代码": "801014.SI", "行业名称": "饲料", "上级行业": "农林牧渔"},
        {"行业代码": "801010.SI", "行业名称": "农林牧渔", "上级行业": "农林牧渔"},
    ]
)

THIRD_INFO = pd.DataFrame(
    [
        {"行业代码": "850111.SI", "行业名称": "种子", "上级行业": "种植业"},
        {"行业代码": "850112.SI", "行业名称": "粮食种植", "上级行业": "种植业"},
        {"行业代码": "850121.SI", "行业名称": "饲料", "上级行业": "饲料"},
    ]
)

CONS = {
    "850111.SI": ["300175", "600519"],
    "850112.SI": ["000998"],
    "850121.SI": ["002714"],
}
BROKEN = {"850121.SI"}


class _FakeClient:
    def __init__(self, broken: set[str] | None = None):
        self.broken = broken or set()
        self.calls: list[str] = []

    def second_info(self):
        return SECOND_INFO

    def third_info(self):
        return THIRD_INFO

    def third_cons(self, symbol: str):
        self.calls.append(symbol)
        if symbol in self.broken:
            raise RuntimeError("shenwan page flaky")
        return pd.DataFrame({"股票代码": CONS.get(symbol, [])})


class _GappedClient(_FakeClient):
    """指定行业抓不到，用来构造"部分覆盖"的缓存。"""

    def __init__(self, gaps: tuple[str, ...] = ("850112.SI",)):
        super().__init__()
        self.gaps = gaps
        self.seen: list[str] = []

    def third_cons(self, symbol: str):
        self.seen.append(symbol)
        if symbol in self.gaps:
            return pd.DataFrame({"股票代码": []})
        return super().third_cons(symbol)


def _build_partial(path: Path, gaps: tuple[str, ...] = ("850112.SI",)) -> _GappedClient:
    client = _GappedClient(gaps)
    build_sw_industry_map(path, client=client, retries=1, max_workers=1)
    return client


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "sw_industry_map.db"
    monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
    build_sw_industry_map(path, client=_FakeClient())
    return path


@pytest.mark.unit
class TestBuildMap:
    def test_writes_industry_hierarchy_with_level1(self, cache):
        con = sqlite3.connect(cache)
        try:
            rows = {
                r[0]: (r[1], r[2], r[3])
                for r in con.execute("SELECT name, level1, level2, level3 FROM sw_industry")
            }
        finally:
            con.close()
        assert rows["种子"] == ("农林牧渔", "种植业", "种子")
        assert rows["粮食种植"] == ("农林牧渔", "种植业", "粮食种植")

    def test_normalizes_ticker_to_digits(self, cache):
        assert get_sw_industry("300175.SZ") == ("农林牧渔", "种植业", "种子")
        assert get_sw_industry("300175") == ("农林牧渔", "种植业", "种子")

    def test_records_fetched_at_for_staleness(self, cache):
        age = get_sw_industry_map_age_days(cache)
        assert age is not None and age < 0.01

    def test_survives_flaky_industry_and_keeps_others(self, tmp_path, monkeypatch):
        path = tmp_path / "partial.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        stats = build_sw_industry_map(path, client=_FakeClient(broken=BROKEN))
        assert stats["errors"] == 1
        assert get_sw_industry("300175") == ("农林牧渔", "种植业", "种子")
        assert get_sw_industry("002714") is None

    def test_failed_build_keeps_previous_cache(self, tmp_path, monkeypatch):
        path = tmp_path / "partial.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        _build_partial(path)
        before = get_sw_industry("300175")
        assert get_sw_industry("000998") is None, "前提：该行业尚未覆盖，本轮会真去抓"

        def boom(_symbol):
            raise RuntimeError("no network")

        client = _GappedClient()
        client.third_cons = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            build_sw_industry_map(path, client=client, retries=1)
        assert get_sw_industry("300175") == before
        assert get_sw_industry("000998") is None

    def test_upstream_metadata_outage_falls_back_to_cached_hierarchy(self, tmp_path, monkeypatch):
        """名单是静态的：上游挂了应沿用缓存名单继续扫成分股，而不是废掉整轮。"""
        path = tmp_path / "partial.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        _build_partial(path)

        client = _GappedClient(gaps=())
        client.second_info = lambda: (_ for _ in ()).throw(RuntimeError("blocked"))  # type: ignore[method-assign]
        client.third_info = lambda: (_ for _ in ()).throw(RuntimeError("blocked"))  # type: ignore[method-assign]
        stats = build_sw_industry_map(path, client=client, retries=1)
        assert stats["hierarchy_source"] == "cache"
        assert stats["pending"] == 1
        assert stats["stocks"] > 0
        assert get_sw_industry("000998") == ("农林牧渔", "种植业", "粮食种植")

    def test_metadata_outage_without_cache_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "fresh.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        client = _FakeClient()
        client.third_info = lambda: (_ for _ in ()).throw(RuntimeError("blocked"))  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="no cached copy"):
            build_sw_industry_map(path, client=client, retries=1)
        assert not path.exists()

    def test_interrupted_run_resumes_instead_of_restarting(self, tmp_path, monkeypatch):
        """限流下"跑到一半被杀"必须能续跑，否则每次净损失。

        把一轮"只抓到 2/3 行业"的结果当作中断现场留在 tmp 里，下一轮应只补
        缺的那一个，已抓过的行业不重复请求。
        """
        path = tmp_path / "resume.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        tmp = path.with_suffix(path.suffix + ".tmp")

        gapped = _build_partial(path)
        assert get_sw_industry("000998") is None, "该行业本轮抓不到，应留空"
        assert get_sw_industry("002714") is not None, "已抓到的行业不受影响"
        path.replace(tmp)
        assert not path.exists()

        resumed = _GappedClient(gaps=())
        stats = build_sw_industry_map(path, client=resumed, retries=1, max_workers=1)

        assert resumed.seen == ["850112.SI"], "只该补抓缺的那个行业"
        assert stats["already_covered"] == 2
        assert get_sw_industry("300175") == ("农林牧渔", "种植业", "种子")
        assert get_sw_industry("000998") == ("农林牧渔", "种植业", "粮食种植")
        assert get_sw_industry("002714") == ("农林牧渔", "饲料", "饲料")
        assert not tmp.exists(), "成功后 tmp 应被正式缓存取代"
        assert gapped.seen, "首轮确实抓过"

    def test_industry_filter_skips_unusable_industries(self, tmp_path, monkeypatch):
        """只抓解析时用得上的行业，把限流预算留给有用的。"""
        path = tmp_path / "filtered.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        client = _GappedClient(gaps=())
        stats = build_sw_industry_map(
            path,
            client=client,
            retries=1,
            industry_filter=lambda level2, level3: "种植业" in (level2, level3),
        )
        assert stats["industries"] == 3
        assert stats["candidates"] == 2
        assert set(client.seen) == {"850111.SI", "850112.SI"}
        assert get_sw_industry("300175") == ("农林牧渔", "种植业", "种子")
        assert get_sw_industry("002714") is None, "被过滤掉的行业不该发请求"

    def test_fully_covered_cache_makes_a_successful_noop_run(self, tmp_path, monkeypatch):
        """全部行业已覆盖时 pending 为空：不发请求、不报错、缓存内容不变。"""
        path = tmp_path / "guarded.db"
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(path))
        build_sw_industry_map(path, client=_FakeClient())
        before = get_sw_industry("300175")

        def blocked(_symbol):
            raise RuntimeError("throttled")

        client = _FakeClient()
        client.third_cons = blocked  # type: ignore[method-assign]
        stats = build_sw_industry_map(path, client=client, retries=1)
        assert stats["pending"] == 0
        assert stats["stocks"] == 0
        assert get_sw_industry("300175") == before


@pytest.mark.unit
class TestLookupsWhenUnavailable:
    def test_missing_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(tmp_path / "nope.db"))
        assert get_sw_industry("300175") is None
        assert get_sw_industry_map_age_days(tmp_path / "nope.db") is None

    def test_non_a_share_ticker_digits_are_just_looked_up(self, cache):
        assert get_sw_industry("1810.HK") is None
        assert get_sw_industry("") is None

    def test_uncovered_ticker_returns_none(self, cache):
        assert get_sw_industry("600000") is None


@pytest.mark.unit
class TestAuthoritativeSectorResolution:
    NAMES = ["白酒", "半导体", "消费电子", "种植业与林业", "农产品加工", "饮料制造", "饮料加工"]

    def _resolve(self, monkeypatch, sector: str, ticker: str | None, legacy: str = "农牧饲渔"):
        monkeypatch.setenv("TRADINGAGENTS_SW_INDUSTRY_MAP", str(self.cache_path))
        monkeypatch.setattr(smv, "_registered_industry", lambda _t: legacy)
        return smv._resolve_sector_with_fallbacks(sector, self.NAMES, ticker)

    @pytest.fixture(autouse=True)
    def _cache(self, tmp_path, monkeypatch):
        self.cache_path = tmp_path / "sw.db"
        build_sw_industry_map(self.cache_path, client=_FakeClient())

    def test_authoritative_sw_name_wins_over_legacy_gate(self, monkeypatch):
        resolved, note = self._resolve(monkeypatch, "农牧饲渔", "300175.SZ")
        assert resolved == "种植业与林业"
        assert "申万二级 '种植业'" in note

    def test_respects_audit_note_in_output(self, monkeypatch):
        _, note = self._resolve(monkeypatch, "农牧饲渔", "300175.SZ")
        assert "300175.SZ" in note and "种植业与林业" in note

    def test_degrades_when_authority_absent_from_local_sectors(self, monkeypatch):
        with pytest.raises(NoMarketDataError):
            self._resolve(monkeypatch, "农牧饲渔", "600000.SS")

    def test_degrades_without_ticker(self, monkeypatch):
        with pytest.raises(NoMarketDataError):
            self._resolve(monkeypatch, "农牧饲渔", None)

    def test_never_fabricates_when_authority_ambiguous(self, monkeypatch):
        monkeypatch.setattr(smv, "get_sw_industry", lambda _t: ("食品饮料", "饮料", ""))
        with pytest.raises(NoMarketDataError):
            self._resolve(monkeypatch, "农牧饲渔", "300175.SZ")

    def test_direct_hit_annotates_automatic_match(self, monkeypatch):
        resolved, note = self._resolve(monkeypatch, "白酒行业", "300175.SZ")
        assert resolved == "白酒"
        assert "自动匹配到板块 '白酒'" in note

    def test_exact_request_has_no_note(self, monkeypatch):
        resolved, note = self._resolve(monkeypatch, "白酒", "300175.SZ")
        assert resolved == "白酒"
        assert note == ""

    def test_legacy_industry_still_tried_before_sw(self, monkeypatch):
        monkeypatch.setattr(
            smv, "get_sw_industry", lambda _t: pytest.fail("sw map must not be consulted")
        )
        resolved, note = self._resolve(monkeypatch, "不存在板块", "300175.SZ", legacy="消费电子")
        assert resolved == "消费电子"
        assert "注册行业" in note


@pytest.mark.unit
def test_default_path_is_under_cache_dir(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_SW_INDUSTRY_MAP", raising=False)
    monkeypatch.setenv("TRADINGAGENTS_HOME", "/tmp/ta-home")
    assert sw_industry_map_path() == Path("/tmp/ta-home/cache/sw_industry_map.db")
