"""Announcement body fetching with a disk cache (东财公告正文抓取).

Fetches the plain-text body of Eastmoney announcements from the
``np-cnotice-stock.eastmoney.com`` content API and caches HTML-stripped text
under ``<cache_dir>/<art_code>.txt``.

Fail-open contract: every failure path (cache read error, network error,
non-200, missing JSON key, empty-after-strip) returns an absent entry instead
of raising, so the caller can fall back to title-only rendering (spec §6).
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from tradingagents.dataflows.akshare_common import no_proxy

logger = logging.getLogger(__name__)

_ANN_API = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_ART_CODE_RE = re.compile(r"AN\d+")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CLIENT_SOURCE = "web"
_PAGE_INDEX = "1"
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 8.0


def extract_art_code(url: str) -> str | None:
    """Extract the announcement ``AN<digits>`` art code from a notice URL.

    Returns ``None`` when *url* is empty or contains no art-code fragment.
    """
    if not isinstance(url, str) or not url:
        return None
    match = _ART_CODE_RE.search(url)
    return match.group(0) if match else None


def fetch_bodies(
    art_codes: list[str],
    cache_dir: str,
    *,
    max_workers: int = 5,
    timeout: int = 15,
    retries: int = 3,
) -> dict[str, str]:
    """Concurrently fetch announcement bodies, serving disk cache hits.

    Returns ``{art_code: plain_text}`` where *plain_text* is the HTML-stripped,
    whitespace-collapsed body. Codes that are missing from the API, exhaust all
    retries, or strip to empty are omitted from the result — this function
    never raises (fail-open; the caller renders those rows title-only).
    """
    bodies: dict[str, str] = {}
    if not art_codes:
        return bodies
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as exc:
        logger.warning("Announcement cache dir %s unavailable: %s", cache_dir, exc)

    unique_codes = list(dict.fromkeys(art_codes))
    max_workers = max(1, max_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_one, code, cache_dir, timeout, retries): code
            for code in unique_codes
        }
        for future, code in futures.items():
            try:
                body = future.result()
            except Exception as exc:  # per-item isolation (fail-open)
                logger.warning("Announcement body task for %s failed: %s", code, exc)
                continue
            if body:
                bodies[code] = body
    return bodies


def _fetch_one(code: str, cache_dir: str, timeout: int, retries: int) -> str | None:
    """Return the stripped body for *code*, or ``None`` if unavailable."""
    cached = _read_cache(code, cache_dir)
    if cached is not None:
        return cached

    raw = _fetch_with_retry(code, timeout, retries)
    if not raw:
        return None
    body = _strip_html(raw)
    if not body:
        # MINOR-8: strip 后为空 → 视为抓取缺失，不进返回 dict，不缓存空文件。
        logger.warning("Announcement %s stripped to empty; treating as missing", code)
        return None
    _write_cache(code, cache_dir, body)
    return body


def _read_cache(code: str, cache_dir: str) -> str | None:
    """Return cached stripped text for *code*, or ``None`` on miss/empty."""
    path = os.path.join(cache_dir, f"{code}.txt")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    return text if text.strip() else None


def _fetch_with_retry(code: str, timeout: int, retries: int) -> str | None:
    """Fetch raw (un-stripped) content with exponential backoff; never raises."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _fetch_once(code, timeout)
        except (
            requests.RequestException,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            last_exc = exc
            if attempt < retries:
                delay = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_MAX)
                logger.warning(
                    "Announcement fetch %s failed (%s), retrying in %.1fs",
                    code,
                    exc,
                    delay,
                )
                time.sleep(delay)
    logger.warning(
        "Announcement fetch %s failed after %d attempts: %s",
        code,
        retries + 1,
        last_exc,
    )
    return None


def _fetch_once(code: str, timeout: int) -> str | None:
    """Perform a single GET and extract ``data.notice_content``."""
    params = {"art_code": code, "client_source": _CLIENT_SOURCE, "page_index": _PAGE_INDEX}
    headers = {"User-Agent": _USER_AGENT}
    # no_proxy(): 直接连东财国内端点，不经系统代理（实测否则 SSLError）。
    with no_proxy():
        resp = requests.get(_ANN_API, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    content = data.get("notice_content") if isinstance(data, dict) else None
    return content if isinstance(content, str) else None


def _strip_html(raw: str) -> str:
    """Remove HTML tags, unescape entities and collapse whitespace."""
    text = _TAG_RE.sub("", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _write_cache(code: str, cache_dir: str, body: str) -> None:
    """Persist stripped plain text; best-effort (never raise)."""
    path = os.path.join(cache_dir, f"{code}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError as exc:
        logger.warning("Announcement cache write failed for %s: %s", code, exc)
