"""批量运行报告提示词效果审计脚本。

Usage:
    uv run python scripts/prompt_audit.py reports/20260928_batch_my

针对 batch 输出目录逐股票体检三个层面：

1. 分析师层观察模式 —— 1_analysts/*.md 中决策词（目标价/止损/仓位/建仓/预期收益
   等，中英双语）出现次数。理想状态分析师只做观察不下结论，命中偏高提示
   提示词约束被忽略。
2. 辩论层论证质量 —— 2_research/bull.md / bear.md 中的价格预言模式（目标价、
   "看 87"、"will reach"、"翻倍" 等）与首轮开场白问题（#1176：bull 首轮编造
   bear 已发言）。启发式，需人工复核。
3. 数据缺口总览 —— 各分析师文件 DATA_UNAVAILABLE / DATA_NOT_APPLICABLE /
   <unavailable> 计数，反映工具缺口而非提示词问题。

输出：batch 目录下写 prompt_audit.md（每票一节 + 汇总表），控制台打印摘要。
所有检查均为信息性体检，不产生硬失败退出码。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ANALYST_FILES = ["market", "sentiment", "news", "fundamentals", "governance", "industry"]

DECISION_PATTERNS = {
    "目标价/target price": re.compile(r"目标价|target price|price target", re.I),
    "止损/stop-loss": re.compile(r"止损|stop[- ]?loss", re.I),
    "止盈/take-profit": re.compile(r"止盈|take[- ]?profit", re.I),
    "仓位/position sizing": re.compile(r"仓位|position sizing|仓位管理", re.I),
    "建仓/加仓/减仓": re.compile(r"建仓|加仓|减仓|买入持有|buy and hold", re.I),
    "预期收益/expected return": re.compile(r"预期收益|expected return|预期涨幅", re.I),
}

PRICE_PROPHECY_PATTERNS = {
    "目标价/target price": re.compile(r"目标价|target price|price target", re.I),
    "看N元（价格预言）": re.compile(r"看\s*\d+(\.\d+)?\s*[元美元]", re.I),
    "will reach / hit": re.compile(r"will (reach|hit|top)\s+\$?\d", re.I),
    "翻倍/double": re.compile(r"翻倍|double by|两倍", re.I),
}

DATA_GAP_PATTERN = re.compile(r"DATA_UNAVAILABLE|DATA_NOT_APPLICABLE|<unavailable>")

BEAR_MENTION_OPENING = re.compile(r"空头|bear (analyst|case|researcher|side)", re.I)


def read_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def count_pattern(text: str, pattern: re.Pattern[str]) -> int:
    return len(pattern.findall(text))


def audit_ticker(ticker_dir: Path) -> dict:
    """审计单个股票目录，返回结构化结果。"""
    result: dict = {"ticker": ticker_dir.name, "analysts": {}, "debate": {}, "data_gaps": {}}

    # 1. 分析师层：决策词计数
    for name in ANALYST_FILES:
        path = ticker_dir / "1_analysts" / f"{name}.md"
        text = read_if_exists(path)
        if not text:
            continue
        hits = {label: count_pattern(text, pat) for label, pat in DECISION_PATTERNS.items()}
        gaps = count_pattern(text, DATA_GAP_PATTERN)
        result["analysts"][name] = hits
        if gaps:
            result["data_gaps"][name] = gaps

    # 2. 辩论层
    for side in ("bull", "bear"):
        path = ticker_dir / "2_research" / f"{side}.md"
        text = read_if_exists(path)
        if not text:
            continue
        hits = {label: count_pattern(text, pat) for label, pat in PRICE_PROPHECY_PATTERNS.items()}
        hits_total = sum(hits.values())
        opening_flag = ""
        if side == "bull":
            opening = text[:200]
            if BEAR_MENTION_OPENING.search(opening):
                opening_flag = "⚠️ 首轮提及空头（疑似 #1176 开场白问题，人工复核）"
        result["debate"][side] = {"hits": hits, "total": hits_total, "opening_flag": opening_flag}

    return result


def fmt_hits(hits: dict[str, int]) -> str:
    nonzero = {k: v for k, v in hits.items() if v}
    if not nonzero:
        return "0"
    return ", ".join(f"{k}×{v}" for k, v in nonzero.items())


def render_report(batch_dir: Path, results: list[dict]) -> str:
    lines = [
        f"# 提示词效果审计报告 — {batch_dir.name}",
        "",
        "> 启发式体检，需人工复核；计数为字符串出现次数，非语义判定。",
        "",
        "## 汇总表",
        "",
        "| 股票 | 分析师决策词总数 | 辩论价格预言总数 | 数据缺口标记 | 开场白告警 |",
        "| --- | --- | --- | --- | --- |",
    ]

    for r in results:
        analyst_total = sum(sum(h.values()) for h in r["analysts"].values())
        debate_total = sum(d["total"] for d in r["debate"].values())
        gap_total = sum(r["data_gaps"].values())
        flags = [d["opening_flag"] for d in r["debate"].values() if d["opening_flag"]]
        flag_str = "bull 首轮提及空头" if flags else "—"
        lines.append(
            f"| {r['ticker']} | {analyst_total} | {debate_total} | {gap_total} | {flag_str} |"
        )

    lines += ["", "---", ""]
    for r in results:
        lines.append(f"## {r['ticker']}")
        lines.append("")
        if r["analysts"]:
            lines.append("分析师层决策词：")
            for name, hits in r["analysts"].items():
                lines.append(f"- `{name}.md`: {fmt_hits(hits)}")
        if r["debate"]:
            lines.append("")
            lines.append("辩论层价格预言：")
            for side, d in r["debate"].items():
                lines.append(f"- `{side}.md`: {fmt_hits(d['hits'])}")
                if d["opening_flag"]:
                    lines.append(f"  - {d['opening_flag']}")
        if r["data_gaps"]:
            lines.append("")
            lines.append("数据缺口：")
            for name, n in r["data_gaps"].items():
                lines.append(f"- `{name}.md`: {n} 处")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch_dir", type=Path, help="batch 输出目录，如 reports/20260928_batch_my")
    args = parser.parse_args()

    batch_dir = args.batch_dir
    if not batch_dir.is_dir():
        print(f"错误：目录不存在 {batch_dir}", file=sys.stderr)
        return 1

    ticker_dirs = sorted(
        d for d in batch_dir.iterdir() if d.is_dir() and (d / "complete_report.md").exists()
    )
    if not ticker_dirs:
        print(f"错误：{batch_dir} 下未找到带 complete_report.md 的股票目录", file=sys.stderr)
        return 1

    results = [audit_ticker(d) for d in ticker_dirs]

    analyst_flagged = sum(
        1 for r in results if sum(sum(h.values()) for h in r["analysts"].values()) > 0
    )
    debate_flagged = sum(1 for r in results if sum(d["total"] for d in r["debate"].values()) > 0)
    opening_flagged = sum(
        1 for r in results if any(d["opening_flag"] for d in r["debate"].values())
    )

    print(f"审计 {len(results)} 只股票：")
    print(f"  分析师层命中决策词: {analyst_flagged} 票")
    print(f"  辩论层命中价格预言: {debate_flagged} 票")
    print(f"  开场白疑似问题:    {opening_flagged} 票")

    report_path = batch_dir / "prompt_audit.md"
    report_path.write_text(render_report(batch_dir, results), encoding="utf-8")
    print(f"报告已写入 {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
