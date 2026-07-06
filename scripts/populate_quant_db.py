"""Populate empty quant_core.db tables from akshare (Eastmoney).

Reads the local ``stock_list`` table and fetches missing data via akshare for:

  1. **institutional_holdings** — derived from ``stock_main_stock_holder()``
     (top-10 shareholder list filtered to institution-type holders).
  2. **dragon_tiger** (龙虎榜) — ``stock_lhb_stock_detail_em()`` on recent
     trading dates from ``daily_bars``.
  3. **block_trade** (大宗交易) — **skipped** (no per-stock akshare API).

Usage:
    # Populate every ticker in stock_list that has daily_bars data
    uv run python scripts/populate_quant_db.py --all

    # Populate specific tickers only
    uv run python scripts/populate_quant_db.py 002027.SZ 600519.SS

    # Dry-run — show what would be done without writing
    uv run python scripts/populate_quant_db.py --dry-run --all
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("populate_quant_db")

# ---------------------------------------------------------------------------
# DB path (mirrors smartmoney_vendor.py)
# ---------------------------------------------------------------------------
_DB_PATH = "~/Code/quant_data/quant_core.db"


def _get_connection() -> sqlite3.Connection:
    import os

    path = os.path.expanduser(_DB_PATH)
    if not os.path.exists(path):
        raise FileNotFoundError(f"quant_core.db not found at {path}")
    return sqlite3.connect(path)


def _tscodes_with_daily_bars(conn: sqlite3.Connection) -> list[str]:
    """Return ts_code values that have data in daily_bars."""
    df = pd.read_sql_query("SELECT DISTINCT ts_code FROM daily_bars ORDER BY ts_code", conn)
    return df["ts_code"].tolist()


def _ticker_to_code(ticker: str) -> str:
    """Strip exchange suffix from a ticker."""
    return ticker.split(".")[0]


# ===========================================================================
# Institutional holdings — derive from stock_main_stock_holder
# ===========================================================================


def _populate_institutional_holdings(
    conn: sqlite3.Connection,
    code: str,
    ticker: str,
    dry_run: bool,
) -> bool:
    """Populate institutional_holdings for a single ticker.

    Returns True if data was written (or would be written in dry-run mode).
    """
    from tradingagents.dataflows.akshare_common import _akshare_retry, no_proxy

    # Skip if data already exists
    existing = pd.read_sql_query(
        "SELECT 1 FROM institutional_holdings WHERE ts_code = ? LIMIT 1",
        conn,
        params=(code,),
    )
    if not existing.empty:
        logger.debug("  [skip] institutional_holdings already has data for %s", code)
        return False

    logger.info("  Fetching institutional holdings for %s (%s) ...", ticker, code)

    try:
        with no_proxy():
            df = _akshare_retry(
                lambda: __import__("akshare").stock_main_stock_holder(stock=code),
                max_retries=2,
                base_delay=1.0,
            )
    except Exception as exc:
        logger.warning("  [fail] stock_main_stock_holder for %s: %s", code, exc)
        return False

    if df is None or df.empty:
        logger.info("  [no data] stock_main_stock_holder returned empty for %s", code)
        return False

    # Group by report date (截至日期) and derive institutional metrics
    written = 0
    for report_date, group in df.groupby("截至日期"):
        total_hold_pct = group["持股比例"].sum() if "持股比例" in group.columns else None
        # Count institution-type holders by name pattern
        inst_keywords = "基金|社保|保险|QFII|香港中央结算|中国证券金融|中央汇金|养老"
        inst_mask = group["股东名称"].str.contains(inst_keywords, na=False)
        inst_count = int(inst_mask.sum())
        inst_hold_pct = group.loc[inst_mask, "持股比例"].sum() if inst_count > 0 else None

        if dry_run:
            logger.info(
                "  [dry-run] would insert: %s, %s, institutions=%d, top10_ratio=%s, total_hold_pct=%s",
                code,
                report_date,
                inst_count,
                f"{total_hold_pct:.2f}%" if pd.notna(total_hold_pct) else "N/A",
                f"{inst_hold_pct:.2f}%" if pd.notna(inst_hold_pct) else "N/A",
            )
            written += 1
            continue

        try:
            conn.execute(
                """INSERT OR REPLACE INTO institutional_holdings
                   (ts_code, report_date, institution_count, total_hold_pct,
                    top10_holder_ratio, data_source, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'akshare', datetime('now'))""",
                (
                    code,
                    report_date,
                    inst_count,
                    float(inst_hold_pct) if pd.notna(inst_hold_pct) else None,
                    float(total_hold_pct) if pd.notna(total_hold_pct) else None,
                ),
            )
            conn.commit()
            written += 1
        except Exception as exc:
            logger.warning("  [db-error] %s on %s: %s", code, report_date, exc)

    logger.info("  => %d report periods inserted for %s", written, code)
    return written > 0


# ===========================================================================
# Dragon Tiger — try LHB detail API on recent daily_bars dates
# ===========================================================================


def _populate_dragon_tiger(
    conn: sqlite3.Connection,
    code: str,
    ticker: str,
    dry_run: bool,
) -> bool:
    """Populate dragon_tiger for a single ticker.

    Tries LHB detail API on the 30 most recent trading dates from
    ``daily_bars``.  Most tickers never appear on the LHB, so this
    is best-effort.
    """
    from tradingagents.dataflows.akshare_common import _akshare_retry, no_proxy

    existing = pd.read_sql_query(
        "SELECT 1 FROM dragon_tiger WHERE ts_code = ? LIMIT 1",
        conn,
        params=(code,),
    )
    if not existing.empty:
        return False

    # Get recent trading dates from daily_bars
    dates_df = pd.read_sql_query(
        """SELECT DISTINCT trade_date FROM daily_bars
           WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 30""",
        conn,
        params=(code,),
    )
    if dates_df.empty:
        logger.debug("  [skip] no daily_bars for %s", code)
        return False

    import akshare as ak

    written = 0
    for row in dates_df.itertuples():
        date_str = row.trade_date  # YYYY-MM-DD
        for flag in ("买入", "卖出"):
            try:
                with no_proxy():
                    detail_df = _akshare_retry(
                        lambda f=flag, d=date_str: ak.stock_lhb_stock_detail_em(
                            symbol=code, date=d.replace("-", ""), flag=f
                        ),
                        max_retries=1,
                        base_delay=1.0,
                    )
            except Exception:
                continue  # No LHB data for this date — normal

            if detail_df is None or detail_df.empty:
                continue

            if dry_run:
                logger.info(
                    "  [dry-run] LHB data for %s on %s (%s side, %d rows)",
                    code,
                    date_str,
                    flag,
                    len(detail_df),
                )
                written += 1
                continue

            # Aggregate per-date — the LHB detail has multiple broker entries
            try:
                net_buy = detail_df["净额"].sum() if "净额" in detail_df.columns else None
                buy_amt = detail_df["买入金额"].sum() if "买入金额" in detail_df.columns else None
                sell_amt = detail_df["卖出金额"].sum() if "卖出金额" in detail_df.columns else None
            except Exception:
                net_buy = buy_amt = sell_amt = None

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO dragon_tiger
                       (ts_code, trade_date, net_buy_amount, buy_amount,
                        sell_amount, data_source, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'akshare', datetime('now'))""",
                    (code, date_str, net_buy, buy_amt, sell_amt),
                )
                conn.commit()
                written += 1
            except Exception as exc:
                logger.debug("  [db-error] %s on %s: %s", code, date_str, exc)

            break  # One flag hit is enough per date

    if written:
        logger.info("  => %d LHB records inserted for %s", written, code)
    return written > 0


# ===========================================================================
# Main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Populate empty quant_core.db tables from akshare")
    parser.add_argument("tickers", nargs="*", help="One or more tickers (e.g. 002027.SZ)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all tickers that have daily_bars data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing",
    )
    parser.add_argument(
        "--tables",
        nargs="*",
        default=["institutional_holdings", "dragon_tiger"],
        help="Tables to populate (default: all supported)",
    )
    args = parser.parse_args()

    if not args.tickers and not args.all:
        parser.print_help()
        print("\nError: specify at least one ticker or --all")
        return 1

    conn = _get_connection()
    tables = args.tables

    if args.all:
        all_codes = _tscodes_with_daily_bars(conn)
        logger.info("Processing %d tickers from daily_bars", len(all_codes))
    else:
        all_codes = [_ticker_to_code(t) for t in args.tickers]
        logger.info("Processing %d specified tickers", len(all_codes))

    total_inst = 0
    total_dt = 0

    for code in all_codes:
        ticker = f"{code}.SZ" if code.startswith("0") or code.startswith("3") else f"{code}.SS"
        if code.startswith("8"):
            ticker = f"{code}.BJ"

        if "institutional_holdings" in tables:
            try:
                if _populate_institutional_holdings(conn, code, ticker, args.dry_run):
                    total_inst += 1
            except Exception as exc:
                logger.warning("  [error] institutional_holdings for %s: %s", code, exc)

        if "dragon_tiger" in tables:
            try:
                if _populate_dragon_tiger(conn, code, ticker, args.dry_run):
                    total_dt += 1
            except Exception as exc:
                logger.warning("  [error] dragon_tiger for %s: %s", code, exc)

    logger.info("")
    logger.info("=" * 50)
    logger.info("Summary:")
    logger.info("  Institutional holdings: %d tickers populated", total_inst)
    logger.info("  Dragon tiger:           %d tickers populated", total_dt)
    logger.info("=" * 50)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
