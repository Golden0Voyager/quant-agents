"""Tests for agent utility functions: _clean_identity_value,
sanitize_company_name_in_report, get_instrument_context_from_state."""

from unittest.mock import patch

import pytest

from tradingagents.agents.utils.agent_utils import (
    _clean_identity_value,
    get_instrument_context_from_state,
    sanitize_company_name_in_report,
)


@pytest.mark.unit
class TestCleanIdentityValue:
    """Tests for _clean_identity_value — non-string, empty, placeholder
    values return None; trimmed strings pass through."""

    # --- Non-string inputs ---

    def test_returns_none_for_non_string_int(self):
        assert _clean_identity_value(42) is None

    def test_returns_none_for_non_string_none(self):
        assert _clean_identity_value(None) is None

    def test_returns_none_for_non_string_list(self):
        assert _clean_identity_value([]) is None

    def test_returns_none_for_non_string_dict(self):
        assert _clean_identity_value({"k": "v"}) is None

    # --- Empty / whitespace ---

    def test_returns_none_for_empty_string(self):
        assert _clean_identity_value("") is None

    def test_returns_none_for_whitespace(self):
        assert _clean_identity_value("  ") is None

    # --- Placeholder values (case-insensitive) ---

    def test_returns_none_for_placeholder_none(self):
        assert _clean_identity_value("None") is None

    def test_returns_none_for_placeholder_n_a(self):
        assert _clean_identity_value("n/a") is None

    def test_returns_none_for_placeholder_nan_uppercase(self):
        assert _clean_identity_value("NAN") is None

    def test_returns_none_for_placeholder_null(self):
        assert _clean_identity_value("null") is None

    # --- Valid strings ---

    def test_returns_trimmed_string(self):
        assert _clean_identity_value("  Apple  ") == "Apple"

    def test_preserves_mixed_case(self):
        assert _clean_identity_value("TOTO LTD.") == "TOTO LTD."

    def test_placeholder_with_surrounding_whitespace_still_returns_none(self):
        assert _clean_identity_value("  N/A  ") is None


@pytest.mark.unit
class TestSanitizeCompanyNameInReport:
    """Tests for sanitize_company_name_in_report — A-share tickers get
    wrong company names corrected; non-A-share tickers pass through."""

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value=set(),
    )
    def test_non_ashare_passthrough(self, mock_names):
        report = "AAPL is a great company."
        result = sanitize_company_name_in_report(report, "AAPL", "Apple Inc.")
        assert result == report
        mock_names.assert_not_called()

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value=set(),
    )
    def test_non_ashare_hk_passthrough(self, mock_names):
        report = "Tencent Holdings Ltd."
        result = sanitize_company_name_in_report(report, "0700.HK", "Tencent")
        assert result == report
        mock_names.assert_not_called()

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value={"平安银行", "招商银行", "贵州茅台"},
    )
    def test_correct_name_unchanged(self, mock_names):
        report = "分析平安银行发展前景"
        result = sanitize_company_name_in_report(report, "000001.SZ", "平安银行")
        assert result == "分析平安银行发展前景"

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value={"平安银行", "招商银行", "贵州茅台"},
    )
    def test_wrong_name_replaced(self, mock_names):
        report = "分析招商银行发展前景"
        result = sanitize_company_name_in_report(report, "000001.SZ", "平安银行")
        assert result == "分析平安银行发展前景"

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value={"平安银行", "招商银行", "贵州茅台"},
    )
    def test_multiple_wrong_names_replaced(self, mock_names):
        report = "对比招商银行和贵州茅台"
        result = sanitize_company_name_in_report(report, "000001.SZ", "平安银行")
        assert "招商银行" not in result
        assert "贵州茅台" not in result
        # Both wrong names get replaced with the correct name
        assert result == "对比平安银行和平安银行"

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value={"平安银行", "招商银行", "贵州茅台"},
    )
    def test_wrong_name_replaced_bj_suffix(self, mock_names):
        report = "分析贵州茅台"
        result = sanitize_company_name_in_report(report, "830799.BJ", "平安银行")
        assert result == "分析平安银行"

    @patch(
        "tradingagents.agents.utils.agent_utils._all_a_share_names",
        return_value={"平安银行", "招商银行", "贵州茅台"},
    )
    def test_wrong_name_replaced_ss_suffix(self, mock_names):
        report = "分析贵州茅台"
        result = sanitize_company_name_in_report(report, "600519.SS", "平安银行")
        assert result == "分析平安银行"

    # --- Edge cases ---

    def test_empty_report_returns_empty(self):
        result = sanitize_company_name_in_report("", "000001.SZ", "平安银行")
        assert result == ""

    def test_no_company_name_returns_report_unchanged(self):
        report = "some report"
        result = sanitize_company_name_in_report(report, "000001.SZ", "")
        assert result == report

    def test_no_ticker_company_name_returns_report_unchanged(self):
        report = "some report"
        result = sanitize_company_name_in_report(report, "", "平安银行")
        assert result == report


@pytest.mark.unit
class TestGetInstrumentContextFromState:
    """Tests for get_instrument_context_from_state — pre-computed context
    is returned directly; missing/empty/whitespace context falls through
    to build_instrument_context."""

    def test_returns_existing_context(self):
        state = {
            "instrument_context": "context1",
            "company_of_interest": "AAPL",
        }
        assert get_instrument_context_from_state(state) == "context1"

    def test_calls_build_when_missing(self):
        with patch(
            "tradingagents.agents.utils.agent_utils.build_instrument_context",
            return_value="built context",
        ) as mock_build:
            result = get_instrument_context_from_state(
                {"company_of_interest": "NVDA", "asset_type": "stock"}
            )
        mock_build.assert_called_once_with("NVDA", "stock")
        assert result == "built context"

    def test_falls_through_on_empty_string_context(self):
        with patch(
            "tradingagents.agents.utils.agent_utils.build_instrument_context",
            return_value="built",
        ) as mock_build:
            result = get_instrument_context_from_state(
                {
                    "company_of_interest": "AAPL",
                    "asset_type": "stock",
                    "instrument_context": "",
                }
            )
        mock_build.assert_called_once_with("AAPL", "stock")
        assert result == "built"

    def test_falls_through_on_whitespace_context(self):
        with patch(
            "tradingagents.agents.utils.agent_utils.build_instrument_context",
            return_value="built",
        ) as mock_build:
            result = get_instrument_context_from_state(
                {
                    "company_of_interest": "AAPL",
                    "asset_type": "stock",
                    "instrument_context": "   ",
                }
            )
        mock_build.assert_called_once_with("AAPL", "stock")
        assert result == "built"

    def test_calls_build_with_default_asset_type(self):
        with patch(
            "tradingagents.agents.utils.agent_utils.build_instrument_context",
            return_value="built",
        ) as mock_build:
            get_instrument_context_from_state(
                {"company_of_interest": "TSLA"}
            )
        mock_build.assert_called_once_with("TSLA", "stock")

    def test_non_string_context_ignored(self):
        with patch(
            "tradingagents.agents.utils.agent_utils.build_instrument_context",
            return_value="built",
        ) as mock_build:
            get_instrument_context_from_state(
                {
                    "company_of_interest": "AAPL",
                    "instrument_context": 123,
                }
            )
        mock_build.assert_called_once()
