"""Coverage gap tests for small edge cases across multiple modules."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.interface import (
    VENDOR_METHODS,
    get_category_for_method,
    route_to_vendor,
)
from tradingagents.dataflows.stockstats_utils import (
    MAX_OHLCV_STALE_DAYS,
    _assert_ohlcv_not_stale,
)
from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.portfolio.models import Holding, Portfolio, PortfolioMetadata, Transaction
from tradingagents.portfolio.validators import (
    _parse_number,
    deduplicate_holdings,
    normalize_ticker,
    validate_holding,
)
from tradingagents.agents.utils.rating import parse_rating


# =========================================================================
# portfolio/validators.py — edge cases
# =========================================================================

@pytest.mark.unit
class ValidatorsEdgeTests(unittest.TestCase):
    """Coverage for:
    - _parse_number: non-str non-int/float raise
    - normalize_ticker: isdigit but not 6/0/3 prefix
    - validate_holding: avg_cost < 0
    """

    def test_parse_number_raises_on_non_string(self):
        with self.assertRaises(ValueError):
            _parse_number(None)
        with self.assertRaises(ValueError):
            _parse_number([])

    def test_normalize_ticker_numeric_no_prefix_match(self):
        result = normalize_ticker("123456")
        self.assertEqual(result, "123456")

    def test_validate_holding_negative_avg_cost(self):
        h = Holding(ticker="AAPL", shares=100, avg_cost=-1.0)
        self.assertIsNone(validate_holding(h))


# =========================================================================
# portfolio/models.py — edge cases
# =========================================================================

@pytest.mark.unit
class PortfolioModelsEdgeTests(unittest.TestCase):
    """Coverage for Holding/Transaction/Portfolio edge cases."""

    def test_holding_to_dict_excludes_empty_strings(self):
        h = Holding(ticker="AAPL", shares=100, avg_cost=50.0, name="")
        d = h.to_dict()
        self.assertNotIn("name", d)

    def test_holding_from_dict_with_fallback_ticker(self):
        d = {"shares": 50, "avg_cost": 30.0}
        h = Holding.from_dict(d, ticker="MSFT")
        self.assertEqual(h.ticker, "MSFT")

    def test_portfolio_metadata_to_dict_excludes_none(self):
        m = PortfolioMetadata()
        d = m.to_dict()
        self.assertNotIn("updated_at", d)

    def test_transaction_to_dict_excludes_empty_name(self):
        t = Transaction(date="2025-01-01", ticker="AAPL")
        d = t.to_dict()
        self.assertNotIn("name", d)

    def test_portfolio_methods(self):
        h1 = Holding(ticker="A", shares=100, avg_cost=50.0)
        h2 = Holding(ticker="B", shares=200, avg_cost=30.0)
        p = Portfolio(holdings={"A": h1, "B": h2})
        self.assertIs(p.get_holding("A"), h1)
        self.assertTrue(p.has_holding("A"))
        self.assertFalse(p.has_holding("C"))
        self.assertEqual(p.total_invested(), 100 * 50 + 200 * 30)

    def test_portfolio_total_market_value_with_market_price(self):
        h = Holding(ticker="A", shares=100, avg_cost=50.0, market_price=60.0)
        p = Portfolio(holdings={"A": h})
        self.assertEqual(p.total_market_value(), 100 * 60.0)

    def test_portfolio_total_pnl(self):
        h = Holding(ticker="A", shares=100, avg_cost=50.0, market_price=60.0)
        p = Portfolio(holdings={"A": h})
        self.assertAlmostEqual(p.total_pnl(), 100 * (60.0 - 50.0))

    def test_portfolio_total_pnl_negative(self):
        h = Holding(ticker="A", shares=100, avg_cost=50.0, market_price=40.0)
        p = Portfolio(holdings={"A": h})
        self.assertAlmostEqual(p.total_pnl(), 100 * (40.0 - 50.0))

    def test_portfolio_from_dict(self):
        d = {
            "holdings": {"A": {"shares": 100, "avg_cost": 50.0, "market_price": 60.0}},
            "metadata": {"source_type": "manual"},
            "transactions": [{"date": "2025-01-01", "ticker": "A"}],
        }
        p = Portfolio.from_dict(d)
        self.assertIn("A", p.holdings)
        self.assertEqual(p.metadata.source_type, "manual")
        self.assertEqual(len(p.transactions), 1)

    def test_portfolio_holdings_excludes_empty_strings(self):
        h = Holding(ticker="A", shares=100, avg_cost=50.0, name="x", notes="", grid_strategy="test")
        d = h.to_dict()
        self.assertNotIn("notes", d)

    def test_portfolio_to_dict_serializes_transactions(self):
        t = Transaction(date="2025-01-01", ticker="A", price=100.0, action="买入", shares=10)
        p = Portfolio(
            holdings={"A": Holding(ticker="A", shares=10, avg_cost=100.0)},
            transactions=[t],
        )
        d = p.to_dict()
        self.assertEqual(len(d["transactions"]), 1)


# =========================================================================
# agents/utils/rating.py — Chinese rating fallback
# =========================================================================

@pytest.mark.unit
class RatingEdgeTests(unittest.TestCase):
    def test_chinese_word_direct_match(self):
        result = parse_rating("我认为 买入 是合理的")
        self.assertEqual(result, "Buy")

    def test_chinese_word_underweight(self):
        result = parse_rating("建议 减持")
        self.assertEqual(result, "Underweight")


# =========================================================================
# dataflows/config.py — get_config initialized branch
# =========================================================================

@pytest.mark.unit
class ConfigEdgeTests(unittest.TestCase):
    def test_get_config_initialized(self):
        from tradingagents.dataflows import config as cfg
        cfg._config = {"test": True}
        result = get_config()
        self.assertEqual(result["test"], True)


# =========================================================================
# dataflows/akshare_common.py — no_proxy context manager
# =========================================================================

@pytest.mark.unit
class AkshareCommonEdgeTests(unittest.TestCase):
    def test_no_proxy_clears_env_and_restores(self):
        import os
        from tradingagents.dataflows.akshare_common import no_proxy

        os.environ["HTTP_PROXY"] = "http://proxy:8080"
        os.environ["HTTPS_PROXY"] = "https://proxy:8080"
        with no_proxy():
            self.assertNotIn("HTTP_PROXY", os.environ)
            self.assertNotIn("HTTPS_PROXY", os.environ)
        self.assertEqual(os.environ.get("HTTP_PROXY"), "http://proxy:8080")
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "https://proxy:8080")
        del os.environ["HTTP_PROXY"]
        del os.environ["HTTPS_PROXY"]


# =========================================================================
# dataflows/interface.py — edge cases
# =========================================================================

@pytest.mark.unit
class InterfaceEdgeTests(unittest.TestCase):
    def test_get_category_for_method_not_found(self):
        with self.assertRaises(ValueError) as ctx:
            get_category_for_method("nonexistent_method")
        self.assertIn("nonexistent_method", str(ctx.exception))

    def test_route_to_vendor_method_not_found(self):
        with self.assertRaises(ValueError) as ctx:
            route_to_vendor("nonexistent_method")
        self.assertIn("not found", str(ctx.exception))


# =========================================================================
# dataflows/stockstats_utils.py — MAX_OHLCV_STALE_DAYS constant
# =========================================================================

@pytest.mark.unit
class StockstatsConstantsTest(unittest.TestCase):
    def test_max_stale_days_constant_exists(self):
        self.assertEqual(MAX_OHLCV_STALE_DAYS, 10)


# =========================================================================
# dataflows/symbol_utils.py — normalize_symbol
# =========================================================================

@pytest.mark.unit
class SymbolUtilsEdgeTests(unittest.TestCase):
    def test_normalize_symbol_passthrough(self):
        result = normalize_symbol("AAPL")
        self.assertEqual(result, "AAPL")

    def test_normalize_symbol_empty_string(self):
        self.assertEqual(normalize_symbol(""), "")

    def test_normalize_symbol_gold_alias(self):
        result = normalize_symbol("XAUUSD")
        self.assertEqual(result, "GC=F")

    def test_normalize_symbol_crypto(self):
        result = normalize_symbol("BTCUSD")
        self.assertEqual(result, "BTC-USD")

    def test_normalize_symbol_forex(self):
        result = normalize_symbol("EURUSD")
        self.assertEqual(result, "EURUSD=X")


# =========================================================================
# agents/utils/agent_utils.py — get_language_instruction edge
# =========================================================================

@pytest.mark.unit
class AgentUtilsEdgeTests(unittest.TestCase):
    @patch("tradingagents.dataflows.config.get_config")
    def test_get_language_instruction_chinese(self, mock_config):
        from tradingagents.agents.utils.agent_utils import get_language_instruction
        mock_config.return_value = {"output_language": "Chinese"}
        result = get_language_instruction()
        self.assertIn("Chinese", result)

    @patch("tradingagents.dataflows.config.get_config")
    def test_get_language_instruction_case_insensitive(self, mock_config):
        from tradingagents.agents.utils.agent_utils import get_language_instruction
        mock_config.return_value = {"output_language": "cHiNeSe"}
        result = get_language_instruction()
        self.assertIn("cHiNeSe", result)

    @patch("tradingagents.dataflows.config.get_config")
    def test_get_language_instruction_english_returns_empty(self, mock_config):
        from tradingagents.agents.utils.agent_utils import get_language_instruction
        mock_config.return_value = {"output_language": "English"}
        result = get_language_instruction()
        self.assertEqual(result, "")


# =========================================================================
# agents/utils/technical_indicators_tools.py — edge cases via .func
# =========================================================================

@pytest.mark.unit
class TechnicalIndicatorsToolsEdgeTests(unittest.TestCase):
    @patch("tradingagents.agents.utils.technical_indicators_tools.route_to_vendor")
    def test_get_indicators_rejects_fund_flow(self, mock_route):
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators
        result = get_indicators.func("AAPL", "get_fund_flow", "2025-01-01")
        self.assertIn("standalone tool", result)
        mock_route.assert_not_called()

    @patch("tradingagents.agents.utils.technical_indicators_tools.route_to_vendor")
    def test_get_indicators_multi_indicator_splits(self, mock_route):
        mock_route.return_value = "ind"
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators
        result = get_indicators.func("AAPL", "rsi, macd", "2025-01-01")
        self.assertEqual(mock_route.call_count, 2)

    @patch("tradingagents.agents.utils.technical_indicators_tools.route_to_vendor")
    def test_get_indicators_runtime_error(self, mock_route):
        mock_route.side_effect = RuntimeError("vendor fail")
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators
        result = get_indicators.func("AAPL", "rsi", "2025-01-01")
        self.assertIn("vendor fail", result)

    @patch("tradingagents.agents.utils.technical_indicators_tools.route_to_vendor")
    def test_get_indicators_generic_exception(self, mock_route):
        mock_route.side_effect = ValueError("bad arg")
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators
        result = get_indicators.func("AAPL", "macd", "2025-01-01")
        self.assertIn("bad arg", result)

    @patch("tradingagents.agents.utils.technical_indicators_tools.route_to_vendor")
    def test_get_indicators_with_lookback(self, mock_route):
        mock_route.return_value = "result"
        from tradingagents.agents.utils.technical_indicators_tools import get_indicators
        result = get_indicators.func("AAPL", "sma_20", "2025-01-01", look_back_days=30)
        self.assertEqual(result, "result")


# =========================================================================
# agents/schemas — TraderProposal and PortfolioDecision edge cases
# =========================================================================

@pytest.mark.unit
class TraderEdgeTests(unittest.TestCase):
    def test_trader_proposal_defaults(self):
        from tradingagents.agents.schemas import TraderProposal
        p = TraderProposal(action="Buy", entry_price=100.0, reasoning="Bullish", position_sizing="5%")
        self.assertEqual(p.action, "Buy")
        self.assertIsNone(p.stop_loss)

    def test_trader_proposal_with_all_fields(self):
        from tradingagents.agents.schemas import TraderProposal
        p = TraderProposal(
            action="Sell",
            entry_price=150.0,
            stop_loss=145.0,
            reasoning="Overvalued",
            position_sizing="3%",
        )
        self.assertEqual(p.reasoning, "Overvalued")


@pytest.mark.unit
class PortfolioManagerEdgeTests(unittest.TestCase):
    def test_portfolio_decision_defaults(self):
        from tradingagents.agents.schemas import PortfolioDecision
        d = PortfolioDecision(rating="Hold", executive_summary="Wait", investment_thesis="Neutral")
        self.assertEqual(d.rating, "Hold")

    def test_portfolio_decision_optional_fields(self):
        from tradingagents.agents.schemas import PortfolioDecision
        d = PortfolioDecision(
            rating="Buy",
            executive_summary="Buy now",
            investment_thesis="Bullish",
            price_target=150.0,
            time_horizon="3 months",
            entry_price=140.0,
            position_size="10%",
        )
        self.assertEqual(d.price_target, 150.0)
        self.assertEqual(d.time_horizon, "3 months")


# =========================================================================
# dataflows/akshare_vendor.py — tqdm patch
# =========================================================================

@pytest.mark.unit
class AkshareVendorEdgeTests(unittest.TestCase):
    def test_tqdm_disabled_import(self):
        import tradingagents.dataflows.akshare_vendor
        self.assertTrue(True)


# =========================================================================
# llm_clients/pricing.py — edge cases
# =========================================================================

@pytest.mark.unit
class PricingEdgeTests(unittest.TestCase):
    def test_get_price_missing_provider(self):
        from tradingagents.llm_clients.pricing import get_price
        result = get_price("nonexistent_provider", "some-model")
        self.assertIsNone(result)

    def test_get_price_missing_model(self):
        from tradingagents.llm_clients.pricing import get_price
        result = get_price("openai", "nonexistent-model")
        self.assertIsNone(result)

    def test_get_price_for_model_missing(self):
        from tradingagents.llm_clients.pricing import get_price_for_model
        result = get_price_for_model("completely-fake-model-42")
        self.assertIsNone(result)


# =========================================================================
# agents/utils/memory.py — TradingMemoryLog edge cases
# =========================================================================

@pytest.mark.unit
class MemoryLogEdgeTests(unittest.TestCase):
    """Edge case tests for TradingMemoryLog."""

    def setUp(self):
        from tradingagents.default_config import DEFAULT_CONFIG
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config = dict(DEFAULT_CONFIG)
        self.config["memory_log_path"] = str(Path(self.tmpdir.name) / "mem.md")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _log(self):
        from tradingagents.agents.utils.memory import TradingMemoryLog
        return TradingMemoryLog(self.config)

    def test_store_decision_creates_entry(self):
        log = self._log()
        log.store_decision("AAPL", "2025-06-01", "Bullish outlook")
        entries = log.load_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ticker"], "AAPL")

    def test_store_decision_deduplicates(self):
        log = self._log()
        log.store_decision("AAPL", "2025-06-01", "Bullish outlook")
        log.store_decision("AAPL", "2025-06-01", "Bullish outlook")
        entries = log.load_entries()
        self.assertEqual(len(entries), 1)

    def test_load_entries_empty_when_no_file(self):
        log = self._log()
        log._log_path = Path(self.tmpdir.name) / "nonexistent.md"
        entries = log.load_entries()
        self.assertEqual(entries, [])

    def test_update_with_outcome_nonexistent_entry(self):
        log = self._log()
        log.update_with_outcome("NONEXISTENT", "2025-06-01", 0.05, 0.03, 10, "ok")
        entries = log.load_entries()
        self.assertEqual(entries, [])

    def test_update_with_outcome_updates_pending(self):
        log = self._log()
        log.store_decision("AAPL", "2025-06-01", "Buy it")
        log.update_with_outcome("AAPL", "2025-06-01", 0.05, 0.03, 10, "Worked well")
        entries = log.load_entries()
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["pending"])

    def test_batch_update_with_outcomes(self):
        log = self._log()
        log.store_decision("AAPL", "2025-06-01", "Buy")
        log.store_decision("MSFT", "2025-06-01", "Sell")
        updates = [
            {"ticker": "AAPL", "trade_date": "2025-06-01", "raw_return": 0.05, "alpha_return": 0.03, "holding_days": 10, "reflection": "Good"},
            {"ticker": "MSFT", "trade_date": "2025-06-01", "raw_return": -0.02, "alpha_return": -0.01, "holding_days": 5, "reflection": "Bad"},
        ]
        log.batch_update_with_outcomes(updates)
        entries = log.load_entries()
        self.assertEqual(len(entries), 2)
        self.assertFalse(entries[0]["pending"])
        self.assertFalse(entries[1]["pending"])

    def test_batch_update_with_outcomes_empty(self):
        log = self._log()
        log.batch_update_with_outcomes([])
        entries = log.load_entries()
        self.assertEqual(entries, [])

    def test_get_pending_entries(self):
        log = self._log()
        log.store_decision("AAPL", "2025-06-01", "Hold")
        pending = log.get_pending_entries()
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]["pending"])

    def test_get_past_context_empty(self):
        log = self._log()
        ctx = log.get_past_context("AAPL")
        self.assertEqual(ctx, "")

    def test_get_past_context_no_pending(self):
        log = self._log()
        log.store_decision("AAPL", "2025-06-01", "Buy")
        log.update_with_outcome("AAPL", "2025-06-01", 0.05, 0.03, 10, "Good")
        ctx = log.get_past_context("AAPL")
        self.assertIn("AAPL", ctx)

    def test_log_path_none_returns_silently(self):
        from tradingagents.agents.utils.memory import TradingMemoryLog
        log = TradingMemoryLog({})
        log.store_decision("AAPL", "2025-06-01", "test")
        self.assertIsNone(log._log_path)
