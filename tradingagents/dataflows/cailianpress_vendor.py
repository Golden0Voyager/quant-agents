"""Cailianpress (财联社) data fetcher for China flash news telegrams.

Provides functions to fetch real-time Cailianpress telegrams (快讯电报),
which are event-driven financial news and announcements across A-shares.

This is a separate vendor from Eastmoney to maintain clear separation of concerns:
- **Eastmoney data** → retail investor attention/sentiment signals
- **Cailianpress** → official flash news/company announcements

Main API endpoint pattern (based on open-source implementations):
    https://www.cls.cn/v1/roll/get_roll_list

Returns structured telegrams with timestamps, titles, content, level (A/B/C),
and associated stock codes for filtering.
"""

from __future__ import annotations

import hashlib
import logging

import requests

from tradingagents.dataflows.akshare_common import no_proxy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (matching open-source implementations)
# ---------------------------------------------------------------------------

BASE_URL = "https://www.cls.cn/v1/roll/get_roll_list"

APP_PARAMS = {
    "app": "CailianpressWeb",
    "os": "web",
    "sv": "8.4.6",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.cls.cn/telegraph",
}

RED_KEYWORDS = [
    "利好", "利空", "重要", "突发", "紧急", "关注", "提醒", "涨停", "大跌", "突破",
]


def _generate_signature(params: dict) -> str:
    """Generate API signature.

    Algorithm (matches cls-telegraph implementation):
        sign_str = sorted params joined by "&" with "=value"
        sha1_hash = SHA1(sign_str)
        sign = MD5(sha1_hash)
    """
    sorted_keys = sorted(params.keys())
    params_string = "&".join(f"{key}={params[key]}" for key in sorted_keys)
    sha1_hash = hashlib.sha1(params_string.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_hash.encode("utf-8")).hexdigest()


def _build_request_params(limit: int, last_time: int | None = None) -> dict:
    """Build request parameters with generated signature.

    Uses a *copy* of the module-level APP_PARAMS so that appending
    ``last_time`` for one call does not leak into subsequent calls.
    """
    all_params = {**APP_PARAMS}
    if last_time is not None:
        all_params["last_time"] = str(last_time)
    all_params["rn"] = str(limit)
    all_params["sign"] = _generate_signature(all_params)
    return all_params


def fetch_cailianpress_telegrams(
    limit: int = 20,
    last_time: int | None = None,
) -> str:
    """Fetch latest Cailianpress telegrams (flash news) via nodeapi.

    Uses the ``v1/roll/get_roll_list`` endpoint which does NOT require
    authentication or API keys (based on reverse-engineered public calls).

    Args:
        limit: Maximum number of telegrams to fetch (default 20).
        last_time: Optional Unix timestamp for pagination (fetches before this time).

    Returns:
        Formatted markdown string containing telegrams ready for prompt injection.

    Raises:
        RuntimeError: If API returns error or no data available.
    """
    params = _build_request_params(limit, last_time)
    url = f"{BASE_URL}?{"&".join(f"{k}={v}" for k, v in params.items())}"

    try:
        with no_proxy():
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Cailianpress API request failed: {exc}") from exc

    if data.get("errno", -1) != 0 or data.get("data") is None:
        raise RuntimeError(f"Cailianpress API returned error: {data}")

    roll_data = data["data"].get("roll_data", [])
    if not roll_data:
        raise RuntimeError("No telegrams returned from Cailianpress API")

    lines = ["# 财联社电报 (Flash News - Cailianpress Telegraph)", "", "---", ""]

    # Sort by timestamp descending (API may not be ordered)
    sorted_tps = sorted(roll_data, key=lambda x: x.get("ctime", 0), reverse=True)

    for item in sorted_tps[:limit]:
        ct = item.get("ctime", 0)
        title = item.get("title", "") or ""
        brief = item.get("brief", "") or title
        level = item.get("level", "C").upper()

        # Check if important (level A = red/important)
        is_important = level == "A" or any(kw in (title + brief) for kw in RED_KEYWORDS)

        # Format timestamp (simple HH:MM format)
        from datetime import datetime
        ts_dt = datetime.fromtimestamp(ct)
        ts_str = ts_dt.strftime("%Y-%m-%d %H:%M")

        # Get associated stocks
        stocks = []
        for s in item.get("stock_list") or []:
            name = s.get("name", "")
            sid = s.get("StockID", "")
            rise = s.get("RiseRange")
            rise_str = f" {rise:+.2f}%" if rise is not None else ""
            stocks.append(f"{name}({sid}){rise_str}")

        # Build line entry
        prefix = "⚠️" if is_important else ""
        stock_line = f"   📈 {' | '.join(stocks)}" if stocks else ""

        lines.append(f"**{ts_str}** [{level}] {prefix} {title.strip()}")
        if brief.strip() and brief.strip() != title.strip():
            lines.append(f"> {brief.strip()[:200]}{'...' if len(brief) > 200 else ''}")
        if stock_line:
            lines.append(stock_line)
        lines.append(f"[详情](https://www.cls.cn/detail/{item.get('id', '')})")
        lines.append("")

    lines.extend(["---", f"*共 {len(sorted_tps[:limit])} 条电报 • source: Cailianpress (财联社)*"])
    return "\n".join(lines)
