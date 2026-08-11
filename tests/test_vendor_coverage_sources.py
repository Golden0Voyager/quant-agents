"""Optional A-share data-source contracts and normalization tests."""

from unittest.mock import patch

import akshare as ak
import pandas as pd
import pytest

import tradingagents.dataflows.akshare_vendor as akshare_vendor
import tradingagents.dataflows.interface as interface
import tradingagents.dataflows.tushare_vendor as tushare_vendor
from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.default_config import default_config


@pytest.mark.unit
def test_tushare_is_not_required_when_token_is_missing(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    with pytest.raises(VendorNotConfiguredError):
        tushare_vendor.get_company_announcements(
            "002241.SZ", "2026-08-01", "2026-08-11"
        )


@pytest.mark.unit
def test_cninfo_announcement_rows_are_normalized(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "公告日期": "2026-08-11",
                "公告标题": "关于签订重大合同的公告",
                "公告类型": "重大事项",
                "公告链接": "https://example.test/notice/1",
            }
        ]
    )
    monkeypatch.setattr(ak, "stock_zh_a_disclosure_report_cninfo", lambda **_: frame)

    out = akshare_vendor.get_company_announcements_cninfo(
        "002241.SZ", "2026-08-01", "2026-08-11"
    )

    assert "公告标题" in out
    assert "公告链接" in out
    assert "关于签订重大合同的公告" in out
    assert "https://example.test/notice/1" in out


@pytest.mark.unit
def test_default_config_does_not_enable_tushare():
    cfg = default_config()

    assert "tushare" not in str(cfg["data_vendors"]).lower()
    assert "tushare" not in str(cfg["tool_vendors"]).lower()


@pytest.mark.unit
def test_explicit_tool_chain_can_select_tushare():
    with patch.dict(
        interface.VENDOR_METHODS,
        {"get_company_announcements": {"tushare": object()}},
        clear=False,
    ):
        assert interface._build_vendor_chain(
            "get_company_announcements", "tushare", "002241.SZ"
        ) == ["tushare"]


@pytest.mark.unit
def test_tushare_enabled_setting_appends_optional_fallback(monkeypatch):
    monkeypatch.setenv("TUSHARE_ENABLED", "1")

    chain = interface._build_vendor_chain(
        "get_earnings_estimates", "smartmoney_db,akshare", "002241.SZ"
    )

    assert chain == ["smartmoney_db", "akshare", "tushare"]
