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
  rows in ``data.abilities[]`` (NOT ``item[]``).
- special-data/hot-stock-list:当日热股 Top30（无个股历史，字段名以实测为准，
  解析侧做多键容错）。
"""
from __future__ import annotations

import logging
from datetime import datetime
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

# alias -> (candidate payload keys, display label). The exact key names inside
# data.abilities[] are taken from the API docs; parsing is tolerant (recursive
# flatten + case-insensitive match) so unknown nesting still resolves.
_FINANCIAL_INDICATORS: dict[str, tuple[tuple[str, ...], str]] = {
    "roe": (("roe", "净资产收益率"), "净资产收益率(ROE)"),
    "roa": (("roa", "总资产收益率", "总资产报酬率"), "总资产收益率(ROA)"),
    "gross_margin": (("gross_margin", "毛利率", "销售毛利率"), "毛利率"),
    "net_margin": (("net_margin", "净利率", "销售净利率"), "净利率"),
    "debt_ratio": (("debt_ratio", "资产负债率"), "资产负债率"),
    "current_ratio": (("current_ratio", "流动比率"), "流动比率"),
    "quick_ratio": (("quick_ratio", "速动比率"), "速动比率"),
    "revenue_growth": (("revenue_growth", "营收增长率", "营业收入增长率"), "营收增长率"),
    "profit_growth": (("profit_growth", "净利润增长率"), "净利润增长率"),
    "ocf_to_profit": (("ocf_to_profit", "经营现金流净额与净利润比"), "经营现金流/净利润"),
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


def _flatten_numeric(node: Any, out: dict[str, float]) -> None:
    """Collect numeric leaves of a nested dict/list into ``out`` (first wins)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                _flatten_numeric(value, out)
            elif isinstance(key, str):
                f = safe_float(value)
                if f is not None:
                    out.setdefault(key.lower(), f)
    elif isinstance(node, list):
        for item in node:
            _flatten_numeric(item, out)


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

    flat: dict[str, float] = {}
    _flatten_numeric(abilities, flat)
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

    parts = [f"整体热度排名: #{rank if rank is not None else 'N/A'}", f"{name}({thscode})"]
    if heat is not None:
        parts.append(f"热度: {heat}")
    if price is not None:
        parts.append(f"最新价: {price}")
    if change_pct is not None:
        parts.append(f"涨跌幅: {change_pct}%")

    return (
        f"同花顺热榜 — {ticker} (source: hithink hot-stock-list, 当日Top{len(rows)})\n"
        + "  |  ".join(parts)
    )
