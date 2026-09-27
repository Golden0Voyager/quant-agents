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
