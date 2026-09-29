"""StockTwits public symbol-stream fetcher.

StockTwits exposes a per-symbol message stream at
``api.stocktwits.com/api/2/streams/symbol/{ticker}.json`` that requires no
API key, no OAuth, and no registration. Each message includes a
user-labeled sentiment field (``Bullish``/``Bearish``/null), the message
body, timestamp, and posting user.

The function is deliberately self-contained: short timeout, graceful
degradation on any HTTP or parse failure, and a string return type so
the calling agent gets a uniform interface regardless of whether the
network call succeeded.
"""

from __future__ import annotations

import html
import http.client
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"


def fetch_stocktwits_messages(
    ticker: str, limit: int = 30, timeout: float = 10.0, days_back: int = 7,
    screen=None,
) -> str:
    """Fetch recent StockTwits messages for ``ticker`` and return them as a
    formatted plaintext block ready for prompt injection.

    ``days_back`` filters out messages older than N days (default 7) to
    prevent stale sentiment from influencing backdated analyses.

    ``screen`` is an optional post screen (see
    :mod:`tradingagents.dataflows.post_gate`): it receives the raw message
    bodies and returns one keep flag per body plus a note line. Off-topic
    messages are dropped from the block; the note heads the summary.

    Returns a placeholder string when the endpoint is unreachable, the
    symbol has no messages, or the response shape is unexpected — the
    caller never has to special-case None or exceptions.
    """
    url = _API.format(ticker=ticker.upper())
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        # OSError covers URLError/TimeoutError/connection resets; HTTPException
        # covers chunked-transfer errors (IncompleteRead/BadStatusLine, #1024).
        logger.warning("StockTwits fetch failed for %s: %s", ticker, exc)
        return f"<stocktwits unavailable: {type(exc).__name__}>"

    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not messages:
        return f"<no StockTwits messages found for ${ticker.upper()}>"

    # Filter out messages older than days_back
    if days_back is not None and days_back > 0:
        cutoff = datetime.now(UTC) - timedelta(days=days_back)
        filtered = []
        for m in messages:
            created_str = m.get("created_at", "")
            try:
                # StockTwits timestamps are ISO 8601, e.g. 2024-01-15T10:30:00Z
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if created_dt >= cutoff:
                    filtered.append(m)
            except Exception:  # pragma: no cover  -- fail-open: keep messages with unparseable timestamps
                # If parsing fails, keep the message (fail-open)
                filtered.append(m)
        messages = filtered

    selected = messages[:limit]
    note = ""
    if screen is not None and selected:
        # The screen judges raw bodies (pre-truncation); the formatted block
        # only ever contains kept messages.
        bodies = [html.unescape(m.get("body") or "") for m in selected]
        keep, note = screen(bodies)
        selected = [m for m, k in zip(selected, keep, strict=True) if k]
        if not selected:
            return (
                f"<no StockTwits messages about ${ticker.upper()} remain "
                f"after screening; {len(bodies)} fetched>"
            )

    lines = []
    bullish = bearish = unlabeled = 0
    for m in selected:
        created = m.get("created_at", "")
        user = (m.get("user") or {}).get("username", "?")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
        body = html.unescape(m.get("body") or "").replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "…"

        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"
        lines.append(f"[{created} · @{user} · {tag}] {body}")

    total = bullish + bearish + unlabeled
    bull_pct = round(100 * bullish / total) if total else 0
    bear_pct = round(100 * bearish / total) if total else 0
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) · "
        f"Bearish: {bearish} ({bear_pct}%) · "
        f"Unlabeled: {unlabeled} · "
        f"Total: {total} most-recent messages"
    )
    if note:
        summary = note + "\n" + summary
    return summary + "\n\n" + "\n".join(lines)
