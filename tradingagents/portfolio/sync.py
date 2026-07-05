"""Google Sheet synchronization service for portfolio holdings.

Pulls holdings data from a Google Sheet via the gws CLI and transforms it
into a structured Portfolio object for local storage.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from typing import Any

from tradingagents.portfolio.models import Holding, Portfolio, PortfolioMetadata
from tradingagents.portfolio.validators import (
    _parse_number,
    deduplicate_holdings,
    normalize_ticker,
    validate_holding,
)

logger = logging.getLogger(__name__)

# Default column header mapping for the user's Google Sheet
_DEFAULT_COLUMN_MAP = {
    "ticker": "代码",
    "name": "资产名称",
    "avg_cost": "持仓成本",
    "shares": "持仓数量",
    "market_price": "现价",
    "invested_amount": "投入本金 (元)",
    "pnl_pct": "盈亏率",
    "weight": "仓位占比",
    "grid_strategy": "网格策略",
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


class PortfolioSyncService:
    """Syncs portfolio holdings from Google Sheet to local Portfolio object."""

    def __init__(
        self,
        sheet_id: str,
        worksheet: str = "total",
        column_map: dict[str, str] | None = None,
    ):
        self.sheet_id = sheet_id
        self.worksheet = worksheet
        self.column_map = column_map or _DEFAULT_COLUMN_MAP.copy()

    def sync(self) -> Portfolio:
        """Fetch holdings from Google Sheet and return a validated Portfolio."""
        rows = self._fetch_from_gsheet()
        holdings_list = self._transform_rows(rows)
        holdings_dict = deduplicate_holdings(holdings_list)

        # Build summary
        total_invested = sum(
            h.invested_amount or h.shares * h.avg_cost
            for h in holdings_dict.values()
        )
        total_market_value = sum(
            (h.market_price or h.avg_cost) * h.shares
            for h in holdings_dict.values()
        )
        total_pnl = total_market_value - total_invested
        total_pnl_pct = total_pnl / total_invested if total_invested else 0.0

        metadata = PortfolioMetadata(
            updated_at=datetime.now(UTC).isoformat(),
            source_type="google_sheet",
            source_sheet_id=self.sheet_id,
            source_worksheet=self.worksheet,
        )

        summary = {
            "total_holdings": len(holdings_dict),
            "total_invested": round(total_invested, 2),
            "total_market_value": round(total_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 6),
        }

        return Portfolio(
            holdings=holdings_dict,
            metadata=metadata,
            summary=summary,
        )

    def _fetch_from_gsheet(self) -> list[list[str]]:
        """Fetch raw rows from Google Sheet."""
        range_str = f"{self.worksheet}!A1:Z1000"
        return _run_gws_command(self.sheet_id, range_str)

    def _transform_rows(self, rows: list[list[str]]) -> list[Holding]:
        """Transform raw sheet rows into validated Holding objects."""
        if not rows or len(rows) < 2:
            logger.warning("No data found in sheet")
            return []

        headers = [h.strip() for h in rows[0]]
        col_indices = self._resolve_column_indices(headers)

        holdings: list[Holding] = []
        for row in rows[1:]:
            holding = self._transform_single_row(row, col_indices)
            if holding is not None:
                validated = validate_holding(holding)
                if validated:
                    holdings.append(validated)

        logger.info("Transformed %d valid holdings from %d rows", len(holdings), len(rows) - 1)
        return holdings

    def _resolve_column_indices(self, headers: list[str]) -> dict[str, int]:
        """Map field names to column indices based on header row."""
        indices: dict[str, int] = {}
        for field, header_name in self.column_map.items():
            try:
                indices[field] = headers.index(header_name)
            except ValueError:
                # Column not found — skip optional columns, raise for required ones
                if field in ("ticker", "shares", "avg_cost"):
                    raise ValueError(
                        f"Required column '{header_name}' not found in headers: {headers}"
                    ) from None
                logger.debug("Optional column '%s' (%s) not found, skipping", header_name, field)
        return indices

    def _transform_single_row(
        self, row: list[str], indices: dict[str, int]
    ) -> Holding | None:
        """Transform a single row into a Holding, or None if invalid."""
        _mandatory = ("ticker", "shares", "avg_cost")
        try:
            required_max = max(indices[f] for f in _mandatory)
        except KeyError as exc:  # pragma: no cover  -- _resolve_column_indices raises on missing mandatory already
            raise ValueError(f"Missing mandatory column index: {exc}") from exc  # pragma: no cover
        if len(row) <= required_max:
            return None

        raw_ticker = row[indices["ticker"]].strip()
        ticker = normalize_ticker(raw_ticker)
        if ticker is None:
            return None

        # Required fields
        try:
            shares = _parse_number(row[indices["shares"]])
            avg_cost = _parse_number(row[indices["avg_cost"]])
        except (ValueError, IndexError, KeyError):
            logger.warning("Skipping row with invalid required data: %s", row)
            return None

        # Optional fields
        def _get(field: str) -> Any:
            idx = indices.get(field)
            if idx is None or idx >= len(row):
                return None
            val = row[idx].strip()
            if not val or val in ("-", "—", "N/A", "n/a"):
                return None
            return val

        name = _get("name") or ""

        market_price = None
        try:
            raw = _get("market_price")
            if raw:
                market_price = _parse_number(raw)
        except ValueError:
            pass

        invested_amount = None
        try:
            raw = _get("invested_amount")
            if raw:
                invested_amount = _parse_number(raw)
        except ValueError:
            pass

        pnl_pct = None
        try:
            raw = _get("pnl_pct")
            if raw:
                # Handle percentage formats: -10.70% or 8.36%
                clean = raw.replace("%", "").strip()
                pnl_pct = float(clean) / 100
        except ValueError:
            pass

        weight = None
        try:
            raw = _get("weight")
            if raw:
                clean = raw.replace("%", "").strip()
                weight = float(clean) / 100
        except ValueError:
            pass

        grid_strategy = _get("grid_strategy")

        return Holding(
            ticker=ticker,
            name=name,
            shares=shares,
            avg_cost=avg_cost,
            market_price=market_price,
            invested_amount=invested_amount,
            pnl_pct=pnl_pct,
            weight=weight,
            grid_strategy=grid_strategy,
        )
