import logging
import os

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.typesafe.ai/v1/systemone"
_BODY_TRUNC = 500

_QUESTION = (
    "Does this article contain material, decision-relevant information about "
    "`state.company_name`'s business, stock, or industry that an investor "
    "should not miss?"
)

_CRITERIA_TRUE = (
    "Earnings or guidance changes, major contracts or orders, M&A, regulatory "
    "actions, management changes, significant industry policy directly "
    "affecting the company, competitive developments that describe the "
    "company's own strategic or market position (including rivals' moves in "
    "markets where the company competes), or substantive analysis with new "
    "company-specific information"
)

_CRITERIA_FALSE = (
    "The company is mentioned but the article provides no new "
    "decision-relevant information: routine fund-flow data recaps, "
    "shareholder-count updates, margin-trading table entries, or generic "
    "market or sector roundups where the company appears only as a name in a "
    "list or a data table, with no description of its strategy, competitive "
    "position, or business developments"
)

_ANN_QUESTION = (
    "Does this announcement contain material, decision-relevant information "
    "about `state.company_name` that an investor should not miss?"
)

_ANN_CRITERIA_TRUE = (
    "Financial results or guidance, major contracts or orders, M&A or "
    "restructuring, equity events (placement, buyback, share incentive, "
    "shareholder increase/decrease), regulatory or litigation outcomes, "
    "production or capacity changes, or substantive management commentary "
    "on business segments"
)

_ANN_CRITERIA_FALSE = (
    "Routine procedural notices: meeting convocation or resolutions with no "
    "new financial substance, record-date or transfer-suspension notices, "
    "investor-relations activity logs that only restate previously disclosed "
    "figures, standard bylaw or board-committee formalities, and other "
    "announcements carrying no new decision-relevant information"
)


class TypeSafeNewsGate:
    def __init__(self, config: dict, *, config_prefix: str = "jev_news_gate"):
        self._api_key = os.getenv(config.get("jev_api_key_env", "TYPESAFE_API_KEY"), "")
        self._model = config.get("jev_model", "jev-latest")
        self._timeout = config.get(f"{config_prefix}_timeout", 10)
        self._max_articles = config.get(f"{config_prefix}_max_articles", 30)

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def score_articles(
        self,
        articles: list[dict],
        context: dict,
        *,
        question: str | None = None,
        criteria: tuple[str, str] | None = None,
        body_trunc: int | None = None,
    ) -> list[float] | None:
        if not self.available or not articles:
            return None
        judged = articles[: self._max_articles]
        criteria_true, criteria_false = criteria or (_CRITERIA_TRUE, _CRITERIA_FALSE)
        trunc = _BODY_TRUNC if body_trunc is None else body_trunc
        questions = {
            f"article_{i}": {
                "type": "noul",
                "instructions": {
                    "article": {
                        "title": a["title"],
                        "body": a["body"][:trunc],
                        "publisher": a["publisher"],
                        "pub_date": a["pub_date"],
                    },
                    "question": _QUESTION if question is None else question,
                },
                "criteria": {"true": criteria_true, "false": criteria_false},
            }
            for i, a in enumerate(judged)
        }
        try:
            resp = requests.post(
                _API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "state": context, "questions": questions},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            answers = resp.json()["answers"]
            scores = [answers[f"article_{i}"]["noul"] for i in range(len(judged))]
        except Exception as exc:  # noqa: BLE001 — 门控故障绝不外抛
            logger.warning("Jev news gate scoring failed: %s", exc)
            return None
        scores.extend([1.0] * (len(articles) - len(judged)))
        return scores
