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
from tradingagents.dataflows.errors import NoMarketDataError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Function A — Hot rank (人气排名)
# ---------------------------------------------------------------------------


def _hot_rank_table_from_local(ticker: str, bare_code: str) -> str | None:
    """Read the Eastmoney top-100 hot-rank entry from quant_core.db.

    Returns ``None`` only when no local snapshot exists at all (caller then
    falls back to the online fetch / HiThink substitute). When a snapshot
    exists, returns either the rank line or the explicit 未进入 top-100
    negative line — both carry the snapshot date so analysts can weigh
    freshness. The endpoint is intermittently WAF-blocked, so a snapshot
    is used regardless of age (same policy as 千股千评).
    """
    try:
        from tradingagents.dataflows import smartmoney_vendor

        snapshot_date = smartmoney_vendor.get_stock_hot_rank_snapshot_date()
        if snapshot_date is None:
            return None
        row = smartmoney_vendor.get_stock_hot_rank(ticker)
    except NoMarketDataError:
        # Snapshot exists but the ticker is not in the top-100 — a real
        # negative signal, not data degradation (mirrors the online path).
        return (
            f"整体人气排名: 未进入 top-100（{bare_code} 不在 {snapshot_date} "
            "东财人气榜快照内，属明确的阴性结果：关注度一般，非数据缺失）"
        )
    except Exception as exc:  # noqa: BLE001 — degrade to online fetch
        logger.debug("local stock_hot_rank unavailable for %s: %s", ticker, exc)
        return None

    def _num(value: Any, digits: int = 2) -> str:
        try:
            if value is None:
                return "N/A"
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return str(value)

    rank = row.get("rank")
    rank = int(rank) if rank is not None else "N/A"
    name = row.get("name") or bare_code
    code = row.get("code") or bare_code
    line = (
        f"整体人气排名: #{rank}  |  {name}({code})  |  "
        f"最新价: {_num(row.get('close_price'))}  |  "
        f"涨跌幅: {_num(row.get('change_pct'))}%"
    )
    prev = row.get("prev_rank")
    if prev is not None:
        try:
            line += f"  |  昨日排名: #{int(prev)}"
        except (TypeError, ValueError):
            pass
    line += f"  (快照日: {row.get('trade_date')}, source: quant_core.db)"
    return line


def _hithink_hot_rank_line(ticker: str) -> str | None:
    """Best-effort HiThink substitute for the Eastmoney hot-rank table.

    The Eastmoney table endpoint (``stock_hot_rank_em``) has been refusing
    connections since ~2026-08-25 (anti-scraping), which degraded nearly
    every A-share sentiment prefetch to ``partial``. HiThink's official
    hot-stock-list covers the "current rank" line for tickers in its daily
    Top30. Returns ``None`` when hithink is unconfigured, errors, or the
    ticker is not in the Top30 — the caller then keeps the placeholder.
    """
    try:
        from tradingagents.dataflows.hithink_vendor import get_hot_rank

        return get_hot_rank(ticker)
    except Exception as exc:  # noqa: BLE001 — substitute must degrade silently
        logger.debug("hithink hot-rank substitute failed for %s: %s", ticker, exc)
        return None


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
    # Local-first: quant_pipeline maintains a daily top-100 snapshot in
    # quant_core.db (stock_hot_rank table), avoiding the intermittently
    # WAF-blocked emappdata endpoint that raised JSONDecodeError in the
    # akshare path. Falls back to the online table (then HiThink Top30)
    # only when no local snapshot exists at all.
    hot_rank_lines: list[str] = []
    _local = _hot_rank_table_from_local(ticker, bare_code)
    if _local is not None:
        hot_rank_lines.append(_local)
    else:
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
                # 未进 top-100 是明确的阴性信号（今日不热门），不是数据缺失——
                # 不用尖括号占位符，避免被路由层误判为 partial 降级
                # (20260826 批次 22/23 票因此被打上 partial=1，稀释了降级信号)
                hot_rank_lines.append(
                    f"整体人气排名: 未进入 top-100（{bare_code} 今日不在东财人气榜内，"
                    "属明确的阴性结果：关注度一般，非数据缺失）"
                )
        except Exception as exc:
            logger.warning("Eastmoney hot-rank table fetch failed for %s: %s", ticker, exc)
            hithink_line = _hithink_hot_rank_line(ticker)
            if hithink_line:
                hot_rank_lines.append(hithink_line)
            else:
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


def _stock_comment_from_local(bare_code: str) -> str | None:
    """Read the 千股千评 comprehensive-score snapshot from quant_core.db.

    Returns the formatted 综合评分 block, or ``None`` when the local
    snapshot is unavailable (missing table / no row) so the caller falls
    back to the online ``stock_comment_em()`` table fetch. A snapshot is
    used regardless of age — during weekends/holidays it is still the
    latest available data — but ages beyond 7 calendar days are logged.
    """
    try:
        from tradingagents.dataflows.smartmoney_vendor import get_stock_comment

        row = get_stock_comment(bare_code)
    except Exception as exc:  # noqa: BLE001 — degrade to online fetch
        logger.debug("local stock_comment unavailable for %s: %s", bare_code, exc)
        return None

    trade_date = str(row.get("trade_date") or "?")
    try:
        from datetime import date

        age_days = (date.today() - date.fromisoformat(trade_date[:10])).days
        if age_days > 7:
            logger.warning(
                "stock_comment snapshot for %s is %d days old (%s)",
                bare_code, age_days, trade_date,
            )
    except ValueError:
        pass

    def _num(value: Any, digits: int = 2) -> str:
        try:
            if value is None:
                return "N/A"
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return str(value)

    score = _num(row.get("composite_score"))
    rank = row.get("rank")
    rank = int(rank) if rank is not None else "N/A"
    return (
        f"综合评分 — {row.get('name')}({bare_code})\n"
        f"  综合得分: {score}/100  |  关注指数: {_num(row.get('focus_index'))}/100  |  "
        f"目前排名: #{rank}\n"
        f"  最新价: {_num(row.get('close_price'))}  |  涨跌幅: {_num(row.get('change_pct'))}%  |  "
        f"换手率: {_num(row.get('turnover'))}%\n"
        f"  市盈率: {_num(row.get('pe_dynamic'))}  |  机构参与度: {_num(row.get('org_participation'))}  |  "
        f"上升: {_num(row.get('rank_up'), 0)}\n"
        f"  (快照交易日: {trade_date}, source: quant_core.db)"
    )


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
    # Local-first: quant_pipeline maintains a daily full-market snapshot in
    # quant_core.db (stock_comment table), avoiding the slow whole-table
    # ak.stock_comment_em() fetch that timed out in ~46% of runs. Falls back
    # to the online table when the local snapshot is unavailable.
    comment_lines: list[str] = []
    _local = _stock_comment_from_local(bare_code)
    if _local is not None:
        comment_lines.append(_local)
    else:
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
# Function C — Market-wide hot keywords/concepts (热点概念)
# ---------------------------------------------------------------------------

def fetch_eastmoney_hot_keywords(limit: int = 15) -> str:
    """Fetch market-wide hot keywords/concepts from Eastmoney (东方财富).

    Uses ``ak.stock_hot_keyword_em()`` which returns the top trending
    concept板块 (sectors/concepts) with heat scores across all A-shares.
    This is a market-wide sentiment signal — not ticker-specific — so it
    provides context on what the market is collectively focused on.

    Returns a placeholder string on any failure.
    """
    try:
        with no_proxy():
            df = _akshare_retry(lambda: ak.stock_hot_keyword_em(), max_retries=3)
        if df is None or df.empty:
            return "<Eastmoney hot keywords: no data returned>"

        lines = ["Eastmoney 市场热点概念 (实时热度排名):"]
        recent = df.head(limit)
        for _, row in recent.iterrows():
            cols = df.columns.tolist()
            date = _safe_col(row, cols, "时间", 0)
            code = _safe_col(row, cols, "股票代码", 1)
            concept = _safe_col(row, cols, "概念名称", 2)
            concept_code = _safe_col(row, cols, "概念代码", 3)
            heat = _safe_col(row, cols, "热度", 4)
            lines.append(
                f"  [{date}] {concept}({concept_code}) — 代码: {code} — 热度: {heat}"
            )
        if len(recent) < len(df):
            lines.append(f"  (共 {len(df)} 个热点概念，显示前 {len(recent)} 个)")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Eastmoney hot keywords fetch failed: %s", exc)
        return (
            f"<Eastmoney hot keywords unavailable: {type(exc).__name__}>"
        )


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
