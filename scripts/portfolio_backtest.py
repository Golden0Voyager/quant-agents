"""Portfolio backtest: agent 推荐 vs 实盘操作对比回测.

流程:
1. 从 PortfolioRepository 加载持仓 + 交易（JSON 缺失时提示先跑 sync-holdings）
2. 扫 reports/YYYYMMDD_batch_*/batch_summary.json 提取历史评级（复用
   rating_backtest 的 load_ratings）
3. pair_recommendations 配对推荐与实盘成交；compute_comparison 用
   quant_core.db 的 daily_bars 回填 5/10/20 个交易日收益差 (delta)
4. 落库 ~/.tradingagents/portfolio_comparison.db（INSERT OR REPLACE，可重复执行）
5. 生成 reports/portfolio_comparison.md: ① 组合汇总 + 冲突清单
   ② 聚合统计表 ③ 逐事件明细表

用法:
    uv run python scripts/portfolio_backtest.py               # 全量对比+报告
    uv run python scripts/portfolio_backtest.py --md out.md   # 指定报告路径
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# scripts/ 不是包: 同目录 import rating_backtest，仓库根入 path 供 tradingagents 导入
_HERE = str(Path(__file__).resolve().parent)
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rating_backtest import load_ratings  # noqa: E402

from tradingagents.portfolio.analysis import (  # noqa: E402
    HORIZONS,
    analyze_portfolio,
    compute_comparison,
    pair_recommendations,
)
from tradingagents.portfolio.repository import PortfolioRepository  # noqa: E402

QUANT_DB = os.getenv("QUANT_DB_PATH", os.path.expanduser("~/Code/quant_data/quant_core.db"))
COMPARISON_DB = os.path.expanduser("~/.tradingagents/portfolio_comparison.db")
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

SERIES_LEN = 25  # 基准日后回填的交易日数（覆盖 20 日窗口留余量）


# ---------------------------------------------------------------------------
# 1. 行情回填 price_loader（daily_bars, ts_code 为裸 6 位代码）
# ---------------------------------------------------------------------------

def make_price_loader(db_path: str = QUANT_DB):
    """构造 price_loader(ticker, batch_date) -> (base_close, series).

    base_close = 首个 trade_date >= batch_date 的收盘价（与 rating_backtest 同口径）；
    series = 其后 25 个交易日 [(trade_date, close)]。全 ticker 行情一次性缓存，
    避免逐事件重复扫表。
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    cache: dict[str, tuple[list[str], list[float]]] = {}

    def load(ticker: str, batch_date: str):
        code = str(ticker).split(".")[0]
        if code not in cache:
            rows = conn.execute(
                "SELECT trade_date, close FROM daily_bars WHERE ts_code = ?"
                " ORDER BY trade_date",
                (code,),
            ).fetchall()
            cache[code] = ([r[0] for r in rows], [r[1] for r in rows])
        dates, closes = cache[code]
        base_i = next((i for i, d in enumerate(dates) if d >= batch_date), None)
        if base_i is None or closes[base_i] is None:
            return None, []
        series = list(zip(
            dates[base_i + 1 : base_i + 1 + SERIES_LEN],
            closes[base_i + 1 : base_i + 1 + SERIES_LEN],
            strict=True,
        ))
        return closes[base_i], series

    return load


# ---------------------------------------------------------------------------
# 2. 落库（纯衍生数据，INSERT OR REPLACE 可重复执行）
# ---------------------------------------------------------------------------

def save_comparison(events: list[dict]) -> None:
    os.makedirs(os.path.dirname(COMPARISON_DB), exist_ok=True)
    conn = sqlite3.connect(COMPARISON_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS portfolio_comparison (
            batch_date TEXT NOT NULL, ticker TEXT NOT NULL,
            rating TEXT, direction TEXT, confidence TEXT,
            status TEXT, trade_date TEXT, trade_price REAL, trade_shares REAL,
            grid INTEGER, base_close REAL,
            agent_ret_5 REAL, agent_ret_10 REAL, agent_ret_20 REAL,
            user_ret_5 REAL, user_ret_10 REAL, user_ret_20 REAL,
            delta_5 REAL, delta_10 REAL, delta_20 REAL,
            PRIMARY KEY (batch_date, ticker)
        )"""
    )
    conn.executemany(
        """INSERT OR REPLACE INTO portfolio_comparison VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                e["batch_date"], e["ticker"], e["rating"], e["direction"],
                e.get("confidence"), e["status"],
                e.get("trade_date"), e.get("trade_price"), e.get("trade_shares"),
                1 if e.get("grid") else 0, e.get("base_close"),
                e.get("agent_ret_5"), e.get("agent_ret_10"), e.get("agent_ret_20"),
                e.get("user_ret_5"), e.get("user_ret_10"), e.get("user_ret_20"),
                e.get("delta_5"), e.get("delta_10"), e.get("delta_20"),
            )
            for e in events
        ],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 3. 报告渲染
# ---------------------------------------------------------------------------

def _pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "—"


def _fmt_stat(stat: dict | None, key: str) -> str:
    if not stat:
        return "—"
    v = stat.get(key)
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if key == "win_rate" else f"{v * 100:+.2f}%"


def _agg_rows(lines: list[str], label: str, agg: dict) -> None:
    for h in HORIZONS:
        stat = agg["horizons"][h]
        n = stat["n"] if stat else 0
        lines.append(
            f"| {label} | {h}日 | {n} | {_fmt_stat(stat, 'mean')} "
            f"| {_fmt_stat(stat, 'median')} | {_fmt_stat(stat, 'win_rate')} |"
        )


def render_report(analysis: dict, comparison: dict) -> str:
    lines = [
        "# 组合对比报告 (Agent 推荐 vs 实盘操作)",
        "",
        "## 1. 组合汇总",
        "",
        f"- 总市值: {analysis['total_market_value_format']}"
        f" | 总投入: {analysis['total_invested_format']}"
        f" | 总盈亏: {analysis['total_pnl_format']}",
        f"- Top5 权重集中度: {analysis['top5_concentration'] * 100:.1f}%"
        + "（"
        + "、".join(f"{x['ticker']} {x['weight'] * 100:.1f}%" for x in analysis["top5"])
        + "）",
    ]
    ts = analysis["txn_stats"]
    lines.append(
        f"- 近{ts['window_days']}天交易（截至 {ts['anchor_date'] or '—'}）: "
        f"买入 {ts['buy_count']} 次 / 卖出 {ts['sell_count']} 次，"
        f"手续费合计 {ts['fee_total_format']}；分红 {ts['dividend_count']} 笔"
    )
    lines.append("")

    lines.append("### 浮亏 > 20% 清单")
    lines.append("")
    if analysis["deep_losers"]:
        lines.append("| 代码 | 名称 | 浮亏 | 权重 |")
        lines.append("|---|---|---:|---:|")
        for x in analysis["deep_losers"]:
            lines.append(
                f"| {x['ticker']} | {x['name'] or '—'} "
                f"| {x['pnl_pct'] * 100:.1f}% | {x['weight'] * 100:.1f}% |"
            )
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("### 评级 × 持仓冲突")
    lines.append("")
    if analysis["heavy_bearish"]:
        for x in analysis["heavy_bearish"]:
            lines.append(
                f"- ⚠️ 重仓看空: {x['ticker']} 权重 {x['weight'] * 100:.1f}%，"
                f"最新评级 {x['rating']}（{x['batch_date']}）"
            )
    if analysis["missed_opportunities"]:
        for x in analysis["missed_opportunities"]:
            lines.append(
                f"- 💡 机会提示: {x['ticker']} 最新评级 {x['rating']}"
                f"（{x['batch_date']}）但无持仓"
            )
    if not analysis["heavy_bearish"] and not analysis["missed_opportunities"]:
        lines.append("（无冲突）")
    lines.append("")

    # 2. 聚合统计
    pr = comparison["pair_rate"]
    lines += [
        "## 2. 聚合统计 (delta = 用户收益 − 照抄 agent 收益，正 = 用户更优)",
        "",
        f"- 方向性推荐事件: {comparison['total_events']} 条，"
        f"配对到实盘成交 {comparison['matched']} 条"
        f"（配对率 {f'{pr * 100:.1f}%' if pr is not None else '—'}）",
        "",
        "| 分组 | 窗口 | 样本数 | delta 均值 | delta 中位数 | 用户胜率 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    _agg_rows(lines, "全部", comparison["overall"])
    _agg_rows(lines, "看多", comparison["by_direction"]["bullish"])
    _agg_rows(lines, "看空", comparison["by_direction"]["bearish"])
    # 网格/非网格分组刻意不渲染：网格交易只发生在 ETF 上，而 agent 不推荐 ETF，
    # 配对事件永远落在非网格桶，分组行只会是两行空数据。
    lines.append("")
    lines.append(
        "> 看多: agent 收益以基准收盘为成本，用户收益以实际买入价为成本；"
        "看空: agent_ret = -(价格变动)，user_ret = (卖出价 − 期末价)/基准收盘。"
        "窗口序列不足时该 horizon 不计入。"
    )
    lines.append("")

    # 3. 逐事件明细
    lines += [
        "## 3. 逐事件明细",
        "",
        "| 推荐日 | 代码 | 评级 | 状态 | 成交日 | 成交价 | Δ5 | Δ10 | Δ20 |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for e in comparison["events"]:
        price_str = f"{e['trade_price']:.2f}" if e.get("trade_price") else "—"
        lines.append(
            f"| {e['batch_date']} | {e['ticker']} | {e['rating']} | {e['status']} "
            f"| {e.get('trade_date') or '—'} "
            f"| {price_str} "
            f"| {_pct(e.get('delta_5'))} | {_pct(e.get('delta_10'))} | {_pct(e.get('delta_20'))} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="agent 推荐 vs 实盘操作对比回测")
    parser.add_argument("--md", default=str(REPORTS_DIR / "portfolio_comparison.md"))
    parser.add_argument(
        "--reports-dir", default=str(REPORTS_DIR),
        help="batch 报告根目录 (默认取脚本所在仓库的 reports/；"
             "从 worktree 运行时需指向主 checkout 的 reports/)",
    )
    args = parser.parse_args()

    repo = PortfolioRepository()
    if not repo.exists():
        print(
            f"错误: 持仓文件不存在: {repo.path}\n"
            "请先运行 'uv run tradingagents sync-holdings' 同步持仓。"
        )
        sys.exit(1)
    portfolio = repo.load()
    print(f"持仓: {len(portfolio.holdings)} 只 / 交易 {len(portfolio.transactions)} 笔")

    recs = load_ratings(Path(args.reports_dir))
    print(f"提取评级: {len(recs)} 条")

    # 每票取最新批次的评级（持仓冲突清单用）
    latest_ratings: dict[str, dict] = {}
    for r in recs:
        cur = latest_ratings.get(r["ticker"])
        if cur is None or r["batch_date"] > cur["batch_date"]:
            latest_ratings[r["ticker"]] = {
                "rating": r["rating"], "batch_date": r["batch_date"],
            }
    analysis = analyze_portfolio(portfolio, latest_ratings)

    pairs = pair_recommendations(recs, portfolio.transactions)
    price_loader = make_price_loader()
    comparison = compute_comparison(pairs, price_loader)
    pr = comparison["pair_rate"]
    pr_str = f"{pr * 100:.1f}%" if pr is not None else "—"
    print(f"配对: {comparison['matched']}/{comparison['total_events']} (配对率 {pr_str})")

    save_comparison(comparison["events"])
    print(f"已落库: {COMPARISON_DB} (portfolio_comparison 表)")

    report = render_report(analysis, comparison)
    Path(args.md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md).write_text(report, encoding="utf-8")
    print(f"对比报告: {args.md}")


if __name__ == "__main__":
    main()
