"""申万行业归属缓存（ticker → 申万一级/二级/三级）。

为什么需要它：`quant_core.db` 的 `stock_list.industry` 用的是旧门类口径
（"农牧饲渔"、"贸易行业"），而 `sector_fund_flow.sector_name` 用申万口径，两者
对同一只票给出互不相容的名字，且库里没有任何"个股 → 板块"成员表，板块名解析
无法在本地闭环。唯一非盲的解法是拿申万官方成分股反查权威归属，再用权威名去
比对本地板块名。

缓存而非每次现抓：申万接口不稳定，且 335 个三级行业需要 335 次请求，构建一次
约 20–75s。因此只在显式刷新时联网，查询路径永远只读本地 SQLite。
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Protocol

_ENV_PATH_VAR = "TRADINGAGENTS_SW_INDUSTRY_MAP"
_BACKOFF_BASE = 1.5
_CHUNK_SIZE = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sw_industry (
    code   TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    level1 TEXT NOT NULL,
    level2 TEXT NOT NULL,
    level3 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sw_stock_industry (
    ticker TEXT PRIMARY KEY,
    code   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sw_map_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ShenwanClient(Protocol):
    """申万行业数据源（生产实现转发 akshare，测试用假实现）。"""

    def second_info(self): ...

    def third_info(self): ...

    def third_cons(self, symbol: str): ...


class AkshareShenwanClient:
    """把 akshare 的三个申万接口收敛成一个窄接口，便于整体替换与测试。"""

    def second_info(self):
        import akshare as ak

        return ak.sw_index_second_info()

    def third_info(self):
        import akshare as ak

        return ak.sw_index_third_info()

    def third_cons(self, symbol: str):
        import akshare as ak

        return ak.sw_index_third_cons(symbol=symbol)


def _tradingagents_home() -> Path:
    return Path(os.environ.get("TRADINGAGENTS_HOME", Path.home() / ".tradingagents"))


def sw_industry_map_path() -> Path:
    """缓存 DB 路径。默认 ~/.tradingagents/cache/sw_industry_map.db。"""
    override = os.environ.get(_ENV_PATH_VAR)
    if override:
        return Path(override).expanduser()
    return _tradingagents_home() / "cache" / "sw_industry_map.db"


def _code_digits(raw: str) -> str:
    return "".join(ch for ch in str(raw) if ch.isdigit())


def get_sw_industry(ticker: str) -> tuple[str, str, str] | None:
    """返回 (申万一级, 二级, 三级)；缓存缺失或未收录该票时返回 None。

    查不到不是错误：调用方据此降级，而不是猜一个板块。
    """
    digits = _code_digits(ticker)
    if not digits:
        return None
    path = sw_industry_map_path()
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT i.level1, i.level2, i.level3"
            " FROM sw_stock_industry s JOIN sw_industry i ON i.code = s.code"
            " WHERE s.ticker = ?",
            (digits,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return (row[0], row[1], row[2]) if row else None


def get_sw_industry_map_age_days(path: Path | None = None) -> float | None:
    """缓存最后刷新距今天数；无缓存或无时间戳返回 None。"""
    db = Path(path) if path else sw_industry_map_path()
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = con.execute("SELECT value FROM sw_map_meta WHERE key='fetched_at'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row:
        return None
    try:
        stamp = datetime.fromisoformat(row[0])
    except ValueError:
        return None
    return (datetime.now() - stamp).total_seconds() / 86400.0


def _fetch_hierarchy(source: ShenwanClient, retries: int) -> tuple[dict[str, str], list[tuple[str, str, str]]] | None:
    """抓申万二/三级名单。返回 None 表示上游暂时不可用。"""
    for attempt in range(retries):
        try:
            second = source.second_info()
            third = source.third_info()
            if third is None or not len(third):
                raise ValueError("empty sw_index_third_info")
        except Exception:  # noqa: BLE001 — 名单抓取失败时由调用方降级到缓存
            if attempt == retries - 1:
                return None
            time.sleep(_BACKOFF_BASE * (2**attempt))
            continue
        level1_of: dict[str, str] = {}
        if second is not None and len(second):
            for _, r in second.iterrows():
                level1_of[str(r["行业名称"])] = (
                    str(r["上级行业"]) if "上级行业" in second.columns else ""
                )
        industries = [
            (str(r["行业代码"]), str(r["行业名称"]), str(r["上级行业"]))
            for _, r in third.iterrows()
        ]
        return level1_of, industries
    return None


def _cached_hierarchy(target: Path) -> tuple[dict[str, str], list[tuple[str, str, str]]] | None:
    """从已有缓存读回申万名单。上游限流时用它继续跑成分股扫描。"""
    if not target.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = con.execute("SELECT code, name, level1, level2, level3 FROM sw_industry").fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not rows:
        return None
    level1_of = {r[3]: r[2] for r in rows if r[2]}
    return level1_of, [(r[0], r[4], r[3]) for r in rows]


def build_sw_industry_map(
    dest: Path | None = None,
    *,
    max_workers: int = 4,
    retries: int = 3,
    request_delay: float = 0.0,
    budget_seconds: float | None = None,
    industry_filter: Callable[[str, str], bool] | None = None,
    client: ShenwanClient | None = None,
) -> dict[str, int | str]:
    """抓取申万三级成分股并重建缓存，返回统计。

    与旧缓存**合并**而非整体替换：申万页面抓取不稳定，某一轮失败的行业若直接
    丢出会让覆盖 silently 变差。写入走临时库再 ``os.replace`` 原子替换；一轮
    下来一个成分股都没抓到时直接抛错、保留上一份可用缓存。

    ``max_workers`` 默认压到 4：并发 10 实测会被目标站点限流，324/335 个行业
    抓取失败。``budget_seconds`` 让一次运行有界（实测站点被限流时单轮全量可能
    跑不完），未跑到的行业计入 ``skipped``，靠"合并"语义跨多次运行逐步补齐。

    申万二/三级名单是静态的（一年最多变一次），所以名单抓取失败时降级复用缓存里
    的名单，只跳过成分股扫描那一步——否则一次元数据抖动会废掉整轮几百次请求。

    ``request_delay`` 配合 ``max_workers=1`` 是"礼貌爬取"模式：实测限流主要由持续
    负载触发（并发 10 → 324/335 失败；单发与低频可过），约 1.2s 间隔能在阈值下
    跑完全量。

    ``industry_filter(level2, level3)`` 用于只抓"解析时用得上"的行业：本地 90 个
    板块名里有一部分在申万口径下没有对应项，抓这些行业的成分股纯属浪费限流预算
    （实测可省约 25% 请求）。谓词由调用方注入而非在这里算，是为了避免与
    smartmoney_vendor 循环导入。
    """
    source = client or AkshareShenwanClient()
    target = Path(dest) if dest else sw_industry_map_path()

    hierarchy = _fetch_hierarchy(source, retries)
    hierarchy_source = "fetched"
    if hierarchy is None:
        cached = _cached_hierarchy(target) or _cached_hierarchy(
            target.with_suffix(target.suffix + ".tmp")
        )
        if cached is None:
            raise RuntimeError(
                "shenwan industry list unavailable upstream and no cached copy to fall back on"
            )
        level1_of, industries = cached
        hierarchy_source = "cache"
    else:
        level1_of, industries = hierarchy

    considered = len(industries)
    if industry_filter is not None:
        industries = [ind for ind in industries if industry_filter(ind[2], ind[1])]

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    # 不删残留 tmp：它是上一轮被中断时的进度载体。续跑时已抓到的行业会被跳过，
    # 所以"跑到一半被杀"不再是净损失——这正是限流环境下能收敛的关键。
    con = sqlite3.connect(tmp)
    con.executescript(_SCHEMA)
    started = time.monotonic()
    try:
        if target.exists():
            old = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                con.executemany(
                    "INSERT OR REPLACE INTO sw_industry VALUES (?, ?, ?, ?, ?)",
                    old.execute("SELECT code, name, level1, level2, level3 FROM sw_industry"),
                )
                con.executemany(
                    "INSERT OR REPLACE INTO sw_stock_industry VALUES (?, ?)",
                    old.execute("SELECT ticker, code FROM sw_stock_industry"),
                )
            finally:
                old.close()
        # 必须在并入正式缓存之后再算已覆盖集合：新建的 tmp 是空的，
        # 提前算会让每轮都把 335 个行业重抓一遍，续跑形同虚设。
        already = {row[0] for row in con.execute("SELECT DISTINCT code FROM sw_stock_industry")}
        pending = [ind for ind in industries if ind[0] not in already]
        stats: dict[str, int] = {
            "industries": considered,
            "candidates": len(industries),
            "already_covered": len(already),
            "pending": len(pending),
            "stocks": 0,
            "empty_industries": 0,
            "errors": 0,
            "skipped": 0,
        }
        con.executemany(
            "INSERT OR REPLACE INTO sw_industry (code, name, level1, level2, level3)"
            " VALUES (?, ?, ?, ?, ?)",
            [(code, name, level1_of.get(parent, ""), parent, name) for code, name, parent in industries],
        )

        def load(code: str) -> tuple[list[str], bool]:
            for attempt in range(retries):
                if request_delay:
                    time.sleep(request_delay)
                try:
                    df = source.third_cons(symbol=code)
                except Exception:  # noqa: BLE001 — 单个三级抓取失败不应中断整体构建
                    if attempt == retries - 1:
                        return [], True
                    time.sleep(_BACKOFF_BASE * (2**attempt))
                    continue
                if df is None or len(df) == 0 or "股票代码" not in df.columns:
                    return [], False
                return [str(v) for v in df["股票代码"].tolist()], False
            return [], True

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for start in range(0, len(pending), _CHUNK_SIZE):
                if budget_seconds is not None and time.monotonic() - started > budget_seconds:
                    stats["skipped"] = len(pending) - start
                    break
                chunk = pending[start : start + _CHUNK_SIZE]
                for (code, _name, _parent), (members, errored) in zip(
                    chunk, pool.map(load, [c for c, _, _ in chunk]), strict=True
                ):
                    if errored:
                        stats["errors"] += 1
                    elif not members:
                        stats["empty_industries"] += 1
                    if members:
                        con.executemany(
                            "INSERT OR REPLACE INTO sw_stock_industry (ticker, code)"
                            " VALUES (?, ?)",
                            [(_code_digits(raw), code) for raw in members],
                        )
                    stats["stocks"] += len(members)
                con.commit()
        if pending and stats["stocks"] == 0:
            raise RuntimeError(
                "shenwan constituent fetch returned no members; keeping the previous cache"
            )
        con.execute(
            "INSERT OR REPLACE INTO sw_map_meta (key, value) VALUES ('fetched_at', ?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        con.commit()
    finally:
        con.close()
    os.replace(tmp, target)
    result: dict[str, int | str] = {**stats, "hierarchy_source": hierarchy_source}
    return result
