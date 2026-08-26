"""HiThink (同花顺) vendor — A-share financials and hot rank via Financial-API.

Each function mirrors the signature and output format of its akshare_vendor
counterpart (headers, Chinese labels, ``format_money_cn`` units) so analyst
prompts see an identical shape regardless of which vendor served the data.

All calls go through hithink_common.hithink_get(), which handles the
ApiResponse envelope, 4001/5xxx backoff, and raises VendorNotConfiguredError
when HITHINK_FINANCE_API_KEY is unset — the router then skips to the next
vendor in the chain. HiThink covers A-shares only; non-A-share tickers fail
fast with NoMarketDataError *before* any HTTP call (the router's non-A-share
name filter only skips vendors literally named smartmoney_db/akshare).

Endpoint contract (docs/tonghuashun_api.md):
- financials/*: params thscode + period=annual|quarterly + limit (1-20,
  mutually exclusive with start/end). Response rows in ``data.item[]`` with
  fiscal_year/fiscal_period/period_end_ms metadata; amounts in yuan; ``null``
  means undisclosed (passed through, never zero-filled).
- financials/indicators: params thscode + report={yyyy}-{1|2|3|4}; response
  in ``data.abilities[]`` (NOT ``item[]``), each entry {"ability": group,
  "indicators": [{"index_id", "value"}]} — verified live 2026-08-26.
- special-data/hot-stock-list: 当日热股 Top30, rows {thscode, ticker, name,
  rank, heat, rank_change, rank_trend} — verified live 2026-08-26; no
  per-ticker history (falls back to Eastmoney for non-Top30 names).
- special-data/anomaly-analysis-stock: 个股异动解读, batch param ``thscodes``
  (``thscode`` errors 1001), rows {thscode, stock_name, tag_name,
  keyword_list, analysis_content} — verified live 2026-08-26; empty item =
  no anomaly today (standard NoMarketDataError degradation).
- valuations/snapshot: batch ``thscodes``, rows {pe_ttm, pe_mrq, pb_mrq,
  ps_ttm, pcf_ttm} as plain numbers — verified live 2026-08-26.
- auction/snapshot: batch ``thscodes``, top-level auction_phase/data_status
  plus rows {auction_price, auction_pct, auction_volume (手),
  auction_amount (元), ...} — verified live 2026-08-26.
- auction/short-term-benchmark: no params; ``date`` + ~6 benchmark stocks
  {name, ticker, auction_pct, tags} — verified live 2026-08-26.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from .akshare_common import format_money_cn, is_a_share_ticker, safe_float
from .errors import NoMarketDataError
from .hithink_common import hithink_get, ticker_to_thscode

logger = logging.getLogger(__name__)


def _require_a_share(symbol: str) -> str:
    """Return the HiThink thscode for *symbol*; fast-fail non-A-share tickers."""
    if not is_a_share_ticker(symbol):
        raise NoMarketDataError(
            symbol, detail="hithink vendor covers A-shares only"
        )
    return ticker_to_thscode(symbol)


def _period_label(item: dict[str, Any]) -> str:
    """Render a statement row's report period as YYYY-MM-DD (or fiscal fallback)."""
    ms = safe_float(item.get("period_end_ms"))
    if ms is not None:
        # Local timezone matches the A-share trading calendar (UTC+8), like the
        # akshare REPORT_DATE strings this output format mirrors.
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
    fiscal_year = item.get("fiscal_year")
    fiscal_period = item.get("fiscal_period")
    if fiscal_year and fiscal_period:
        return f"{fiscal_year}-{fiscal_period}"
    return "N/A"


def _format_row_section(row: dict[str, Any], fields) -> str:
    """Format (hithink_key, label) pairs — same rules as akshare_vendor.

    |v| >= 1000 → format_money_cn (亿/万); smaller values (EPS, ratios) keep
    the raw float with 4 decimals. Undisclosed (null) fields are skipped.
    """
    lines = []
    for key, label in fields:
        v = safe_float(row.get(key))
        if v is None:
            continue
        if abs(v) >= 1000:
            lines.append(f"- {label}: {format_money_cn(v)}")
        else:
            lines.append(f"- {label}: {v:.4f}")
    return "\n".join(lines) if lines else "- (no fields available)"


def _fetch_statement_items(
    path: str,
    symbol: str,
    freq: str,
    statement_label: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Fetch a financial-statement endpoint and return (thscode, item list)."""
    thscode = _require_a_share(symbol)
    period = "annual" if str(freq).lower().startswith("ann") else "quarterly"
    data = hithink_get(
        path, {"thscode": thscode, "period": period, "limit": 4}
    )
    items = data.get("item")
    if not isinstance(items, list) or not items:
        raise NoMarketDataError(
            symbol,
            canonical=thscode,
            detail=f"no {statement_label} available for {symbol} via hithink",
        )
    rows = [it for it in items if isinstance(it, dict)]
    if not rows:
        raise NoMarketDataError(
            symbol,
            canonical=thscode,
            detail=f"no {statement_label} available for {symbol} via hithink",
        )
    return thscode, rows


_INCOME_FIELDS = [
    ("operating_income", "营业总收入"),
    ("operating_costs", "营业成本"),
    ("sales_fee", "销售费用"),
    ("manage_fee", "管理费用"),
    ("research_and_development_expenses", "研发费用"),
    ("operating_profit", "营业利润"),
    ("profit_total", "利润总额"),
    ("income_tax_expense", "所得税费用"),
    ("net_profit", "净利润"),
    ("parent_holder_net_profit", "归母净利润"),
    ("basic_eps", "基本每股收益"),
]

_BALANCE_FIELDS = [
    ("assets_total", "总资产"),
    ("total_current_assets", "流动资产"),
    ("cash", "货币资金"),
    ("accounts_receivable", "应收账款"),
    ("total_debt", "总负债"),
    ("holder_equity_total", "股东权益合计"),
]

_CASHFLOW_FIELDS = [
    ("act_cash_flow_net", "经营活动现金流净额"),
    ("invest_cash_flow_net", "投资活动现金流净额"),
    ("financing_cash_flow_net", "筹资活动现金流净额"),
    ("pay_fixed_assets_etc_cash", "购建固定资产等支付现金"),
    ("pay_dividends_profits_interest_cash", "分配股利利润或偿付利息支付现金"),
    ("cash_equivalents_net_addition", "现金及等价物净增加额"),
]


def get_income_statement(
    symbol: Annotated[str, "A-share ticker"],
    freq: Annotated[str, "annual/quarterly"] = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Fetch A-share income statement (latest report period) via HiThink."""
    _, rows = _fetch_statement_items(
        "/api/a-share/financials/income-statements", symbol, freq, "income statement"
    )
    latest = rows[0]
    period = _period_label(latest)
    header = (
        f"# Income Statement for {symbol.upper()} ({period})\n"
        f"# Source: hithink (同花顺 Financial-API 利润表)\n"
        f"# Currency: {latest.get('currency') or 'CNY'} (元)\n\n"
    )
    return header + _format_row_section(latest, _INCOME_FIELDS)


def get_balance_sheet(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Fetch A-share balance sheet (latest report period) via HiThink."""
    _, rows = _fetch_statement_items(
        "/api/a-share/financials/balance-sheets", symbol, freq, "balance sheet"
    )
    latest = rows[0]
    period = _period_label(latest)
    header = (
        f"# Balance Sheet for {symbol.upper()} ({period})\n"
        f"# Source: hithink (同花顺 Financial-API 资产负债表)\n"
        f"# Currency: {latest.get('currency') or 'CNY'} (元)\n\n"
    )
    return header + _format_row_section(latest, _BALANCE_FIELDS)


def get_cashflow(
    symbol: Annotated[str, "A-share ticker"],
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Fetch A-share cash flow statement (latest report period) via HiThink."""
    _, rows = _fetch_statement_items(
        "/api/a-share/financials/cash-flow-statements", symbol, freq, "cash flow statement"
    )
    latest = rows[0]
    period = _period_label(latest)
    header = (
        f"# Cash Flow Statement for {symbol.upper()} ({period})\n"
        f"# Source: hithink (同花顺 Financial-API 现金流量表)\n"
        f"# Currency: {latest.get('currency') or 'CNY'} (元)\n\n"
    )
    return header + _format_row_section(latest, _CASHFLOW_FIELDS)


# ---------------------------------------------------------------------------
# Financial indicators (financials/indicators — five categories, 23 ratios)
# ---------------------------------------------------------------------------

# alias -> (candidate payload index_ids, display label). index_ids verified
# against the live API (2026-08-26, 600519.SH): abilities[] groups by ability
# (growth/profitability/solvency/operation/cash-flow), each holding
# indicators[] = {"index_id": ..., "value": "..."}; values are stringified
# percentages (e.g. "89.7592" = 89.76%), None means undisclosed.
_FINANCIAL_INDICATORS: dict[str, tuple[tuple[str, ...], str]] = {
    "roe": (("index_weighted_avg_roe", "index_deduct_weighted_avg_roe", "加权roe", "净资产收益率"), "净资产收益率(ROE,加权)"),
    "roa": (("total_assets_net_ratio", "总资产净利率", "总资产收益率"), "总资产净利率(ROA)"),
    "gross_margin": (("sale_gross_margin", "毛利率", "销售毛利率"), "毛利率"),
    "net_margin": (("sale_net_interest_ratio", "净利率", "销售净利率"), "净利率"),
    "debt_ratio": (("assets_debt_ratio", "资产负债率"), "资产负债率"),
    "current_ratio": (("current_ratio", "流动比率"), "流动比率"),
    "quick_ratio": (("quick_ratio", "速动比率"), "速动比率"),
    "revenue_growth": (("calculate_operating_income_yoy_growth_ratio", "营收增长率", "营业收入增长率"), "营收增长率(同比)"),
    "profit_growth": (("calculate_parent_holder_net_profit_yoy_growth_ratio", "净利润增长率"), "归母净利润增长率(同比)"),
    "ocf_to_profit": (("net_profit_cash_content", "净现比", "经营现金流净额与净利润比"), "净现比(经营现金流/净利润)"),
}

def _build_alias_lookup() -> dict[str, tuple[tuple[str, ...], str]]:
    lookup: dict[str, tuple[tuple[str, ...], str]] = {}
    for canonical, entry in _FINANCIAL_INDICATORS.items():
        lookup[canonical] = entry
        for key in entry[0]:
            lookup.setdefault(key.lower(), entry)
    return lookup


_ALIAS_LOOKUP = _build_alias_lookup()


def _report_period_for(curr_date: str | None) -> str:
    """Latest reliably-published report period as {yyyy}-{quarter}.

    Publication deadlines: Q1 by Apr 30, Q2 by Aug 31, Q3 by Oct 31; the
    annual (Q4) report is only guaranteed after Apr 30, so Jan-Apr maps back
    to the previous year's Q3 (same lag logic as akshare's yjbb picker).
    """
    dt = None
    if curr_date:
        try:
            dt = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d")
        except ValueError:
            dt = None
    if dt is None:
        dt = datetime.now()
    y, m = dt.year, dt.month
    if m >= 11:
        return f"{y}-3"
    if m >= 8:
        return f"{y}-2"
    if m >= 5:
        return f"{y}-1"
    return f"{y - 1}-3"


def _index_values(abilities: list[Any]) -> dict[str, float]:
    """Map each indicator's ``index_id`` to its numeric value (first wins).

    Real payload shape: ``abilities[] = {"ability": "growth", "indicators":
    [{"index_id": "...", "value": "6.538"}, ...]}`` — values arrive as strings
    and None means undisclosed (skipped). Keying by ``index_id`` (not by the
    generic ``value`` leaf key, which would collide across indicators).
    """
    out: dict[str, float] = {}
    for group in abilities:
        if not isinstance(group, dict):
            continue
        for ind in group.get("indicators") or []:
            if not isinstance(ind, dict):
                continue
            key = ind.get("index_id")
            val = safe_float(ind.get("value"))
            if isinstance(key, str) and val is not None:
                out.setdefault(key.lower(), val)
    return out


def get_indicators(
    symbol: Annotated[str, "A-share ticker"],
    indicator: Annotated[str, "financial indicator name e.g. roe, gross_margin"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"],
    look_back_days: Annotated[int, "Unused; HiThink indicators are per report period"] = 30,
) -> str:
    """Fetch one A-share *financial* indicator (ROE/毛利率/…) via HiThink.

    Registered under the routed ``get_indicators`` method, which primarily
    serves stockstats technical indicators computed from K-line data. HiThink
    has no K-line-derived indicators, so any name outside the financial-alias
    map raises NoMarketDataError *without an HTTP call* and the router falls
    through to akshare — existing technical-indicator behavior is unchanged.
    """
    thscode = _require_a_share(symbol)
    name = str(indicator).strip().lower()
    entry = _ALIAS_LOOKUP.get(name)
    if entry is None:
        raise NoMarketDataError(
            symbol,
            canonical=thscode,
            detail=(
                f"'{indicator}' is not a hithink financial indicator "
                "(technical indicators are computed from K-line by other vendors)"
            ),
        )
    candidate_keys, label = entry

    report = _report_period_for(curr_date)
    data = hithink_get(
        "/api/a-share/financials/indicators",
        {"thscode": thscode, "report": report},
    )
    abilities = data.get("abilities")
    if not isinstance(abilities, list) or not abilities:
        raise NoMarketDataError(
            symbol,
            canonical=thscode,
            detail=f"no financial indicators for report={report} via hithink",
        )

    flat = _index_values(abilities)
    value = next(
        (flat[key] for key in (k.lower() for k in candidate_keys) if key in flat),
        None,
    )
    if value is None:
        raise NoMarketDataError(
            symbol,
            canonical=thscode,
            detail=(
                f"indicator '{indicator}' not found in hithink abilities payload "
                f"for report={report}"
            ),
        )

    return (
        f"## {name} values for {symbol.upper()} "
        f"(report period {report}, source: hithink / 同花顺财务指标)\n\n"
        f"{report}: {label} = {value}"
    )


# ---------------------------------------------------------------------------
# Hot rank (special-data/hot-stock-list — 当日热股 Top30)
# ---------------------------------------------------------------------------

_TREND_LABELS = {"up": "上升", "down": "下降", "flat": "持平"}


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value among *keys* (case-insensitive)."""
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


def get_hot_rank(ticker: str, limit: int = 20) -> str:
    """HiThink hot-stock-list replacement for the Eastmoney hot rank.

    The endpoint returns the market-wide Top30 hot stocks of the day (there is
    no per-ticker history here), so this answers the "is it hot right now, and
    where does it rank" half of fetch_eastmoney_hot_rank. When the ticker is
    not in the Top30 we raise NoMarketDataError so the router falls back to
    the Eastmoney implementation, which searches a wider top-100 table and
    adds rank history — the richer answer for non-hot names.
    """
    thscode = _require_a_share(ticker)
    bare_code = ticker.strip().upper().split(".")[0]

    data = hithink_get("/api/a-share/special-data/hot-stock-list")
    rows = data.get("item") or data.get("list") or data.get("items")
    if not isinstance(rows, list) or not rows:
        raise NoMarketDataError(
            ticker,
            canonical=thscode,
            detail="empty hot-stock-list payload via hithink",
        )

    match: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(
            _first_present(row, "thscode", "code", "ticker", "代码") or ""
        ).upper()
        if thscode in code or bare_code in code:
            match = row
            break

    if match is None:
        raise NoMarketDataError(
            ticker,
            canonical=thscode,
            detail="not in hithink Top30 hot list",
        )

    rank = _first_present(match, "rank", "排名", "hot_rank")
    name = _first_present(match, "name", "股票名称", "名称") or "N/A"
    heat = _first_present(match, "heat", "热度", "hot_value", "popularity")
    price = _first_present(match, "price", "最新价", "last_price")
    change_pct = _first_present(match, "change_pct", "涨跌幅", "pct_change")
    rank_change = _first_present(match, "rank_change", "排名变动")
    trend = _TREND_LABELS.get(
        str(_first_present(match, "rank_trend") or "").lower()
    )

    parts = [f"整体热度排名: #{rank if rank is not None else 'N/A'}", f"{name}({thscode})"]
    if heat is not None:
        parts.append(f"热度: {heat}")
    if rank_change is not None:
        parts.append(f"排名变动: {rank_change}" + (f"（{trend}）" if trend else ""))
    elif trend:
        parts.append(f"排名趋势: {trend}")
    if price is not None:
        parts.append(f"最新价: {price}")
    if change_pct is not None:
        parts.append(f"涨跌幅: {change_pct}%")

    return (
        f"同花顺热榜 — {ticker} (source: hithink hot-stock-list, 当日Top{len(rows)})\n"
        + "  |  ".join(parts)
    )


# ---------------------------------------------------------------------------
# Dragon tiger (special-data/dragon-tiger-list — 按交易日的全市场龙虎榜)
# ---------------------------------------------------------------------------

# Endpoint contracts verified live 2026-08-26:
# - dragon-tiger-list: params board_type=all|org|hot_money, date=yyyy-MM-dd
#   (MUST be a trading day — explicit non-trading days return code=1002;
#   omitting returns the latest available trading day). Response data:
#   trade_date/count/stock_count + stock_items[] = {thscode, ticker, name,
#   concept_list, change (小数), net_value/net_rate (元/小数), hot_rank,
#   buy_value, sell_value, limit_reason, range_days (1=当日榜, 3=3日榜),
#   org_net_value, hot_money_net_value}. Same stock may appear twice
#   (当日榜 + 3日榜). Amounts in yuan.
# - limit-up-pool / limit-down-pool: param date_ms (Asia/Shanghai 00:00 毫秒戳
#   — 注意 overview 文档里的 trade_date=YYYYMMDD 会被静默忽略并返回当日数据，
#   实测确认必须用 date_ms); page/size(1..200)/sort_field/sort_dir; 非交易日
#   返回 total=0 空池（不报错）。Response data: pagination{total,pages,size,
#   page} + item[]。
# - calendar/trading-days: data.item[] = {"date_ms", "date": "YYYYMMDD"},
#   近一年序列，升序。

_SH_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai


def _snap_to_trade_date(curr_date: str) -> str:
    """Snap *curr_date* to the latest A-share trading day ≤ it (yyyy-MM-dd).

    dragon-tiger-list rejects explicit non-trading days with code=1002 (no
    auto-fallback), so snap via the trading-days calendar (~1-year window).
    Dates older than the window are passed through — the endpoint then answers
    with code=1003 and the router falls back to the next vendor.
    """
    digits = "".join(ch for ch in str(curr_date) if ch.isdigit())[:8]
    data = hithink_get("/api/a-share/calendar/trading-days")
    days = sorted(
        str(it["date"])
        for it in (data.get("item") or [])
        if isinstance(it, dict) and it.get("date")
    )
    eligible = [d for d in days if d <= digits]
    chosen = eligible[-1] if eligible else digits
    return f"{chosen[:4]}-{chosen[4:6]}-{chosen[6:8]}"


def get_dragon_tiger(
    symbol: str,
    curr_date: str | None = None,
) -> str:
    """Fetch A-share dragon-tiger-board (龙虎榜) data via HiThink.

    The endpoint is a market-wide per-day board, so this filters the day
    (snapped to the latest trading day ≤ curr_date) down to *symbol*. A stock
    not on the board that day raises NoMarketDataError and the router falls
    back to akshare, which discovers the stock's most recent appearance ever —
    the richer answer for names that have not been listed lately.
    """
    thscode = _require_a_share(symbol)
    params: dict[str, Any] = {"board_type": "all"}
    if curr_date:
        params["date"] = _snap_to_trade_date(curr_date)

    data = hithink_get("/api/a-share/special-data/dragon-tiger-list", params)
    items = data.get("stock_items")
    rows = [
        r
        for r in (items if isinstance(items, list) else [])
        if isinstance(r, dict) and str(r.get("thscode", "")).upper() == thscode
    ]
    trade_date = data.get("trade_date") or params.get("date") or "N/A"
    if not rows:
        raise NoMarketDataError(
            symbol,
            canonical=thscode,
            detail=f"not on dragon-tiger board on {trade_date} via hithink",
        )

    lines = [
        f"## {symbol.upper()} Dragon Tiger Board (龙虎榜) (source: hithink / 同花顺)",
        f"Date: {trade_date}",
        f"Total records: {len(rows)} entries",
        "",
    ]
    for r in rows:
        period = "当日榜" if r.get("range_days") == 1 else f"{r.get('range_days')}日榜"
        lines.append(f"**{period}** ({r.get('name', 'N/A')}):")
        change = safe_float(r.get("change"))
        if change is not None:
            lines.append(f"- 涨跌幅: {change * 100:+.2f}%")
        net_value = safe_float(r.get("net_value"))
        net_rate = safe_float(r.get("net_rate"))
        if net_value is not None:
            rate = f" (占成交 {net_rate * 100:.2f}%)" if net_rate is not None else ""
            lines.append(f"- 龙虎榜净买入: {format_money_cn(net_value)}{rate}")
        buy = safe_float(r.get("buy_value"))
        sell = safe_float(r.get("sell_value"))
        if buy is not None or sell is not None:
            lines.append(
                f"- 买入/卖出: {format_money_cn(buy)} / {format_money_cn(sell)}"
            )
        org_net = safe_float(r.get("org_net_value"))
        if org_net is not None:
            lines.append(f"- 机构净买入: {format_money_cn(org_net)}")
        hm_net = safe_float(r.get("hot_money_net_value"))
        if hm_net is not None:
            lines.append(f"- 游资净买入: {format_money_cn(hm_net)}")
        if r.get("hot_rank") is not None:
            lines.append(f"- 同花顺人气排名: #{r['hot_rank']}")
        if r.get("limit_reason"):
            lines.append(f"- 上榜原因: {r['limit_reason']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Limit-up / limit-down pools (special-data/limit-up-pool + limit-down-pool)
# ---------------------------------------------------------------------------


def _date_ms_for(trade_date: str) -> int:
    """Convert a trade date (YYYY-MM-DD or YYYYMMDD) to Shanghai-midnight ms."""
    digits = "".join(ch for ch in str(trade_date) if ch.isdigit())
    if len(digits) < 8:
        raise NoMarketDataError(
            str(trade_date), detail=f"unparseable trade_date {trade_date!r}"
        )
    dt = datetime.strptime(digits[:8], "%Y%m%d").replace(tzinfo=_SH_TZ)
    return int(dt.timestamp() * 1000)


def _dashed_date(trade_date: str) -> str:
    digits = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else str(trade_date)


def _fetch_pool(path: str, date_ms: int, sort_field: str) -> list[dict[str, Any]]:
    """Fetch all pages of a limit pool (size=200, capped at 10 pages)."""
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        data = hithink_get(
            path,
            {
                "date_ms": date_ms,
                "page": page,
                "size": 200,
                "sort_field": sort_field,
                "sort_dir": "desc",
            },
        )
        items = data.get("item")
        if not isinstance(items, list) or not items:
            break
        rows.extend(it for it in items if isinstance(it, dict))
        pagination = data.get("pagination") or {}
        pages = safe_float(pagination.get("pages")) or 1
        if page >= int(pages) or page >= 10:
            break
        page += 1
    return rows


def get_limit_up_down(trade_date: str) -> str:
    """Fetch market-wide limit-up/limit-down stats for a trading date via HiThink.

    Output mirrors smartmoney_vendor.get_limit_up_down: counts + up/down ratio
    + 连板分布 + sample stocks. The industry-distribution section is omitted —
    the HiThink pool payload carries no industry field (涨停原因 is shown in
    the samples instead).
    """
    date_ms = _date_ms_for(trade_date)
    up_rows = _fetch_pool(
        "/api/a-share/special-data/limit-up-pool", date_ms, "continue_day_cnt"
    )
    down_rows = _fetch_pool(
        "/api/a-share/special-data/limit-down-pool", date_ms, "last_limit_time"
    )

    if not up_rows and not down_rows:
        raise NoMarketDataError(
            trade_date,
            trade_date,
            f"no limit-up/limit-down data for {trade_date} via hithink",
        )

    lines = [
        f"## A-Share Limit-Up / Limit-Down Stats for {_dashed_date(trade_date)} "
        f"(source: hithink / 同花顺)",
        "",
        f"- **Limit-up stocks (涨停)**: {len(up_rows)}",
        f"- **Limit-down stocks (跌停)**: {len(down_rows)}",
        f"- **Up/Down ratio**: {len(up_rows)}:{len(down_rows)}",
    ]

    # 连板分布 (board-count distribution) — limit-up only
    boards: dict[int, list[str]] = {}
    for r in up_rows:
        cnt = safe_float(r.get("continue_day_cnt"))
        bc = int(cnt) if cnt is not None and cnt >= 1 else 1
        boards.setdefault(bc, []).append(str(r.get("name") or "N/A"))
    if boards:
        lines.append("")
        lines.append("**连板分布 (Board-count distribution):**")
        for bc in sorted(boards, reverse=True):
            names = ", ".join(boards[bc])[:80]
            label = f"{bc}连板" if bc > 1 else "首板"
            lines.append(f"- {label}: {len(boards[bc])} 只 ({names})")

    # Sample limit-up stocks (top 10 by board count, then seal money)
    if up_rows:
        sample = sorted(
            up_rows,
            key=lambda r: (
                -(safe_float(r.get("continue_day_cnt")) or 1),
                -(safe_float(r.get("seal_money")) or 0),
            ),
        )[:10]
        lines.append("")
        lines.append("**Sample limit-up stocks:**")
        for r in sample:
            cnt = safe_float(r.get("continue_day_cnt")) or 1
            bc_str = f" ({int(cnt)}连板)" if cnt > 1 else ""
            reason = f" [{r['limit_up_reason']}]" if r.get("limit_up_reason") else ""
            lines.append(f"- {r.get('name', 'N/A')}{bc_str}{reason}")

    # Sample limit-down stocks
    if down_rows:
        lines.append("")
        lines.append("**Sample limit-down stocks:**")
        for r in down_rows[:10]:
            pct = safe_float(r.get("price_change_ratio_pct"))
            pct_str = f" ({pct:.2f}%)" if pct is not None else ""
            lines.append(f"- {r.get('name', 'N/A')}{pct_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Anomaly reasons (special-data/anomaly-analysis-stock — 个股异动解读)
# ---------------------------------------------------------------------------

_ANOMALY_MAX_CHARS = 800
_ANOMALY_DISCLAIMER_MARKER = "（免责声明："


def _strip_anomaly_disclaimer(text: str) -> str:
    """Drop the boilerplate AI disclaimer appended to every analysis_content."""
    idx = text.find(_ANOMALY_DISCLAIMER_MARKER)
    if idx > 0:
        text = text[:idx]
    return text.strip()


def _match_thscode_row(
    rows: Any, thscode: str, ticker: str, label: str
) -> dict[str, Any]:
    """Pick the row matching thscode out of a batch ``item[]`` payload."""
    if not isinstance(rows, list) or not rows:
        raise NoMarketDataError(
            ticker, canonical=thscode, detail=f"no {label} record today via hithink"
        )
    match = next(
        (
            r
            for r in rows
            if isinstance(r, dict) and str(r.get("thscode", "")).upper() == thscode
        ),
        None,
    )
    if match is None:
        raise NoMarketDataError(
            ticker, canonical=thscode, detail=f"{label} payload missing ticker row"
        )
    return match


def get_anomaly_reason(ticker: str) -> str:
    """Official per-stock anomaly explanation (为什么动) via HiThink.

    Verified live 2026-08-26: batch param ``thscodes`` (a single code still
    uses it; ``thscode`` errors with code=1001). item[] rows carry thscode /
    stock_name / tag_name / keyword_list / analysis_content (multi-paragraph,
    always ending with a fixed AI disclaimer that we strip; overlong content
    is truncated to keep prompts bounded). A stock with no anomaly today
    returns an empty item list -> NoMarketDataError, i.e. the standard
    DATA_UNAVAILABLE degradation (not a hard failure).
    """
    thscode = _require_a_share(ticker)
    data = hithink_get(
        "/api/a-share/special-data/anomaly-analysis-stock",
        {"thscodes": thscode},
    )
    match = _match_thscode_row(data.get("item"), thscode, ticker, "anomaly")

    content = _strip_anomaly_disclaimer(str(match.get("analysis_content") or ""))
    if len(content) > _ANOMALY_MAX_CHARS:
        content = content[:_ANOMALY_MAX_CHARS].rstrip() + " …"
    keywords = match.get("keyword_list")
    kw_text = "、".join(str(k) for k in keywords) if isinstance(keywords, list) else ""

    parts = [
        f"同花顺异动解读 — {ticker} (source: hithink anomaly-analysis-stock, 当日)",
        f"标签: {match.get('tag_name') or 'N/A'}"
        + (f"  |  关键词: {kw_text}" if kw_text else ""),
    ]
    if content:
        parts.append(content)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Valuation snapshot (valuations/snapshot — 官方估值快照)
# ---------------------------------------------------------------------------

_VALUATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("pe_ttm", "PE(TTM)"),
    ("pe_mrq", "PE(MRQ)"),
    ("pb_mrq", "PB(MRQ)"),
    ("ps_ttm", "PS(TTM)"),
    ("pcf_ttm", "PCF(TTM)"),
)


def fetch_valuation_metrics(ticker: str) -> dict[str, float]:
    """Structured official valuation multiples (PE/PB/PS/PCF) for a ticker.

    Verified live 2026-08-26: batch param ``thscodes``; item[] rows carry
    pe_ttm/pe_mrq/pb_mrq/ps_ttm/pcf_ttm as plain numbers. Returns a sparse
    dict (only multiples the API actually returned); raises NoMarketDataError
    for non-A-shares (before any HTTP call), a missing row, or a row with no
    usable multiples. Used by market_data_validator as the cross-validation
    anchor and by get_valuation_snapshot for prompt rendering.
    """
    thscode = _require_a_share(ticker)
    data = hithink_get("/api/a-share/valuations/snapshot", {"thscodes": thscode})
    row = _match_thscode_row(data.get("item"), thscode, ticker, "valuation snapshot")
    metrics: dict[str, float] = {}
    for key, _label in _VALUATION_FIELDS:
        value = safe_float(row.get(key))
        if value is not None:
            metrics[key] = value
    if not metrics:
        raise NoMarketDataError(
            ticker, canonical=thscode, detail="valuation snapshot has no usable multiples"
        )
    return metrics


def get_valuation_snapshot(ticker: str) -> str:
    """Prompt-ready rendering of the official valuation snapshot."""
    metrics = fetch_valuation_metrics(ticker)
    lines = [
        f"同花顺估值快照 — {ticker} (source: hithink valuations/snapshot, 最新)",
        "| 指标 | 值 |",
        "|---|---:|",
    ]
    for key, label in _VALUATION_FIELDS:
        if key in metrics:
            lines.append(f"| {label} | {metrics[key]:.2f} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auction data (auction/snapshot + short-term-benchmark — 集合竞价/短线风向标)
# ---------------------------------------------------------------------------


def get_auction_snapshot(ticker: str) -> str:
    """Call-auction snapshot for a ticker (盘前集合竞价强弱).

    Verified live 2026-08-26: batch param ``thscodes``; top-level
    ``auction_phase``/``data_status`` tell whether the auction is live
    (pre-open) or already closed/final; item[] rows carry auction_price /
    auction_pct / auction_volume (手) / auction_amount (元) /
    auction_unmatched / auction_turnover_pct / auction_yesterday_ratio_pct /
    auction_volume_ratio / pre_close_price / open_price. After the close the
    block describes that morning's auction — still useful context for the
    day's open strength.
    """
    thscode = _require_a_share(ticker)
    data = hithink_get("/api/a-share/auction/snapshot", {"thscodes": thscode})
    row = _match_thscode_row(data.get("item"), thscode, ticker, "auction snapshot")

    def _pct(key: str) -> str:
        value = safe_float(row.get(key))
        return f"{value:+.2f}%" if value is not None else "N/A"

    def _num(key: str) -> str:
        value = safe_float(row.get(key))
        return f"{value:.2f}" if value is not None else "N/A"

    amount = safe_float(row.get("auction_amount"))
    volume = safe_float(row.get("auction_volume"))
    unmatched = safe_float(row.get("auction_unmatched"))
    lines = [
        f"同花顺集合竞价 — {ticker} (source: hithink auction/snapshot, "
        f"phase={data.get('auction_phase') or 'unknown'}/"
        f"{data.get('data_status') or 'unknown'})",
        f"- 竞价价格: {_num('auction_price')} ({_pct('auction_pct')})"
        f"  |  昨收: {_num('pre_close_price')}  |  今开: {_num('open_price')}",
        f"- 竞价量: {f'{volume:.0f} 手' if volume is not None else 'N/A'}"
        f"  |  竞价额: {format_money_cn(amount) if amount is not None else 'N/A'}"
        f"  |  未匹配量: {f'{unmatched:.0f} 手' if unmatched is not None else 'N/A'}",
        f"- 竞价换手: {_pct('auction_turnover_pct')}"
        f"  |  竞价量/昨日竞价: {_pct('auction_yesterday_ratio_pct')}"
        f"  |  量比: {_num('auction_volume_ratio')}",
    ]
    return "\n".join(lines)


def get_short_term_benchmark() -> str:
    """Market-wide short-term sentiment gauge (短线风向标) via HiThink.

    Verified live 2026-08-26: no params (``/api/a-share/auction/short-term-
    benchmark``); returns ``date`` plus ~6 benchmark stocks with their
    call-auction pct change and sector tags. Empty item -> NoMarketDataError.
    """
    data = hithink_get("/api/a-share/auction/short-term-benchmark", {})
    rows = data.get("item")
    if not isinstance(rows, list) or not rows:
        raise NoMarketDataError(
            "MARKET", detail="empty short-term-benchmark list via hithink"
        )
    lines = [
        "同花顺短线风向标 (source: hithink auction/short-term-benchmark, "
        f"date={data.get('date') or 'unknown'})",
        "| 标的 | 竞价涨跌幅 | 标签 |",
        "|---|---:|---|",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        pct = safe_float(row.get("auction_pct"))
        pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
        tags = row.get("tags")
        tag_str = "/".join(str(t) for t in tags) if isinstance(tags, list) else ""
        lines.append(
            f"| {row.get('name', 'N/A')} ({row.get('ticker', '')})"
            f" | {pct_str} | {tag_str} |"
        )
    return "\n".join(lines)
