"""Output-layer time-scope guard for analyst reports.

LLM-generated reports occasionally cite news dated *after* the analysis
date (tool results stamped with the fetch date, stale caches relabelled,
or hallucinated future dates). Downstream readers treat every date in an
analyst report as evidence from the analysis window, so this module drops
future-dated lines at the output layer — after the LLM has finished, right
before the report is written into graph state.
"""

import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Explicit full dates only: 2026-09-24, 2026/9/24, 2026年9月24日.
# Deliberately NOT matching bare "9月24日" / "Sep 24" — without a year they
# cannot be proven future-dated, and yearless dates in Chinese news are
# almost always relative to the analysis window anyway.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})/(\d{1,2})/(\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
)


def _first_future_date(line: str, analysis_date: date) -> date | None:
    """Return the earliest explicit full date in ``line`` after the analysis date."""
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(line):
            try:
                parsed = date(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                )
            except ValueError:
                continue  # e.g. 2026-02-30 — malformed, not future-dated
            if parsed > analysis_date:
                return parsed
    return None


def drop_future_dated_lines(
    text: str, analysis_date: date | datetime | str
) -> tuple[str, list[str]]:
    """Drop lines citing an explicit full date later than ``analysis_date``.

    A line carrying a future date is removed whole: blanking just the date
    would leave the future event's claims standing, and editing inside a
    Markdown table row would break column alignment for every other row.

    Returns ``(kept_text, dropped_lines)``. Dropped lines are returned for
    logging/auditing; the caller decides whether to surface them.
    """
    if isinstance(analysis_date, datetime):
        analysis_date = analysis_date.date()
    elif isinstance(analysis_date, str):
        analysis_date = datetime.strptime(analysis_date.strip()[:10], "%Y-%m-%d").date()

    if not text:
        return text, []

    kept: list[str] = []
    dropped: list[str] = []
    for line in text.splitlines(keepends=True):
        future = _first_future_date(line, analysis_date)
        if future is None:
            kept.append(line)
        else:
            dropped.append(line)
            logger.info(
                "Dropped future-dated line (%s > %s): %s",
                future.isoformat(),
                analysis_date.isoformat(),
                line.strip()[:120],
            )

    return "\n".join(kept), dropped
