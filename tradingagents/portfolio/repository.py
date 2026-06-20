"""Portfolio repository: local JSON persistence layer.

Provides read/write/query operations for the portfolio holdings store,
located alongside other quant data in ~/Code/data/quant_data/.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingagents.portfolio.models import Portfolio

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = os.path.expanduser("~/Code/data/quant_data")
_DEFAULT_FILENAME = "tradingagents_portfolio.json"


class PortfolioRepository:
    """JSON-backed repository for portfolio holdings.

    Stores data in a human-readable JSON file alongside other quant data
    (e.g. quant_core.db) for unified management.
    """

    def __init__(self, data_path: str | None = None):
        self._path = Path(data_path) if data_path else Path(_DEFAULT_DATA_DIR) / _DEFAULT_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        """Check if the portfolio file exists."""
        return self._path.exists() and self._path.stat().st_size > 0

    def get_mtime(self) -> datetime | None:
        """Get the last modification time of the portfolio file."""
        if not self.exists():
            return None
        return datetime.fromtimestamp(self._path.stat().st_mtime)

    def load(self) -> Portfolio:
        """Load portfolio from local JSON file.

        Raises:
            FileNotFoundError: if portfolio file does not exist.
            ValueError: if JSON is malformed.
        """
        if not self.exists():
            raise FileNotFoundError(
                f"Portfolio file not found: {self._path}\n"
                f"Run 'uv run tradingagents sync-holdings' first."
            )

        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            # Backup corrupted file and raise
            backup_path = self._path.with_suffix(".json.bak")
            shutil.copy2(self._path, backup_path)
            raise ValueError(
                f"Portfolio JSON is corrupted. Backup saved to {backup_path}. "
                f"Please re-run sync-holdings."
            ) from exc

        return Portfolio.from_dict(data)

    def save(self, portfolio: Portfolio) -> None:
        """Save portfolio to local JSON file atomically."""
        # Ensure parent directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file then replace (atomic)
        temp_path = self._path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(
                    portfolio.to_dict(),
                    f,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            # Atomic replace on POSIX
            os.replace(temp_path, self._path)
            logger.info("Portfolio saved to %s", self._path)
        except Exception:
            # Clean up temp file on failure
            if temp_path.exists():
                temp_path.unlink()
            raise

    def get_holding(self, ticker: str) -> dict[str, Any] | None:
        """Get a specific holding by ticker (convenience method)."""
        try:
            portfolio = self.load()
            holding = portfolio.get_holding(ticker)
            return holding.to_dict() if holding else None
        except FileNotFoundError:
            return None
