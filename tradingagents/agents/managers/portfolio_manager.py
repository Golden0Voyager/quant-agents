"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import logging
import re

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_or_build_data_quality_summary,
)
from tradingagents.agents.utils.structured import (
    FALLBACK_MARKER,
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot

logger = logging.getLogger(__name__)

# Maximum allowed deviation between a quoted entry/stop and the verified
# snapshot Close. Mirrors the "~25%" rule stated in the PM prompt; anything
# beyond it is stripped deterministically (see _enforce_snapshot_tolerance).
ENTRY_STOP_TOLERANCE = 0.25


def _extract_snapshot_close(snapshot: str | None) -> float | None:
    """Parse the latest verified Close price out of the snapshot markdown table.

    The snapshot produced by ``build_verified_market_snapshot`` renders the
    latest OHLCV row in a markdown table; the Close row uses ``| Close | <n> |``.
    Returned as a float so the caller can compare against the Trader's quoted
    entry / stop and reject obvious miscalibrations. Returns ``None`` when the
    snapshot is unavailable or the table is missing.
    """
    if not snapshot:
        return None
    match = re.search(r"\|\s*Close\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|", snapshot)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _enforce_snapshot_tolerance(
    decision: PortfolioDecision, snapshot_close: float | None
) -> tuple[PortfolioDecision, str | None]:
    """Null out entry/stop that deviate from the verified close by more than 25%.

    Deterministic backstop for the tolerance rule stated in the prompt: the
    decision itself is kept, only the miscalibrated field is removed. Returns
    the (possibly mutated) decision plus a markdown note describing the
    removals, or ``None`` when nothing was stripped. Skips validation entirely
    when no verified close is available.
    """
    if not snapshot_close:
        return decision, None
    stripped: list[str] = []
    for field_name, label in (("entry_price", "entry price"), ("stop_loss", "stop-loss")):
        value = getattr(decision, field_name)
        if value is None:
            continue
        if abs(value - snapshot_close) / snapshot_close > ENTRY_STOP_TOLERANCE:
            logger.warning(
                "PM stripped %s=%s: deviates from verified close %s by more than %.0f%%",
                field_name, value, snapshot_close, ENTRY_STOP_TOLERANCE * 100,
            )
            setattr(decision, field_name, None)
            stripped.append(label)
    if not stripped:
        return decision, None
    note = (
        f"**Note**: {' and '.join(stripped)} removed: the quoted level deviated "
        f"from the verified market close ({snapshot_close}) by more than 25%."
    )
    return decision, note


# Bullish/neutral/bearish buckets of the 5-tier rating scale and the
# Trader's 3-tier action scale, used to flag direction conflicts.
_RATING_DIRECTION = {
    "buy": "bullish",
    "overweight": "bullish",
    "hold": "neutral",
    "underweight": "bearish",
    "sell": "bearish",
}
_ACTION_DIRECTION = {"buy": "bullish", "hold": "neutral", "sell": "bearish"}

# Treat target and stop as "equal" when within 0.1% — a target a hair above
# the stop is still geometrically meaningless.
_PRICE_EQUAL_TOLERANCE = 0.001
# Risk/reward below this is flagged in the note: cut position size instead of
# tightening the stop (mirrors the Trader prompt's 1.5x-ATR guidance).
_MIN_RISK_REWARD = 1.5

_TRADER_ACTION_RE = re.compile(r"FINAL TRANSACTION PROPOSAL:\s*\*\*([A-Za-z]+)\*\*")


def _extract_trader_action(trader_plan: str | None) -> str | None:
    """Pull the Trader's FINAL TRANSACTION PROPOSAL action from the rendered plan."""
    if not trader_plan:
        return None
    match = _TRADER_ACTION_RE.search(trader_plan)
    return match.group(1).lower() if match else None


def _enforce_decision_consistency(
    decision: PortfolioDecision, trader_plan: str | None
) -> tuple[PortfolioDecision, str | None]:
    """Deterministic backstops on the PM's numeric decision fields.

    - ``price_target`` at or below ``stop_loss`` (within 0.1%) is nulled:
      a target below the stop is geometrically meaningless and showed up in
      batch audits as a copied-over number.
    - When entry/stop/target are coherent (long setup), risk/reward is
      recomputed in code and appended as a note, so the figure on record is
      arithmetic rather than model recall. R/R below 1.5 flags that position
      size — not the stop — should be cut.
    - A rating whose direction contradicts the Trader's action gets an
      advisory note (the rating itself is kept: the PM may legitimately
      override the Trader, but the override must be visible).

    Returns the (possibly mutated) decision plus a combined markdown note, or
    ``None`` when nothing needed attention.
    """
    notes: list[str] = []

    target, entry, stop = decision.price_target, decision.entry_price, decision.stop_loss

    if (
        target is not None
        and stop is not None
        and stop > 0
        and target <= stop * (1 + _PRICE_EQUAL_TOLERANCE)
    ):
        logger.warning(
            "PM stripped price_target=%s: at or below stop_loss=%s",
            target, stop,
        )
        decision.price_target = None
        notes.append(
            f"**Note**: price target {target} removed: it sat at or below "
            f"the stop-loss {stop}, which is geometrically meaningless."
        )

    if (
        entry is not None
        and stop is not None
        and decision.price_target is not None
        and entry > stop
        and decision.price_target > entry
    ):
        rr = (decision.price_target - entry) / (entry - stop)
        rr_note = f"**Note**: code-computed risk/reward = {rr:.2f}"
        if rr < _MIN_RISK_REWARD:
            rr_note += (
                " — below 1.5, so prefer cutting position size rather than "
                "tightening the stop."
            )
        notes.append(rr_note + ".")

    action = _extract_trader_action(trader_plan)
    rating_dir = _RATING_DIRECTION.get(decision.rating.value.lower())
    action_dir = _ACTION_DIRECTION.get(action) if action else None
    if (
        rating_dir
        and action_dir
        and rating_dir != "neutral"
        and action_dir != "neutral"
        and rating_dir != action_dir
    ):
        notes.append(
            f"**Note**: rating {decision.rating.value} contradicts the Trader's "
            f"{action.upper()} action; make sure the override is deliberate and "
            f"justified in the thesis."
        )

    if not notes:
        return decision, None
    return decision, " ".join(notes)


def _validate_pm_decision(
    decision: PortfolioDecision, snapshot_close: float | None, trader_plan: str | None
) -> tuple[PortfolioDecision, str | None]:
    """Compose all deterministic PM post-checks, merging their notes.

    Order matters: snapshot tolerance strips miscalibrated entry/stop first,
    then decision consistency evaluates the surviving numbers (and nulls an
    incoherent target). Notes from both checks are concatenated.
    """
    decision, tolerance_note = _enforce_snapshot_tolerance(decision, snapshot_close)
    decision, consistency_note = _enforce_decision_consistency(decision, trader_plan)
    notes = [n for n in (tolerance_note, consistency_note) if n]
    if not notes:
        return decision, None
    return decision, " ".join(notes)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]
        ticker = state["company_of_interest"]
        trade_date = state.get("trade_date", "")

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        holdings_context = state.get("holdings_context", {})
        transactions_context = state.get("transactions_context", [])
        holdings_line = ""
        if holdings_context:
            from tradingagents.portfolio import Holding, Portfolio, Transaction, build_pm_prompt
            portfolio = Portfolio(
                holdings={
                    t: Holding.from_dict(d, ticker=t) for t, d in holdings_context.items()
                }
            )
            transactions = [Transaction.from_dict(t) for t in transactions_context]
            holdings_prompt = build_pm_prompt(
                state["company_of_interest"], portfolio, transactions
            )
            if holdings_prompt:
                holdings_line = f"\n**Current Position:**\n{holdings_prompt}\n"

        # Use the verified snapshot built at graph start. All agents share the
        # same snapshot so the PM cannot see a different price than the Trader.
        # We fall back to building one only when the shared snapshot is missing
        # (e.g. tests or programmatic state creation).
        snapshot = state.get("verified_market_snapshot", "")
        if not snapshot:
            try:
                snapshot = build_verified_market_snapshot(ticker, trade_date)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PM could not refresh verified market snapshot for %s on %s: %s",
                    ticker, trade_date, exc,
                )
                snapshot = None
        snapshot_close = _extract_snapshot_close(snapshot)
        snapshot_block = snapshot if snapshot else (
            "Verified market data is unavailable for this ticker on the "
            f"requested date ({trade_date}). Treat any Trader-quoted entry / "
            "stop as suspect and prefer leaving them null."
        )

        fundamentals_snapshot_block = state.get("verified_fundamentals_snapshot", "")
        if not fundamentals_snapshot_block:
            fundamentals_snapshot_block = (
                "Verified fundamentals snapshot is unavailable for this ticker on the "
                f"requested date ({trade_date}). Any fundamental numbers cited by "
                "analysts are unverified; set confidence to low."
            )

        data_quality_summary = get_or_build_data_quality_summary(state)

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}{holdings_line}
**Risk Analysts Debate History:**
{history}

---

**Verified Market Snapshot** (source of truth for any price level):

{snapshot_block}

---

**Verified Fundamentals Snapshot** (source of truth for any fundamental number):

{fundamentals_snapshot_block}

---

**Data Quality of Analyst Reports:**
{data_quality_summary}

---

**Decision Requirements:**
- Extract the entry price, stop-loss, and position sizing from the Trader's transaction proposal above and populate the corresponding fields in your decision.
- The Trader is required to quote entry and stop-loss from the snapshot below. If the Trader's quoted value differs from the snapshot's latest Close by more than ~25% in either direction, OR the snapshot is unavailable, leave entry_price and stop_loss as null rather than copying the suspect number. This rule is also enforced in code: any entry_price or stop_loss you return that deviates from the verified Close by more than 25% will be automatically stripped from the decision. Do not estimate, extrapolate, or recall a price from training data, gross-margin ratios, or any other fundamental number.
- If the Trader's proposal lacks any of these values, leave that field empty rather than estimating or inventing a number.
- price_target, when you set one, must sit clearly above both entry_price and stop_loss; a target at or below the stop is geometrically meaningless and will be automatically removed in code. When entry, stop, and target are all present and coherent, risk/reward is recomputed in code and attached to the decision.
- Position sizing may be a string (e.g. ``5% of portfolio``, ``1,000 shares``) and is not bound by the snapshot.
- Provide a `confidence` level (low/medium/high). If the verified market snapshot or verified fundamentals snapshot is unavailable, or the evidence is mixed, set confidence to `low` or null.
- Populate `data_sources` with the sources you actually used, e.g. ``market_snapshot``, ``fundamentals_snapshot``, ``trader_proposal``, ``risk_debate``. Do not claim a source you did not consult.
- Ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
            validate=lambda decision: _validate_pm_decision(
                decision, snapshot_close, trader_plan
            ),
        )

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "structured_fallback_agents": (
                ["Portfolio Manager"]
                if FALLBACK_MARKER in final_trade_decision
                else []
            ),
        }

    return portfolio_manager_node
