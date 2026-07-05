"""Edge-case tests for uncovered paths in agent_utils.py.

Targets:
- build_instrument_context: sector-only and industry-only identity branches
- _all_a_share_names: exception path (import/network failure)
- get_or_build_data_quality_summary: existing summary and build paths
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.utils.agent_utils import (
    _all_a_share_names,
    build_instrument_context,
    get_or_build_data_quality_summary,
)


@pytest.mark.unit
class TestBuildInstrumentContextSectorIndustryEdges:
    """Covers the elif-sector and elif-industry branches in build_instrument_context."""

    def test_sector_only_no_industry(self) -> None:
        """identity with sector but no industry -> 'Sector: ...' only."""
        context = build_instrument_context(
            "AAPL",
            identity={"sector": "Technology"},
        )
        assert "Sector: Technology" in context
        assert "Business classification" not in context
        assert "Industry:" not in context

    def test_industry_only_no_sector(self) -> None:
        """identity with industry but no sector -> 'Industry: ...' only."""
        context = build_instrument_context(
            "AAPL",
            identity={"industry": "Consumer Electronics"},
        )
        assert "Industry: Consumer Electronics" in context
        assert "Business classification" not in context
        assert "Sector:" not in context

    def test_neither_sector_nor_industry(self) -> None:
        """identity with neither sector nor industry -> no classification line."""
        context = build_instrument_context(
            "AAPL",
            identity={"company_name": "Apple Inc."},
        )
        assert "Company: Apple Inc." in context
        assert "Business classification" not in context
        assert "Sector:" not in context
        assert "Industry:" not in context

    def test_sector_only_with_confirmed_name(self) -> None:
        """confirmed_name takes priority over identity for the name,
        but sector still comes from identity."""
        context = build_instrument_context(
            "AAPL",
            identity={"sector": "Technology", "company_name": "Apple Inc."},
            confirmed_name="Apple Inc. (user confirmed)",
        )
        assert "Company: Apple Inc. (user confirmed)" in context
        assert "Sector: Technology" in context


@pytest.mark.unit
class TestAllAShareNames:
    """Covers the _all_a_share_names function (always mocked in other tests).

    Note: ``_all_a_share_names`` does ``import akshare as ak`` inside its
    body, making ``ak`` a local variable. Patching at
    ``tradingagents.agents.utils.agent_utils.ak`` doesn't work because the
    import statement doesn't read from the module's attribute namespace.
    Instead we use ``patch.dict(sys.modules, ...)`` so the inline import
    resolves to our mock.
    """

    def test_exception_returns_empty_set(self) -> None:
        """When akshare raises an exception, _all_a_share_names returns empty set."""
        mock_akshare = MagicMock()
        mock_akshare.stock_info_a_code_name.side_effect = RuntimeError("network error")
        with patch.dict("sys.modules", {"akshare": mock_akshare}):
            result = _all_a_share_names()
        assert result == set()

    def test_normal_path_returns_names(self) -> None:
        """When akshare works, _all_a_share_names returns a set of names."""
        import pandas as pd
        mock_akshare = MagicMock()
        df = pd.DataFrame({"name": ["平安银行", "招商银行", "贵州茅台"]})
        mock_akshare.stock_info_a_code_name.return_value = df
        with patch.dict("sys.modules", {"akshare": mock_akshare}):
            result = _all_a_share_names()
        assert result == {"平安银行", "招商银行", "贵州茅台"}


@pytest.mark.unit
class TestGetOrBuildDataQualitySummary:
    """Covers get_or_build_data_quality_summary function."""

    def test_returns_existing_summary(self) -> None:
        """When data_quality_summary is a non-empty string, return it directly."""
        state = {"data_quality_summary": "All 5 analysts reported data"}
        result = get_or_build_data_quality_summary(state)
        assert result == "All 5 analysts reported data"

    def test_returns_empty_string_if_only_whitespace(self) -> None:
        """Whitespace-only summary falls through to build."""
        with patch(
            "tradingagents.graph.analyst_execution.build_data_quality_summary",
            return_value="built summary",
        ) as mock_build:
            result = get_or_build_data_quality_summary(
                {"data_quality_summary": "   "}
            )
        mock_build.assert_called_once()
        assert result == "built summary"

    def test_returns_empty_string_if_not_string(self) -> None:
        """Non-string summary falls through to build."""
        with patch(
            "tradingagents.graph.analyst_execution.build_data_quality_summary",
            return_value="built summary",
        ) as mock_build:
            result = get_or_build_data_quality_summary(
                {"data_quality_summary": 123}
            )
        mock_build.assert_called_once()
        assert result == "built summary"

    def test_builds_when_summary_missing(self) -> None:
        """Missing key falls through to build."""
        with patch(
            "tradingagents.graph.analyst_execution.build_data_quality_summary",
            return_value="built summary",
        ) as mock_build:
            result = get_or_build_data_quality_summary(
                {"company_of_interest": "AAPL"}
            )
        mock_build.assert_called_once()
        assert result == "built summary"

    def test_build_passes_state_and_specs(self) -> None:
        """Verify the build function receives the correct arguments."""
        state = {"company_of_interest": "AAPL"}
        with patch(
            "tradingagents.graph.analyst_execution.ANALYST_NODE_SPECS",
            {"a1": "spec1", "a2": "spec2"},
        ), patch(
            "tradingagents.graph.analyst_execution.build_data_quality_summary",
        ) as mock_build:
            get_or_build_data_quality_summary(state)
        mock_build.assert_called_once_with(state, ["spec1", "spec2"])
