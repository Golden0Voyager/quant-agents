"""Deterministic market-data verification snapshot.

The market analyst is an LLM that can confabulate exact numbers — citing a
Bollinger band or a "historically validated bounce" that the underlying data
doesn't support (#830). This module computes a ground-truth snapshot (latest
OHLCV row on or before the analysis date, common indicators, recent closes)
the analyst is told to treat as the source of truth for any exact numeric
claim. Deterministic, no LLM involved.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.akshare_common import safe_float
from tradingagents.dataflows.interface import route_to_vendor_with_source
from tradingagents.dataflows.stockstats_utils import load_ohlcv

# A fixed, common indicator set so the snapshot is the same shape every run.
DEFAULT_SNAPSHOT_INDICATORS: tuple[str, ...] = (
    "close_10_ema", "close_50_sma", "close_200_sma",
    "rsi", "boll", "boll_ub", "boll_lb",
    "macd", "macds", "macdh", "atr",
)


def _verified_rows(symbol: str, curr_date: str, refresh: bool = False) -> pd.DataFrame:
    """OHLCV on or before curr_date, date-sorted. Raises if nothing usable.

    ``load_ohlcv`` already normalizes the Date column and filters out
    look-ahead rows, but we re-apply the cutoff defensively — this is a
    verification path, so it must not trust its input to be pre-filtered.
    """
    data = load_ohlcv(symbol, curr_date, refresh=refresh)
    if data is None or data.empty:
        raise ValueError(f"No OHLCV data available for {symbol}.")

    df = data.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    df = df[df["Date"] <= pd.to_datetime(curr_date)].sort_values("Date")
    if df.empty:
        raise ValueError(f"No OHLCV rows on or before {curr_date} for {symbol}.")
    return df


def _fmt(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Iterable[str] | None = None,
    refresh: bool = False,
) -> str:
    """Render a ground-truth snapshot: latest OHLCV row, indicators, recent closes."""
    # `df` keeps the original capitalized OHLCV columns (Open/High/Low/Close/
    # Volume); stockstats `wrap()` lowercases columns and adds indicator
    # columns, so read raw prices from `df` and indicators from `stock_df`.
    df = _verified_rows(symbol, curr_date, refresh=refresh)
    stock_df = wrap(df.copy())

    selected = tuple(indicators or DEFAULT_SNAPSHOT_INDICATORS)
    indicator_values: dict[str, str] = {}
    for name in selected:
        try:
            stock_df[name]  # triggers stockstats calculation
            indicator_values[name] = _fmt(stock_df.iloc[-1][name])
        except Exception as exc:  # noqa: BLE001 — one bad indicator shouldn't sink the snapshot
            indicator_values[name] = f"N/A ({type(exc).__name__})"

    latest = df.iloc[-1]
    latest_date = _fmt(latest["Date"])
    window = max(1, min(int(look_back_days), 30))
    recent = df.tail(window)

    lines = [
        f"## Verified market data snapshot for {symbol.upper()}",
        "",
        f"- Requested analysis date: {curr_date}",
        f"- Latest trading row used: {latest_date}",
        "- Rows after the requested analysis date are excluded before verification.",
        "",
        "### Latest verified OHLCV row",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    for field in ("Open", "High", "Low", "Close", "Volume"):
        lines.append(f"| {field} | {_fmt(latest.get(field))} |")

    lines += ["", "### Verified technical indicators (latest row)", "",
              "| Indicator | Value |", "|---|---:|"]
    for name, value in indicator_values.items():
        lines.append(f"| {name} | {value} |")

    lines += ["", f"### Recent verified closes (last {len(recent)} rows)", "",
              "| Date | Close |", "|---|---:|"]
    for _, row in recent.iterrows():
        lines.append(f"| {_fmt(row['Date'])} | {_fmt(row.get('Close'))} |")

    lines += [
        "",
        "Use this snapshot as the source of truth for exact OHLCV, price-level, "
        "and indicator-value claims. If another tool output conflicts with it, "
        "flag the discrepancy rather than inventing a reconciled number. Do not "
        "claim historical validation, support/resistance bounces, or exact "
        "percentage moves unless directly supported by tool output with concrete "
        "dates and prices.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verified fundamentals snapshot
# ---------------------------------------------------------------------------


_FUNDAMENTAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "pe_ttm": (r"PE\(TTM\)\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",),
    "pb": (r"PB\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",),
    "ps_ttm": (r"PS\(TTM\)\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",),
    "dividend_yield": (r"股息率\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",),
    "market_cap_billion_cny": (
        r"≈\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*billion\s*CNY",
        r"总市值\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*亿",
    ),
    "roe": (
        r"ROE\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",
        r"净资产收益率\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",
    ),
    "roa": (r"ROA\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",),
    "gross_margin": (
        r"毛利率\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",
        r"销售毛利率\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",
    ),
    "net_margin": (r"净利率\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",),
    "revenue_growth": (
        r"营收同比增长(?:\(YoY\))?\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",
    ),
    "profit_growth": (
        r"净利润同比增长(?:\(YoY\))?\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",
    ),
    "eps_growth": (r"EPS同比增长\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",),
    "peg": (r"PEG\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",),
    "debt_ratio": (r"资产负债率\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*%?",),
    "eps": (
        r"EPS\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)(?!\s*%)",
        r"每股收益\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",
    ),
    "bps": (r"每股净资产\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",),
    "revenue": (r"营业总收入\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",),
    "net_profit": (r"净利润\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)(?!\s*同比)",),
    "operating_cashflow": (
        r"经营活动现金流净额\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",
        r"每股经营现金流量\s*[:：]\s*([+-]?[0-9,]+(?:\.[0-9]+)?)",
    ),
}


def _extract_fundamental_metrics(text: str) -> dict[str, float]:
    """Extract canonical fundamental metrics from vendor markdown text."""
    metrics: dict[str, float] = {}
    for key, patterns in _FUNDAMENTAL_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).replace(",", "")
                value = safe_float(raw)
                if value is not None:
                    # market_cap_billion_cny from the 亿 line needs dividing by 10.
                    if key == "market_cap_billion_cny" and "亿" in match.group(0) and "billion" not in match.group(0):
                        value = value / 10.0
                    metrics[key] = value
                break
    return metrics


def build_verified_fundamentals_snapshot(symbol: str, curr_date: str) -> dict:
    """Build a structured fundamentals snapshot from configured vendors.

    The implementation reuses the existing vendor routing layer (smartmoney_db
    first when configured, then akshare for A-shares, yfinance/alpha_vantage
    for global tickers) instead of re-implementing raw vendor calls.  The
    returned dict is intentionally sparse: only metrics actually returned by
    the vendor are included.
    """
    try:
        routed = route_to_vendor_with_source("get_fundamentals", symbol, curr_date)
    except Exception as exc:  # noqa: BLE001 — a missing fundamentals vendor must not crash the run
        return {"symbol": symbol, "as_of": curr_date, "error": str(exc)}

    text = routed.data
    metadata = {
        "symbol": symbol,
        "as_of": curr_date,
        "source": routed.vendor,
    }

    if not text or (
        "NO_DATA_AVAILABLE" in text
        or "DATA_UNAVAILABLE" in text
        or "No fundamentals available" in text
    ):
        return {**metadata, "error": "fundamentals data unavailable"}

    metrics = _extract_fundamental_metrics(text)
    if not metrics:
        return {**metadata, "error": "no recognized fundamental metrics"}
    return {**metadata, **metrics}


def render_fundamentals_snapshot(snapshot: dict) -> str:
    """Render a fundamentals snapshot dict to the markdown block injected into prompts."""
    symbol = snapshot.get("symbol", "Unknown")
    metric_keys = set(_FUNDAMENTAL_PATTERNS)
    if snapshot.get("error") or not metric_keys.intersection(snapshot):
        reason = snapshot.get("error", "no recognized fundamental metrics")
        return (
            f"Fundamentals snapshot unavailable for {symbol.upper()} as of "
            f"{snapshot.get('as_of', 'unknown')}: {reason}. Exact fundamental "
            "claims are unverified; do not estimate missing values."
        )

    lines = [
        f"## Verified fundamentals snapshot for {symbol.upper()}",
        f"- Analysis date: {snapshot.get('as_of', 'unknown')}",
        f"- Source vendor: {snapshot.get('source') or 'unknown'}",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    display_order = [
        ("pe_ttm", "PE(TTM)"),
        ("pb", "PB"),
        ("ps_ttm", "PS(TTM)"),
        ("dividend_yield", "Dividend Yield (%)"),
        ("market_cap_billion_cny", "Market Cap (billion CNY)"),
        ("roe", "ROE (%)"),
        ("roa", "ROA (%)"),
        ("gross_margin", "Gross Margin (%)"),
        ("net_margin", "Net Margin (%)"),
        ("revenue_growth", "Revenue Growth (%)"),
        ("profit_growth", "Profit Growth (%)"),
        ("eps_growth", "EPS Growth (%)"),
        ("peg", "PEG"),
        ("debt_ratio", "Debt Ratio (%)"),
        ("eps", "EPS"),
        ("bps", "BPS"),
        ("revenue", "Revenue"),
        ("net_profit", "Net Profit"),
        ("operating_cashflow", "Operating Cash Flow"),
    ]
    for key, label in display_order:
        if key in snapshot:
            lines.append(f"| {label} | {_fmt(snapshot[key])} |")

    if len(lines) <= 4:
        lines.append("| (no fundamentals available) | N/A |")

    lines.extend([
        "",
        "Source: fundamentals_snapshot. "
        "Use only the numbers above for any exact fundamental claim. "
        "If a metric is missing, say it is unavailable; do not estimate or recall a value.",
    ])
    return "\n".join(lines)
