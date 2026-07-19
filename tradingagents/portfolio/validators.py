"""Portfolio data validation and normalization utilities."""
from __future__ import annotations

import logging
from typing import Any

from tradingagents.portfolio.models import Holding

logger = logging.getLogger(__name__)


def parse_number(value: Any, fallback: float | None = None) -> float:
    """Strip thousand separators/currency symbols and parse as float.

    Args:
        value: The value to parse (int, float, or string).
        fallback: If provided, return this value when parsing fails or the
            input is empty/invalid. If None (default), a ValueError is raised.

    Returns:
        The parsed float value.

    Raises:
        ValueError: If the value cannot be parsed and no fallback is provided.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not value or not isinstance(value, str):
        if fallback is not None:
            return fallback
        raise ValueError(f"Cannot parse number from {type(value)}")
    cleaned = (
        value.strip()
        .replace(",", "")
        .replace("，", "")
        .replace("、", "")
        .replace("$", "")
        .replace("¥", "")
    )
    if not cleaned:
        if fallback is not None:
            return fallback
        raise ValueError("Cannot parse empty string as number")
    try:
        return float(cleaned)
    except (ValueError, TypeError) as exc:
        if fallback is not None:
            return fallback
        raise ValueError(f"Cannot parse number from {value!r}") from exc


# Backward-compatible alias for existing callers/tests.
_parse_number = parse_number


def normalize_ticker(raw: str) -> str | None:
    """Convert bare A-share numeric codes to exchange-qualified format.

    - 6xxxxx  -> 6xxxxx.SS (Shanghai)
    - 0xxxxx  -> 0xxxxx.SZ (Shenzhen)
    - 3xxxxx  -> 3xxxxx.SZ (Shenzhen ChiNext)
    - Others (e.g. HK1810, ETF names) are returned as-is.
    - Empty cells, dashes, and Chinese headers are filtered out.
    """
    raw = raw.strip()
    if not raw or raw in ("-", "—", "合计", "可用现金", "N/A", "n/a"):
        return None
    if raw.isdigit():
        # A-share numeric code
        if raw.startswith("6"):
            return f"{raw}.SS"
        if raw.startswith(("0", "3")):
            return f"{raw}.SZ"
    return raw


def validate_holding(holding: Holding) -> Holding | None:
    """Validate a holding and return it, or None if invalid.

    Performs the following checks:
    - ticker must be non-empty
    - shares must be > 0
    - avg_cost must be >= 0
    """
    if not holding.ticker:
        logger.warning("Skipping holding with empty ticker")
        return None
    if holding.shares <= 0:
        logger.warning(f"Skipping {holding.ticker}: shares={holding.shares} <= 0")
        return None
    if holding.avg_cost < 0:
        logger.warning(f"Skipping {holding.ticker}: avg_cost={holding.avg_cost} < 0")
        return None
    return holding


def deduplicate_holdings(holdings: list[Holding]) -> dict[str, Holding]:
    """Deduplicate holdings by ticker, keeping the last occurrence.

    Returns a dict mapping ticker -> Holding.
    """
    result: dict[str, Holding] = {}
    for h in holdings:
        if h.ticker:
            result[h.ticker] = h
    return result
