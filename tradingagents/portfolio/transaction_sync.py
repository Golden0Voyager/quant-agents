"""Google Sheet synchronization service for transaction history.

Pulls trade transaction records from a Google Sheet via the gws CLI
and transforms them into structured Transaction objects.
"""
from __future__ import annotations

import json
import logging
import subprocess

from tradingagents.portfolio.models import Transaction
from tradingagents.portfolio.validators import parse_number

logger = logging.getLogger(__name__)

# Column header mapping for the transaction sheet
_COLUMN_MAP = {
    "date": "交易时间",
    "ticker": "代码",
    "name": "名称",
    "price": "成本单价",
    "action": "动作",
    "shares": "份额变动",
    "fee": "手续费",
    "cash_change": "资金变动",
    "tag": "网格建仓",
}


def _run_gws_command(sheet_id: str, range_str: str) -> list[list[str]]:
    """Run gws CLI to read sheet values.

    The gws CLI must already be authenticated (``gws auth login``).
    """
    cmd = [
        "gws",
        "sheets",
        "spreadsheets",
        "values",
        "get",
        "--params",
        json.dumps({"spreadsheetId": sheet_id, "range": range_str}),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gws CLI not found. Please install Google Workspace CLI: "
            "https://github.com/googleworkspace/cli"
        ) from exc
    except subprocess.CalledProcessError as exc:
        logger.error("gws CLI failed: %s", exc.stderr)
        raise RuntimeError(f"gws CLI failed: {exc.stderr}") from exc

    data = json.loads(result.stdout)
    return data.get("values", [])


def _resolve_column_indices(headers: list[str]) -> dict[str, int]:
    """Map field names to column indices based on header row."""
    indices: dict[str, int] = {}
    for field, header_name in _COLUMN_MAP.items():
        try:
            indices[field] = headers.index(header_name)
        except ValueError:
            logger.warning("Column '%s' not found in headers: %s", header_name, headers)
    return indices


def _transform_row(row: list[str], indices: dict[str, int]) -> Transaction | None:
    """Transform a raw sheet row into a Transaction."""
    max_idx = max(indices.values())
    if len(row) <= max_idx:
        return None

    date_val = row[indices["date"]].strip() if "date" in indices else ""
    ticker_val = row[indices["ticker"]].strip() if "ticker" in indices else ""
    if not date_val or not ticker_val:
        return None

    action_val = row[indices["action"]].strip() if "action" in indices else ""
    shares_val = parse_number(row[indices["shares"]], fallback=0.0) if "shares" in indices else 0.0
    price_val = parse_number(row[indices["price"]], fallback=0.0) if "price" in indices else 0.0
    fee_val = parse_number(row[indices["fee"]], fallback=0.0) if "fee" in indices else None
    cash_change_val = (
        parse_number(row[indices["cash_change"]], fallback=0.0)
        if "cash_change" in indices
        else None
    )
    tag_val = row[indices["tag"]].strip() if "tag" in indices else None
    name_val = row[indices["name"]].strip() if "name" in indices else ""

    # Normalize action to standard values
    action_normalized = action_val
    if "买" in action_val:
        action_normalized = "买入"
    elif "卖" in action_val:
        action_normalized = "卖出"
    elif "分红" in action_val or "红" in action_val:
        action_normalized = "分红"

    return Transaction(
        date=date_val,
        ticker=ticker_val,
        name=name_val,
        price=price_val,
        action=action_normalized,
        shares=shares_val,
        fee=fee_val if fee_val != 0.0 else None,
        cash_change=cash_change_val if cash_change_val != 0.0 else None,
        tag=tag_val,
    )


class TransactionSyncService:
    """Syncs trade transaction history from Google Sheet."""

    def __init__(self, sheet_id: str, worksheet: str = "stock transitions"):
        self.sheet_id = sheet_id
        self.worksheet = worksheet

    def sync(self) -> list[Transaction]:
        """Fetch all transactions from the Google Sheet."""
        range_str = f"{self.worksheet}!A1:L1000"
        rows = _run_gws_command(self.sheet_id, range_str)

        if not rows or len(rows) < 2:
            logger.warning("No transaction data found in sheet")
            return []

        headers = [h.strip() for h in rows[0]]
        indices = _resolve_column_indices(headers)

        transactions: list[Transaction] = []
        for row in rows[1:]:
            tx = _transform_row(row, indices)
            if tx is not None:
                transactions.append(tx)

        logger.info("Synced %d transactions from sheet", len(transactions))
        return transactions
