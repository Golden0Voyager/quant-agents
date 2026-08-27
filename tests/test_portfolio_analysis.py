"""Tests for tradingagents/portfolio/analysis.py.

Covers:
1. 配对窗口边界（当日不计入 / 窗口外 / 方向不匹配 / 分红跳过 / no_action）
2. 看多/看空两侧收益口径与符号正确性
3. no_price（base_close 缺失）与 horizon 序列不足
4. grid 切片与聚合统计
5. analyze_portfolio 冲突清单 / 浮亏清单 / 近 90 天交易统计
"""
from __future__ import annotations

import pytest

from tradingagents.portfolio.analysis import (
    analyze_portfolio,
    compute_comparison,
    pair_recommendations,
)
from tradingagents.portfolio.models import Holding, Portfolio, Transaction


def _txn(date: str, ticker: str, action: str, price: float = 10.0,
         shares: float = 100, fee: float = 5.0, tag: str | None = None,
         cash_change: float | None = None) -> Transaction:
    return Transaction(
        date=date, ticker=ticker, price=price, action=action,
        shares=shares, fee=fee, tag=tag, cash_change=cash_change,
    )


def _rec(ticker: str, rating: str, batch_date: str = "2026-08-10") -> dict:
    return {
        "batch_date": batch_date, "ticker": ticker, "rating": rating,
        "entry": None, "stop": None, "confidence": "high",
    }


# ---------------------------------------------------------------------------
# pair_recommendations: 窗口与方向边界
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPairRecommendations:
    def test_hold_skipped(self):
        pairs = pair_recommendations([_rec("600519", "Hold")], [])
        assert pairs == []

    def test_match_first_trade_in_window(self):
        txns = [
            _txn("2026-08-12", "600519.SS", "买入", price=11.0),
            _txn("2026-08-14", "600519", "买入", price=12.0),
        ]
        pairs = pair_recommendations([_rec("600519", "Buy")], txns)
        assert len(pairs) == 1
        p = pairs[0]
        assert p["status"] == "matched"
        assert p["trade_date"] == "2026-08-12"  # 取窗口内首笔
        assert p["trade_price"] == 11.0
        assert p["direction"] == "bullish"
        assert p["grid"] is False

    def test_same_day_excluded_and_window_edge_included(self):
        # 当日 (batch_date 当天) 不计入；窗口右边界 batch_date+10 计入
        txns = [
            _txn("2026-08-10", "600519", "买入", price=99.0),  # 当日 → 排除
            _txn("2026-08-20", "600519", "买入", price=11.0),  # +10 天 → 命中
        ]
        pairs = pair_recommendations([_rec("600519", "Buy")], txns)
        assert pairs[0]["status"] == "matched"
        assert pairs[0]["trade_date"] == "2026-08-20"

    def test_out_of_window_no_action(self):
        txns = [_txn("2026-08-21", "600519", "买入")]  # +11 天 → 窗口外
        pairs = pair_recommendations([_rec("600519", "Buy")], txns)
        assert pairs[0]["status"] == "no_action"
        assert pairs[0]["trade_price"] is None

    def test_direction_mismatch_no_action(self):
        # 看多推荐配不到卖出；看空推荐配不到买入
        pairs = pair_recommendations(
            [_rec("600519", "Buy")], [_txn("2026-08-12", "600519", "卖出")]
        )
        assert pairs[0]["status"] == "no_action"
        pairs = pair_recommendations(
            [_rec("600519", "Sell")], [_txn("2026-08-12", "600519", "买入")]
        )
        assert pairs[0]["status"] == "no_action"

    def test_bearish_matches_sell(self):
        txns = [_txn("2026-08-11", "002241.SZ", "卖出", price=20.0)]
        pairs = pair_recommendations([_rec("002241", "Underweight")], txns)
        assert pairs[0]["status"] == "matched"
        assert pairs[0]["direction"] == "bearish"
        assert pairs[0]["trade_price"] == 20.0

    def test_dividend_never_matched(self):
        txns = [_txn("2026-08-11", "600519", "分红", cash_change=1000.0)]
        pairs = pair_recommendations([_rec("600519", "Buy")], txns)
        assert pairs[0]["status"] == "no_action"

    def test_tagged_trade_marked_grid(self):
        txns = [_txn("2026-08-12", "600519", "买入", tag="网格")]
        pairs = pair_recommendations([_rec("600519", "Overweight")], txns)
        assert pairs[0]["grid"] is True

    def test_ticker_suffix_normalization(self):
        txns = [_txn("2026-08-12", "600519.SS", "买入")]
        pairs = pair_recommendations([_rec("600519", "Buy")], txns)
        assert pairs[0]["status"] == "matched"
        assert pairs[0]["ticker"] == "600519"


# ---------------------------------------------------------------------------
# compute_comparison: 收益口径
# ---------------------------------------------------------------------------

def _series(closes: list[float], start: str = "2026-08-11") -> list[tuple[str, float]]:
    """生成连续伪交易日序列（日期只作标签，不追求真实日历）。"""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), c) for i, c in enumerate(closes)]


@pytest.mark.unit
class TestComputeComparison:
    def test_bullish_signs(self):
        # base=10, 买入价=9.5, 5 日后 close=11
        # agent_ret_5 = 11/10-1 = +10%; user_ret_5 = 11/9.5-1 ≈ +15.79%; delta > 0
        closes = [10.0] * 4 + [11.0] + [10.0] * 20
        loader = lambda t, bd: (10.0, _series(closes))  # noqa: E731
        pairs = pair_recommendations(
            [_rec("600519", "Buy")],
            [_txn("2026-08-11", "600519", "买入", price=9.5)],
        )
        result = compute_comparison(pairs, loader)
        e = result["events"][0]
        assert e["status"] == "matched"
        assert e["agent_ret_5"] == pytest.approx(0.10)
        assert e["user_ret_5"] == pytest.approx(11 / 9.5 - 1)
        assert e["delta_5"] == pytest.approx(11 / 9.5 - 1 - 0.10)
        assert e["delta_5"] > 0

    def test_bearish_signs(self):
        # base=10, 卖出价=10.5, 5 日后 close=9（看空判对：价格下跌）
        # agent_ret_5 = -(9/10-1) = +10%; user_ret_5 = (10.5-9)/10 = +15%
        closes = [10.0] * 4 + [9.0] + [10.0] * 20
        loader = lambda t, bd: (10.0, _series(closes))  # noqa: E731
        pairs = pair_recommendations(
            [_rec("600519", "Sell")],
            [_txn("2026-08-11", "600519", "卖出", price=10.5)],
        )
        result = compute_comparison(pairs, loader)
        e = result["events"][0]
        assert e["agent_ret_5"] == pytest.approx(0.10)
        assert e["user_ret_5"] == pytest.approx((10.5 - 9.0) / 10.0)
        assert e["delta_5"] == pytest.approx(0.15 - 0.10)

    def test_no_price_marks_status(self):
        loader = lambda t, bd: (None, [])  # noqa: E731
        pairs = pair_recommendations(
            [_rec("600519", "Buy")],
            [_txn("2026-08-11", "600519", "买入")],
        )
        result = compute_comparison(pairs, loader)
        e = result["events"][0]
        assert e["status"] == "no_price"
        assert result["overall"]["count"] == 0  # 不参与聚合
        assert result["matched"] == 1  # 配对率仍按成交计

    def test_short_series_horizon_none(self):
        # 只有 6 个交易日: h=5 有值，h=10/20 为 None
        closes = [11.0] * 6
        loader = lambda t, bd: (10.0, _series(closes))  # noqa: E731
        pairs = pair_recommendations(
            [_rec("600519", "Buy")],
            [_txn("2026-08-11", "600519", "买入", price=10.0)],
        )
        result = compute_comparison(pairs, loader)
        e = result["events"][0]
        assert e["delta_5"] is not None
        assert e["delta_10"] is None
        assert e["delta_20"] is None
        assert result["overall"]["horizons"][20] is None

    def test_aggregate_and_slices(self):
        # 两个看多事件: 一个 grid 一个非 grid，delta_5 一正一负
        closes = [10.0] * 4 + [11.0] + [10.0] * 20  # 5 日后 = 11 → 相对 base=10 涨 10%
        loader = lambda t, bd: (10.0, _series(closes))  # noqa: E731
        recs = [_rec("600519", "Buy"), _rec("002241", "Buy")]
        txns = [
            _txn("2026-08-11", "600519", "买入", price=10.0),   # delta_5 = 0
            _txn("2026-08-11", "002241", "买入", price=9.0, tag="网格"),  # delta_5 > 0
        ]
        pairs = pair_recommendations(recs, txns)
        result = compute_comparison(pairs, loader)

        assert result["total_events"] == 2
        assert result["matched"] == 2
        assert result["pair_rate"] == pytest.approx(1.0)

        overall5 = result["overall"]["horizons"][5]
        assert overall5["n"] == 2
        # user1 delta = 0.10-0.10=0; user2 delta = (11/9-1)-0.10 ≈ 0.122
        expected = (0.0, 11 / 9 - 1 - 0.10)
        assert overall5["mean"] == pytest.approx(sum(expected) / 2)
        assert overall5["median"] == pytest.approx(sum(expected) / 2)
        assert overall5["win_rate"] == pytest.approx(0.5)  # 仅 delta>0 的 1 个

        bull = result["by_direction"]["bullish"]["horizons"][5]
        assert bull["n"] == 2
        assert result["by_direction"]["bearish"]["count"] == 0
        assert result["by_direction"]["bearish"]["horizons"][5] is None

        grid_agg = result["by_grid"]["grid"]["horizons"][5]
        non_grid_agg = result["by_grid"]["non_grid"]["horizons"][5]
        assert grid_agg["n"] == 1 and grid_agg["win_rate"] == pytest.approx(1.0)
        assert non_grid_agg["n"] == 1 and non_grid_agg["win_rate"] == pytest.approx(0.0)

    def test_pair_rate_with_no_action(self):
        pairs = pair_recommendations(
            [_rec("600519", "Buy"), _rec("002241", "Sell")],
            [_txn("2026-08-11", "600519", "买入")],
        )
        loader = lambda t, bd: (10.0, _series([10.0] * 25))  # noqa: E731
        result = compute_comparison(pairs, loader)
        assert result["total_events"] == 2
        assert result["matched"] == 1
        assert result["pair_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# analyze_portfolio: 冲突清单与交易统计
# ---------------------------------------------------------------------------

def _portfolio() -> Portfolio:
    return Portfolio(
        holdings={
            "600519.SS": Holding(
                ticker="600519.SS", name="贵州茅台", shares=100,
                avg_cost=1500, market_price=1400, market_value=140_000,
                invested_amount=150_000, pnl_pct=-0.0667, weight=0.5833,
            ),
            "002241.SZ": Holding(
                ticker="002241.SZ", name="歌尔股份", shares=1000,
                avg_cost=20, market_price=15, market_value=15_000,
                invested_amount=20_000, pnl_pct=-0.25, weight=0.0625,
            ),
            "300750.SZ": Holding(
                ticker="300750.SZ", name="宁德时代", shares=500,
                avg_cost=170, market_price=170, market_value=85_000,
                invested_amount=85_000, pnl_pct=0.0, weight=0.3542,
            ),
        },
        transactions=[
            _txn("2026-08-20", "600519.SS", "买入", fee=10.0),
            _txn("2026-08-21", "002241.SZ", "卖出", fee=8.0, tag="网格"),
            _txn("2026-08-22", "600519.SS", "分红", fee=None, cash_change=1696.0),
            _txn("2026-05-01", "600519.SS", "买入", fee=99.0),  # 90 天窗口外
        ],
    )


@pytest.mark.unit
class TestAnalyzePortfolio:
    def test_summary_and_format(self):
        result = analyze_portfolio(_portfolio(), {})
        assert result["total_market_value"] == pytest.approx(240_000)
        assert result["total_invested"] == pytest.approx(255_000)
        assert result["total_pnl"] == pytest.approx(-15_000)
        assert result["total_market_value_format"] == "24.00万"
        assert result["total_pnl_format"] == "-1.50万"

    def test_top5_concentration(self):
        result = analyze_portfolio(_portfolio(), {})
        assert result["top5_concentration"] == pytest.approx(1.0)  # 仅 3 只
        assert result["top5"][0]["ticker"] == "600519.SS"

    def test_deep_losers(self):
        result = analyze_portfolio(_portfolio(), {})
        losers = {x["ticker"] for x in result["deep_losers"]}
        assert losers == {"002241.SZ"}  # -25% 入选，-6.67% 不入选

    def test_heavy_bearish_conflict(self):
        ratings = {"600519": {"rating": "Sell", "batch_date": "2026-08-24"}}
        result = analyze_portfolio(_portfolio(), ratings)
        assert len(result["heavy_bearish"]) == 1
        assert result["heavy_bearish"][0]["ticker"] == "600519.SS"
        assert result["missed_opportunities"] == []

    def test_light_bearish_no_conflict(self):
        # 002241 评级 Sell 但权重 6.25% < 10% → 不算重仓看空
        ratings = {"002241": {"rating": "Sell", "batch_date": "2026-08-24"}}
        result = analyze_portfolio(_portfolio(), ratings)
        assert result["heavy_bearish"] == []

    def test_missed_opportunity(self):
        ratings = {
            "601318": {"rating": "Buy", "batch_date": "2026-08-24"},   # 无持仓 → 机会
            "300750": {"rating": "Overweight", "batch_date": "2026-08-24"},  # 有持仓 → 不提示
        }
        result = analyze_portfolio(_portfolio(), ratings)
        assert [x["ticker"] for x in result["missed_opportunities"]] == ["601318"]

    def test_txn_stats_90d(self):
        result = analyze_portfolio(_portfolio(), {})
        ts = result["txn_stats"]
        assert ts["anchor_date"] == "2026-08-22"
        assert ts["buy_count"] == 1  # 2026-05-01 的买入在窗口外
        assert ts["sell_count"] == 1
        assert ts["fee_total"] == pytest.approx(18.0)
        assert ts["dividend_count"] == 1
        assert ts["dividend_amount"] == pytest.approx(1696.0)

    def test_empty_portfolio(self):
        result = analyze_portfolio(Portfolio(), {})
        assert result["total_market_value"] == 0
        assert result["top5_concentration"] == 0
        assert result["txn_stats"]["anchor_date"] is None
