"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Chinese → English rating mapping for multilingual output support.
_CN_TO_EN_RATING: dict[str, str] = {
    "买入": "Buy", "做多": "Buy", "强力买入": "Buy", "加仓": "Buy",
    "增持": "Overweight", "超配": "Overweight", "加码": "Overweight",
    "持有": "Hold", "观望": "Hold", "中性": "Hold", "持平": "Hold", "持仓": "Hold",
    "减持": "Underweight", "低配": "Underweight", "减仓": "Underweight", "减码": "Underweight",
    "卖出": "Sell", "做空": "Sell", "清仓": "Sell", "止损": "Sell", "强力卖出": "Sell",
}

# Matches "Rating: X" / "rating - X" / "Rating: **X**" / "评级：X" / "决策：X" — tolerates
# markdown bold wrappers and either a colon or hyphen separator, in English or Chinese.
_RATING_LABEL_RE = re.compile(
    r"(?:rating|decision|评级|建议|决策|结论|评级结果).*?[:：\-][\s*]*([\w一-鿿]+)", re.IGNORECASE)


def normalize_rating_label(raw: str) -> str | None:
    """Return the canonical 5-tier rating if ``raw`` is a valid rating label.

    Accepts both English tier words (case-insensitive) and Chinese synonyms
    from :data:`_CN_TO_EN_RATING`. Returns ``None`` for anything that is not
    a rating word — e.g. a ticker code (``002594``) that a lax regex captured
    from a heading like ``### 最终交易决策：**002594.SZ``. Callers use this to
    validate *candidates* before trusting one, instead of letting
    :func:`parse_rating` silently default to ``Hold``.
    """
    if not raw:
        return None
    clean = raw.strip("*:.,`'\" 　")
    if not clean:
        return None
    lower = clean.lower()
    if lower in _RATING_SET:
        return clean.capitalize()
    if clean in _CN_TO_EN_RATING:
        return _CN_TO_EN_RATING[clean]
    return None


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Three-pass strategy:
    1. Look for an explicit "Rating: X" / "评级：X" label (tolerant of markdown bold).
    2. Map common Chinese rating words to English equivalents.
    3. Fall back to the first 5-tier English rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m:
            raw = m.group(1).strip("*:.,")
            lower = raw.lower()
            if lower in _RATING_SET:
                return raw.capitalize()
            if raw in _CN_TO_EN_RATING:
                return _CN_TO_EN_RATING[raw]

    for line in text.splitlines():
        for word in line.split():
            clean = word.strip("*:.,")
            if clean in _CN_TO_EN_RATING:
                return _CN_TO_EN_RATING[clean]

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default
