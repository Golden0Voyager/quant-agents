"""Real-time snapshot for cross-validating reports against ground truth.

This module is intentionally NOT registered as a vendor — it is consumed
directly by scripts/report_auditor.py to perform fact-checking against
freshly fetched market data.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import akshare as ak

from . import xq_token as xq_token_mod
from .akshare_common import (
    is_a_share_ticker,
    no_proxy,
    safe_float,
    to_akshare_symbol,
)

logger = logging.getLogger(__name__)


def _with_retry(fn: Callable, *, attempts: int = 3, delay_s: float = 2.0) -> Any | None:
    """Run *fn* up to *attempts* times. Returns the result or None on failure.

    Eastmoney/Xueqiu intermittently close the connection (`RemoteDisconnected`)
    or return rate-limit 400s. Each retry waits delay_s * (2 ** i) seconds,
    so attempts=3 yields 2s + 4s = 6s of cumulative back-off before giving up.

    Only transient upstream errors are retried/absorbed: raw network errors
    plus ``akshare.exceptions.APIError`` — akshare wraps upstream HTTP/API
    failures (rate-limit 400s, and Xueqiu's 400016 "requires a valid
    xq_a_token" when the configured token expires) in APIError rather than
    raising the raw requests exception, so without this entry a stale token
    would crash callers instead of degrading to the Eastmoney fallback.
    Programming errors (changed akshare schema, TypeError, ...) propagate
    immediately instead of being masked behind retries. Every attempt is
    bounded by ``_call_with_timeout`` (AKSHARE_TIMEOUT) so a hung socket
    cannot block forever, and exhaustion is logged at WARNING so total
    failure is distinguishable from a legitimately empty result.
    """
    from .akshare_common import _call_with_timeout, _network_error_types

    network_errors = _network_error_types()
    try:
        from akshare.exceptions import APIError as _AkshareAPIError

        retryable_errors = network_errors + (_AkshareAPIError,)
    except ImportError:  # pragma: no cover - akshare ships this module
        retryable_errors = network_errors

    last_exc: BaseException | None = None
    for i in range(attempts):
        try:
            return _call_with_timeout(fn)
        except retryable_errors as exc:
            last_exc = exc
            if i < attempts - 1:
                time.sleep(delay_s * (2**i))
    logger.warning("realtime fetch failed after %d attempts: %s", attempts, last_exc)
    return None


def fetch_realtime_snapshot(
    ticker: str, xq_token: str | None = None
) -> dict[str, Any] | None:
    """Return a canonical snapshot of current price/PE/PB/market_cap.

    Returns None when *ticker* is not an A-share or every data source fails.
    Xueqiu provides intraday price/PE/PB when XUEQIU_TOKEN is available;
    Eastmoney always provides company name and market cap as a fallback.
    """
    if not is_a_share_ticker(ticker):
        return None

    bare = to_akshare_symbol(ticker, "bare")
    prefixed = to_akshare_symbol(ticker, "upper_prefix")
    # Explicit argument wins; otherwise the (normalized) env token, but a
    # token already proven dead this process is skipped without burning
    # retry budget on it again.
    token = xq_token_mod.normalize_xq_token(xq_token) if xq_token else xq_token_mod.current_env_token()
    if token and xq_token_mod.is_known_bad(token):
        logger.info("skipping known-dead XUEQIU_TOKEN for %s; using Eastmoney fallback", ticker)
        token = None
    if not token and not xq_token:
        logger.info(
            "XUEQIU_TOKEN 未配置，跳过雪球实时源（东财兜底仍可用）。"
            "配置方式：uv run python scripts/update_xq_token.py \"<xq_a_token>\""
        )

    snap: dict[str, Any] = {
        "ticker": ticker.upper(),
        "company_name": "",
        "price": None,
        "pe_ttm": None,
        "pb": None,
        "market_cap_yi": None,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources": [],
    }

    with no_proxy():
        if token:
            df = _with_retry(
                lambda: ak.stock_individual_spot_xq(symbol=prefixed, token=token)
            )
            if df is None:
                # Distinguish "expired token" from a transient blip once,
                # remember dead tokens for the process, and surface the
                # exact recovery command to the operator.
                xq_token_mod.handle_possible_expiry(token)
            if df is not None and not df.empty:
                d = dict(zip(df["item"], df["value"], strict=False))
                # Xueqiu uses 现价/资产净值·总市值/名称 — not 最新/总市值/股票简称.
                snap["price"] = safe_float(d.get("现价") or d.get("最新"))
                snap["pe_ttm"] = safe_float(d.get("市盈率(TTM)"))
                snap["pb"] = safe_float(d.get("市净率"))
                mc = safe_float(d.get("资产净值/总市值") or d.get("总市值"))
                if mc is not None:
                    snap["market_cap_yi"] = round(mc / 1e8, 2)
                name = d.get("名称")
                if name:
                    snap["company_name"] = str(name).strip()
                snap["sources"].append("xueqiu")

        info = _with_retry(lambda: ak.stock_individual_info_em(symbol=bare))
        if info is not None and not info.empty:
            d = dict(zip(info["item"], info["value"], strict=False))
            if not snap["company_name"]:
                snap["company_name"] = str(d.get("股票简称", "")).strip()
            if snap["market_cap_yi"] is None:
                mc = safe_float(d.get("总市值"))
                if mc is not None:
                    snap["market_cap_yi"] = round(mc / 1e8, 2)
            snap["sources"].append("eastmoney_info")

    if not snap["sources"]:
        return None
    return snap
