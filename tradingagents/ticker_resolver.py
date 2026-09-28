"""Ticker resolver: normalize user input to exchange-qualified ticker + company name.

Supports:
- Chinese A-share numeric codes (auto-append .SS/.SZ/.BJ)
- Chinese company names (HiThink tickers/search → akshare lookup + local cache)
- Exchange-qualified tickers (pass-through with yfinance validation)
- International tickers (pass-through)
"""

from __future__ import annotations

import json
import os
import re

from tradingagents.market_context import Market, infer_market as _infer_market


def infer_market(ticker: str) -> Market:
    """Expose canonical market identity beside ticker resolution."""
    return _infer_market(ticker)

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")
_DEFAULT_CACHE_DIR = os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache"))
_NAME_MAP_PATH = os.path.join(_DEFAULT_CACHE_DIR, "a_share_name_map.json")


_CACHE: dict[str, str] | None = None


def _load_name_cache() -> dict[str, str]:
    """Load cached Chinese name -> ticker mapping."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if os.path.exists(_NAME_MAP_PATH):
        with open(_NAME_MAP_PATH, encoding="utf-8") as f:
            _CACHE = json.load(f)
        return _CACHE
    return {}


def _save_name_cache(data: dict[str, str]) -> None:
    """Save Chinese name -> ticker mapping to disk."""
    global _CACHE
    os.makedirs(_DEFAULT_CACHE_DIR, exist_ok=True)
    with open(_NAME_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _CACHE = data


# ---------------------------------------------------------------------------
# A-share suffix rules
# ---------------------------------------------------------------------------

_A_SHARE_SUFFIX_RULES = [
    # Shanghai
    (("600", "601", "603", "605", "688"), ".SS"),
    # Shenzhen (包括创业板 300/301)
    (("000", "001", "002", "003", "300", "301"), ".SZ"),
    # Beijing
    (("82", "83", "87", "88", "43", "92"), ".BJ"),
]


def _is_numeric_code(s: str) -> bool:
    return s.isdigit()


def _append_a_share_suffix(code: str) -> str:
    """Append exchange suffix for Chinese A-share numeric codes.

    Raises ValueError if the prefix is not recognised or the code length
    is not 6 digits (the standard A-share format).
    """
    if len(code) != 6:
        raise ValueError(
            f"A 股代码应为 6 位数字，收到 {len(code)} 位: 「{code}」。"
            "请检查输入是否有误。"
        )
    for prefixes, suffix in _A_SHARE_SUFFIX_RULES:
        if any(code.startswith(p) for p in prefixes):
            return code + suffix
    raise ValueError(
        f"无法识别 A 股代码前缀: {code}。"
        "请输入完整的带后缀代码（如 600901.SS）或有效的 6 位数字代码。"
    )


# ---------------------------------------------------------------------------
# Chinese name resolution (HiThink official search → akshare fallback)
# ---------------------------------------------------------------------------


def _resolve_chinese_name_hithink(name: str) -> str | None:
    """Resolve a Chinese company name via HiThink ``meta/tickers/search``.

    Official disambiguation endpoint — preferred over the akshare full-table
    fuzzy match when ``HITHINK_FINANCE_API_KEY`` is configured. Returns the
    bare 6-digit code, or None on any error / no A-share match.
    """
    try:
        from tradingagents.dataflows import hithink_common

        if not hithink_common.is_available():
            return None
        items = hithink_common.search_tickers(name, asset_type="a-share", limit=10)
    except Exception:
        return None

    a_share_items = [it for it in items if it.get("asset_type") == "a-share"]

    def _valid_code(value: object) -> str | None:
        # The caller feeds the result into suffix inference which requires a
        # bare 6-digit code; a malformed hit (e.g. a thscode like
        # "600519.SH") would otherwise turn a good lookup into a hard crash.
        if isinstance(value, str) and len(value) == 6 and value.isdigit():
            return value
        return None

    # Exact name match wins; otherwise take the API's top-ranked A-share hit.
    for it in a_share_items:
        if it.get("name") == name:
            return _valid_code(it.get("ticker"))
    for it in a_share_items:
        code = _valid_code(it.get("ticker"))
        if code:
            return code
    return None


def _build_name_map() -> dict[str, str]:
    """Fetch full A-share name->ticker mapping from akshare."""
    try:
        import akshare as ak
    except ImportError as exc:
        raise ImportError(
            "解析中文股票名称需要 akshare。请执行: uv pip install akshare"
        ) from exc

    df = ak.stock_info_a_code_name()
    # df columns: ["code", "name"]
    mapping = {}
    for _, row in df.iterrows():
        name = str(row["name"]).strip()
        code = str(row["code"]).strip()
        if name and code:
            mapping[name] = code
    return mapping


def _resolve_chinese_name(name: str) -> str:
    """Resolve a Chinese company name to its 6-digit numeric code.

    Tries the HiThink official search first (when configured), then falls
    back to the local JSON cache refreshed from akshare on cache miss,
    with fuzzy matching as the last resort.
    """
    import difflib

    hithink_code = _resolve_chinese_name_hithink(name)
    if hithink_code:
        return hithink_code

    cache = _load_name_cache()

    def _find(name: str, data: dict) -> str | None:
        # Exact match
        if name in data:
            return data[name]
        # Partial match (input is substring of a cached name)
        for full_name, code in data.items():
            if name in full_name:
                return code
        # Reverse partial match (cached name is substring of input)
        for full_name, code in data.items():
            if full_name in name:
                return code
        # Fuzzy match — best ratio > 0.6
        best_match = difflib.get_close_matches(name, data.keys(), n=1, cutoff=0.6)
        if best_match:
            return data[best_match[0]]
        return None

    result = _find(name, cache)
    if result is not None:
        return result

    # Cache miss — rebuild from akshare
    cache = _build_name_map()
    _save_name_cache(cache)

    result = _find(name, cache)
    if result is not None:
        return result

    # Suggest closest names for the error message
    suggestions = difflib.get_close_matches(name, cache.keys(), n=3, cutoff=0.4)
    hint = f" 您是否想输入: {', '.join(suggestions)}?" if suggestions else ""
    raise ValueError(f"未找到中文名称对应的股票代码: {name}。{hint}")


# ---------------------------------------------------------------------------
# A-share name lookup (akshare, authoritative for Chinese equities)
# ---------------------------------------------------------------------------


_A_SHARE_NAME_BY_CODE: dict[str, str] | None = None


def _fetch_company_name_from_akshare(ticker: str) -> str | None:
    """Look up company name by code using akshare (East Money source).

    Only applies to A-share tickers (suffix .SS, .SZ, .BJ). Returns None
    for non-A-share tickers or on any error. The result is cached so the
    akshare table is fetched at most once per process.
    """
    suffix = ticker.split(".")[-1].upper() if "." in ticker else ""
    if suffix not in ("SS", "SZ", "BJ"):
        return None
    bare = ticker.split(".")[0]

    global _A_SHARE_NAME_BY_CODE
    if _A_SHARE_NAME_BY_CODE is None:
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            _A_SHARE_NAME_BY_CODE = {  # pragma: no cover  -- driven only when akshare returns a valid DataFrame (tests currently mock as ImportError / empty)
                str(row["code"]).strip(): str(row["name"]).strip()
                for _, row in df.iterrows()
            }
        except Exception:
            _A_SHARE_NAME_BY_CODE = {}  # cache empty so we don't retry

    return _A_SHARE_NAME_BY_CODE.get(bare)


def _fetch_company_name_from_hithink(ticker: str) -> str | None:
    """Look up A-share company name by code via HiThink ``meta/tickers/search``.

    Only applies to A-share tickers (suffix .SS, .SZ, .BJ). Returns None for
    non-A-share tickers, when no API key is configured, or on any error.
    Rescues the case where the akshare full-table fetch fails: a single
    targeted search instead of pulling the whole code-name table.
    """
    suffix = ticker.split(".")[-1].upper() if "." in ticker else ""
    if suffix not in ("SS", "SZ", "BJ"):
        return None
    bare = ticker.split(".")[0]

    try:
        from tradingagents.dataflows import hithink_common

        if not hithink_common.is_available():
            return None
        items = hithink_common.search_tickers(bare, asset_type="a-share", limit=10)
    except Exception:
        return None

    for it in items:
        if it.get("ticker") == bare and it.get("asset_type") == "a-share":
            return it.get("name") or None
    return None


# ---------------------------------------------------------------------------
# yfinance validation / company name fetch
# ---------------------------------------------------------------------------


def _fetch_company_name_from_db(ticker: str) -> str | None:
    """Query the local quant_core.db for the company name.

    The stock_list table stores bare codes (e.g. '002241') without suffixes.
    Returns None if the ticker is not found or the DB is unavailable.
    """
    db_path = os.path.expanduser("~/Code/quant_data/quant_core.db")
    if not os.path.exists(db_path):
        return None
    bare = ticker.split(".")[0]
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=3)
        row = conn.execute(
            "SELECT name FROM stock_list WHERE code = ? LIMIT 1", (bare,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _fetch_company_name(ticker: str) -> str | None:
    """Use yfinance to fetch the company's longName.

    Returns None on any error so the caller can fall back gracefully.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return info.get("longName") or info.get("shortName")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_ticker(user_input: str, *, fetch_name: bool = True) -> dict[str, str]:
    """Resolve arbitrary user input to a normalised ticker and company name.

    Args:
        user_input: raw ticker or (Chinese) company name.
        fetch_name: when False, skip the company-name lookup entirely and
            return ``company_name`` as an empty string. Name resolution hits
            live vendor endpoints (akshare/yfinance) and can stall for
            minutes on a bad network — pure filesystem/coding callers (e.g.
            report-folder matching) should pass False.

    Returns a dict with keys:
        - "ticker": exchange-qualified ticker (e.g. "600901.SS")
        - "company_name": company long name from yfinance, or empty string
    """
    raw = user_input.strip()
    if not raw:
        raise ValueError("股票代码/名称不能为空")

    # 1. Already has a known exchange suffix -> extract and pass through
    # Use a capturing group so we pull out just the ticker, not surrounding
    # LLM hallucination like "极速查询到的证券代码为 601899.SS".
    suffix_match = re.search(
        r"\b([A-Z0-9._\-^]+\.(SS|SZ|BJ|HK|TO|L|T|F|AS|BR|MI|ST|PA|SW|TW|VX|SA))\b",
        raw,
        re.IGNORECASE,
    )

    def _resolve_name(ticker: str) -> str:
        """Resolve company name: akshare > local DB > hithink > yfinance."""
        if not fetch_name:
            return ""
        return (
            _fetch_company_name_from_akshare(ticker)
            or _fetch_company_name_from_db(ticker)
            or _fetch_company_name_from_hithink(ticker)
            or _fetch_company_name(ticker)
            or ""
        )

    if suffix_match:
        ticker = suffix_match.group(1).upper()
        return {"ticker": ticker, "company_name": _resolve_name(ticker)}

    # 2. Pure numeric -> A-share suffix auto-append
    if _is_numeric_code(raw):
        ticker = _append_a_share_suffix(raw).upper()
        return {"ticker": ticker, "company_name": _resolve_name(ticker)}

    # 3. Contains Chinese characters -> resolve via akshare
    if re.search(r"[一-鿿]", raw):
        numeric_code = _resolve_chinese_name(raw)
        ticker = _append_a_share_suffix(numeric_code).upper()
        return {"ticker": ticker, "company_name": _resolve_name(ticker)}

    # 4. International ticker (e.g. AAPL, TSLA, BNS.TO)
    ticker = raw.upper()
    return {"ticker": ticker, "company_name": _resolve_name(ticker)}
