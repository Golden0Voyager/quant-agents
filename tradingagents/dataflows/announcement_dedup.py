"""Identity-key deduplication for announcement rows (东财公告去重).

Pure functions, zero network. Runs *before* bodies are fetched, so it can only
rely on identity keys (``_art_code`` → ``网址`` → ``公告标题``+``公告日期``),
never on body content (spec v1.1, BLOCKER-2).

Two rules:
  1. exact duplicates collapse to the row with the newest ``公告日期``;
  2. series types (e.g. 调研活动) are capped at ``max_per_type`` by date.

Fail-open by construction: rows are plain dicts and no exception path exists.
"""
from __future__ import annotations


def dedup_announcements(
    rows: list[dict],
    *,
    max_per_type: int,
    series_types: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    """Return ``(kept_rows, dropped_rows)`` preserving input order of survivors.

    前置契约：调用方须先把 art_code 写进每行的 ``_art_code``（若缺失则回退
    到 网址 / 标题+日期），使本函数能在抓取正文之前完成去重。

    Rule 1 — exact-dup: rows sharing an identity key collapse to the newest
    ``公告日期`` (ties keep the first occurrence). Rows with all-empty
    identity keys are never considered duplicates.

    Rule 2 — series cap: among survivors whose ``公告类型`` is in
    ``series_types``, keep only the newest ``max_per_type`` by ``公告日期``.
    """
    if not rows:
        return [], []
    series_set = frozenset(series_types)

    kept_idx: list[int] = []
    dropped_idx: list[int] = []
    best_by_key: dict[tuple[str, ...], tuple[str, int]] = {}

    for index, row in enumerate(rows):
        key = _identity_key(row)
        if key is None:
            kept_idx.append(index)
            continue
        date = _ann_date(row)
        best = best_by_key.get(key)
        if best is None:
            best_by_key[key] = (date, index)
            kept_idx.append(index)
            continue
        best_date, best_index = best
        if date > best_date:
            # Newer duplicate replaces the previous best.
            kept_idx.remove(best_index)
            dropped_idx.append(best_index)
            best_by_key[key] = (date, index)
            kept_idx.append(index)
        else:
            # Older or equal duplicate: keep the first occurrence.
            dropped_idx.append(index)

    if series_set and max_per_type >= 0:
        series = [
            (index, _ann_date(rows[index]))
            for index in kept_idx
            if isinstance(rows[index].get("公告类型"), str)
            and rows[index].get("公告类型") in series_set
        ]
        series.sort(key=lambda item: item[1], reverse=True)
        for index, _ in series[max_per_type:]:
            kept_idx.remove(index)
            dropped_idx.append(index)

    kept = [rows[index] for index in kept_idx]
    dropped = [rows[index] for index in sorted(dropped_idx)]
    return kept, dropped


def _ann_date(row: dict) -> str:
    """Return ``公告日期`` as a comparable string ("" when absent)."""
    value = row.get("公告日期")
    return str(value) if value else ""


def _identity_key(row: dict) -> tuple[str, ...] | None:
    """Return the row's identity key by priority, or ``None`` when all empty.

    Priority: ``_art_code`` → ``网址`` → (``公告标题``, ``公告日期``).
    """
    art_code = row.get("_art_code")
    if isinstance(art_code, str) and art_code.strip():
        return ("art_code", art_code.strip())
    url = row.get("网址")
    if isinstance(url, str) and url.strip():
        return ("url", url.strip())
    title = row.get("公告标题")
    date = _ann_date(row)
    if isinstance(title, str) and title.strip() and date:
        return ("title_date", title.strip(), date)
    return None
