"""Portfolio analysis: 持仓体检 + agent 推荐 vs 实盘操作对比（纯函数，无 I/O）.

三个能力:
1. ``analyze_portfolio`` — 组合汇总（市值/盈亏/top5 集中度/浮亏清单）、
   评级 × 持仓冲突清单、近 90 天交易统计。
2. ``pair_recommendations`` — 把历史评级（batch 推荐）与实盘成交记录配对，
   判定用户是否跟随了 agent 建议。
3. ``compute_comparison`` — 对配对成功的事件回填行情（price_loader 由调用方
   注入，本模块不碰 DB），计算「照抄 agent」vs「用户实际操作」在 5/10/20 个
   交易日窗口的收益差 (delta)，并做聚合统计。

口径约定:
- 看多 (Buy/Overweight):  用户收益以实际买入价为成本，agent 收益以基准收盘为成本
- 看空 (Sell/Underweight): agent_ret = -(价格变动)；user_ret = (卖出价 - 期末价)/基准收盘
- delta = user_ret - agent_ret，正值 = 用户操作优于照抄 agent
- 基准收盘 = batch 日后首个交易日收盘（batch 通常在分析日晚间运行，与
  rating_backtest.py 同一约定）
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from statistics import median
from typing import Any

from tradingagents.dataflows.akshare_common import format_money_cn
from tradingagents.portfolio.models import Portfolio, Transaction

BULLISH = {"Buy", "Overweight"}
BEARISH = {"Sell", "Underweight"}
HORIZONS = (5, 10, 20)

# 配对窗口: 推荐日之后 10 个日历日内的同方向首笔成交视为跟随
PAIR_WINDOW_DAYS = 10
# 重仓阈值 / 浮亏阈值（与 portfolio/prompts.py 的 -0.20 约定一致，均为小数）
HEAVY_WEIGHT = 0.10
DEEP_LOSS_PCT = -0.20
# 近 N 天交易统计窗口
RECENT_DAYS = 90
# price_loader 应提供的后续收盘价序列长度（覆盖 20 日窗口留余量）
SERIES_LEN = 25


def _parse_date(value: str | None) -> date | None:
    """解析 'YYYY-MM-DD' 或 'YYYYMMDD' 日期；非法输入返回 None。"""
    if not value:
        return None
    s = str(value).strip()[:10]
    try:
        if "-" in s:
            return date.fromisoformat(s)
        if len(s) == 8 and s.isdigit():
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None
    return None


def _bare_ticker(ticker: str) -> str:
    """剥离交易所后缀: '002241.SZ' -> '002241'。"""
    return str(ticker or "").split(".")[0].strip()


def _holding_market_value(h) -> float:
    """与 Portfolio.total_market_value() 相同的单票市值口径。"""
    return h.market_value or (h.market_price or h.avg_cost) * h.shares


# ---------------------------------------------------------------------------
# 1. 持仓体检
# ---------------------------------------------------------------------------

def analyze_portfolio(portfolio: Portfolio, latest_ratings: dict[str, dict]) -> dict[str, Any]:
    """组合体检：汇总指标 + 集中度 + 浮亏清单 + 评级冲突 + 近 90 天交易统计.

    Args:
        portfolio: 持仓（含 transactions）。
        latest_ratings: ticker（裸 6 位或带后缀均可）→ {"rating", "batch_date"}，
            每票取最新批次（调用方负责挑选）。
    """
    total_mv = portfolio.total_market_value()
    total_inv = portfolio.total_invested()
    total_pnl = portfolio.total_pnl()

    # 权重: 优先 Holding.weight（小数），缺失时按市值占比推算
    weights: dict[str, float] = {}
    for ticker, h in portfolio.holdings.items():
        w = h.weight
        if w is None:
            w = _holding_market_value(h) / total_mv if total_mv else 0.0
        weights[ticker] = w
    top5 = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top5_concentration = sum(w for _, w in top5)

    deep_losers: list[dict[str, Any]] = [
        {
            "ticker": ticker,
            "name": h.name,
            "pnl_pct": h.pnl_pct,
            "weight": weights[ticker],
        }
        for ticker, h in portfolio.holdings.items()
        if h.pnl_pct is not None and h.pnl_pct < DEEP_LOSS_PCT
    ]
    deep_losers.sort(key=lambda x: x["pnl_pct"])

    # 评级 × 持仓冲突: 裸代码对齐 latest_ratings
    ratings_by_bare = {_bare_ticker(t): r for t, r in latest_ratings.items()}
    heavy_bearish = []  # ⚠️ 重仓看空: weight > 10% 但最新评级看空
    missed_opportunities = []  # 机会提示: 最新评级看多但无持仓
    held_bare = {_bare_ticker(t) for t in portfolio.holdings}
    for ticker, w in weights.items():
        r = ratings_by_bare.get(_bare_ticker(ticker))
        if r and r.get("rating") in BEARISH and w > HEAVY_WEIGHT:
            heavy_bearish.append({
                "ticker": ticker,
                "weight": w,
                "rating": r["rating"],
                "batch_date": r.get("batch_date"),
            })
    for bare, r in ratings_by_bare.items():
        if r.get("rating") in BULLISH and bare not in held_bare:
            missed_opportunities.append({
                "ticker": bare,
                "rating": r["rating"],
                "batch_date": r.get("batch_date"),
            })

    # 近 90 天交易统计: 以最新成交日为锚（数据驱动，不依赖系统时钟）
    tx_dates = [d for d in (_parse_date(t.date) for t in portfolio.transactions) if d]
    anchor = max(tx_dates) if tx_dates else None
    recent: list[Transaction] = []
    if anchor:
        cutoff = anchor - timedelta(days=RECENT_DAYS)
        recent = [
            t for t in portfolio.transactions
            if (d := _parse_date(t.date)) and cutoff <= d <= anchor
        ]
    buys = [t for t in recent if t.action == "买入"]
    sells = [t for t in recent if t.action == "卖出"]
    dividends = [t for t in recent if t.action == "分红"]
    fee_total = sum(t.fee or 0.0 for t in buys + sells)
    txn_stats = {
        "window_days": RECENT_DAYS,
        "anchor_date": anchor.isoformat() if anchor else None,
        "buy_count": len(buys),
        "sell_count": len(sells),
        "dividend_count": len(dividends),
        "dividend_amount": sum(t.cash_change or 0.0 for t in dividends),
        "fee_total": fee_total,
        "fee_total_format": format_money_cn(fee_total),
    }

    return {
        "total_market_value": total_mv,
        "total_market_value_format": format_money_cn(total_mv),
        "total_invested": total_inv,
        "total_invested_format": format_money_cn(total_inv),
        "total_pnl": total_pnl,
        "total_pnl_format": format_money_cn(total_pnl),
        "top5": [{"ticker": t, "weight": w} for t, w in top5],
        "top5_concentration": top5_concentration,
        "deep_losers": deep_losers,
        "heavy_bearish": heavy_bearish,
        "missed_opportunities": missed_opportunities,
        "txn_stats": txn_stats,
    }


# ---------------------------------------------------------------------------
# 2. 推荐 × 实盘配对
# ---------------------------------------------------------------------------

def pair_recommendations(
    recs: list[dict], transactions: list[Transaction]
) -> list[dict]:
    """把方向性评级与实盘成交配对，判定用户是否跟随 agent 建议.

    规则:
    - 只处理 Buy/Overweight/Sell/Underweight；Hold 直接跳过（不进结果）
    - 同 ticker（裸代码）、成交日 ∈ (batch_date, batch_date + 10 个日历日]、
      方向一致（看多↔买入，看空↔卖出）的首笔成交记为跟随
    - 分红动作永远跳过
    - tag 含「网格」的成交打 grid=True 标记（手动交易的 tag 不算）
    - 无匹配 → status="no_action"
    """
    pairs: list[dict] = []
    for rec in recs:
        rating = (rec.get("rating") or "").strip()
        if rating not in (BULLISH | BEARISH):
            continue
        bare = _bare_ticker(rec.get("ticker", ""))
        batch_d = _parse_date(rec.get("batch_date"))
        bullish = rating in BULLISH
        want_action = "买入" if bullish else "卖出"

        pair = {
            "batch_date": rec.get("batch_date"),
            "ticker": bare,
            "rating": rating,
            "direction": "bullish" if bullish else "bearish",
            "confidence": rec.get("confidence"),
            "entry": rec.get("entry"),
            "stop": rec.get("stop"),
            "status": "no_action",
            "trade_date": None,
            "trade_price": None,
            "trade_shares": None,
            "trade_action": None,
            "grid": False,
        }
        if batch_d is None:
            pairs.append(pair)
            continue

        best: Transaction | None = None
        best_d: date | None = None
        for t in transactions:
            if t.action == "分红" or t.action != want_action:
                continue
            if _bare_ticker(t.ticker) != bare:
                continue
            d = _parse_date(t.date)
            if d is None or not (batch_d < d <= batch_d + timedelta(days=PAIR_WINDOW_DAYS)):
                continue
            if best_d is None or d < best_d:
                best, best_d = t, d
        if best is not None and best_d is not None:
            pair.update({
                "status": "matched",
                "trade_date": best_d.isoformat(),
                "trade_price": best.price or None,
                "trade_shares": best.shares,
                "trade_action": best.action,
                # 只有明确带「网格」标签的成交才算网格单；手动交易也带 tag
                # （如「手动卖出」「分批卖出」），bool(tag) 会把它们误标为网格。
                "grid": "网格" in (best.tag or ""),
            })
        pairs.append(pair)
    return pairs


# ---------------------------------------------------------------------------
# 3. 收益对比与聚合
# ---------------------------------------------------------------------------

# (基准收盘, 其后约 25 个交易日 [(date, close), ...])；基准缺失时返回 (None, [])
PriceLoader = Callable[[str, str], tuple[float | None, list[tuple[str, float | None]]]]


def _aggregate(events: list[dict]) -> dict[str, Any]:
    """一组事件的聚合: 各 horizon 的 delta 均值/中位数/用户胜率（delta > 0）。"""
    horizons: dict[int, Any] = {}
    for h in HORIZONS:
        ds = [e[f"delta_{h}"] for e in events if e.get(f"delta_{h}") is not None]
        horizons[h] = (
            {
                "n": len(ds),
                "mean": sum(ds) / len(ds),
                "median": median(ds),
                "win_rate": sum(1 for d in ds if d > 0) / len(ds),
            }
            if ds
            else None
        )
    return {"count": len(events), "horizons": horizons}


def compute_comparison(pairs: list[dict], price_loader: PriceLoader) -> dict[str, Any]:
    """对配对事件回填行情并计算 user vs agent 收益差.

    Args:
        pairs: ``pair_recommendations`` 的输出。
        price_loader: callable(ticker, batch_date) -> (base_close, series)，
            series 为基准日之后约 25 个交易日的 [(date, close)]。base_close
            缺失（停牌/无数据）时事件标记 status="no_price"，不参与聚合。
    """
    events: list[dict] = []
    for pair in pairs:
        event = dict(pair)
        if pair["status"] != "matched" or not pair.get("trade_price"):
            events.append(event)
            continue
        base_close, series = price_loader(pair["ticker"], pair["batch_date"])
        if not base_close:
            event["status"] = "no_price"
            events.append(event)
            continue

        trade_price = pair["trade_price"]
        bullish = pair["direction"] == "bullish"
        event["base_close"] = base_close
        for h in HORIZONS:
            agent_ret = user_ret = delta = None
            if len(series) >= h and (close_h := series[h - 1][1]):
                if bullish:
                    agent_ret = close_h / base_close - 1
                    user_ret = close_h / trade_price - 1
                else:
                    agent_ret = -(close_h / base_close - 1)
                    user_ret = (trade_price - close_h) / base_close
                delta = user_ret - agent_ret
            event[f"agent_ret_{h}"] = agent_ret
            event[f"user_ret_{h}"] = user_ret
            event[f"delta_{h}"] = delta
        events.append(event)

    # 配对率按「成交配对成功」计（含后续标为 no_price 的事件）；聚合只取有行情的
    n_matched = sum(1 for p in pairs if p["status"] == "matched")
    priced = [e for e in events if e["status"] == "matched" and e.get("base_close")]
    return {
        "events": events,
        "total_events": len(events),
        "matched": n_matched,
        "pair_rate": n_matched / len(events) if events else None,
        "overall": _aggregate(priced),
        "by_direction": {
            "bullish": _aggregate([e for e in priced if e["direction"] == "bullish"]),
            "bearish": _aggregate([e for e in priced if e["direction"] == "bearish"]),
        },
        "by_grid": {
            "grid": _aggregate([e for e in priced if e["grid"]]),
            "non_grid": _aggregate([e for e in priced if not e["grid"]]),
        },
    }
