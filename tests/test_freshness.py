"""Unit tests for freshness.expected_report_period.

Quarterly disclosure cadence: a report period counts as "expected" once its
filing deadline plus the grace window has passed. Deadlines: Q1 -> Apr 30,
H1 -> Aug 31, Q3 -> Oct 31, annual -> Apr 30 of the next year.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from tradingagents.dataflows.freshness import expected_report_period, nearest_prior_session


class TestExpectedReportPeriod:
    def test_may_expects_q1(self):
        # Q1 deadline Apr 30 + 7d grace = May 7
        assert expected_report_period("2026-05-10") == "2026-03-31"

    def test_early_april_still_expects_prior_q3(self):
        # Q1 deadline has not passed yet; the latest *passed* deadline is Q3
        assert expected_report_period("2026-04-01") == "2025-09-30"

    def test_september_expects_q2(self):
        # H1 deadline Aug 31 + 7d grace = Sep 7
        assert expected_report_period("2026-09-15") == "2026-06-30"

    def test_november_expects_q3(self):
        # Q3 deadline Oct 31 + 7d grace = Nov 7
        assert expected_report_period("2026-11-10") == "2026-09-30"

    def test_january_expects_prior_q3(self):
        # Annual (Q4) deadline Apr 30 has not passed; latest passed is Q3
        assert expected_report_period("2026-01-15") == "2025-09-30"

    def test_annual_shares_q1_deadline(self):
        # Annual (Q4) and Q1 share the Apr 30 filing deadline, so whenever the
        # annual report is expected the (larger) Q1 period is expected too.
        assert expected_report_period("2026-05-10") == "2026-03-31"

    def test_grace_window_respected(self):
        # May 5 is inside the 7-day grace after the Apr 30 deadline: neither
        # the annual nor the Q1 report is expected yet
        assert expected_report_period("2026-05-05") == "2025-09-30"
        # May 8 is past the grace window: both annual and Q1 are expected,
        # and Q1 (2026-03-31) is the larger period
        assert expected_report_period("2026-05-08") == "2026-03-31"

    def test_malformed_anchor_returns_none(self):
        assert expected_report_period("not-a-date") is None

    def test_anchor_none_means_today(self):
        result = expected_report_period(None)
        assert result is None or isinstance(result, str)


class TestNearestPriorSession:
    def test_saturday_snaps_to_friday(self):
        assert nearest_prior_session("2026-07-04") == "2026-07-03"

    def test_sunday_snaps_to_friday(self):
        assert nearest_prior_session("2026-07-05") == "2026-07-03"

    def test_trading_day_unchanged(self):
        assert nearest_prior_session("2026-07-06") == "2026-07-06"

    def test_holiday_snaps_to_prior_session(self):
        # 2026 National Day (Oct 1-7) is holiday-aware, not just weekends
        assert nearest_prior_session("2026-10-05") == "2026-09-30"

    def test_malformed_anchor_returns_none(self):
        assert nearest_prior_session("not-a-date") is None
