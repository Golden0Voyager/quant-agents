import re
from datetime import date, datetime, timedelta
from typing import Annotated

import pandas as pd

SavePathType = Annotated[str, "File path to save data. If None, data is not saved."]

# Tickers can contain letters, digits, dot, dash, underscore, caret
# (index symbols like ^GSPC), equals (futures like GC=F), and plus
# (forex/CFD symbols like XAUUSD+). None of these enable directory
# traversal, so the value never escapes a containing directory when
# interpolated into a path. Anything else is rejected.
_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9._\-\^=+]+$")

# Patterns to extract a clean ticker from LLM-hallucinated text
# (e.g. "极速查询到的证券代码为 601899.SS")
_A_SHARE_TICKER_RE = re.compile(r"\b(\d{6}\.(?:SS|SZ|BJ))\b", re.IGNORECASE)
_GENERIC_TICKER_RE = re.compile(
    r"\b([A-Za-z0-9._\-\^]+\.(?:SS|SZ|BJ|HK|TO|L|T|F|AS|BR|MI|ST|PA|SW|TW|VX|SA))\b",
    re.IGNORECASE,
)


def _sanitize_ticker(value: str) -> str | None:
    """Extract a clean ticker from a potentially polluted string.

    LLMs sometimes wrap tickers in explanatory text (e.g.
    '极速查询到的证券代码为 601899.SS'). This function attempts to
    extract the actual ticker using known patterns.
    """
    m = _A_SHARE_TICKER_RE.search(value)
    if m:
        return m.group(1).upper()

    m = _GENERIC_TICKER_RE.search(value)
    if m:
        return m.group(1).upper()

    return None


def safe_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    Tickers come from user CLI input or from LLM tool calls, both of which
    can be influenced by attacker-controlled content (e.g. prompt injection
    embedded in fetched news). Without validation, a value like
    ``"../../../etc/foo"`` flows into ``os.path.join`` / ``Path /`` and
    escapes the configured cache, checkpoint, or results directory.

    When the value does not directly match the allowed pattern (e.g. LLM
    hallucinates explanatory text around the ticker), this function attempts
    to *extract* the ticker before rejecting.

    Returns the cleaned ticker when valid; raises ``ValueError`` otherwise.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")

    # Direct match — fast path
    if _TICKER_PATH_RE.fullmatch(value) and len(value) <= max_len and set(value) != {"."}:
        return value

    # Try to extract a clean ticker from polluted input (e.g. LLM hallucination)
    cleaned = _sanitize_ticker(value)
    if cleaned and _TICKER_PATH_RE.fullmatch(cleaned) and len(cleaned) <= max_len:
        return cleaned

    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    raise ValueError(
        f"ticker contains characters not allowed in a filesystem path: {value!r}"
    )


def save_output(data: pd.DataFrame, tag: str, save_path: SavePathType = None) -> None:
    if save_path:
        data.to_csv(save_path, encoding="utf-8")
        print(f"{tag} saved to {save_path}")


def get_current_date():
    return date.today().strftime("%Y-%m-%d")


def decorate_all_methods(decorator):
    def class_decorator(cls):
        for attr_name, attr_value in cls.__dict__.items():
            if callable(attr_value):
                setattr(cls, attr_name, decorator(attr_value))
        return cls

    return class_decorator


def get_next_weekday(date):

    if not isinstance(date, datetime):
        date = datetime.strptime(date, "%Y-%m-%d")

    if date.weekday() >= 5:
        days_to_add = 7 - date.weekday()
        next_weekday = date + timedelta(days=days_to_add)
        return next_weekday
    else:
        return date
