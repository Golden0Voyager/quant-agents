"""Trading-session based freshness metrics for local data.

Calendar-day staleness budgets are wrong for A-share data: a healthy local
table legitimately lags 2-3 calendar days over a weekend and up to ~8 days
over Spring Festival / National Day holidays, while a mid-week pipeline
stall already misses material trading sessions after 2-3 days. Measuring lag
in *trading sessions* is simultaneously tighter in the stall case and immune
to holiday false positives.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd


@lru_cache(maxsize=2)
def _calendar(name: str):
    import exchange_calendars as xc

    return xc.get_calendar(name)


def trading_sessions_between(
    latest: str,
    anchor: str,
    calendar: str = "XSHG",
) -> int | None:
    """Number of trading sessions ``d`` with ``latest < d <= anchor``.

    This is the count of *missing* sessions: 0 when the local table is
    current as of ``anchor``, 1 when only the anchor day's bar is not yet
    published, and it stops growing across weekends/holidays (no sessions,
    no growth).

    Returns None when either date is unparseable or the calendar is
    unavailable — callers must treat None as "cannot judge, never block".
    """
    try:
        cal = _calendar(calendar)
        latest_ts = pd.Timestamp(str(latest)[:10])
        anchor_ts = pd.Timestamp(str(anchor)[:10])
        if anchor_ts <= latest_ts:
            return 0
        sessions = cal.sessions_in_range(latest_ts, anchor_ts)
        return int((sessions > latest_ts).sum())
    except Exception:
        return None


# Days of grace after a periodic-report filing deadline before the report is
# treated as "expected" everywhere. A handful of companies file a few days
# late; waiting a week keeps the guard from demanding reports that most of
# the market legitimately has not published yet.
QUARTERLY_FILING_GRACE_DAYS = 7

# A-share periodic-report filing deadlines (披露截止日): Q1 -> Apr 30,
# semi-annual -> Aug 31, Q3 -> Oct 31, annual -> Apr 30 of the next year.
_FILING_DEADLINE_MONTH_DAY = {3: (4, 30), 6: (8, 31), 9: (10, 31), 12: (4, 30)}


def _filing_deadline(period_end: pd.Timestamp) -> pd.Timestamp:
    """Regulatory filing deadline for a quarter-end report period."""
    month, day = _FILING_DEADLINE_MONTH_DAY[period_end.month]
    year = period_end.year + 1 if period_end.month == 12 else period_end.year
    return pd.Timestamp(year=year, month=month, day=day)


def expected_report_period(
    anchor: str | None,
    grace_days: int = QUARTERLY_FILING_GRACE_DAYS,
) -> str | None:
    """Latest quarter-end (``YYYY-MM-DD``) that should be published as of ``anchor``.

    A report period counts as "expected" once its filing deadline plus the
    grace window has passed. E.g. anchored in May, Q1 (03-31, deadline Apr 30)
    is expected; anchored in early April it is not, and the expectation stays
    at Q3 of the previous year. ``anchor=None`` means today. Judged against
    the request's own date anchor, so backtests requesting old dates expect
    old reports and are never penalised.

    Returns None when the anchor is unparseable — callers must treat None as
    "cannot judge, never block".
    """
    try:
        anchor_ts = (
            pd.Timestamp(str(anchor)[:10]) if anchor else pd.Timestamp.today()
        ).normalize()
    except Exception:
        return None
    grace = pd.Timedelta(days=grace_days)
    candidates = []
    for year in (anchor_ts.year - 1, anchor_ts.year):
        for month in (3, 6, 9, 12):
            period_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
            if _filing_deadline(period_end) + grace <= anchor_ts:
                candidates.append(period_end)
    if not candidates:
        return None
    return max(candidates).strftime("%Y-%m-%d")
