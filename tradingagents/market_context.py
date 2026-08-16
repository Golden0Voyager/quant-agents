"""Canonical market identities and exchange-session analysis dates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import exchange_calendars
import pandas as pd

Market = Literal["XSHG", "XHKG", "XNYS", "CRYPTO", "UNKNOWN"]

_CALENDAR_BY_MARKET: dict[Market, str] = {
    "XSHG": "XSHG",
    "XHKG": "XHKG",
    "XNYS": "XNYS",
}
_MAINLAND_SUFFIXES = (".SS", ".SZ", ".BJ")
_US_TICKER_PATTERN = r"[A-Z]{1,5}(?:[.-][A-Z])?"


@dataclass(frozen=True)
class AnalysisDates:
    """The report day, latest market session, and evidence-window end."""

    analysis_date: str
    market_as_of_date: str
    evidence_window_end: str


def infer_market(ticker: str) -> Market:
    """Map a canonical ticker to its market-session calendar identity."""
    normalized = ticker.strip().upper()
    if normalized.endswith(_MAINLAND_SUFFIXES):
        return "XSHG"
    if normalized.endswith(".HK"):
        return "XHKG"
    if normalized.endswith("-USD"):
        return "CRYPTO"
    if re.fullmatch(_US_TICKER_PATTERN, normalized):
        return "XNYS"
    return "UNKNOWN"


def resolve_analysis_dates(ticker: str, analysis_date: str) -> AnalysisDates:
    """Resolve market-as-of date from the relevant exchange calendar."""
    analysis_timestamp = pd.Timestamp(analysis_date).normalize()
    normalized_analysis_date = analysis_timestamp.date().isoformat()
    market = infer_market(ticker)
    calendar_name = _CALENDAR_BY_MARKET.get(market)

    if calendar_name is None:
        market_as_of_date = normalized_analysis_date
    else:
        calendar = exchange_calendars.get_calendar(calendar_name)
        session = calendar.date_to_session(analysis_timestamp, direction="previous")
        market_as_of_date = session.date().isoformat()

    return AnalysisDates(
        analysis_date=normalized_analysis_date,
        market_as_of_date=market_as_of_date,
        evidence_window_end=normalized_analysis_date,
    )
