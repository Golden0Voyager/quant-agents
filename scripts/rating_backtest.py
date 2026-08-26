"""Rating-outcome backtest: 评级准确率回测闭环（第三梯队 #7）.

扫描 reports/YYYYMMDD_batch_*/batch_summary.json 提取历史评级，从
quant_core.db 的 daily_bars 回填 5/10/20 个交易日后的实际收益（含相对
上证指数的超额收益），落库到 ~/.tradingagents/rating_outcomes.db 并生成
准确率基线报告。

判定约定 (三层并排，不合成单一分数):
- L1 方向: 看空判对 = 相对沪深300 ETF 超额收益 < 0；看多判对 = 超额 > 0
- L2 绝对: 看空判对 = 绝对收益 < 0；看多判对 = 绝对收益 > 0 (实盘盈亏视角)
- L3 论点失效: 窗口内 high/low 触及报告自己给出的 stop 价 = 系统自定义的证伪。
  看空评级 stop 在上方 (涨破失效)，看多/持有评级 stop 在下方 (跌破失效)；
  stop 与 base_close 相对位置与评级语义矛盾时标记 ambiguous，不参与 L3。
- 基准价: 首个 trade_date >= batch 日期的收盘价（batch 通常在分析日晚间
  运行，次一交易日收盘是最保守的可执行价格）
- 脚本可重复执行 (INSERT OR REPLACE)，未来批次会自动纳入

用法:
    uv run python scripts/rating_backtest.py                 # 全量回测+报告
    uv run python scripts/rating_backtest.py --md out.md     # 指定报告路径
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

QUANT_DB = os.getenv("QUANT_DB_PATH", os.path.expanduser("~/Code/quant_data/quant_core.db"))
OUTCOME_DB = os.path.expanduser("~/.tradingagents/rating_outcomes.db")
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

HORIZONS = (5, 10, 20)
BEARISH = {"Sell", "Underweight"}
BULLISH = {"Buy", "Overweight"}
# 基准: 沪深300 ETF (etf_daily 自 2026-06-22 起有连续数据，覆盖全部存量 batch)；
# index_daily 仅 13 天历史，作为回退。
BENCHMARK_ETF = "510300"
BENCHMARK_INDEX = "sh000001"  # 上证指数 (回退)


# ---------------------------------------------------------------------------
# 1. 提取历史评级
# ---------------------------------------------------------------------------

def _parse_price(value) -> float | None:
    """解析 batch_summary 里的 entry/stop 字段；非数值 (如 '—') 返回 None。"""
    s = str(value or "").strip().replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def load_ratings(reports_dir: Path) -> list[dict]:
    """兼容两种 batch_summary.json 格式: 旧=list[dict], 新={"rows": [...]}"""
    ratings: list[dict] = []
    for f in sorted(reports_dir.glob("*_batch_*/batch_summary.json")):
        batch_date = f.parent.name[:8]
        if not batch_date.isdigit():
            continue
        date_iso = f"{batch_date[:4]}-{batch_date[4:6]}-{batch_date[6:]}"
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = data.get("rows", []) if isinstance(data, dict) else data
        for r in rows:
            if not isinstance(r, dict):
                continue
            rating = (r.get("rating") or "").strip()
            ticker = str(r.get("ticker") or "").split(".")[0].strip()
            if rating in (BEARISH | BULLISH | {"Hold"}) and len(ticker) == 6 and ticker.isdigit():
                ratings.append({
                    "batch_date": date_iso,
                    "batch_dir": f.parent.name,
                    "ticker": ticker,
                    "rating": rating,
                    "confidence": (r.get("confidence") or "").strip() or None,
                    "entry": _parse_price(r.get("entry")),
                    "stop": _parse_price(r.get("stop")),
                    # 数据完整度切片用: 该票本次运行是否有降级/部分数据
                    "fallback": 1 if r.get("fallback") else 0,
                    "coverage_degraded": r.get("coverage_degraded")
                    if isinstance(r.get("coverage_degraded"), int) else None,
                })
    return ratings


# ---------------------------------------------------------------------------
# 2. 收益回填
# ---------------------------------------------------------------------------

def _load_close_series(conn: sqlite3.Connection, sql: str, params: tuple) -> tuple[list[str], list[float]]:
    rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def compute_outcomes(ratings: list[dict]) -> list[dict]:
    conn = sqlite3.connect(f"file:{QUANT_DB}?mode=ro", uri=True, timeout=30)

    bench_dates, bench_close = _load_close_series(
        conn,
        "SELECT trade_date, close FROM etf_daily WHERE ts_code = ? ORDER BY trade_date",
        (BENCHMARK_ETF,),
    )
    if len(bench_dates) < 10:
        bench_dates, bench_close = _load_close_series(
            conn,
            "SELECT trade_date, close FROM index_daily WHERE index_code = ? ORDER BY trade_date",
            (BENCHMARK_INDEX,),
        )
    bench_idx = {d: i for i, d in enumerate(bench_dates)}

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in ratings:
        by_ticker[r["ticker"]].append(r)

    outcomes: list[dict] = []
    for ticker, items in by_ticker.items():
        rows_ohlc = conn.execute(
            "SELECT trade_date, close, high, low FROM daily_bars"
            " WHERE ts_code = ? ORDER BY trade_date",
            (ticker,),
        ).fetchall()
        if not rows_ohlc:
            continue
        dates = [r[0] for r in rows_ohlc]
        closes = [r[1] for r in rows_ohlc]
        highs = [r[2] for r in rows_ohlc]
        lows = [r[3] for r in rows_ohlc]
        for r in items:
            # 基准日: 首个 trade_date >= batch_date
            base_i = next((i for i, d in enumerate(dates) if d >= r["batch_date"]), None)
            if base_i is None or closes[base_i] is None:
                continue
            base_close = closes[base_i]
            out = dict(r, base_date=dates[base_i], base_close=base_close)

            # L3 前置: stop 方向必须与评级语义一致，否则视为 ambiguous
            stop = r.get("stop")
            is_bearish = r["rating"] in BEARISH
            stop_valid = stop is not None and (
                (is_bearish and stop > base_close)
                or (not is_bearish and stop < base_close)
            )

            for h in HORIZONS:
                ret = excess = None
                stop_hit = None
                if base_i + h < len(dates) and closes[base_i + h]:
                    ret = (closes[base_i + h] - base_close) / base_close * 100.0
                    bi = bench_idx.get(dates[base_i])
                    if bi is not None and bi + h < len(bench_close) and bench_close[bi] and bench_close[bi + h]:
                        bench_ret = (bench_close[bi + h] - bench_close[bi]) / bench_close[bi] * 100.0
                        excess = ret - bench_ret
                    if stop_valid:
                        # 窗口含基准日当天 (盘中即可触发)
                        if is_bearish:
                            ws = [x for x in highs[base_i : base_i + h + 1] if x is not None]
                            stop_hit = 1 if ws and max(ws) >= stop else 0
                        else:
                            ws = [x for x in lows[base_i : base_i + h + 1] if x is not None]
                            stop_hit = 1 if ws and min(ws) <= stop else 0
                out[f"ret_{h}"] = ret
                out[f"excess_{h}"] = excess
                out[f"stop_hit_{h}"] = stop_hit
            outcomes.append(out)

    conn.close()
    return outcomes


def save_outcomes(outcomes: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUTCOME_DB), exist_ok=True)
    conn = sqlite3.connect(OUTCOME_DB)
    # 表为纯衍生数据 (全量可从 reports/ 重算)，schema 升级时直接重建
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rating_outcomes)").fetchall()}
    if cols and {"stop_hit_20", "fallback", "coverage_degraded"} - cols:
        conn.execute("DROP TABLE rating_outcomes")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rating_outcomes (
            batch_date TEXT NOT NULL, ticker TEXT NOT NULL,
            rating TEXT, confidence TEXT,
            base_date TEXT, base_close REAL,
            entry REAL, stop REAL,
            ret_5 REAL, ret_10 REAL, ret_20 REAL,
            excess_5 REAL, excess_10 REAL, excess_20 REAL,
            stop_hit_5 INTEGER, stop_hit_10 INTEGER, stop_hit_20 INTEGER,
            fallback INTEGER, coverage_degraded INTEGER,
            PRIMARY KEY (batch_date, ticker)
        )"""
    )
    conn.executemany(
        """INSERT OR REPLACE INTO rating_outcomes VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                o["batch_date"], o["ticker"], o["rating"], o["confidence"],
                o["base_date"], o["base_close"],
                o.get("entry"), o.get("stop"),
                o.get("ret_5"), o.get("ret_10"), o.get("ret_20"),
                o.get("excess_5"), o.get("excess_10"), o.get("excess_20"),
                o.get("stop_hit_5"), o.get("stop_hit_10"), o.get("stop_hit_20"),
                o.get("fallback"), o.get("coverage_degraded"),
            )
            for o in outcomes
        ],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 3. 基线报告
# ---------------------------------------------------------------------------

def _direction_correct(rating: str, excess: float) -> bool:
    return excess < 0 if rating in BEARISH else excess > 0


def _hit_pct(rows: list[dict], pred) -> str:
    if not rows:
        return "—"
    return f"{100.0 * sum(1 for o in rows if pred(o)) / len(rows):.1f}%"


# ---------------------------------------------------------------------------
# 3a. 校准分析: 单调性检验 + 数据完整度切片
# ---------------------------------------------------------------------------

# 乐观程度打分: 校准良好的系统里，平均超额收益应随乐观程度单调递减
RATING_OPTIMISM = {"Buy": 5, "Overweight": 4, "Hold": 3, "Underweight": 2, "Sell": 1}


def _ranks(values: list[float]) -> list[float]:
    """排名 (1-based，并列取平均名次)，供 Spearman 相关使用。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman 等级相关；样本 <3 或任一序列零方差时返回 None。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy) ** 0.5


def monotonicity_by_horizon(
    outcomes: list[dict], horizons: tuple[int, ...] = HORIZONS
) -> dict[int, dict]:
    """每个窗口: 各评级的平均超额 + 乐观程度与平均超额的 Spearman 相关。

    rho 接近 +1 = 评级排序与实际表现一致 (校准良好)；接近 0 或负 = 校准失效。
    """
    result: dict[int, dict] = {}
    for h in horizons:
        means: dict[str, float] = {}
        counts: dict[str, int] = {}
        for rating in RATING_OPTIMISM:
            rows = [
                o for o in outcomes
                if o["rating"] == rating and o.get(f"excess_{h}") is not None
            ]
            if rows:
                means[rating] = sum(o[f"excess_{h}"] for o in rows) / len(rows)
                counts[rating] = len(rows)
        rho = spearman(
            [RATING_OPTIMISM[r] for r in means],
            [means[r] for r in means],
        )
        result[h] = {"means": means, "counts": counts, "rho": rho}
    return result


def _is_degraded(outcome: dict) -> bool:
    return bool(outcome.get("fallback")) or bool(outcome.get("coverage_degraded"))


def completeness_by_horizon(
    outcomes: list[dict], horizons: tuple[int, ...] = HORIZONS
) -> dict[int, dict[str, list[dict]]]:
    """每个窗口按数据完整度分桶 (degraded = 有 fallback 或 coverage 降级)。

    只保留方向性评级 (Hold 无 L1/L2 判定)，供"数据缺失是否伤害准确率"切片。
    """
    directional = [o for o in outcomes if o["rating"] in (BEARISH | BULLISH)]
    result: dict[int, dict[str, list[dict]]] = {}
    for h in horizons:
        rows = [o for o in directional if o.get(f"excess_{h}") is not None]
        result[h] = {
            "degraded": [o for o in rows if _is_degraded(o)],
            "clean": [o for o in rows if not _is_degraded(o)],
        }
    return result


def compute_regime_context(outcomes: list[dict]) -> str:
    """样本窗口的市况上下文：基准在窗口内的累计涨跌幅。

    防止把单一市况下的方向命中率误读为系统能力：反弹市里看空天然全错，
    下跌市里看多天然全错，跨市况结论需要更长样本。
    """
    if not outcomes:
        return ""
    start = min(o["base_date"] for o in outcomes)
    end = max(o["base_date"] for o in outcomes)
    try:
        conn = sqlite3.connect(f"file:{QUANT_DB}?mode=ro", uri=True, timeout=15)
        rows = conn.execute(
            "SELECT trade_date, close FROM etf_daily WHERE ts_code = ?"
            " AND trade_date >= ? ORDER BY trade_date",
            (BENCHMARK_ETF, start),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return ""
    if len(rows) < 2 or not rows[0][1]:
        return ""
    bench_ret = (rows[-1][1] - rows[0][1]) / rows[0][1] * 100.0
    return (
        f"- ⚠️ 市况上下文: 样本评级日分布于 {start} ~ {end}，期间至今沪深300 ETF 累计 {bench_ret:+.1f}%。"
        f"单一市况下看{'空' if bench_ret > 0 else '多'}类命中率天然偏低，"
        "方向性结论受市况混杂影响，不应据此对系统看多/看空倾向做永久性矫正；"
        "机制性结论 (如 stop 触发/whipsaw 率) 不受此限。"
    )


def render_report(outcomes: list[dict], regime_note: str = "") -> str:
    n_stop = sum(1 for o in outcomes if o.get("stop") is not None)
    lines = [
        "# 评级准确率基线报告 (Rating Accuracy Baseline)",
        "",
        f"- 样本: {len(outcomes)} 条评级 (来自 reports/ 存量 batch)，其中 {n_stop} 条带数值型 stop 价",
        "- L1 超额命中: 看空判对 = 相对沪深300 ETF 超额 < 0；看多判对 = 超额 > 0 (选股 α 能力)",
        "- L2 绝对命中: 看空判对 = 绝对收益 < 0；看多判对 = 绝对收益 > 0 (实盘盈亏视角)",
        "- L3 论点存活: 窗口内未触及报告自己给的 stop 价 (系统自定义的未被证伪率，Hold 也适用)",
        "- 单调性检验: 评级乐观程度排序与各评级平均超额收益的 Spearman 相关 (校准的严格定义)",
        "- 数据完整度切片: 运行有 vendor 降级/部分数据的票 vs 完整运行的票，方向命中率对比",
        "- 基准价: batch 日后首个交易日收盘价",
    ]
    if regime_note:
        lines.append(regime_note)
    lines.append("")

    for h in HORIZONS:
        lines.append(f"## {h} 个交易日窗口")
        lines.append("")
        lines.append("| 评级 | 样本数 | 平均收益% | 平均超额% | L1超额命中 | L2绝对命中 | L3论点存活(样本) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for rating in ("Sell", "Underweight", "Hold", "Overweight", "Buy"):
            rows = [
                o for o in outcomes
                if o["rating"] == rating and o.get(f"excess_{h}") is not None
            ]
            if not rows:
                lines.append(f"| {rating} | 0 | — | — | — | — | — |")
                continue
            avg_ret = sum(o[f"ret_{h}"] for o in rows) / len(rows)
            avg_exc = sum(o[f"excess_{h}"] for o in rows) / len(rows)
            directional = rating != "Hold"
            l1 = _hit_pct(rows, lambda o, h=h: _direction_correct(o["rating"], o[f"excess_{h}"])) if directional else "—"
            l2 = _hit_pct(
                rows,
                lambda o, h=h: (o[f"ret_{h}"] < 0) if o["rating"] in BEARISH else (o[f"ret_{h}"] > 0),
            ) if directional else "—"
            stop_rows = [o for o in rows if o.get(f"stop_hit_{h}") is not None]
            l3 = (
                _hit_pct(stop_rows, lambda o, h=h: o[f"stop_hit_{h}"] == 0) + f" ({len(stop_rows)})"
                if stop_rows else "—"
            )
            lines.append(
                f"| {rating} | {len(rows)} | {avg_ret:+.2f} | {avg_exc:+.2f} | {l1} | {l2} | {l3} |"
            )
        lines.append("")

    # 单调性检验: 乐观程度排序 vs 平均超额收益 (校准的严格定义)
    lines.append("## 单调性检验 (评级排序 vs 实际表现)")
    lines.append("")
    lines.append("| 窗口 | 各评级平均超额% (Buy→Sell) | Spearman ρ | 判定 |")
    lines.append("|---|---|---:|---|")
    for h, stats in monotonicity_by_horizon(outcomes).items():
        means = stats["means"]
        if not means:
            lines.append(f"| {h}日 | (无样本) | — | — |")
            continue
        cells = " / ".join(
            f"{r[:4]} {means[r]:+.2f}({stats['counts'][r]})"
            for r in RATING_OPTIMISM
            if r in means
        )
        rho = stats["rho"]
        if rho is None:
            verdict = "样本/分桶不足"
            rho_str = "—"
        else:
            rho_str = f"{rho:+.2f}"
            verdict = "✅ 单调" if rho >= 0.8 else ("🟡 部分" if rho >= 0.4 else "❌ 失序")
        lines.append(f"| {h}日 | {cells} | {rho_str} | {verdict} |")
    lines.append("")
    lines.append("> ρ ≥ 0.8 视为校准良好 (评级越乐观实际超额越高)；单一市况下该指标同样受混杂影响，需跨市况样本确认。")
    lines.append("")

    # 数据完整度切片: 降级运行是否伤害方向准确率
    lines.append("## 数据完整度 × 方向命中率 (仅方向性评级)")
    lines.append("")
    lines.append("| 窗口 | 分组 | 样本数 | L1超额命中 | L2绝对命中 | 平均超额% |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for h, buckets in completeness_by_horizon(outcomes).items():
        for label, key in (("完整", "clean"), ("有降级", "degraded")):
            rows = buckets[key]
            if not rows:
                lines.append(f"| {h}日 | {label} | 0 | — | — | — |")
                continue
            l1 = _hit_pct(rows, lambda o, h=h: _direction_correct(o["rating"], o[f"excess_{h}"]))
            l2 = _hit_pct(
                rows,
                lambda o, h=h: (o[f"ret_{h}"] < 0) if o["rating"] in BEARISH else (o[f"ret_{h}"] > 0),
            )
            avg_exc = sum(o[f"excess_{h}"] for o in rows) / len(rows)
            lines.append(f"| {h}日 | {label} | {len(rows)} | {l1} | {l2} | {avg_exc:+.2f} |")
    lines.append("")
    lines.append("> 「有降级」= 该票运行出现 vendor fallback 或 coverage 降级；两桶命中率持续拉开 = 数据完整度是准确率瓶颈，应优先补数据而非调 prompt。")
    lines.append("")

    # Stop 质量分析: 触发 stop 的样本，窗口末是否回到 stop 的"安全侧" (whipsaw = stop 太紧)
    lines.append("## Stop 质量分析 (触发论点失效的样本)")
    lines.append("")
    lines.append("| 窗口 | 触发数/可判定数 | 触发率 | 其中窗口末回到安全侧 (whipsaw) | whipsaw占比 |")
    lines.append("|---|---:|---:|---:|---:|")
    for h in HORIZONS:
        judged = [o for o in outcomes if o.get(f"stop_hit_{h}") is not None]
        hit = [o for o in judged if o[f"stop_hit_{h}"] == 1]
        if not judged:
            lines.append(f"| {h}日 | 0 | — | — | — |")
            continue
        whipsaw = 0
        for o in hit:
            end_close = o["base_close"] * (1 + o[f"ret_{h}"] / 100.0)
            if o["rating"] in BEARISH:
                whipsaw += 1 if end_close < o["stop"] else 0  # 涨破后又跌回 stop 下方
            else:
                whipsaw += 1 if end_close > o["stop"] else 0  # 跌破后又涨回 stop 上方
        w_pct = f"{100.0 * whipsaw / len(hit):.1f}%" if hit else "—"
        lines.append(
            f"| {h}日 | {len(hit)}/{len(judged)} | {100.0 * len(hit) / len(judged):.1f}% "
            f"| {whipsaw} | {w_pct} |"
        )
    lines.append("")
    lines.append("> whipsaw 占比高 = stop 普遍设得太紧 (盘中扫损后价格回转)，应反馈给 Trader 改用 ATR 倍数定位。")
    lines.append("")

    # 置信度维度（全部窗口）
    lines.append("## 置信度 × 方向命中率 (仅方向性评级)")
    lines.append("")
    lines.append("| 窗口 | 置信度 | 样本数 | 方向命中率 | 平均超额% |")
    lines.append("|---|---|---:|---:|---:|")
    for h in HORIZONS:
        for conf in ("high", "medium", "low", None):
            rows = [
                o for o in outcomes
                if o["rating"] in (BEARISH | BULLISH)
                and (o.get("confidence") or None) == conf
                and o.get(f"excess_{h}") is not None
            ]
            label = conf or "(未标注)"
            if not rows:
                lines.append(f"| {h}日 | {label} | 0 | — | — |")
                continue
            n_hit = sum(1 for o in rows if _direction_correct(o["rating"], o[f"excess_{h}"]))
            avg_exc = sum(o[f"excess_{h}"] for o in rows) / len(rows)
            lines.append(f"| {h}日 | {label} | {len(rows)} | {100.0 * n_hit / len(rows):.1f}% | {avg_exc:+.2f} |")
    lines.append("")

    pending = sum(1 for o in outcomes if o.get("excess_20") is None)
    lines.append(f"> 注: {pending} 条评级因后续交易日不足 20 天，20 日窗口暂无法判定（等待数据自然累积）。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="评级准确率回测")
    parser.add_argument("--md", default=str(REPORTS_DIR / "rating_accuracy_baseline.md"))
    parser.add_argument(
        "--reports-dir", default=str(REPORTS_DIR),
        help="batch 报告根目录 (默认取脚本所在仓库的 reports/；"
             "从 worktree 运行时需指向主 checkout 的 reports/)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="静默模式：只刷新 outcomes 库与报告文件，不打印报告正文 (供 batch 末自动调用)",
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    ratings = load_ratings(reports_dir)
    print(f"提取评级: {len(ratings)} 条")
    outcomes = compute_outcomes(ratings)
    print(f"可回测(有基准价): {len(outcomes)} 条")
    save_outcomes(outcomes)
    print(f"已落库: {OUTCOME_DB} (rating_outcomes 表)")

    report = render_report(outcomes, compute_regime_context(outcomes))
    Path(args.md).write_text(report, encoding="utf-8")
    print(f"基线报告: {args.md}")
    if not args.quiet:
        print()
        print(report)


if __name__ == "__main__":
    main()
