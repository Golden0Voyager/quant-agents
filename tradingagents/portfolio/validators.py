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

    - 6xxxxx, 51xxxx, 56xxxx, 58xxxx -> .SS (Shanghai)
    - 0xxxxx, 3xxxxx, 15xxxx, 16xxxx -> .SZ (Shenzhen)
    - 82/83/87/88/43/92xxxx          -> .BJ (Beijing)
    - Others (e.g. HK1810, international tickers) are returned as-is.
    - Empty cells, dashes, and Chinese headers are filtered out.
    """
    raw = raw.strip()
    if not raw or raw in ("-", "—", "合计", "可用现金", "N/A", "n/a"):
        return None
    if raw.isdigit():
        # A-share and ETF numeric codes (6 digits)
        if raw.startswith(("6", "51", "56", "58")):
            return f"{raw}.SS"
        if raw.startswith(("0", "3", "15", "16")):
            return f"{raw}.SZ"
        if raw.startswith(("82", "83", "87", "88", "43", "92")):
            return f"{raw}.BJ"
    return raw


def canonical_ticker(ticker: str) -> str:
    """Return a canonicalized ticker representation for lookup and comparison.

    Normalizes:
    - Case and whitespace: ' 000603.sz ' -> '000603.SZ'
    - HK formats: 'HK1810', '01810.HK', '1810.HK' -> '1810.HK'
    - Bare 6-digit A-share / ETF codes: '000603' -> '000603.SZ'
    - Existing suffixed or international tickers: uppercase
    """
    if not ticker:
        return ""
    t = ticker.strip().upper()

    # HK formats: 'HK1810', 'HK01810', '1810.HK', '01810.HK'
    if t.startswith("HK") and t[2:].isdigit():
        code = t[2:].lstrip("0") or "0"
        return f"{code}.HK"
    if t.endswith(".HK"):
        code = t[:-3].lstrip("0") or "0"
        return f"{code}.HK"

    # Bare 6-digit numeric codes
    if t.isdigit() and len(t) == 6:
        normalized = normalize_ticker(t)
        if normalized:
            return normalized

    return t


def ticker_matches(a: str, b: str) -> bool:
    """Check whether two ticker strings represent the same financial instrument.

    Supports matching across:
    - Exact and case-insensitive equality
    - Canonical representations (e.g. 'HK1810' and '1810.HK')
    - Bare codes vs exchange-qualified codes (e.g. '000603' and '000603.SZ')
    - Prevents cross-matching contradictory exchange suffixes (e.g. '000603.SZ' != '000603.SS')
    """
    if not a or not b:
        return False
    a_clean = a.strip().upper()
    b_clean = b.strip().upper()
    if a_clean == b_clean:
        return True

    # Conflicting exchange suffixes cannot match
    a_suffix = a_clean.split(".")[-1] if "." in a_clean else ""
    b_suffix = b_clean.split(".")[-1] if "." in b_clean else ""
    if a_suffix and b_suffix and a_suffix != b_suffix:
        return False

    # Compare canonical forms
    a_canon = canonical_ticker(a_clean)
    b_canon = canonical_ticker(b_clean)
    if a_canon and b_canon and a_canon == b_canon:
        return True

    # Bare code match for 6-digit A-shares
    a_bare = a_clean.split(".")[0]
    b_bare = b_clean.split(".")[0]
    if a_bare == b_bare and len(a_bare) == 6 and a_bare.isdigit():
        return True

    # HK variant cross-match (e.g. HK1810 and 1810)
    a_hk = a_clean[2:].lstrip("0") if (a_clean.startswith("HK") and a_clean[2:].isdigit()) else None
    b_hk = b_clean[2:].lstrip("0") if (b_clean.startswith("HK") and b_clean[2:].isdigit()) else None
    if a_hk and b_clean.endswith(".HK") and b_clean[:-3].lstrip("0") == a_hk:
        return True
    if b_hk and a_clean.endswith(".HK") and a_clean[:-3].lstrip("0") == b_hk:
        return True

    return False


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
