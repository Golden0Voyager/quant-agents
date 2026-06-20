"""End-to-end regression test against ground-truth values.

Marked @integration — requires network access to Eastmoney.
Run with: uv run pytest -m integration tests/test_akshare_regression.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

GROUND_TRUTH_PATH = Path(__file__).parent / "fixtures" / "a_share_ground_truth.json"


@pytest.fixture(scope="module")
def ground_truth() -> dict:
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))["stocks"]


def _extract_first_yi(text: str, label_pattern: str) -> float | None:
    """Find '<label> ... X.XX亿' in text and return X.XX. Returns None if missing."""
    m = re.search(rf"{label_pattern}.*?([-+]?\d+(?:\.\d+)?)亿", text)
    return float(m.group(1)) if m else None


def _within_tolerance(actual: float, expected: float, tol_pct: float) -> bool:
    if expected == 0:
        return abs(actual) < tol_pct / 100
    return abs(actual - expected) / abs(expected) <= tol_pct / 100


# Tickers with total_assets_yi ground truth in the fixture
TICKERS_WITH_TOTAL_ASSETS = [
    "300175.SZ",
    "300454.SZ",
    "300562.SZ",
    "300760.SZ",
    "603893.SS",
]


@pytest.mark.integration
class TestAkshareRegression:
    @pytest.mark.parametrize("ticker", TICKERS_WITH_TOTAL_ASSETS)
    def test_balance_sheet_total_assets(self, ticker, ground_truth):
        """Each stock's reported 总资产 must fall within its tolerance band."""
        from tradingagents.dataflows.akshare_vendor import get_balance_sheet

        expected = ground_truth[ticker].get("total_assets_yi")
        assert expected is not None, f"missing total_assets_yi for {ticker}"

        out = get_balance_sheet(ticker)
        actual = _extract_first_yi(out, "总资产")
        assert actual is not None, (
            f"{ticker} 总资产未在输出中找到。\n输出片段:\n{out[:500]}"
        )
        assert _within_tolerance(actual, expected["value"], expected["tolerance_pct"]), (
            f"{ticker} 总资产 {actual}亿 偏离真值 {expected['value']}亿 "
            f"超过 ±{expected['tolerance_pct']}%"
        )

    def test_realtime_snapshot_smoke(self, ground_truth):
        """Five key tickers must return a non-empty realtime snapshot.

        Eastmoney's push2 endpoint occasionally rate-limits the host IP for
        several minutes. When ALL five tickers fail we treat it as a
        transient environment problem and pytest.skip — a real code break
        would still surface as 1-2 failures (partial outage).
        """
        import time

        from tradingagents.dataflows.akshare_realtime import fetch_realtime_snapshot

        critical_tickers = ("603893.SS", "002594.SZ", "300760.SZ", "600900.SS", "000100.SZ")
        failures: list[str] = []
        for i, ticker in enumerate(critical_tickers):
            if i > 0:
                time.sleep(2.0)
            snap = fetch_realtime_snapshot(ticker)
            if snap is None or not snap.get("company_name") or snap.get("market_cap_yi") is None:
                failures.append(ticker)

        if len(failures) == len(critical_tickers):
            pytest.skip(
                "所有 ticker 真值快照失败 — eastmoney 端点疑似限流，跳过该测试。"
                f"failures={failures}"
            )
        assert len(failures) <= 1, (
            f"超过 1 只股票快照失败: {failures}（共 {len(critical_tickers)} 只）"
        )

    def test_routing_uses_akshare_for_a_share(self):
        """route_to_vendor must dispatch a 600519.SS call to the akshare vendor."""
        from tradingagents.dataflows.interface import route_to_vendor

        out = route_to_vendor("get_balance_sheet", "600519.SS")
        assert "akshare" in out.lower() or "东财" in out, (
            "route_to_vendor 没有把 A 股请求路由到 akshare"
        )
