"""Tests for Pydantic decision schemas and their render helpers."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TraderAction,
    TraderProposal,
    render_pm_decision,
    render_research_plan,
    render_trader_proposal,
)


@pytest.mark.unit
class TestResearchPlanSchema:
    def test_serializes_confidence_and_key_assumptions(self):
        plan = ResearchPlan(
            recommendation=PortfolioRating.BUY,
            rationale="Bull case is solid.",
            strategic_actions="Add gradually.",
            confidence="high",
            key_assumptions=["Revenue growth > 20%", "Margins stable"],
        )
        assert plan.confidence == "high"
        assert plan.key_assumptions == ["Revenue growth > 20%", "Margins stable"]

    def test_confidence_optional(self):
        plan = ResearchPlan(
            recommendation=PortfolioRating.HOLD,
            rationale="Balanced.",
            strategic_actions="Wait.",
        )
        assert plan.confidence is None
        assert plan.key_assumptions == []

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValidationError):
            ResearchPlan(
                recommendation=PortfolioRating.SELL,
                rationale="x",
                strategic_actions="y",
                confidence="very_high",
            )

    def test_render_includes_confidence_and_key_assumptions(self):
        plan = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="r",
            strategic_actions="s",
            confidence="medium",
            key_assumptions=["A", "B"],
        )
        md = render_research_plan(plan)
        assert "**Confidence**: medium" in md
        assert "**Key Assumptions**: A, B" in md


@pytest.mark.unit
class TestTraderProposalSchema:
    def test_serializes_confidence_and_price_source(self):
        p = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Strong setup.",
            entry_price=150.0,
            stop_loss=140.0,
            confidence="high",
            price_source="Close from verified market snapshot",
        )
        assert p.confidence == "high"
        assert p.price_source == "Close from verified market snapshot"

    def test_render_includes_confidence_and_price_source(self):
        p = TraderProposal(
            action=TraderAction.HOLD,
            reasoning="r",
            confidence="low",
            price_source="null — snapshot unavailable",
        )
        md = render_trader_proposal(p)
        assert "**Confidence**: low" in md
        assert "**Price Source**: null — snapshot unavailable" in md

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValidationError):
            TraderProposal(
                action=TraderAction.SELL,
                reasoning="x",
                confidence="maybe",
            )


@pytest.mark.unit
class TestPortfolioDecisionSchema:
    def test_serializes_confidence_and_data_sources(self):
        d = PortfolioDecision(
            rating=PortfolioRating.BUY,
            executive_summary="Buy.",
            investment_thesis="T.",
            confidence="medium",
            data_sources=["market_snapshot", "fundamentals_snapshot"],
        )
        assert d.confidence == "medium"
        assert "fundamentals_snapshot" in d.data_sources

    def test_render_includes_confidence_and_data_sources(self):
        d = PortfolioDecision(
            rating=PortfolioRating.UNDERWEIGHT,
            executive_summary="s",
            investment_thesis="t",
            confidence="low",
            data_sources=["market_snapshot"],
        )
        md = render_pm_decision(d)
        assert "**Confidence**: low" in md
        assert "**Data Sources**: market_snapshot" in md

    def test_data_sources_include_fundamentals_snapshot(self):
        """Regression: PM must be able to record the fundamentals snapshot as a source."""
        d = PortfolioDecision(
            rating=PortfolioRating.HOLD,
            executive_summary="s",
            investment_thesis="t",
            data_sources=["fundamentals_snapshot"],
        )
        assert "fundamentals_snapshot" in d.data_sources
