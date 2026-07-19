"""Diagnose why A-share data dimensions report "unavailable" in reports.

Usage:
    uv run python scripts/diagnose_ashare_data.py [TICKER ...] \
        [--date YYYY-MM-DD] [--sector 白酒]

Background: the market / news / fundamentals analysts already bind tools for
macro (PMI/M2/社融), research reports, earnings estimates, shareholder counts,
sector fund flow and industry valuation, yet reports keep saying these are
"unavailable". The data all flows through ``route_to_vendor`` whose chain is
``smartmoney_db -> akshare -> yfinance``; the local ``quant_core.db`` is an
empty stub for several of these dimensions, so they fall through to akshare and
surface as "unavailable" only when akshare itself fails at runtime.

This script isolates *where* each dimension breaks by running two independent
probes per tool:

1. Routing layer — ``route_to_vendor_with_source(method, ...)`` exercises the
   real fallback chain and reports which vendor ultimately served the request
   (or the exception / no-data sentinel that ended the chain).
2. Raw akshare layer — the underlying ``ak.*`` endpoint under ``no_proxy()``
   isolates a genuine akshare / network / proxy failure from a routing or stub
   issue.

Read-only: no database writes, no config changes. A non-zero exit code means at
least one routing probe returned no data or raised.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any


@contextlib.contextmanager
def _silence():
    """Swallow stdout/stderr (akshare's tqdm bars, vendor logs) around a call so
    the diagnostic table stays clean even when stdout and stderr are merged."""
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        yield


def _code_of(ticker: str) -> str:
    """Return the bare 6-digit code for raw akshare calls (strip .SS/.SZ/.BJ)."""
    return ticker.split(".")[0].strip()


def _classify_routing(data: Any) -> tuple[str, str]:
    """Map a routing payload to (status, detail).

    ``route_to_vendor`` returns a canonical failure sentinel string (rather than
    raising) when every vendor is exhausted; treat that as NO_DATA.
    """
    from tradingagents.dataflows.interface import _is_failure_sentinel

    if isinstance(data, str):
        if not data.strip():
            return "NO_DATA", "empty string"
        if _is_failure_sentinel(data):
            return "NO_DATA", data.strip().splitlines()[0][:120]
        return "OK", f"{len(data)} chars"
    if data is None:
        return "NO_DATA", "None"
    return "OK", type(data).__name__


def _probe_routing(method: str, args: tuple) -> tuple[str, str | None, str]:
    """Run one method through the real vendor fallback chain.

    Returns ``(status, served_vendor, detail)`` where status is OK / NO_DATA /
    ERROR. Never raises — every failure is captured for the report.
    """
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.dataflows.interface import route_to_vendor_with_source

    try:
        with _silence():
            result = route_to_vendor_with_source(method, *args)
    except NoMarketDataError as exc:
        return "NO_DATA", None, f"NoMarketDataError: {str(exc)[:120]}"
    except Exception as exc:  # noqa: BLE001 — diagnostic must survive any vendor error
        return "ERROR", None, f"{type(exc).__name__}: {str(exc)[:120]}"
    status, detail = _classify_routing(result.data)
    return status, result.vendor, detail


def _probe_raw_akshare(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Call a raw ``ak.*`` endpoint under ``no_proxy()``; classify the outcome.

    Returns a short status string. Never raises.
    """
    try:
        from tradingagents.dataflows.akshare_common import no_proxy
    except Exception as exc:  # noqa: BLE001
        return f"ERROR importing no_proxy: {type(exc).__name__}"

    try:
        with no_proxy(), _silence():
            df = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — isolate akshare-lib/network failures
        return f"ERROR {type(exc).__name__}: {str(exc)[:90]}"

    if df is None:
        return "EMPTY (None)"
    rows = getattr(df, "empty", None)
    if rows is True:
        return "EMPTY (0 rows)"
    n = len(df) if hasattr(df, "__len__") else "?"
    return f"OK ({n} rows)"


def _build_probes(ticker: str, date: str, sector: str) -> list[dict]:
    """Assemble the probe table for one ticker. ``raw`` is built lazily so a
    missing/renamed akshare endpoint degrades to a captured ERROR, not a crash."""
    import akshare as ak

    code = _code_of(ticker)
    return [
        {"label": "macro:pmi", "method": "get_macro_indicators",
         "args": ("pmi", date, None), "raw": ak.macro_china_pmi, "raw_args": ()},
        {"label": "macro:m2", "method": "get_macro_indicators",
         "args": ("m2", date, None), "raw": ak.macro_china_money_supply, "raw_args": ()},
        {"label": "macro:social_finance", "method": "get_macro_indicators",
         "args": ("social_finance", date, None), "raw": ak.macro_china_shrzgm, "raw_args": ()},
        {"label": "research_reports", "method": "get_research_reports",
         "args": (ticker,), "raw": ak.stock_research_report_em, "raw_kwargs": {"symbol": code}},
        {"label": "earnings_estimates", "method": "get_earnings_estimates",
         "args": (ticker,), "raw": None},
        {"label": "shareholder_count", "method": "get_shareholder_count",
         "args": (ticker,), "raw": ak.stock_zh_a_gdhs_detail_em, "raw_kwargs": {"symbol": code}},
        {"label": "sector_fund_flow", "method": "get_sector_fund_flow",
         "args": (sector,), "raw": ak.stock_sector_fund_flow_hist, "raw_kwargs": {"symbol": sector}},
        {"label": "industry_valuation", "method": "get_industry_valuation",
         "args": (ticker,), "raw": None},
    ]


def _diagnose_ticker(ticker: str, date: str, sector: str) -> int:
    """Run all probes for one ticker; print a table. Return count of failures."""
    try:
        probes = _build_probes(ticker, date, sector)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not import akshare / build probes: {type(exc).__name__}: {exc}")
        return 1

    header = f"{'dimension':22} {'routing':8} {'vendor':13} {'raw akshare':22} detail"
    print(f"\n=== {ticker} (sector probe: {sector}, date: {date}) ===")
    print(header)
    print("-" * len(header))

    failures = 0
    for p in probes:
        status, vendor, detail = _probe_routing(p["method"], p["args"])
        if status != "OK":
            failures += 1
        if p.get("raw") is not None:
            raw = _probe_raw_akshare(p["raw"], *p.get("raw_args", ()), **p.get("raw_kwargs", {}))
        else:
            raw = "—"
        print(f"{p['label']:22} {status:8} {str(vendor or '—'):13} {raw:22} {detail}")

    return failures


def main() -> int:
    # The terminal exception for each probe is surfaced in the table's detail
    # column, so silence the per-vendor fallback warnings that would otherwise
    # interleave with the table. Also nudge tqdm toward quiet where honoured.
    logging.getLogger("tradingagents").setLevel(logging.ERROR)
    os.environ.setdefault("TQDM_DISABLE", "1")

    parser = argparse.ArgumentParser(
        description="Diagnose A-share data availability across the vendor chain."
    )
    parser.add_argument(
        "tickers", nargs="*", default=["600519.SS", "000975.SZ"],
        help="A-share tickers to probe (default: 600519.SS 000975.SZ)",
    )
    parser.add_argument(
        "--date", default=datetime.now().strftime("%Y-%m-%d"),
        help="Analysis date yyyy-mm-dd (default: today)",
    )
    parser.add_argument(
        "--sector", default="银行",
        help="Sample sector name for the sector-fund-flow probe (default: 银行)",
    )
    args = parser.parse_args()

    total_failures = 0
    for ticker in args.tickers:
        total_failures += _diagnose_ticker(ticker, args.date, args.sector)

    print(
        f"\nSummary: {total_failures} routing probe(s) returned no data or errored "
        f"across {len(args.tickers)} ticker(s)."
    )
    print(
        "Interpretation: routing=NO_DATA + raw akshare=OK  -> stub/routing gap; "
        "routing=NO_DATA + raw akshare=ERROR -> akshare/proxy/endpoint failure."
    )
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
