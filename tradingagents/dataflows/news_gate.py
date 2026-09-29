import json
import logging
import os
from datetime import datetime, timezone

from tradingagents.dataflows.config import get_config
from tradingagents.llm_clients.typesafe_client import TypeSafeNewsGate

logger = logging.getLogger(__name__)


def akshare_row_to_article(row) -> dict:
    return {
        "title": str(row.get("新闻标题", "")),
        "body": str(row.get("新闻内容", "")),
        "publisher": str(row.get("文章来源", "")),
        "pub_date": str(row.get("发布时间", "")),
        "link": str(row.get("新闻链接", "")),
    }


def apply_news_gate(
    articles: list[dict],
    symbol: str,
    company_name: str,
    date_range: str = "",
    *,
    config_prefix: str = "jev_news_gate",
    question: str | None = None,
    criteria: dict[str, str] | None = None,
    body_trunc: int | None = None,
) -> tuple[list[int], list[int]] | None:
    config = get_config()
    if not config.get(f"{config_prefix}_enabled") or not articles:
        return None

    shadow = config.get(f"{config_prefix}_shadow", True)
    # 正式模式下 keep_floor 用来省 API 调用；shadow 模式下它必须让路——
    # shadow 的目的正是收集小样本的分数分布来校准这个阈值，跳过就没有数据。
    if not shadow and len(articles) <= config.get(f"{config_prefix}_keep_floor", 5):
        return None

    gate = TypeSafeNewsGate(config, config_prefix=config_prefix)
    criteria_tuple: tuple[str, str] | None = None
    if criteria is not None:
        criteria_tuple = (criteria["true"], criteria["false"])
    scores = gate.score_articles(
        articles,
        {"ticker": symbol, "company_name": company_name, "date_range": date_range},
        question=question,
        criteria=criteria_tuple,
        body_trunc=body_trunc,
    )
    if scores is None:
        return None

    threshold = config.get(f"{config_prefix}_threshold", 0.5)
    kept = [i for i, s in enumerate(scores) if s >= threshold]
    demoted = [i for i, s in enumerate(scores) if s < threshold]
    _log_decision(config, symbol, articles, scores, kept, demoted, shadow=shadow)

    if shadow:
        logger.info("Jev gate (shadow) %s: would demote %d/%d articles",
                    symbol, len(demoted), len(articles))
        return None
    return kept, demoted


def _log_decision(config, symbol, articles, scores, kept, demoted, shadow, source="news"):
    path = os.path.join(config.get("data_cache_dir", "."), "jev_gate_decisions.jsonl")
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 — datetime.UTC needs 3.11+, floor is 3.10
        "symbol": symbol,
        "source": source,
        "shadow": shadow,
        "articles": [
            {"title": a["title"], "score": s, "kept": i in kept}
            for i, (a, s) in enumerate(zip(articles, scores, strict=True))
        ],
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Jev gate decision log write failed: %s", exc)
