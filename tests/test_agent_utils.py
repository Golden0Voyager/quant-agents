"""Tests for agent utility functions in agent_utils.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.agents.analysts.sentiment_analyst import (
    create_sentiment_analyst,
    create_social_media_analyst,
)
from tradingagents.agents.trader.trader import _extract_market_analyst_price
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


# ---------------------------------------------------------------------------
# agent_utils — get_language_instruction
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetLanguageInstruction:
    """Tests for get_language_instruction — returns '' for English,
    instruction string for other languages."""

    def test_english_returns_empty_string(self):
        from tradingagents.agents.utils.agent_utils import get_language_instruction

        with patch(
            "tradingagents.dataflows.config.get_config",
            return_value={"output_language": "English"},
        ):
            result = get_language_instruction()
        assert result == ""

    def test_case_insensitive_english(self):
        from tradingagents.agents.utils.agent_utils import get_language_instruction

        with patch(
            "tradingagents.dataflows.config.get_config",
            return_value={"output_language": "english"},
        ):
            result = get_language_instruction()
        assert result == ""

    def test_chinese_returns_instruction(self):
        from tradingagents.agents.utils.agent_utils import get_language_instruction

        with patch(
            "tradingagents.dataflows.config.get_config",
            return_value={"output_language": "Chinese"},
        ):
            result = get_language_instruction()
        assert result == " Write your entire response in Chinese."

    def test_default_config_missing_language_returns_empty(self):
        from tradingagents.agents.utils.agent_utils import get_language_instruction

        with patch(
            "tradingagents.dataflows.config.get_config",
            return_value={},
        ):
            result = get_language_instruction()
        assert result == ""


# ---------------------------------------------------------------------------
# agent_utils — resolve_instrument_identity
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestResolveInstrumentIdentity:
    """Tests for resolve_instrument_identity — yfinance lookup with
    caching, error handling, and identity extraction."""

    def setup_method(self):
        """Clear lru_cache between tests to prevent cache pollution."""
        from tradingagents.agents.utils.agent_utils import resolve_instrument_identity
        resolve_instrument_identity.cache_clear()

    def test_returns_identity_with_all_fields(self):
        from tradingagents.agents.utils.agent_utils import resolve_instrument_identity

        mock_info = {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "exchange": "NASDAQ",
            "quoteType": "EQUITY",
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        with patch(
            "tradingagents.agents.utils.agent_utils.yf.Ticker",
            return_value=mock_ticker,
        ), patch(
            "tradingagents.dataflows.symbol_utils.normalize_symbol",
            return_value="AAPL",
        ):
            identity = resolve_instrument_identity("AAPL")

        assert identity["company_name"] == "Apple Inc."
        assert identity["sector"] == "Technology"
        assert identity["industry"] == "Consumer Electronics"
        assert identity["exchange"] == "NASDAQ"
        assert identity["quote_type"] == "EQUITY"

    def test_fallback_to_short_name(self):
        from tradingagents.agents.utils.agent_utils import resolve_instrument_identity

        mock_info = {"shortName": "Apple"}
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        with patch(
            "tradingagents.agents.utils.agent_utils.yf.Ticker",
            return_value=mock_ticker,
        ), patch(
            "tradingagents.dataflows.symbol_utils.normalize_symbol",
            return_value="AAPL",
        ):
            identity = resolve_instrument_identity("AAPL")

        assert identity["company_name"] == "Apple"

    def test_empty_info_on_exception(self):
        from tradingagents.agents.utils.agent_utils import resolve_instrument_identity

        with patch(
            "tradingagents.agents.utils.agent_utils.yf.Ticker",
            side_effect=ConnectionError("network error"),
        ):
            identity = resolve_instrument_identity("AAPL")

        assert identity == {}

    def test_empty_info_when_info_is_none(self):
        from tradingagents.agents.utils.agent_utils import resolve_instrument_identity

        mock_ticker = MagicMock()
        mock_ticker.info = None

        with patch(
            "tradingagents.agents.utils.agent_utils.yf.Ticker",
            return_value=mock_ticker,
        ), patch(
            "tradingagents.dataflows.symbol_utils.normalize_symbol",
            return_value="AAPL",
        ):
            identity = resolve_instrument_identity("AAPL")

        assert identity == {}


# ---------------------------------------------------------------------------
# agent_utils — build_instrument_context
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildInstrumentContext:
    """Tests for build_instrument_context — builds context string with
    ticker, identity details, and crypto handling."""

    def test_ticker_only_no_identity(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        result = build_instrument_context("AAPL")
        assert "`AAPL`" in result
        assert "instrument" in result
        assert "Resolved identity" not in result

    def test_with_confirmed_name(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        result = build_instrument_context("600519.SS", confirmed_name="贵州茅台")
        assert "Company: 贵州茅台" in result
        assert "Resolved identity" in result

    def test_with_identity_company_name(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        identity = {"company_name": "Apple Inc."}
        result = build_instrument_context("AAPL", identity=identity)
        assert "Company: Apple Inc." in result

    def test_with_sector_and_industry(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        identity = {
            "company_name": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
        }
        result = build_instrument_context("AAPL", identity=identity)
        assert "Technology / Consumer Electronics" in result

    def test_with_sector_only(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        identity = {"sector": "Technology"}
        result = build_instrument_context("AAPL", identity=identity)
        assert "Sector: Technology" in result

    def test_with_industry_only(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        identity = {"industry": "Semiconductors"}
        result = build_instrument_context("AAPL", identity=identity)
        assert "Industry: Semiconductors" in result

    def test_with_exchange(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        identity = {"exchange": "NASDAQ"}
        result = build_instrument_context("AAPL", identity=identity)
        assert "Exchange: NASDAQ" in result

    def test_crypto_asset(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        result = build_instrument_context("BTC-USD", asset_type="crypto")
        assert "asset" in result
        assert "crypto asset" in result
        assert "company fundamentals" in result

    def test_confirmed_name_takes_priority(self):
        from tradingagents.agents.utils.agent_utils import build_instrument_context

        identity = {"company_name": "Wrong Name"}
        result = build_instrument_context(
            "600519.SS",
            identity=identity,
            confirmed_name="贵州茅台",
        )
        assert "Company: 贵州茅台" in result
        assert "Wrong Name" not in result


# ---------------------------------------------------------------------------
# agent_utils — _all_a_share_names
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAllAShareNames:
    """Tests for _all_a_share_names — fetches A-share names from akshare."""

    def test_returns_names_from_akshare(self):
        from tradingagents.agents.utils.agent_utils import _all_a_share_names

        mock_df = MagicMock()
        mock_df.iterrows.return_value = [
            (0, {"name": "平安银行"}),
            (1, {"name": "贵州茅台"}),
        ]

        with patch("akshare.stock_info_a_code_name", return_value=mock_df):
            names = _all_a_share_names()

        assert "平安银行" in names
        assert "贵州茅台" in names
        assert len(names) == 2

    def test_returns_empty_set_on_exception(self):
        from tradingagents.agents.utils.agent_utils import _all_a_share_names

        with patch(
            "akshare.stock_info_a_code_name",
            side_effect=RuntimeError("akshare error"),
        ):
            names = _all_a_share_names()

        assert names == set()


# ---------------------------------------------------------------------------
# agent_utils — create_msg_delete
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCreateMsgDelete:
    """Tests for create_msg_delete — returns delete_messages function
    that clears messages and adds context-anchored placeholder."""

    def test_returns_delete_messages_function(self):
        from tradingagents.agents.utils.agent_utils import create_msg_delete

        result = create_msg_delete()
        assert callable(result)

    def test_delete_messages_clears_and_adds_placeholder(self):
        from tradingagents.agents.utils.agent_utils import create_msg_delete

        msg1 = MagicMock(id="msg1")
        msg2 = MagicMock(id="msg2")

        state = {
            "messages": [msg1, msg2],
            "company_of_interest": "AAPL",
            "asset_type": "stock",
            "trade_date": "2026-06-15",
        }

        delete_fn = create_msg_delete()
        result = delete_fn(state)

        assert "messages" in result
        messages = result["messages"]
        # Should have 2 RemoveMessage + 1 HumanMessage
        assert len(messages) == 3

        from langchain_core.messages import RemoveMessage
        assert isinstance(messages[0], RemoveMessage)
        assert messages[0].id == "msg1"
        assert isinstance(messages[1], RemoveMessage)
        assert messages[1].id == "msg2"

        from langchain_core.messages import HumanMessage
        assert isinstance(messages[2], HumanMessage)
        assert "AAPL" in messages[2].content
        assert "2026-06-15" in messages[2].content

    def test_delete_messages_defaults_date_when_missing(self):
        from tradingagents.agents.utils.agent_utils import create_msg_delete

        state = {
            "messages": [],
            "company_of_interest": "AAPL",
        }

        delete_fn = create_msg_delete()
        result = delete_fn(state)

        messages = result["messages"]
        assert len(messages) == 1  # Only the placeholder
        assert "the requested date" in messages[0].content


# ---------------------------------------------------------------------------
# agent_utils — get_or_build_data_quality_summary
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetOrBuildDataQualitySummary:
    """Tests for get_or_build_data_quality_summary — returns existing
    summary or builds from analyst reports."""

    def test_returns_existing_summary(self):
        from tradingagents.agents.utils.agent_utils import get_or_build_data_quality_summary

        result = get_or_build_data_quality_summary(
            {"data_quality_summary": "all data ok"}
        )
        assert result == "all data ok"

    def test_ignores_empty_string_summary(self):
        from tradingagents.agents.utils.agent_utils import get_or_build_data_quality_summary

        with patch(
            "tradingagents.graph.analyst_execution.build_data_quality_summary",
            return_value="built summary",
        ), patch(
            "tradingagents.graph.analyst_execution.ANALYST_NODE_SPECS",
            {"a": MagicMock(), "b": MagicMock()},
        ):
            result = get_or_build_data_quality_summary(
                {"data_quality_summary": ""}
            )
        assert result == "built summary"

    def test_missing_summary_builds_from_analyst_specs(self):
        from tradingagents.agents.utils.agent_utils import get_or_build_data_quality_summary

        with patch(
            "tradingagents.graph.analyst_execution.build_data_quality_summary",
            return_value="built from specs",
        ), patch(
            "tradingagents.graph.analyst_execution.ANALYST_NODE_SPECS",
            {"market": MagicMock(), "news": MagicMock()},
        ):
            result = get_or_build_data_quality_summary({})
        assert result == "built from specs"


# ===========================================================================
# _extract_market_analyst_price — pure regex function from the trader agent.
# Merged from tests/test_trader_extract_price.py and collected under
# @pytest.mark.parametrize to surface the regex variants as readable test ids.
# ===========================================================================


@pytest.mark.unit
class TestExtractMarketAnalystPrice:
    """Unit tests for the trader's regex-based price extractor."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param("", None, id="empty-string"),
            pytest.param("现价：160.51", "160.51", id="chinese-xianjia-fw-colon"),
            pytest.param("现价: 160.51", "160.51", id="chinese-xianjia-hw-colon"),
            pytest.param("收盘价: 91.60", "91.60", id="chinese-shoupanjia"),
            pytest.param("最新价：85.30", "85.30", id="chinese-zuixinjia"),
            pytest.param("价格: 50.00", "50.00", id="chinese-jiage"),
            pytest.param("Close: 91.60", "91.60", id="english-close"),
            pytest.param("Price: 150.25", "150.25", id="english-price"),
            pytest.param("当前价格是120.50元", "120.50", id="current-prefix"),
            pytest.param("| Close | 91.60 |", "91.60", id="table-row"),
            pytest.param("现价：100.50 Close: 200.75", "100.50", id="first-pattern-wins"),
            pytest.param("No price data available", None, id="no-match"),
            pytest.param("现价：100", "100", id="integer-price"),
        ],
    )
    def test_extract(self, text, expected):
        assert _extract_market_analyst_price(text) == expected

    def test_returns_none_for_none(self):
        # type: ignore[arg-type] — callsite-aware None contract test.
        assert _extract_market_analyst_price(None) is None


# ===========================================================================
# Deprecated create_social_media_analyst shim.
# Merged from tests/test_social_media_analyst_deprecated.py — guards the
# backwards-compatibility alias to create_sentiment_analyst.
# ===========================================================================


@pytest.mark.unit
class TestDeprecatedSocialMediaAnalyst:
    """Covers create_social_media_analyst's deprecation warning + delegation."""

    def test_emits_deprecation_warning(self) -> None:
        import warnings

        llm = MagicMock()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            create_social_media_analyst(llm)

        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 1
        msg = str(deprecation_warnings[0].message)
        assert "create_social_media_analyst" in msg
        assert "create_sentiment_analyst" in msg

    def test_returns_same_type_as_create_sentiment_analyst(self) -> None:
        llm = MagicMock()
        legacy_node = create_social_media_analyst(llm)
        current_node = create_sentiment_analyst(llm)
        assert callable(legacy_node)
        assert type(legacy_node) is type(current_node)

    def test_delegated_node_runs_same_logic(self) -> None:
        from tradingagents.agents.schemas import SentimentBand, SentimentReport

        report = SentimentReport(
            overall_band=SentimentBand.BULLISH,
            overall_score=8.0,
            confidence="high",
            narrative="Strong sentiment across all sources.",
        )
        structured = MagicMock()
        structured.invoke.return_value = report
        llm = MagicMock()
        llm.with_structured_output.return_value = structured

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            node = create_social_media_analyst(llm)

        result = node({
            "company_of_interest": "NVDA",
            "trade_date": "2026-01-15",
            "asset_type": "stock",
            "messages": [],
        })
        sr = result["sentiment_report"]
        assert "Bullish" in sr
        assert "(Score: 8.0/10)" in sr


# ===========================================================================
# i18n: every report-producing agent must apply get_language_instruction().
# Merged from tests/test_i18n_coverage.py — static-analysis only; the
# get_language_instruction() runtime tests already live in
# TestGetLanguageInstruction above.
# ===========================================================================


_AGENTS_DIR = Path(__file__).resolve().parents[1] / "tradingagents" / "agents"

# Every node whose text reaches the saved report. If you add a
# report-producing agent, add it here and make it call
# get_language_instruction().
_REPORT_AGENTS = [
    "analysts/market_analyst.py",
    "analysts/news_analyst.py",
    "analysts/fundamentals_analyst.py",
    "analysts/sentiment_analyst.py",
    "researchers/bull_researcher.py",
    "researchers/bear_researcher.py",
    "managers/research_manager.py",
    "managers/portfolio_manager.py",
    "risk_mgmt/aggressive_debator.py",
    "risk_mgmt/conservative_debator.py",
    "risk_mgmt/neutral_debator.py",
    "trader/trader.py",
]


@pytest.mark.unit
@pytest.mark.parametrize("rel", _REPORT_AGENTS)
def test_report_agent_applies_language_instruction(rel):
    path = _AGENTS_DIR / rel
    assert path.exists(), f"missing agent module: {rel}"
    src = path.read_text(encoding="utf-8")
    assert "get_language_instruction()" in src, (
        f"{rel} does not apply get_language_instruction(); its output would "
        f"ignore the configured output_language (#740/#801)."
    )
