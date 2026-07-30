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
    if cols and "stop_hit_20" not in cols:
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
            PRIMARY KEY (batch_date, ticker)
        )"""
    )
    conn.executemany(
        """INSERT OR REPLACE INTO rating_outcomes VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                o["batch_date"], o["ticker"], o["rating"], o["confidence"],
                o["base_date"], o["base_close"],
                o.get("entry"), o.get("stop"),
                o.get("ret_5"), o.get("ret_10"), o.get("ret_20"),
                o.get("excess_5"), o.get("excess_10"), o.get("excess_20"),
                o.get("stop_hit_5"), o.get("stop_hit_10"), o.get("stop_hit_20"),
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


def render_report(outcomes: list[dict]) -> str:
    n_stop = sum(1 for o in outcomes if o.get("stop") is not None)
    lines = [
        "# 评级准确率基线报告 (Rating Accuracy Baseline)",
        "",
        f"- 样本: {len(outcomes)} 条评级 (来自 reports/ 存量 batch)，其中 {n_stop} 条带数值型 stop 价",
        "- L1 超额命中: 看空判对 = 相对沪深300 ETF 超额 < 0；看多判对 = 超额 > 0 (选股 α 能力)",
        "- L2 绝对命中: 看空判对 = 绝对收益 < 0；看多判对 = 绝对收益 > 0 (实盘盈亏视角)",
        "- L3 论点存活: 窗口内未触及报告自己给的 stop 价 (系统自定义的未被证伪率，Hold 也适用)",
        "- 基准价: batch 日后首个交易日收盘价",
        "",
    ]

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
            l1 = _hit_pct(rows, lambda o: _direction_correct(o["rating"], o[f"excess_{h}"])) if directional else "—"
            l2 = _hit_pct(
                rows,
                lambda o: (o[f"ret_{h}"] < 0) if o["rating"] in BEARISH else (o[f"ret_{h}"] > 0),
            ) if directional else "—"
            stop_rows = [o for o in rows if o.get(f"stop_hit_{h}") is not None]
            l3 = (
                _hit_pct(stop_rows, lambda o: o[f"stop_hit_{h}"] == 0) + f" ({len(stop_rows)})"
                if stop_rows else "—"
            )
            lines.append(
                f"| {rating} | {len(rows)} | {avg_ret:+.2f} | {avg_exc:+.2f} | {l1} | {l2} | {l3} |"
            )
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

    # 置信度维度（20 日窗口）
    lines.append("## 置信度 × 方向命中率 (20 个交易日, 仅方向性评级)")
    lines.append("")
    lines.append("| 置信度 | 样本数 | 方向命中率 | 平均超额% |")
    lines.append("|---|---:|---:|---:|")
    for conf in ("high", "medium", "low", None):
        rows = [
            o for o in outcomes
            if o["rating"] in (BEARISH | BULLISH)
            and (o.get("confidence") or None) == conf
            and o.get("excess_20") is not None
        ]
        label = conf or "(未标注)"
        if not rows:
            lines.append(f"| {label} | 0 | — | — |")
            continue
        n_hit = sum(1 for o in rows if _direction_correct(o["rating"], o["excess_20"]))
        avg_exc = sum(o["excess_20"] for o in rows) / len(rows)
        lines.append(f"| {label} | {len(rows)} | {100.0 * n_hit / len(rows):.1f}% | {avg_exc:+.2f} |")
    lines.append("")

    pending = sum(1 for o in outcomes if o.get("excess_20") is None)
    lines.append(f"> 注: {pending} 条评级因后续交易日不足 20 天，20 日窗口暂无法判定（等待数据自然累积）。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="评级准确率回测")
    parser.add_argument("--md", default=str(REPORTS_DIR / "rating_accuracy_baseline.md"))
    args = parser.parse_args()

    ratings = load_ratings(REPORTS_DIR)
    print(f"提取评级: {len(ratings)} 条")
    outcomes = compute_outcomes(ratings)
    print(f"可回测(有基准价): {len(outcomes)} 条")
    save_outcomes(outcomes)
    print(f"已落库: {OUTCOME_DB} (rating_outcomes 表)")

    report = render_report(outcomes)
    Path(args.md).write_text(report, encoding="utf-8")
    print(f"基线报告: {args.md}")
    print()
    print(report)


if __name__ == "__main__":
    main()
