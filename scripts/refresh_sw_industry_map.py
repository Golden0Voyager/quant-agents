#!/usr/bin/env python
"""Refresh the Shenwan industry cache used to resolve sector names.

Usage:
    uv run python scripts/refresh_sw_industry_map.py                # build if stale
    uv run python scripts/refresh_sw_industry_map.py --force        # always rebuild
    uv run python scripts/refresh_sw_industry_map.py --check-only   # coverage report only

The cache maps every A-share ticker to its official Shenwan level-1/2/3
industry, which is the only non-blind way to answer "which sector does this
stock belong to" — ``stock_list.industry`` in quant_core.db uses the older
CSRC gate vocabulary ("农牧饲渔") while ``sector_fund_flow`` uses Shenwan
("养殖业"), and the database carries no sector membership table.

Exit code 0 = cache present and coverage printed, 1 = cache missing/stale
under --check-only.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.dataflows.errors import NoMarketDataError  # noqa: E402
from tradingagents.dataflows.smartmoney_vendor import (  # noqa: E402
    _DB_PATH,
    _resolve_sector_name,
)
from tradingagents.dataflows.sw_industry_map import (  # noqa: E402
    build_sw_industry_map,
    get_sw_industry,
    get_sw_industry_map_age_days,
    sw_industry_map_path,
)

DEFAULT_MAX_AGE_DAYS = 7.0


def _local_sector_names() -> list[str]:
    if not Path(_DB_PATH).exists():
        return []
    con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT sector_name FROM sector_fund_flow").fetchall()
    finally:
        con.close()
    return sorted(str(r[0]) for r in rows if r[0])


def _sw_names() -> set[str]:
    path = sw_industry_map_path()
    if not path.exists():
        return set()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT level2, level3 FROM sw_industry").fetchall()
    finally:
        con.close()
    return {str(v) for row in rows for v in row if v}


def _report_coverage(sectors: list[str], sw_names: set[str]) -> None:
    """用真实解析器算覆盖率，避免报告与运行时行为不一致。"""
    if not sectors:
        print("quant_core.db 不可用或 sector_fund_flow 为空，跳过覆盖率统计")
        return
    if not sw_names:
        print("申万缓存为空，跳过覆盖率统计")
        return
    universe = sorted(sw_names)
    unresolved: list[str] = []
    for name in sectors:
        try:
            _resolve_sector_name(name, universe)
        except NoMarketDataError:
            unresolved.append(name)
    print()
    print(
        f"本地板块 {len(sectors)} 个 ← 申万权威名可解析 {len(sectors) - len(unresolved)} 个，"
        f"仍无解 {len(unresolved)} 个"
    )
    if unresolved:
        print("无解板块（这些板块的个股只能降级，不会被瞎映射）:")
        for name in unresolved:
            print(f"  - {name}")


def _probe_examples(sectors: list[str]) -> None:
    if not sectors:
        return
    con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT code, name, industry FROM stock_list WHERE industry IS NOT NULL"
            " ORDER BY code LIMIT 400"
        ).fetchall()
    finally:
        con.close()
    resolved = 0
    samples: list[str] = []
    for code, name, industry in rows:
        hit = get_sw_industry(code)
        if not hit:
            continue
        if hit[1] in sectors or any(hit[1] in s or s in hit[1] for s in sectors):
            resolved += 1
        if len(samples) < 5 and hit[1] not in sectors:
            samples.append(f"  {code} {name}: 旧门类'{industry}' → 申万二级'{hit[1]}'（本地无同名板块）")
    print()
    print(f"抽样 {len(rows)} 只：{resolved} 只的申万二级能落到本地板块")
    for line in samples:
        print(line)


def _useful_industry(level2: str, level3: str, local: list[str]) -> bool:
    """该申万行业的二/三级名能否解析到本地某个板块——不能则不必抓成分股。"""
    for name in (level2, level3):
        if not name:
            continue
        try:
            _resolve_sector_name(name, local)
        except NoMarketDataError:
            continue
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rebuild even if the cache is fresh")
    parser.add_argument("--check-only", action="store_true", help="only report coverage")
    parser.add_argument("--workers", type=int, default=4, help="concurrent fetches (default 4)")
    parser.add_argument(
        "--sweep-delay",
        type=float,
        default=0.0,
        help="seconds between constituent requests; 1.2 with --workers 1 is the polite-crawl mode",
    )
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=240.0,
        help="bound one run so throttled upstreams cannot hang it (default 240, 0 = no limit)",
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"skip rebuild when the cache is younger than this (default {DEFAULT_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--all-industries",
        action="store_true",
        help="fetch every SW industry instead of only those that can map to a local sector",
    )

    args = parser.parse_args()

    path = sw_industry_map_path()
    age = get_sw_industry_map_age_days(path)
    print(f"缓存路径: {path}")
    print(f"缓存年龄: {'缺失' if age is None else f'{age:.1f} 天'}")

    if args.check_only:
        if age is None:
            return 1
    elif args.force or age is None or age > args.max_age_days:
        budget = args.budget_seconds or None
        print(
            f"开始抓取申万三级成分股（并发 {args.workers}，间隔 {args.sweep_delay}s，"
            f"预算 {budget or '无'}s）…"
        )
        try:
            local = _local_sector_names()
            stats = build_sw_industry_map(
                max_workers=args.workers,
                budget_seconds=budget,
                request_delay=args.sweep_delay,
                industry_filter=None if args.all_industries else (lambda l2, l3: _useful_industry(l2, l3, local)),
            )
        except Exception as exc:  # noqa: BLE001 — 上游限流/改版属常态，缓存不会被破坏
            print(f"刷新失败: {type(exc).__name__}: {str(exc)[:160]}")
            print("申万上游当前不可用（限流或页面结构变更），本次未改动任何缓存")
            print("板块名解析会退化为'只认本地板块名 + stock_list 注册行业'，仍不会瞎映射")
            return 1
        print(
            f"完成: 申万行业 {stats['industries']}，本轮候选 {stats['candidates']}，"
            f"已覆盖 {stats['already_covered']}，待抓 {stats['pending']}，"
            f"新增成分股 {stats['stocks']}，空行业 {stats['empty_industries']}，"
            f"抓取失败 {stats['errors']}，未跑到 {stats['skipped']}，"
            f"名单来源 {stats['hierarchy_source']}"
        )
        if stats["hierarchy_source"] == "cache":
            print("提示: 申万行业名单上游不可用，本轮沿用缓存中的静态名单（只扫成分股）")
        if stats["errors"] or stats["skipped"]:
            print("提示: 申万站点限流/不稳定，未覆盖行业会保留上一轮结果；重跑本脚本可继续补齐")
    else:
        print(f"缓存仍新鲜（{age:.1f} 天 < {args.max_age_days}），跳过抓取")

    sectors = _local_sector_names()
    _report_coverage(sectors, _sw_names())
    _probe_examples(sectors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
