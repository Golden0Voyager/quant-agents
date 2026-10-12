import logging
from datetime import date, datetime

import pytest

from tradingagents.agents.utils.time_scope import drop_future_dated_lines


@pytest.mark.unit
class TestDropFutureDatedLines:
    def test_future_iso_date_line_is_dropped(self):
        text = (
            "## News\n"
            "| Date | Event |\n"
            "| 2026-10-11 | 公司发布三季度预告 |\n"
            "| 2026-10-10 | 股东大会通过议案 |\n"
            "| 2026-10-09 | 与供应商签订长单 |\n"
        )
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert "2026-10-11" not in kept
        assert "2026-10-10" not in kept
        assert "2026-10-09" in kept
        assert len(dropped) == 2

    def test_chinese_date_format_is_dropped(self):
        text = "2026年10月11日，公司公告重大资产重组。\n2026年10月9日，公司日常经营正常。\n"
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert "重大资产重组" not in kept
        assert "日常经营正常" in kept
        assert len(dropped) == 1

    def test_slash_date_format_is_dropped(self):
        text = "On 2026/10/12 the firm announced a buyback.\nOn 2026/10/9 nothing happened.\n"
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert "buyback" not in kept
        assert "nothing happened" in kept

    def test_past_and_same_day_lines_are_kept(self):
        text = (
            "2026-10-08 业绩预告超预期\n"
            "2026-10-09 当日龙虎榜净买入\n"
        )
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert kept.count("\n") == 1 or "业绩预告" in kept
        assert dropped == []

    def test_yearless_date_is_kept(self):
        # Bare month-day without a year cannot be proven future-dated.
        text = "10月11日将召开临时股东大会，需关注后续进展。\n"
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert kept == text
        assert dropped == []

    def test_malformed_date_is_kept(self):
        text = "2026-02-30 的异常日期不触发剔除。\n"
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert kept == text
        assert dropped == []

    def test_future_target_year_in_prose_is_dropped(self):
        # Any explicit full date after the analysis date goes, even inside
        # prose — the guard favours precision over recall here.
        text = "公司指引 2027-03-31 前完成产能翻倍。\n"
        kept, dropped = drop_future_dated_lines(text, date(2026, 10, 9))
        assert kept == ""
        assert len(dropped) == 1

    def test_accepts_string_and_datetime_analysis_date(self):
        text = "2026-10-11 未来新闻\n2026-10-01 过去新闻\n"
        kept_str, _ = drop_future_dated_lines(text, "2026-10-09")
        kept_dt, _ = drop_future_dated_lines(text, datetime(2026, 10, 9))
        assert kept_str == kept_dt
        assert "未来新闻" not in kept_str

    def test_empty_text_passthrough(self):
        kept, dropped = drop_future_dated_lines("", date(2026, 10, 9))
        assert kept == ""
        assert dropped == []

    def test_logs_dropped_lines(self, caplog):
        text = "2026-10-11 未来新闻\n"
        with caplog.at_level(logging.INFO, logger="tradingagents.agents.utils.time_scope"):
            drop_future_dated_lines(text, date(2026, 10, 9))
        assert any("Dropped future-dated line" in r.message for r in caplog.records)
