"""Eastmoney (东方财富) sentiment data fetcher for A-share tickers.

Provides two functions that replace the broken StockTwits / Reddit sources:

  1. ``fetch_eastmoney_hot_rank``  — 个股人气排名 from the hot-rank table
     (``ak.stock_hot_rank_em()``) plus historical rank / fan-composition
     details (``ak.stock_hot_rank_detail_em()``).

  2. ``fetch_eastmoney_guba_sentiment`` — 股吧 sentiment signals from the
     千股千评 dataset (``ak.stock_comment_em()``), user-attention index
     (``ak.stock_comment_detail_scrd_focus_em()``), and participation
     willingness (``ak.stock_comment_detail_scrd_desire_em()``).

Both functions follow the same contract as the StockTwits / Reddit fetchers:
they always return a ``str`` (never raise, never return ``None``), degrace
gracefully with a placeholder on any failure, and are safe to call from
analyst nodes without try/except guards.

Non-A-share tickers are handled gracefully — the functions detect the market
and return an appropriate placeholder with minimal overhead.
"""

from __future__ import annotations

import logging
from typing import Any

import akshare as ak

from tradingagents.dataflows.akshare_common import (
    _akshare_retry,
    is_a_share_ticker,
    no_proxy,
    safe_float,
    to_akshare_symbol,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Function A — Hot rank (人气排名)
# ---------------------------------------------------------------------------

def fetch_eastmoney_hot_rank(ticker: str, limit: int = 20) -> str:
    """Fetch Eastmoney hot-rank data for *ticker* and return it as a formatted
    plaintext block ready for prompt injection.

    Combines two sources:

    * The overall hot-rank table (``stock_hot_rank_em()``) filtered to the
      target symbol — shows the stock's rank, price, and daily change among
      the top-100 most-watched A-shares.
    * The historical rank detail (``stock_hot_rank_detail_em()``) — shows
      the stock's rank trajectory and fan-composition (新晋粉丝 / 铁杆粉丝
      ratio) over the past year.

    Returns a placeholder string on any failure or for non-A-share tickers.
    """
    if not is_a_share_ticker(ticker):
        return f"<Eastmoney hot-rank unavailable for non-A-share ticker: {ticker}>"

    bare_code = to_akshare_symbol(ticker, "bare")
    prefixed_code = to_akshare_symbol(ticker, "upper_prefix")

    # --- Source 1: overall hot-rank table ---
    hot_rank_lines: list[str] = []
    try:
        with no_proxy():
            rank_df = _akshare_retry(lambda: ak.stock_hot_rank_em(), max_retries=3)
        mask = rank_df.iloc[:, 0].astype(str).str.contains(
            bare_code, na=False
        ) | rank_df.iloc[:, 1].astype(str).str.contains(bare_code, na=False)
        match = rank_df[mask]
        if not match.empty:
            row = match.iloc[0]
            cols = rank_df.columns.tolist()
            rank_val = _safe_col(row, cols, "当前排名")
            price = _safe_col(row, cols, "最新价")
            change_pct = _safe_col(row, cols, "涨跌幅")
            change_amt = _safe_col(row, cols, "涨跌额")
            name = _safe_col(row, cols, "股票名称")
            code = _safe_col(row, cols, "代码")
            hot_rank_lines.append(
                f"整体人气排名: #{rank_val}  |  {name}({code})  |  "
                f"最新价: {price}  |  涨跌幅: {change_pct}%  |  涨跌额: {change_amt}"
            )
        else:
            hot_rank_lines.append(
                f"<{bare_code} not found in the top-100 hot rank list>"
            )
    except Exception as exc:
        logger.warning("Eastmoney hot-rank table fetch failed for %s: %s", ticker, exc)
        hot_rank_lines.append(f"<hot-rank table unavailable: {type(exc).__name__}>")

    # --- Source 2: historical rank detail ---
    detail_lines: list[str] = []
    try:
        with no_proxy():
            detail_df = _akshare_retry(
                lambda: ak.stock_hot_rank_detail_em(symbol=prefixed_code),
                max_retries=3,
            )
        if detail_df is not None and not detail_df.empty:
            # API returns rows oldest-first; tail() selects the newest `limit` rows.
            recent = detail_df.tail(limit)
            for _, row in recent.iterrows():
                cols = detail_df.columns.tolist()
                date = _safe_col(row, cols, "时间", 0)
                rank_pos = _safe_col(row, cols, "排名", 1)
                new_fan = _safe_col(row, cols, "新晋粉丝", 3)
                loyal_fan = _safe_col(row, cols, "铁杆粉丝", 4)
                detail_lines.append(
                    f"  [{date}] 排名: {rank_pos}  |  新晋粉丝: {new_fan}  "
                    f"|  铁杆粉丝: {loyal_fan}"
                )
        else:
            detail_lines.append(
                f"<no historical rank detail available for {bare_code}>"
            )
    except Exception as exc:
        logger.warning(
            "Eastmoney hot-rank detail fetch failed for %s: %s", ticker, exc
        )
        detail_lines.append(
            f"<historical rank detail unavailable: {type(exc).__name__}>"
        )

    summary = f"Eastmoney 人气排名 — {ticker} (最近 {min(len(detail_lines), limit)} 天)\n"
    if hot_rank_lines:
        summary += "\n".join(hot_rank_lines) + "\n\n"
    summary += "历史排名与粉丝构成:\n" + "\n".join(detail_lines)
    return summary


# ---------------------------------------------------------------------------
# Function B — Guba sentiment (股吧情绪)
# ---------------------------------------------------------------------------

def fetch_eastmoney_guba_sentiment(ticker: str, limit: int = 10) -> str:
    """Fetch Eastmoney Guba (股吧) sentiment indicators for *ticker*.

    Combines three complementary sources from the 千股千评 system:

    * **Comprehensive score** (综合得分) from ``stock_comment_em()`` — a
      composite rating (0–100) with sub-scores for institutional participation,
      upward trend, etc.
    * **User-attention index** (用户关注指数) from
      ``stock_comment_detail_scrd_focus_em()`` — a daily time-series (0–100)
      of how much attention retail investors are paying to the stock.
    * **Participation willingness** (参与意愿) from
      ``stock_comment_detail_scrd_desire_em()`` — a daily measure of whether
      retail investors want to buy (higher = more willing).

    Returns a placeholder string on any failure or for non-A-share tickers.
    """
    if not is_a_share_ticker(ticker):
        return (
            f"<Eastmoney Guba sentiment unavailable for non-A-share ticker: "
            f"{ticker}>"
        )

    bare_code = to_akshare_symbol(ticker, "bare")

    # --- Source 1: comprehensive score (千股千评) ---
    comment_lines: list[str] = []
    try:
        with no_proxy():
            comment_df = _akshare_retry(lambda: ak.stock_comment_em(), max_retries=3)
        mask = comment_df.iloc[:, 1].astype(str).str.contains(bare_code, na=False)
        match = comment_df[mask]
        if not match.empty:
            row = match.iloc[0]
            cols = comment_df.columns.tolist()
            name = _safe_col(row, cols, "名称", 2)
            score = _safe_col(row, cols, "综合得分", 9)
            rank = _safe_col(row, cols, "目前排名", 11)
            attention = _safe_col(row, cols, "关注指数", 12)
            price = _safe_col(row, cols, "最新价", 3)
            change_pct = _safe_col(row, cols, "涨跌幅", 4)
            turnover = _safe_col(row, cols, "换手率", 5)
            pe = _safe_col(row, cols, "市盈率", 6)
            inst_participation = _safe_col(row, cols, "机构参与度", 8)
            trend_up = _safe_col(row, cols, "上升", 10)
            comment_lines.append(
                f"综合评分 — {name}({bare_code})\n"
                f"  综合得分: {score}/100  |  关注指数: {attention}/100  |  "
                f"目前排名: #{rank}\n"
                f"  最新价: {price}  |  涨跌幅: {change_pct}%  |  "
                f"换手率: {turnover}%\n"
                f"  市盈率: {pe}  |  机构参与度: {inst_participation}  |  "
                f"上升: {trend_up}"
            )
        else:
            comment_lines.append(
                f"<{bare_code} not found in 千股千评 dataset>"
            )
    except Exception as exc:
        logger.warning(
            "Eastmoney comment table fetch failed for %s: %s", ticker, exc
        )
        comment_lines.append(
            f"<comprehensive score unavailable: {type(exc).__name__}>"
        )

    # --- Source 2: user-attention index time-series ---
    focus_lines: list[str] = []
    try:
        with no_proxy():
            focus_df = _akshare_retry(
                lambda: ak.stock_comment_detail_scrd_focus_em(symbol=bare_code),
                max_retries=3,
            )
        if focus_df is not None and not focus_df.empty:
            recent = focus_df.tail(limit)
            for _, row in recent.iterrows():
                cols = focus_df.columns.tolist()
                date = _safe_col(row, cols, "交易日", 0)
                index_val = _safe_col(row, cols, "用户关注指数", 1)
                focus_lines.append(f"  [{date}] 用户关注指数: {index_val}/100")
        else:
            focus_lines.append(
                f"<no user-attention data available for {bare_code}>"
            )
    except Exception as exc:
        logger.warning(
            "Eastmoney user-attention fetch failed for %s: %s", ticker, exc
        )
        focus_lines.append(
            f"<user-attention index unavailable: {type(exc).__name__}>"
        )

    # --- Source 3: participation willingness time-series ---
    desire_lines: list[str] = []
    try:
        with no_proxy():
            desire_df = _akshare_retry(
                lambda: ak.stock_comment_detail_scrd_desire_em(symbol=bare_code),
                max_retries=3,
            )
        if desire_df is not None and not desire_df.empty:
            recent = desire_df.tail(limit)
            for _, row in recent.iterrows():
                cols = desire_df.columns.tolist()
                date = _safe_col(row, cols, "交易日期", 0)
                will_val = _safe_col(row, cols, "参与意愿", 2)
                will_5d = _safe_col(row, cols, "5日平均参与意愿", 3)
                will_chg = _safe_col(row, cols, "参与意愿变化", 4)
                desire_lines.append(
                    f"  [{date}] 参与意愿: {will_val}  |  "
                    f"5日均值: {will_5d}  |  变化: {will_chg}"
                )
        else:
            desire_lines.append(
                f"<no participation-willingness data available for {bare_code}>"
            )
    except Exception as exc:
        logger.warning(
            "Eastmoney participation-willingness fetch failed for %s: %s",
            ticker,
            exc,
        )
        desire_lines.append(
            f"<participation willingness unavailable: {type(exc).__name__}>"
        )

    parts = [
        f"Eastmoney 股吧情绪 — {ticker}:",
        "",
        "▎千股千评综合评分",
        "\n".join(comment_lines),
        "",
        f"▎用户关注指数 (近{len(focus_lines)}个交易日)",
        "\n".join(focus_lines) if focus_lines else "  <无数据>",
        "",
        f"▎参与意愿趋势 (近{len(desire_lines)}个交易日)",
        "\n".join(desire_lines) if desire_lines else "  <无数据>",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_col(
    row: Any, cols: list[str], name: str, fallback_idx: int | None = None
) -> str:
    """Safely extract a column value from a pandas row by name or index.

    Returns ``"N/A"`` when the column is missing or the value is None/NaN.
    """
    value: Any = None
    try:
        if name in cols:
            idx = cols.index(name)
            value = row.iloc[idx]
        elif fallback_idx is not None and fallback_idx < len(row):
            value = row.iloc[fallback_idx]
    except Exception:
        return "N/A"

    sf = safe_float(value)
    if sf is not None:
        # Format floats nicely
        if abs(sf) >= 100:
            return f"{sf:.1f}"
        return f"{sf:.2f}"
    if value is None or (isinstance(value, float) and (value != value)):  # NaN check
        return "N/A"
    return str(value)
