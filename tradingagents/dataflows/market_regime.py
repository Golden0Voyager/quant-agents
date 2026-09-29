"""Market regime digest + shared daily report for A-share runs (P2/P3).

Builds a once-per-trading-day, whole-market context: index performance,
market breadth (涨跌家数 / 涨跌停 / 新高新低 / 破净占比), all read from the
local quant_core.db — no network calls. An optional one-shot LLM synthesis
turns the digest into a structured regime report (risk grade + trend +
observation points) following the market-context dimensions of
TradingAgents-CN's ``index_analyst``.

The synthesized report is cached in-process and on disk (``data_cache_dir``)
keyed by ``(trade_date, data_date)`` — the calendar date *and* the newest
index date inside the digest. The data date matters because the local
pipeline can refresh ``quant_core.db`` intraday: a run at 10:00 and a run at
16:30 on the same calendar day see different data, and the second one must
not reuse the first one's summary. A 24-ticker batch therefore pays for ONE
LLM call instead of 24, and every ticker is judged against the *same* market
context, which also makes cross-ticker comparison fairer.

Only a *successful* synthesis is cached. Fail-open everywhere: any data
source or the LLM call may fail; the digest marks missing sections as
unavailable and the orchestrator falls back to the raw digest text so
downstream prompts always get something usable — but an unsynthesized
digest never reaches the disk cache, otherwise one failed provider call
would cost the whole day its regime report.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

# (quant_core.db index_code, display name)
_REGIME_INDICES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
)

_LOOKBACK_DAYS = 30

_DIGEST_UNAVAILABLE = "DATA_UNAVAILABLE"

# Keyed by "<trade_date>|<data_date>"; holds synthesized reports only, so a
# failed synthesis never short-circuits a later call that could succeed.
_PROCESS_CACHE: dict[str, str] = {}


def _fetch_index_rows(trade_date: str) -> list[dict[str, Any]]:
    """Latest index performance per regime index, from quant_core.db."""
    from tradingagents.dataflows.smartmoney_vendor import get_index_daily_df

    start = _lookback_start(trade_date)
    rows: list[dict[str, Any]] = []
    for code, name in _REGIME_INDICES:
        df = get_index_daily_df(code, start, trade_date)
        if df is None or len(df) < 2:
            rows.append({"name": name, "code": code, "available": False})
            continue
        closes = df["Close"].astype(float)
        last = df.iloc[-1]
        prev_close = float(closes.iloc[-2])
        close = float(last["Close"])
        pct = (close / prev_close - 1.0) * 100.0 if prev_close else None
        # index_daily stores zero volume in current pipeline rows, so derive
        # participation proxies from the close series instead: 5-day momentum
        # and where the close sits inside the 20-day high-low range.
        pct_5d = (close / float(closes.iloc[-6]) - 1.0) * 100.0 if len(closes) >= 6 and closes.iloc[-6] else None
        window = closes.iloc[-20:]
        span = float(window.max()) - float(window.min())
        range_pos = (close - float(window.min())) / span if span > 0 else None
        rows.append(
            {
                "name": name,
                "code": code,
                "available": True,
                "date": str(last["Date"]),
                "close": close,
                "pct_change": pct,
                "pct_change_5d": pct_5d,
                "range_position_20d": range_pos,
                "above_20d_high": bool(close >= float(window.max())),
            }
        )
    return rows


def _lookback_start(trade_date: str) -> str:
    from datetime import timedelta

    try:
        from datetime import date as _date

        end = _date.fromisoformat(trade_date)
    except ValueError:
        return trade_date
    return (end - timedelta(days=_LOOKBACK_DAYS * 2)).isoformat()


def _fetch_breadth(trade_date: str) -> dict[str, Any] | None:
    from tradingagents.dataflows.smartmoney_vendor import get_market_breadth

    breadth = get_market_breadth(trade_date)
    if breadth is None:
        return None
    # The pipeline occasionally writes all-zero legu rows after a failed
    # scrape; an all-zero breadth row carries no signal, treat as missing.
    if (
        (breadth.get("up_count") or 0) == 0
        and (breadth.get("down_count") or 0) == 0
        and (breadth.get("limit_up") or 0) == 0
    ):
        logger.debug("market_breadth row %s is all-zero, treating as unavailable", breadth.get("date"))
        return None
    return breadth


def build_market_regime_digest(trade_date: str) -> dict[str, Any]:
    """Assemble the market-wide data digest for one trading date."""
    indices = _fetch_index_rows(trade_date)
    breadth = _fetch_breadth(trade_date)
    return {
        "trade_date": trade_date,
        "indices": indices,
        "breadth": breadth,
    }


def extract_data_date(digest: Mapping[str, Any]) -> str:
    """Newest index date carried by ``digest``.

    The pipeline refreshes ``quant_core.db`` on its own schedule (a run
    before the refresh and a run after it share a calendar date but not
    their data), so the cache has to key on this rather than on
    ``trade_date`` alone. Falls back to ``trade_date`` when every index row
    is unavailable — no better anchor exists then, and the digest itself
    says so.
    """
    trade_date = str(digest.get("trade_date") or "")
    dates = [
        str(row["date"])
        for row in digest.get("indices") or []
        if row.get("available") and row.get("date")
    ]
    return max(dates) if dates else trade_date


def _fmt_pct(value: float | None) -> str:
    return f"{value:+.2f}%" if value is not None else "N/A"


def render_market_regime_digest(digest: dict[str, Any]) -> str:
    """Render the digest as a Markdown data block (no LLM interpretation)."""
    lines: list[str] = [
        f"# A-share market digest — {digest['trade_date']}（全市场共享数据，非个股）",
        "",
        "## 主要指数（最新交易日）",
        "",
        "| 指数 | 收盘 | 涨跌幅 | 5日涨跌幅 | 20日区间位置 | 创20日新高 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in digest["indices"]:
        if not row.get("available"):
            lines.append(f"| {row['name']} | {_DIGEST_UNAVAILABLE} | — | — | — | — |")
            continue
        pos = row.get("range_position_20d")
        pos_str = f"{pos * 100:.0f}%" if pos is not None else "N/A"
        high = "是" if row.get("above_20d_high") else "否"
        lines.append(
            f"| {row['name']} | {row['close']:.2f} | {_fmt_pct(row.get('pct_change'))} "
            f"| {_fmt_pct(row.get('pct_change_5d'))} | {pos_str} | {high} |"
        )

    breadth = digest.get("breadth")
    lines += ["", "## 市场宽度（涨跌家数 / 涨跌停 / 新高新低）", ""]
    if not breadth:
        lines.append(f"市场宽度数据 {_DIGEST_UNAVAILABLE}（本地 market_breadth 表无该日期前数据）。")
    else:
        lines.append(f"数据日期：{breadth.get('date')}")
        lines.append("")
        lines.append(
            f"- 涨跌平家数：上涨 {breadth.get('up_count')} / 下跌 {breadth.get('down_count')} / "
            f"平盘 {breadth.get('flat_count')}"
        )
        lines.append(
            f"- 涨跌停：涨停 {breadth.get('limit_up')}（真实 {breadth.get('real_limit_up')}）/ "
            f"跌停 {breadth.get('limit_down')}（真实 {breadth.get('real_limit_down')}）"
        )
        lines.append(
            f"- 新高新低：20日新高 {breadth.get('high20')} / 新低 {breadth.get('low20')}；"
            f"60日新高 {breadth.get('high60')} / 新低 {breadth.get('low60')}；"
            f"120日新高 {breadth.get('high120')} / 新低 {breadth.get('low120')}"
        )
        lines.append(f"- 破净占比：{breadth.get('below_net_asset_ratio')}%")
        lines.append(f"- 市场活跃度：{breadth.get('activity_ratio')}%")

    return "\n".join(lines)


_REGIME_SYNTHESIS_PROMPT = """你是一位 A 股市场环境（regime）分析师。以下是一份某日 A 股全市场数据摘要（指数表现 + 市场宽度）。

{digest}

请基于以上数据输出一份简洁的市场环境报告（300-500 字），严格使用如下结构：

1. **市场概况**：主要指数表现一句话点评（涨跌分化、风格偏向）
2. **技术面**：指数趋势状态（位置与量能配合）
3. **市场宽度**：涨跌家数、涨跌停、新高新低所反映的参与度与赚钱效应
4. **风险评估**：🟢 低风险 / 🟡 中等风险 / 🔴 高风险（三选一，附一句理由）
5. **趋势判断**：📈 上涨 / 📉 下跌 / ➡️ 震荡（三选一）
6. **后续观察点**：2-3 条具体的下一步跟踪信号

约束：只描述市场整体环境，不得涉及任何个股买卖建议；不要编造摘要之外的数据；摘要中标记 {unavailable} 的维度直接说明缺失。{language}"""


def synthesize_market_regime_report(digest_text: str, llm: Any) -> tuple[str, bool]:
    """One-shot LLM synthesis of the digest into a structured regime report.

    Returns ``(report, synthesized)``. ``synthesized`` is False whenever the
    LLM failed or came back empty and ``report`` is the raw digest — the
    caller needs that flag to avoid caching an unsynthesized digest as if it
    were a real regime report.

    Fail-open: any exception returns the raw digest so callers always have
    usable context.
    """
    from tradingagents.agents.utils.agent_utils import get_language_instruction

    prompt = _REGIME_SYNTHESIS_PROMPT.format(
        digest=digest_text,
        unavailable=_DIGEST_UNAVAILABLE,
        language=get_language_instruction(),
    )
    try:
        response = llm.invoke(prompt)
        content = getattr(response, "content", None) or str(response)
        content = content.strip()
        if not content:
            raise ValueError("empty regime synthesis")
        return content, True
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning("Market regime synthesis failed, using raw digest: %s", exc)
        return digest_text, False


def _disk_cache_path(data_cache_dir: str | None, trade_date: str, data_date: str) -> str | None:
    if not data_cache_dir:
        return None
    return os.path.join(data_cache_dir, f"market_regime_{trade_date}_{data_date}.md")


def get_market_regime_report(
    trade_date: str,
    llm: Any | None = None,
    *,
    data_cache_dir: str | None = None,
) -> str:
    """Return the shared market regime report for ``trade_date``.

    The digest is always rebuilt (local SQLite reads only) because it is
    what reveals the *data* date, which the caches key on. The expensive
    part — the LLM synthesis — is skipped when either cache already holds a
    synthesized report for ``(trade_date, data_date)``.

    Neither cache is written unless the synthesis actually succeeded: an
    unsynthesized digest is returned to the caller but never persisted, so
    a single provider outage cannot cost the rest of the day its report.
    """
    digest = build_market_regime_digest(str(trade_date))
    data_date = extract_data_date(digest)
    cache_key = f"{trade_date}|{data_date}"

    if cache_key in _PROCESS_CACHE:
        return _PROCESS_CACHE[cache_key]

    disk_path = _disk_cache_path(data_cache_dir, str(trade_date), data_date)
    if disk_path and os.path.exists(disk_path):
        try:
            with open(disk_path, encoding="utf-8") as fh:
                cached = fh.read().strip()
            if cached:
                _PROCESS_CACHE[cache_key] = cached
                return cached
        except OSError as exc:
            logger.debug("Market regime disk cache unreadable: %s", exc)

    digest_text = render_market_regime_digest(digest)
    if llm is None:
        # Digest-only: cheap to rebuild, and caching it would starve a
        # later call in the same process that does have an LLM.
        return digest_text

    report, synthesized = synthesize_market_regime_report(digest_text, llm)
    if not synthesized:
        return report

    _PROCESS_CACHE[cache_key] = report
    if disk_path:
        try:
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(report)
        except OSError as exc:
            logger.debug("Market regime disk cache unwritable: %s", exc)
    return report


def clear_market_regime_cache() -> None:
    """Drop the process-level cache (tests)."""
    _PROCESS_CACHE.clear()
