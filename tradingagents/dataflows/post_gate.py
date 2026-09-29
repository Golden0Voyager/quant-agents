"""Jev screening for raw social posts (StockTwits / Reddit).

Upstream parity: TauricResearch/TradingAgents #1376 screens each social post
with TypeSafe's Jev before it reaches the Sentiment Analyst — posts that are
clearly about something else are dropped, and the block says how many were
kept. This fork adapts that idea to the local gate infrastructure
(:class:`~tradingagents.llm_clients.typesafe_client.TypeSafeNewsGate`,
config family ``jev_post_gate_*``, shadow mode, decision logging) instead of
porting the upstream standalone client.

The screen is a callable matching the ``screen`` parameter of the social
fetchers::

    screen(posts: list[str]) -> tuple[list[bool], str]

— one keep flag per post plus a note line for the top of the source's block.
Without ``jev_post_gate_enabled`` (or without a TypeSafe key, or in shadow
mode) the screen is None and fetchers return posts unscreened; any scoring
failure keeps every post and the note says screening was unavailable
(fail-open, mirroring the news gate).
"""

import logging

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.news_gate import _log_decision
from tradingagents.llm_clients.typesafe_client import TypeSafeNewsGate

logger = logging.getLogger(__name__)

# A post is dropped only on a clear "not about it"; the uncertain middle
# stays (upstream #1376 uses the same 0.3 cut).
_DEFAULT_THRESHOLD = 0.3


def apply_post_gate(
    posts: list[str],
    symbol: str,
    company_name: str,
    *,
    source: str = "post",
) -> tuple[list[int], list[int]] | None:
    """Score raw posts for instrument relevance; return kept/demoted indices.

    Returns None when the gate is disabled, unconfigured, in shadow mode, or
    scoring fails — callers then keep every post. Demotion decisions are
    logged to ``jev_gate_decisions.jsonl`` either way so the threshold can be
    calibrated from shadow-mode data.
    """
    config = get_config()
    if not config.get("jev_post_gate_enabled") or not posts:
        return None

    shadow = config.get("jev_post_gate_shadow", True)
    if not shadow and len(posts) <= config.get("jev_post_gate_keep_floor", 5):
        return None

    gate = TypeSafeNewsGate(config, config_prefix="jev_post_gate")
    scores = gate.score_posts(
        posts,
        {"ticker": symbol, "company_name": company_name},
    )
    if scores is None:
        return None

    threshold = config.get("jev_post_gate_threshold", _DEFAULT_THRESHOLD)
    kept = [i for i, s in enumerate(scores) if s >= threshold]
    demoted = [i for i, s in enumerate(scores) if s < threshold]
    _log_decision(
        config, symbol,
        [{"title": p[:80]} for p in posts], scores, kept, demoted,
        shadow=shadow, source=source,
    )

    if shadow:
        logger.info(
            "Jev post gate (shadow) %s: would demote %d/%d posts",
            symbol, len(demoted), len(posts),
        )
        return None
    return kept, demoted


def make_post_screen(symbol: str, company_name: str = "", *, source: str = "post"):
    """Build a social-post screen for the fetchers, or None when inactive.

    Resolves the company name best-effort when not supplied; the returned
    closure keeps every post and explains itself in the note whenever the
    gate cannot run (fail-open).
    """
    config = get_config()
    if not config.get("jev_post_gate_enabled"):
        return None
    if not company_name:
        try:
            from tradingagents.ticker_resolver import resolve_ticker

            company_name = (resolve_ticker(symbol).get("company_name") or "").strip()
        except Exception:  # noqa: BLE001 — name lookup must never break screening
            company_name = ""
    if not company_name:
        return None
    instrument = f"{company_name} ({symbol})"

    def screen(posts: list[str]) -> tuple[list[bool], str]:
        try:
            result = apply_post_gate(posts, symbol, company_name, source=source)
        except Exception as exc:  # noqa: BLE001 — screening must never break a fetch
            logger.warning("Jev post screen crashed for %s: %s", instrument, exc)
            result = None
        if result is None:
            return [True] * len(posts), ""
        kept_idx, _ = result
        keep = [i in kept_idx for i in range(len(posts))]
        kept_n = len(kept_idx)
        note = (
            f"Screened by Jev: {kept_n} of the {len(posts)} posts fetched are "
            f"about {instrument}; {len(posts) - kept_n} off-topic post(s) dropped."
        )
        return keep, note

    return screen
