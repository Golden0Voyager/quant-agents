from __future__ import annotations

import pytest

from tradingagents.market_context import (
    AnalysisDates,
    infer_market,
    resolve_analysis_dates,
)


@pytest.mark.unit
def test_infer_market_uses_canonical_exchange_identifiers():
    assert infer_market("002413.SZ") == "XSHG"
    assert infer_market("1810.HK") == "XHKG"
    assert infer_market("AAPL") == "XNYS"
    assert infer_market("BTC-USD") == "CRYPTO"
    assert infer_market("SHOP.TO") == "UNKNOWN"


@pytest.mark.unit
def test_resolve_analysis_dates_uses_previous_session_for_weekend():
    assert resolve_analysis_dates("002413.SZ", "2026-08-16") == AnalysisDates(
        analysis_date="2026-08-16",
        market_as_of_date="2026-08-14",
        evidence_window_end="2026-08-16",
    )


@pytest.mark.unit
def test_resolve_analysis_dates_skips_mainland_national_day_holiday():
    assert resolve_analysis_dates("002413.SZ", "2024-10-03") == AnalysisDates(
        analysis_date="2024-10-03",
        market_as_of_date="2024-09-30",
        evidence_window_end="2024-10-03",
    )


@pytest.mark.unit
def test_resolve_analysis_dates_uses_xhkg_only_holiday():
    assert resolve_analysis_dates("1810.HK", "2024-07-01") == AnalysisDates(
        analysis_date="2024-07-01",
        market_as_of_date="2024-06-28",
        evidence_window_end="2024-07-01",
    )


@pytest.mark.unit
def test_resolve_analysis_dates_keeps_crypto_on_calendar_day():
    assert resolve_analysis_dates("BTC-USD", "2026-08-16") == AnalysisDates(
        analysis_date="2026-08-16",
        market_as_of_date="2026-08-16",
        evidence_window_end="2026-08-16",
    )
