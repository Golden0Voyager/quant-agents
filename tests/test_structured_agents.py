"""Tests for structured-output agents (Trader, Research Manager, Sentiment Analyst).

The Portfolio Manager has its own coverage in tests/test_memory_log.py
(which exercises the full memory-log → PM injection cycle).  This file
covers the parallel schemas, render functions, and graceful-fallback
behavior we added for the Trader, Research Manager, and Sentiment Analyst
so they share the same deterministic output shape.
"""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
    TraderAction,
    TraderProposal,
    render_pm_decision,
    render_research_plan,
    render_sentiment_report,
    render_trader_proposal,
)
from tradingagents.agents.trader.trader import create_trader

# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderTraderProposal:
    def test_minimal_required_fields(self):
        p = TraderProposal(action=TraderAction.HOLD, reasoning="Balanced setup; no edge.")
        md = render_trader_proposal(p)
        assert "**Action**: Hold" in md
        assert "**Reasoning**: Balanced setup; no edge." in md
        # The trailing FINAL TRANSACTION PROPOSAL line is preserved for the
        # analyst stop-signal text and any external code that greps for it.
        assert "FINAL TRANSACTION PROPOSAL: **HOLD**" in md

    def test_optional_fields_included_when_present(self):
        p = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Strong technicals + fundamentals.",
            entry_price=189.5,
            stop_loss=178.0,
            position_sizing="6% of portfolio",
        )
        md = render_trader_proposal(p)
        assert "**Action**: Buy" in md
        assert "**Entry Price**: 189.5" in md
        assert "**Stop Loss**: 178.0" in md
        assert "**Position Sizing**: 6% of portfolio" in md
        assert "FINAL TRANSACTION PROPOSAL: **BUY**" in md

    def test_optional_fields_omitted_when_absent(self):
        p = TraderProposal(action=TraderAction.SELL, reasoning="Guidance cut.")
        md = render_trader_proposal(p)
        assert "Entry Price" not in md
        assert "Stop Loss" not in md
        assert "Position Sizing" not in md
        assert "FINAL TRANSACTION PROPOSAL: **SELL**" in md

    def test_render_includes_confidence_and_price_source(self):
        p = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Technical breakout.",
            entry_price=100.0,
            stop_loss=95.0,
            confidence="high",
            price_source="Close from verified market snapshot",
        )
        md = render_trader_proposal(p)
        assert "**Confidence**: high" in md
        assert "**Price Source**: Close from verified market snapshot" in md


@pytest.mark.unit
class TestNullishFloatCoercion:
    """A weak LLM may write "None"/"N/A" into an optional float field (#1058);
    coerce those to None so the structured call validates instead of erroring."""

    def test_trader_nullish_strings_coerce_to_none(self):
        for sentinel in ("None", "N/A", "null", "-", "", "TBD"):
            p = TraderProposal(
                action=TraderAction.HOLD,
                reasoning="x",
                entry_price=sentinel,
                stop_loss=sentinel,
            )
            assert p.entry_price is None
            assert p.stop_loss is None

    def test_trader_real_numeric_string_still_parses(self):
        p = TraderProposal(action=TraderAction.BUY, reasoning="x", entry_price="189.5")
        assert p.entry_price == 189.5

    def test_pm_nullish_price_target_coerces_to_none(self):
        d = PortfolioDecision(
            rating=PortfolioRating.OVERWEIGHT,
            executive_summary="s",
            investment_thesis="t",
            price_target="N/A",
        )
        assert d.price_target is None


@pytest.mark.unit
class TestRenderResearchPlan:
    def test_required_fields(self):
        p = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="Bull case carried; tailwinds intact.",
            strategic_actions="Build position over two weeks; cap at 5%.",
        )
        md = render_research_plan(p)
        assert "**Recommendation**: Overweight" in md
        assert "**Rationale**: Bull case carried" in md
        assert "**Strategic Actions**: Build position" in md

    def test_all_5_tier_ratings_render(self):
        for rating in PortfolioRating:
            p = ResearchPlan(
                recommendation=rating,
                rationale="r",
                strategic_actions="s",
            )
            md = render_research_plan(p)
            assert f"**Recommendation**: {rating.value}" in md

    def test_render_includes_confidence_and_key_assumptions(self):
        p = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="r",
            strategic_actions="s",
            confidence="medium",
            key_assumptions=["Revenue growth stable", "Rates unchanged"],
        )
        md = render_research_plan(p)
        assert "**Confidence**: medium" in md
        assert "**Key Assumptions**: Revenue growth stable, Rates unchanged" in md


# ---------------------------------------------------------------------------
# Trader agent: structured happy path + fallback
# ---------------------------------------------------------------------------


def _make_trader_state():
    return {
        "company_of_interest": "NVDA",
        "investment_plan": "**Recommendation**: Buy\n**Rationale**: ...\n**Strategic Actions**: ...",
    }


def _structured_trader_llm(captured: dict, proposal: TraderProposal | None = None):
    """Build a MagicMock LLM whose with_structured_output binding captures the
    prompt and returns a real TraderProposal so render_trader_proposal works.
    """
    if proposal is None:
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Strong setup.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or proposal
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
def test_invoke_structured_falls_back_when_result_is_none():
    # A thinking model can answer in plain text, leaving the parser with None.
    # That must fall back to free text, not crash on render(None) (#1051).
    from tradingagents.agents.utils.structured import invoke_structured_or_freetext

    structured = MagicMock()
    structured.invoke.return_value = None
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="FREETEXT")

    out = invoke_structured_or_freetext(
        structured, plain, "prompt", render=lambda r: r.rating, agent_name="t"
    )
    assert "STRUCTURED_FALLBACK" in out
    assert "FREETEXT" in out
    plain.invoke.assert_called_once()


@pytest.mark.unit
class TestTraderAgent:
    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="AI capex cycle intact; institutional flows constructive.",
            entry_price=189.5,
            stop_loss=178.0,
            position_sizing="6% of portfolio",
        )
        llm = _structured_trader_llm(captured, proposal)
        trader = create_trader(llm)
        result = trader(_make_trader_state())
        plan = result["trader_investment_plan"]
        assert "**Action**: Buy" in plan
        assert "**Entry Price**: 189.5" in plan
        assert "FINAL TRANSACTION PROPOSAL: **BUY**" in plan
        # The same rendered markdown is also added to messages for downstream agents.
        assert plan in result["messages"][0].content

    def test_prompt_includes_investment_plan(self):
        captured = {}
        llm = _structured_trader_llm(captured)
        trader = create_trader(llm)
        trader(_make_trader_state())
        # The investment plan is in the user message of the captured prompt.
        prompt = captured["prompt"]
        assert any("Proposed Investment Plan" in m["content"] for m in prompt)

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        plain_response = (
            "**Action**: Sell\n\nGuidance cut hits margins.\n\n"
            "FINAL TRANSACTION PROPOSAL: **SELL**"
        )
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain_response)
        trader = create_trader(llm)
        state = _make_trader_state()
        result = trader(state)
        assert "STRUCTURED_FALLBACK" in result["trader_investment_plan"]
        assert plain_response in result["trader_investment_plan"]
        assert result["structured_fallback_agents"] == ["Trader"]
        assert "_structured_fallback" not in state

    def test_prompt_quotes_verified_snapshot(self, monkeypatch):
        """Regression for #bug-2026-06-06-price-hallucination: the Trader used to
        be told ``You MUST include concrete entry price … even if the research
        plan does not explicitly state them``, which drove the LLM to invent
        numbers (e.g. quoting TCL科技 as 12.5 when the verified close was 4.89)
        whenever the upstream Market Analyst could not pull a quote. The new
        prompt injects ``build_verified_market_snapshot`` and demands the price
        be quoted from it, or set to null if the snapshot is unavailable."""
        captured = {}
        llm = _structured_trader_llm(captured)
        trader = create_trader(llm)
        # _make_trader_state does not set trade_date; default to today's date so
        # the snapshot path produces content.
        from datetime import date
        state = _make_trader_state()
        state["trade_date"] = date(2026, 6, 6).isoformat()
        monkeypatch.setattr(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            lambda symbol, curr_date: f"FAKE_SNAPSHOT for {symbol} on {curr_date}",
        )
        trader(state)
        # Trader forwards a list of message dicts to structured_llm.invoke.
        # Flatten the captured messages into a single string for assertions.
        prompt = " ".join(
            msg["content"] for msg in captured["prompt"]
            if isinstance(msg, dict) and "content" in msg
        )
        # The prompt must carry the snapshot the LLM is told to quote from.
        assert "FAKE_SNAPSHOT for NVDA on 2026-06-06" in prompt
        # The prompt must instruct the LLM to quote price from the snapshot
        # or set it null — never invent a number.
        assert "from the Verified Market Snapshot" in prompt
        assert (
            "set entry_price and stop_loss to null rather than guessing" in prompt
            or "set entry_price and stop_loss to null" in prompt
        )
        # The old ``You MUST include concrete entry price`` clause is gone.
        assert "You MUST include concrete entry price" not in prompt

    def test_prompt_handles_unavailable_snapshot(self, monkeypatch):
        """When the snapshot vendor raises (e.g. unknown ticker, offline cache),
        the Trader prompt must still be rendered with a stub block that tells
        the LLM to leave entry/stop as null instead of guessing."""
        captured = {}
        llm = _structured_trader_llm(captured)
        trader = create_trader(llm)
        state = _make_trader_state()
        state["trade_date"] = "2026-06-06"

        def _raise(symbol, curr_date):
            raise RuntimeError("vendor offline")

        monkeypatch.setattr(
            "tradingagents.agents.trader.trader.build_verified_market_snapshot",
            _raise,
        )
        trader(state)
        prompt = " ".join(
            msg["content"] for msg in captured["prompt"]
            if isinstance(msg, dict) and "content" in msg
        )
        assert "Verified market data is unavailable" in prompt
        assert "set entry_price and stop_loss to null" in prompt


# ---------------------------------------------------------------------------
# Research Manager agent: structured happy path + fallback
# ---------------------------------------------------------------------------


def _make_rm_state():
    return {
        "company_of_interest": "NVDA",
        "investment_debate_state": {
            "history": "Bull and bear arguments here.",
            "bull_history": "Bull says...",
            "bear_history": "Bear says...",
            "current_response": "",
            "judge_decision": "",
            "count": 1,
        },
    }


def _structured_rm_llm(captured: dict, plan: ResearchPlan | None = None):
    if plan is None:
        plan = ResearchPlan(
            recommendation=PortfolioRating.HOLD,
            rationale="Balanced view across both sides.",
            strategic_actions="Hold current position; reassess after earnings.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or plan
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
class TestResearchManagerAgent:
    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        plan = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="Bull case is stronger; AI tailwind intact.",
            strategic_actions="Build position gradually over two weeks.",
        )
        llm = _structured_rm_llm(captured, plan)
        rm = create_research_manager(llm)
        result = rm(_make_rm_state())
        ip = result["investment_plan"]
        assert "**Recommendation**: Overweight" in ip
        assert "**Rationale**: Bull case" in ip
        assert "**Strategic Actions**: Build position" in ip

    def test_prompt_uses_5_tier_rating_scale(self):
        """The RM prompt must list all five tiers so the schema enum matches user expectations."""
        captured = {}
        llm = _structured_rm_llm(captured)
        rm = create_research_manager(llm)
        rm(_make_rm_state())
        prompt = captured["prompt"]
        for tier in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
            assert f"**{tier}**" in prompt, f"missing {tier} in prompt"

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        plain_response = "**Recommendation**: Sell\n\n**Rationale**: ...\n\n**Strategic Actions**: ..."
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain_response)
        rm = create_research_manager(llm)
        state = _make_rm_state()
        result = rm(state)
        assert "STRUCTURED_FALLBACK" in result["investment_plan"]
        assert plain_response in result["investment_plan"]
        assert result["structured_fallback_agents"] == ["Research Manager"]
        assert "_structured_fallback" not in state


# ---------------------------------------------------------------------------
# Portfolio Decision render
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderPortfolioDecision:
    def test_render_includes_confidence_and_data_sources(self):
        from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

        d = PortfolioDecision(
            rating=PortfolioRating.OVERWEIGHT,
            executive_summary="Accumulate on dips.",
            investment_thesis="Fundamentals support higher valuation.",
            confidence="medium",
            data_sources=["market_snapshot", "fundamentals_snapshot"],
        )
        md = render_pm_decision(d)
        assert "**Confidence**: medium" in md
        assert "**Data Sources**: market_snapshot, fundamentals_snapshot" in md


# ---------------------------------------------------------------------------
# Sentiment Analyst: schema, render, structured happy path + fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderSentimentReport:
    def test_header_contains_band_and_score(self):
        report = SentimentReport(
            overall_band=SentimentBand.BULLISH,
            overall_score=7.2,
            confidence="high",
            narrative="Source breakdown here.",
        )
        md = render_sentiment_report(report)
        assert "**Overall Sentiment:** **Bullish**" in md
        assert "(Score: 7.2/10)" in md

    def test_header_contains_confidence(self):
        report = SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            overall_score=5.0,
            confidence="low",
            narrative="Limited data.",
        )
        assert "**Confidence:** Low" in render_sentiment_report(report)

    def test_narrative_preserved_in_output(self):
        narrative = "## Breakdown\n\nStockTwits: 70% bullish.\n\n| Signal | Direction |\n|---|---|\n| News | Neutral |"
        report = SentimentReport(
            overall_band=SentimentBand.MILDLY_BULLISH,
            overall_score=6.0,
            confidence="medium",
            narrative=narrative,
        )
        assert narrative in render_sentiment_report(report)

    def test_all_six_bands_render(self):
        for band in SentimentBand:
            report = SentimentReport(
                overall_band=band, overall_score=5.0,
                confidence="medium", narrative="n",
            )
            assert band.value in render_sentiment_report(report)

    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            SentimentReport(
                overall_band=SentimentBand.BULLISH, overall_score=11.0,
                confidence="high", narrative="n",
            )


def _make_sentiment_state():
    return {
        "company_of_interest": "NVDA",
        "trade_date": "2026-01-15",
        "asset_type": "stock",
        "market": "XNYS",
        "messages": [],
    }


def _structured_sentiment_llm(captured: dict, report: SentimentReport | None = None):
    """MagicMock LLM whose structured binding captures the prompt and returns
    a real SentimentReport so render_sentiment_report works."""
    if report is None:
        report = SentimentReport(
            overall_band=SentimentBand.BULLISH, overall_score=7.5,
            confidence="high",
            narrative="StockTwits 75% bullish. News constructive. Reddit upbeat.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or report
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
class TestSentimentAnalystAgent:
    @pytest.fixture(autouse=True)
    def _stub_sentiment_sources(self, monkeypatch):
        from tradingagents.agents.analysts import sentiment_analyst as module

        monkeypatch.setattr(module.get_news, "func", lambda *args: "NEWS_DATA")
        results = {
            "fetch_eastmoney_hot_rank": "HOT_RANK_DATA",
            "fetch_eastmoney_guba_sentiment": "GUBA_DATA",
            "fetch_eastmoney_hot_keywords": "HOT_KEYWORDS_DATA",
        }
        monkeypatch.setattr(module, "route_to_vendor", lambda method, *args: results[method])

    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        report = SentimentReport(
            overall_band=SentimentBand.MILDLY_BEARISH, overall_score=4.0,
            confidence="medium", narrative="Mixed signals across sources.",
        )
        analyst = create_sentiment_analyst(_structured_sentiment_llm(captured, report))
        sr = analyst(_make_sentiment_state())["sentiment_report"]
        assert "**Overall Sentiment:** **Mildly Bearish**" in sr
        assert "(Score: 4.0/10)" in sr
        assert "Mixed signals across sources." in sr

    def test_sentiment_report_also_in_messages(self):
        captured = {}
        analyst = create_sentiment_analyst(_structured_sentiment_llm(captured))
        result = analyst(_make_sentiment_state())
        assert len(result["messages"]) == 1
        assert result["sentiment_report"] == result["messages"][0].content

    def test_prompt_contains_ticker(self):
        captured = {}
        create_sentiment_analyst(_structured_sentiment_llm(captured))(_make_sentiment_state())
        assert any("NVDA" in str(m) for m in captured["prompt"])

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        plain = "**Overall Sentiment:** **Bearish** (Score: 3.0/10)\n**Confidence:** Low\n\nLimited data."
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain)
        report = create_sentiment_analyst(llm)(_make_sentiment_state())["sentiment_report"]
        assert "STRUCTURED_FALLBACK" in report
        assert plain in report

        result = create_sentiment_analyst(llm)(_make_sentiment_state())
        assert result["structured_fallback_agents"] == ["Sentiment Analyst"]

    def test_falls_back_to_freetext_when_structured_call_fails(self):
        plain = "Fallback free-text sentiment."
        structured = MagicMock()
        structured.invoke.side_effect = ValueError("bad JSON from model")
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        llm.invoke.return_value = MagicMock(content=plain)
        report = create_sentiment_analyst(llm)(_make_sentiment_state())["sentiment_report"]
        assert "STRUCTURED_FALLBACK" in report
        assert plain in report

    def test_falls_back_to_freetext_when_structured_call_hits_rate_limit(self):
        """Regression: transient provider errors from the structured path still fall back."""
        plain = "Fallback free-text sentiment after rate limit."
        structured = MagicMock()
        structured.invoke.side_effect = RuntimeError("rate limit exceeded on dimension: tpm")
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        llm.invoke.return_value = MagicMock(content=plain)
        report = create_sentiment_analyst(llm)(_make_sentiment_state())["sentiment_report"]
        assert "STRUCTURED_FALLBACK" in report
        assert plain in report

    def test_news_rate_limit_degrades_without_aborting_node(self, monkeypatch):
        from tradingagents.agents.analysts import sentiment_analyst as module
        from tradingagents.dataflows.errors import VendorRateLimitError

        def raise_rate_limit(*args):
            raise VendorRateLimitError("Yahoo Finance rate-limited for NVDA")

        monkeypatch.setattr(module.get_news, "func", raise_rate_limit)
        captured = {}
        result = create_sentiment_analyst(_structured_sentiment_llm(captured))(
            _make_sentiment_state()
        )

        assert "sentiment_report" in result
        assert "DATA_UNAVAILABLE" in "\n".join(str(message) for message in captured["prompt"])

    def test_hk_prefetch_skips_every_a_share_only_sentiment_source(self, monkeypatch):
        from tradingagents.agents.analysts import sentiment_analyst as module

        news = MagicMock(return_value="HK_NEWS")
        enrichment_route = MagicMock(return_value="SHOULD_NOT_RUN")
        monkeypatch.setattr(module.get_news, "func", news)
        monkeypatch.setattr(module, "route_to_vendor", enrichment_route)
        state = {
            **_make_sentiment_state(),
            "company_of_interest": "1810.HK",
            "market": "XHKG",
        }

        create_sentiment_analyst(_structured_sentiment_llm({}))(state)

        news.assert_called_once()
        enrichment_route.assert_not_called()


@pytest.mark.unit
class TestSocialMediaAnalystShim:
    """Deprecated ``create_social_media_analyst`` backwards-compatibility shim.

    Covers ``sentiment_analyst.py`` lines 210-217.
    """

    @pytest.fixture(autouse=True)
    def _stub_sentiment_sources(self, monkeypatch):
        from tradingagents.agents.analysts import sentiment_analyst as module

        monkeypatch.setattr(module.get_news, "func", lambda *args: "NEWS_DATA")
        results = {
            "fetch_eastmoney_hot_rank": "HOT_RANK_DATA",
            "fetch_eastmoney_guba_sentiment": "GUBA_DATA",
            "fetch_eastmoney_hot_keywords": "HOT_KEYWORDS_DATA",
        }
        monkeypatch.setattr(module, "route_to_vendor", lambda method, *args: results[method])

    def test_returns_callable(self):
        from tradingagents.agents.analysts.sentiment_analyst import create_social_media_analyst

        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content="**Overall Sentiment:** **Bearish**")
        node = create_social_media_analyst(llm)
        assert callable(node)

    def test_emits_deprecation_warning(self):
        from tradingagents.agents.analysts.sentiment_analyst import create_social_media_analyst

        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content="**Overall Sentiment:** **Bearish**")
        with pytest.warns(DeprecationWarning, match="create_social_media_analyst is deprecated"):
            create_social_media_analyst(llm)

    def test_delegates_to_create_sentiment_analyst(self):
        from tradingagents.agents.analysts.sentiment_analyst import (
            create_sentiment_analyst,
            create_social_media_analyst,
        )

        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content="**Overall Sentiment:** **Mildly Bullish**")
        social_node = create_social_media_analyst(llm)
        sentiment_node = create_sentiment_analyst(llm)

        state = _make_sentiment_state()
        social_result = social_node(state)
        sentiment_result = sentiment_node(state)
        assert social_result["sentiment_report"] == sentiment_result["sentiment_report"]
