"""Optional Tushare Pro adapters for broader A-share coverage.

Tushare is deliberately imported lazily.  The default TradingAgents install
does not require the package or a token; selecting a Tushare route without
either produces a typed ``VendorNotConfiguredError`` for the router to
record and fall back from.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd

from .akshare_common import to_akshare_symbol
from .errors import NoMarketDataError, VendorNotConfiguredError


def _ts_code(symbol: str) -> str:
    code = to_akshare_symbol(symbol, "bare")
    suffix = symbol.upper().rsplit(".", 1)[-1]
    exchange = {"SS": "SH", "SZ": "SZ", "BJ": "BJ"}.get(suffix)
    if exchange is None:
        raise VendorNotConfiguredError(f"Tushare only supports A-share symbols: {symbol}")
    return f"{code}.{exchange}"


def _date(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y%m%d")


def _pro():
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise VendorNotConfiguredError(
            "TUSHARE_TOKEN is required to use the optional Tushare vendor"
        )
    try:
        import tushare as ts
    except ImportError as exc:
        raise VendorNotConfiguredError(
            "The optional 'tushare' package is not installed"
        ) from exc
    try:
        return ts.pro_api(token)
    except Exception as exc:
        raise VendorNotConfiguredError(f"Tushare client initialization failed: {exc}") from exc


def _rows_or_raise(symbol: str, frame: Any, method: str) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        raise NoMarketDataError(symbol, detail=f"no {method} data returned by Tushare")
    return frame


def _format_rows(title: str, symbol: str, frame: pd.DataFrame, source: str) -> str:
    lines = [
        f"## {symbol.upper()} {title} (source: {source})",
        f"Total records: {len(frame)}",
        "",
    ]
    for row in frame.head(20).to_dict(orient="records"):
        lines.append("- " + "; ".join(f"{key}: {value}" for key, value in row.items()))
    return "\n".join(lines)


def get_company_announcements(symbol: str, start_date: str, end_date: str) -> str:
    pro = _pro()
    frame = pro.anns_d(
        ts_code=_ts_code(symbol),
        start_date=_date(start_date),
        end_date=_date(end_date),
    )
    return _format_rows(
        "Company Announcements",
        symbol,
        _rows_or_raise(symbol, frame, "company announcement"),
        "Tushare anns_d",
    )


def get_earnings_estimates(symbol: str, curr_date: str | None = None) -> str:
    kwargs = {"ts_code": _ts_code(symbol)}
    if curr_date:
        kwargs["ann_date"] = _date(curr_date)
    frame = _rows_or_raise(symbol, _pro().forecast(**kwargs), "earnings forecast")
    return _format_rows("Earnings Forecast", symbol, frame, "Tushare forecast")


def get_margin_trading(symbol: str, curr_date: str | None = None) -> str:
    kwargs = {"ts_code": _ts_code(symbol)}
    if curr_date:
        kwargs["end_date"] = _date(curr_date)
    frame = _rows_or_raise(symbol, _pro().margin(**kwargs), "margin-trading")
    return _format_rows("Margin Trading", symbol, frame, "Tushare margin")


def get_pledge_ratio(symbol: str) -> str:
    frame = _rows_or_raise(symbol, _pro().pledge_stat(ts_code=_ts_code(symbol)), "pledge")
    return _format_rows("Pledge Ratio", symbol, frame, "Tushare pledge_stat")


def get_fund_flow(symbol: str, curr_date: str | None = None) -> str:
    kwargs = {"ts_code": _ts_code(symbol)}
    if curr_date:
        kwargs["trade_date"] = _date(curr_date)
    frame = _rows_or_raise(symbol, _pro().moneyflow(**kwargs), "fund-flow")
    return _format_rows("Fund Flow", symbol, frame, "Tushare moneyflow")
