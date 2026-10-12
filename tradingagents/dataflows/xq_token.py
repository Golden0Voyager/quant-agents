"""Xueqiu ``xq_a_token`` lifecycle: validate, invalidate, update.

Xueqiu does not hand out anonymous tokens — the quote endpoint answers
400016 without a valid login-session cookie, and simply visiting
xueqiu.com no longer rotates a fresh ``xq_a_token`` into Set-Cookie
(both verified 2026-10-12). Tokens therefore cannot be refreshed
automatically; they must be copied from a logged-in browser
(DevTools → Application → Cookies → xueqiu.com → xq_a_token).

This module automates everything around that one manual step:

- detect expiry lazily, the first time a fetch with the token fails;
- remember a known-dead token for the rest of the process so we stop
  burning retry budget and probe calls on every fetch;
- a validated one-shot update (``scripts/update_xq_token.py``) that
  normalizes cookie-form pastes, probes the token live, and writes it to
  ``.env`` so the next run picks it up.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Probe symbol: 浦发银行 — large-cap, always quoted, cheap to ask.
_PROBE_SYMBOL = "SH600000"

# A validated token is trusted for this long before an explicit
# re-validation is considered worthwhile. Fetches do not re-validate on
# their own — they rely on the known-bad set — to keep the hot path cheap.
_VALIDITY_TTL_H = 6

_LOCK = threading.Lock()
_KNOWN_BAD: set[str] = set()
_VALIDATED_AT: dict[str, datetime] = {}


def normalize_xq_token(raw: str) -> str:
    """Normalize a pasted token: bare value or cookie ``xq_a_token=...;`` form."""
    token = raw.strip().rstrip(";").strip()
    if token.lower().startswith("xq_a_token="):
        token = token.split("=", 1)[1].strip()
    return token


def current_env_token() -> str | None:
    """Read XUEQIU_TOKEN / XQ_TOKEN from the environment, normalized."""
    raw = os.getenv("XUEQIU_TOKEN") or os.getenv("XQ_TOKEN")
    return normalize_xq_token(raw) if raw else None


def is_known_bad(token: str) -> bool:
    with _LOCK:
        return token in _KNOWN_BAD


def mark_known_bad(token: str) -> None:
    with _LOCK:
        _KNOWN_BAD.add(token)


def clear_known_bad(token: str) -> None:
    with _LOCK:
        _KNOWN_BAD.discard(token)


def _probe(token: str, probe_symbol: str = _PROBE_SYMBOL) -> str:
    """Probe the token against Xueqiu's quote endpoint.

    Returns ``"ok"`` (usable), ``"auth"`` (400016 / login required) or
    ``"network"`` (could not tell — do not treat as expired).
    """
    import akshare as ak

    from .akshare_common import no_proxy

    with no_proxy():
        try:
            df = ak.stock_individual_spot_xq(symbol=probe_symbol, token=token, timeout=8)
        except Exception as exc:  # noqa: BLE001 - triaged below
            if type(exc).__name__ == "APIError":
                return "auth"
            logger.debug("xq token probe inconclusive: %s: %s", type(exc).__name__, exc)
            return "network"
    if df is not None and not df.empty:
        with _LOCK:
            _VALIDATED_AT[token] = datetime.now()
        return "ok"
    return "network"


def validate_xq_token(token: str) -> bool:
    """True only when the token passed the live probe."""
    return _probe(token) == "ok"


def update_xq_token(raw_token: str, *, persist: bool = True) -> str:
    """Install a new token: normalize → live-validate → env (+ .env).

    Raises ``ValueError`` when the token fails the live probe, so a typo'd
    or already-expired paste fails loudly here instead of silently
    degrading every later fetch.
    """
    token = normalize_xq_token(raw_token)
    if not token:
        raise ValueError("xq_a_token 为空")
    if _probe(token) != "ok":
        raise ValueError(
            "token 未通过雪球实测探针（quote.json 无数据或要求登录态）。"
            "请从已登录的浏览器 DevTools → Application → Cookies → "
            "xueqiu.com 复制有效的 xq_a_token 后重试。"
        )
    os.environ["XUEQIU_TOKEN"] = token
    clear_known_bad(token)
    if persist:
        _persist_to_dotenv(token)
    logger.info("XUEQIU_TOKEN updated and validated")
    return token


def _persist_to_dotenv(token: str) -> Path | None:
    """Best-effort write to .env (gitignored); never raises."""
    try:
        from dotenv import find_dotenv, set_key

        path = find_dotenv(usecwd=True)
        if not path:
            logger.warning("未从当前目录找到 .env；token 仅对本进程生效")
            return None
        set_key(path, "XUEQIU_TOKEN", token)
        return Path(path)
    except Exception as exc:  # noqa: BLE001 - persist is best-effort
        logger.warning("写入 .env 失败（token 已对本进程生效）: %s", exc)
        return None


def handle_possible_expiry(token: str) -> None:
    """Called when a fetch with ``token`` failed.

    Confirms expiry with one probe (transient failures must not mark a
    good token dead), then remembers it for the process and tells the
    operator exactly how to fix it. Idempotent per token.
    """
    if is_known_bad(token):
        return
    result = _probe(token)
    if result == "ok":
        return  # transient failure, token is fine
    if result == "network":
        logger.warning(
            "雪球探测结果不确定（网络层失败），暂不排除 token 失效；"
            "本次自动降级到东财兜底源。"
        )
        return
    mark_known_bad(token)
    logger.warning(
        "雪球 xq_a_token 已失效（实测 400016 要求登录态）。XQ 实时源本进程内"
        "自动降级到东财兜底（company_name / 总市值仍可用）。如需恢复 XQ 源，"
        "请在浏览器登录 xueqiu.com，从 DevTools → Application → Cookies → "
        "xueqiu.com 复制新的 xq_a_token，然后运行：\n"
        "    uv run python scripts/update_xq_token.py \"<新token>\""
    )
