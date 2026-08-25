"""Tests for trading_graph: constructor, fetch returns, resolve pending entries,
log state, run graph, propagate checkpoint paths, and helper methods."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from langgraph.prebuilt import ToolNode

from tradingagents.default_config import DEFAULT_CONFIG


def _minimal_config():
    """Return a config that safely passes through constructor checks."""
    c = dict(DEFAULT_CONFIG)
    c["data_cache_dir"] = "/tmp/tradingagents_test_cache"
    c["results_dir"] = "/tmp/tradingagents_test_results"
    return c


def _construct_graph(config_override=None, callbacks=None):
    """Helper: build a TradingAgentsGraph with extensive mocking."""
    config = {**(_minimal_config()), **(config_override or {})}
    with (
        patch("tradingagents.graph.trading_graph.set_config") as mock_set_config,
        patch("tradingagents.graph.trading_graph.os.makedirs"),
        patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
        patch("tradingagents.graph.trading_graph.TradingMemoryLog") as mock_memory_log,
        patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
        patch("tradingagents.graph.trading_graph.Propagator") as mock_propagator,
        patch("tradingagents.graph.trading_graph.Reflector") as mock_reflector,
        patch("tradingagents.graph.trading_graph.SignalProcessor") as mock_signal_proc,
    ):
        mock_llm_instance = MagicMock()
        mock_llm_instance.get_llm.return_value = MagicMock()
        mock_create_llm.return_value = mock_llm_instance

        mock_workflow = MagicMock()
        mock_graph_setup_instance = MagicMock()
        mock_graph_setup_instance.setup_graph.return_value = mock_workflow
        mock_graph_setup.return_value = mock_graph_setup_instance

        mock_propagator_instance = MagicMock()
        mock_propagator.return_value = mock_propagator_instance

        mock_reflector_instance = MagicMock()
        mock_reflector.return_value = mock_reflector_instance

        mock_signal_proc_instance = MagicMock()
        mock_signal_proc.return_value = mock_signal_proc_instance

        mock_memory_log_instance = MagicMock()
        mock_memory_log.return_value = mock_memory_log_instance

        from tradingagents.graph.trading_graph import TradingAgentsGraph

        g = TradingAgentsGraph(
            selected_analysts=["market", "social"],
            debug=False,
            config=config,
            callbacks=callbacks,
        )
        # Store mocks for verification
        g._mocks = {
            "set_config": mock_set_config,
            "create_llm": mock_create_llm,
            "memory_log": mock_memory_log,
            "graph_setup": mock_graph_setup,
            "propagator": mock_propagator,
            "reflector": mock_reflector,
            "signal_processor": mock_signal_proc,
            "llm_instance": mock_llm_instance,
            "workflow": mock_workflow,
            "graph_setup_instance": mock_graph_setup_instance,
            "propagator_instance": mock_propagator_instance,
        }
        return g


def _make_graph():
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    with patch.object(TradingAgentsGraph, "__init__", return_value=None):
        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        g.config = {
            "data_cache_dir": "/tmp/tradingagents_test_cache",
            "results_dir": "/tmp/tradingagents_test_results",
            "benchmark_map": {"": "SPY"},
            "checkpoint_enabled": False,
        }
        g.callbacks = []
        g.memory_log = MagicMock()
        g.reflector = MagicMock()
        g.log_states_dict = {}
        g.ticker = "AAPL"
        g.debug = False
        g.node_timings = []
        g.analyst_wall_times = {}
        g.analyst_wall_time_summary = ""
        g.total_stream_time = 0.0
        g.signal_processor = MagicMock()
        g.signal_processor.process_signal.return_value = "Buy"
        g.propagator = MagicMock()
        g.propagator.create_initial_state.return_value = {
            "company_of_interest": "AAPL",
            "trade_date": "2026-06-15",
            "market_report": "",
            "verified_market_snapshot": "MOCK_SNAPSHOT",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "governance_report": "",
            "industry_report": "",
            "investment_debate_state": {
                "bull_history": [], "bear_history": [],
                "history": [], "current_response": "",
                "judge_decision": "",
            },
            "trader_investment_plan": {},
            "risk_debate_state": {
                "aggressive_history": [], "conservative_history": [],
                "neutral_history": [], "history": [],
                "judge_decision": "",
            },
            "investment_plan": {},
            "final_trade_decision": "Hold",
        }
        g.propagator.get_graph_args.return_value = {"config": {}}
        g.selected_analysts = ["market"]
        g.graph = MagicMock()
        g.workflow = MagicMock()
        g.graph_setup = MagicMock()
        g.graph_setup.setup_graph.return_value = MagicMock()
        g.resolve_instrument_context = MagicMock(return_value="context")
        g._checkpointer_ctx = None
        return g


# ---------------------------------------------------------------------------
# _resolve_benchmark
# ---------------------------------------------------------------------------


@pytest.mark.unit
class ResolveBenchmarkTests(unittest.TestCase):
    def _make_graph(self, config):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            g.config = config
            return g

    def test_uses_explicit_benchmark(self):
        g = self._make_graph({"benchmark_ticker": "^N225", "benchmark_map": {"": "SPY"}})
        self.assertEqual(g._resolve_benchmark("7203.T"), "^N225")

    def test_matches_by_suffix(self):
        g = self._make_graph({
            "benchmark_ticker": None,
            "benchmark_map": {".T": "^N225", ".HK": "^HSI", "": "SPY"},
        })
        self.assertEqual(g._resolve_benchmark("7203.T"), "^N225")

    def test_case_insensitive_suffix_match(self):
        g = self._make_graph({
            "benchmark_ticker": None,
            "benchmark_map": {".to": "^GSPTSE", "": "SPY"},
        })
        self.assertEqual(g._resolve_benchmark("BNS.TO"), "^GSPTSE")

    def test_falls_back_to_empty_suffix(self):
        g = self._make_graph({
            "benchmark_ticker": None,
            "benchmark_map": {"": "SPY"},
        })
        self.assertEqual(g._resolve_benchmark("AAPL"), "SPY")

    def test_unknown_suffix_falls_back_to_empty(self):
        g = self._make_graph({
            "benchmark_ticker": None,
            "benchmark_map": {".T": "^N225", "": "SPY"},
        })
        self.assertEqual(g._resolve_benchmark("FOO.XX"), "SPY")

    def test_no_suffix_on_ticker_with_dot(self):
        """Tickers like BRK.B have a dot but no mapped suffix -> fallback."""
        g = self._make_graph({
            "benchmark_ticker": None,
            "benchmark_map": {"": "SPY"},
        })
        self.assertEqual(g._resolve_benchmark("BRK.B"), "SPY")

    def test_empty_benchmark_map_uses_default(self):
        g = self._make_graph({
            "benchmark_ticker": None,
            "benchmark_map": {},
        })
        self.assertEqual(g._resolve_benchmark("AAPL"), "SPY")

    def test_explicit_benchmark_wins_over_suffix(self):
        g = self._make_graph({
            "benchmark_ticker": "SPY",
            "benchmark_map": {".T": "^N225", "": "SPY"},
        })
        self.assertEqual(g._resolve_benchmark("7203.T"), "SPY")


# ---------------------------------------------------------------------------
# _get_provider_kwargs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class GetProviderKwargsTests(unittest.TestCase):
    def _make_graph(self, config):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            g.config = config
            return g

    def test_temperature_conversion(self):
        g = self._make_graph({"llm_provider": "openai", "temperature": "0.5"})
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["temperature"], 0.5)

    def test_temperature_as_float_passthrough(self):
        g = self._make_graph({"llm_provider": "openai", "temperature": 0.7})
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["temperature"], 0.7)

    def test_no_temperature_when_none(self):
        g = self._make_graph({"llm_provider": "openai", "temperature": None})
        kwargs = g._get_provider_kwargs()
        self.assertNotIn("temperature", kwargs)

    def test_no_temperature_when_empty_string(self):
        g = self._make_graph({"llm_provider": "openai", "temperature": ""})
        kwargs = g._get_provider_kwargs()
        self.assertNotIn("temperature", kwargs)

    def test_temperature_missing_key(self):
        g = self._make_graph({"llm_provider": "openai"})
        kwargs = g._get_provider_kwargs()
        self.assertNotIn("temperature", kwargs)

    def test_google_thinking_level(self):
        g = self._make_graph({"llm_provider": "google", "google_thinking_level": "high"})
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["thinking_level"], "high")

    def test_google_no_thinking_level(self):
        g = self._make_graph({"llm_provider": "google"})
        kwargs = g._get_provider_kwargs()
        self.assertNotIn("thinking_level", kwargs)

    def test_openai_reasoning_effort(self):
        g = self._make_graph({"llm_provider": "openai", "openai_reasoning_effort": "high"})
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["reasoning_effort"], "high")

    def test_openai_no_reasoning_effort(self):
        g = self._make_graph({"llm_provider": "openai"})
        kwargs = g._get_provider_kwargs()
        self.assertNotIn("reasoning_effort", kwargs)

    def test_anthropic_effort(self):
        g = self._make_graph({"llm_provider": "anthropic", "anthropic_effort": "high"})
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["effort"], "high")

    def test_anthropic_no_effort(self):
        g = self._make_graph({"llm_provider": "anthropic"})
        kwargs = g._get_provider_kwargs()
        self.assertNotIn("effort", kwargs)

    def test_unknown_provider_no_extra_kwargs(self):
        g = self._make_graph({"llm_provider": "unknown_provider"})
        kwargs = g._get_provider_kwargs()
        # Retry config is always forwarded; provider-specific kwargs are not.
        self.assertIn("retry_config", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("effort", kwargs)
        self.assertNotIn("thinking_level", kwargs)
        self.assertNotIn("reasoning_effort", kwargs)

    def test_provider_case_insensitive(self):
        g = self._make_graph({"llm_provider": "OpenAI", "openai_reasoning_effort": "medium"})
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["reasoning_effort"], "medium")

    def test_temperature_with_google(self):
        g = self._make_graph({
            "llm_provider": "google",
            "google_thinking_level": "high",
            "temperature": "0.3",
        })
        kwargs = g._get_provider_kwargs()
        self.assertEqual(kwargs["thinking_level"], "high")
        self.assertEqual(kwargs["temperature"], 0.3)


# ---------------------------------------------------------------------------
# _create_tool_nodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class CreateToolNodesTests(unittest.TestCase):
    def _make_graph(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            return g

    def test_returns_dict_with_all_keys(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        expected_keys = {"market", "social", "news", "governance", "industry", "fundamentals"}
        self.assertEqual(set(nodes.keys()), expected_keys)

    def test_all_values_are_tool_nodes(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        for key, node in nodes.items():
            with self.subTest(key=key):
                self.assertIsInstance(node, ToolNode)

    def test_market_node_has_stock_data_and_indicators(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["market"].tools_by_name.keys())
        for name in ("get_stock_data", "get_indicators", "get_fund_flow",
                      "get_sector_fund_flow", "get_chip_distribution",
                      "get_limit_up_down", "get_index_daily",
                      "get_verified_market_snapshot"):
            with self.subTest(tool=name):
                self.assertIn(name, tool_names)

    def test_social_node_has_news(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["social"].tools_by_name.keys())
        self.assertIn("get_news", tool_names)

    def test_news_node_has_multiple_tools(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["news"].tools_by_name.keys())
        for name in ("get_news", "get_global_news", "get_insider_transactions",
                      "get_company_announcements", "get_macro_indicators",
                      "get_research_reports", "get_cailianpress_telegrams"):
            with self.subTest(tool=name):
                self.assertIn(name, tool_names)

    def test_news_node_has_research_reports(self):
        """get_research_reports must be registered in the news ToolNode so the LLM can call it."""
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["news"].tools_by_name.keys())
        self.assertIn("get_research_reports", tool_names)

    def test_governance_node_has_governance_tools(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["governance"].tools_by_name.keys())
        for name in ("get_company_announcements", "get_insider_transactions",
                      "get_news", "get_restricted_release",
                      "get_institutional_intelligence", "get_northbound_hold",
                      "get_margin_trading", "get_pledge_ratio", "get_dragon_tiger",
                      "get_block_trade"):
            with self.subTest(tool=name):
                self.assertIn(name, tool_names)

    def test_industry_node_has_industry_tools(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["industry"].tools_by_name.keys())
        for name in ("get_industry_valuation", "get_concept_board",
                      "get_macro_indicators", "get_sector_fund_flow"):
            with self.subTest(tool=name):
                self.assertIn(name, tool_names)

    def test_fundamentals_node_has_fundamental_tools(self):
        g = self._make_graph()
        nodes = g._create_tool_nodes()
        tool_names = list(nodes["fundamentals"].tools_by_name.keys())
        for name in ("get_fundamentals", "get_balance_sheet", "get_cashflow",
                      "get_income_statement", "get_historical_valuation",
                      "get_earnings_forecast", "get_earnings_estimates",
                      "get_shareholder_count", "get_dividend_history"):
            with self.subTest(tool=name):
                self.assertIn(name, tool_names)


# ---------------------------------------------------------------------------
# process_signal
# ---------------------------------------------------------------------------


@pytest.mark.unit
class ProcessSignalTests(unittest.TestCase):
    def test_delegates_to_signal_processor(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        mock_processor = MagicMock()
        mock_processor.process_signal.return_value = "Buy"
        g.signal_processor = mock_processor
        result = g.process_signal("**Rating**: Buy")
        self.assertEqual(result, "Buy")
        mock_processor.process_signal.assert_called_once_with("**Rating**: Buy")


# ---------------------------------------------------------------------------
# resolve_instrument_context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class ResolveInstrumentContextTests(unittest.TestCase):
    def test_delegates_to_agent_utils(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        with (
            patch("tradingagents.graph.trading_graph.resolve_instrument_identity") as mock_resolve,
            patch("tradingagents.graph.trading_graph.build_instrument_context") as mock_build,
        ):
            mock_resolve.return_value = {"company_name": "Apple Inc.", "sector": "Technology"}
            mock_build.return_value = "Instrument: AAPL (Apple Inc.)"

            result = g.resolve_instrument_context("AAPL", asset_type="stock")

            mock_resolve.assert_called_once_with("AAPL")
            mock_build.assert_called_once_with(
                "AAPL", "stock", {"company_name": "Apple Inc.", "sector": "Technology"},
                confirmed_name=None,
            )
            self.assertEqual(result, "Instrument: AAPL (Apple Inc.)")

    def test_defaults_to_stock_asset_type(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        with (
            patch("tradingagents.graph.trading_graph.resolve_instrument_identity") as mock_resolve,
            patch("tradingagents.graph.trading_graph.build_instrument_context") as mock_build,
        ):
            mock_resolve.return_value = {}
            mock_build.return_value = "Instrument: BTC"

            result = g.resolve_instrument_context("BTC", asset_type="crypto")

            mock_build.assert_called_once_with("BTC", "crypto", {}, confirmed_name=None)
            self.assertEqual(result, "Instrument: BTC")

    def test_confirmed_name_overrides_identity(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        with (
            patch("tradingagents.graph.trading_graph.resolve_instrument_identity") as mock_resolve,
            patch("tradingagents.graph.trading_graph.build_instrument_context") as mock_build,
        ):
            mock_resolve.return_value = {"company_name": "WRONG NAME"}
            mock_build.return_value = "Instrument: 300002.SZ"

            result = g.resolve_instrument_context(
                "300002.SZ", asset_type="stock", confirmed_name="神州泰岳",
            )

            mock_build.assert_called_once_with(
                "300002.SZ", "stock", {"company_name": "WRONG NAME"},
                confirmed_name="神州泰岳",
            )
            self.assertEqual(result, "Instrument: 300002.SZ")

    def test_identity_lookup_failure_returns_ticker_only_context(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        g = TradingAgentsGraph.__new__(TradingAgentsGraph)
        with (
            patch("tradingagents.graph.trading_graph.resolve_instrument_identity") as mock_resolve,
            patch("tradingagents.graph.trading_graph.build_instrument_context") as mock_build,
        ):
            mock_resolve.return_value = {}
            mock_build.return_value = "Instrument: `XYZ`."

            result = g.resolve_instrument_context("XYZ")
            self.assertEqual(result, "Instrument: `XYZ`.")


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class ConstructorTests(unittest.TestCase):
    def setUp(self):
        self.config = _minimal_config()

    def test_default_analysts(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            g = TradingAgentsGraph(config=self.config)

            self.assertEqual(g.selected_analysts, ["market", "social", "news", "fundamentals"])
            self.assertFalse(g.debug)
            self.assertEqual(g.config["data_cache_dir"], self.config["data_cache_dir"])
            mock_setup_instance.setup_graph.assert_called_once_with(
                ["market", "social", "news", "fundamentals"]
            )

    def test_custom_analysts(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            g = TradingAgentsGraph(
                selected_analysts=["market", "news"],
                config=self.config,
            )

            self.assertEqual(g.selected_analysts, ["market", "news"])
            mock_setup_instance.setup_graph.assert_called_once_with(["market", "news"])

    def test_callbacks_passed_to_llm(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        callback_obj = MagicMock()

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            g = TradingAgentsGraph(config=self.config, callbacks=[callback_obj])

            # Both LLM clients should have received callbacks in kwargs
            for call_args in mock_create_llm.call_args_list:
                self.assertIn("callbacks", call_args.kwargs)
                self.assertEqual(call_args.kwargs["callbacks"], [callback_obj])

            self.assertEqual(g.callbacks, [callback_obj])

    def test_no_callbacks_empty_list(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            g = TradingAgentsGraph(config=self.config)

            for call_args in mock_create_llm.call_args_list:
                self.assertNotIn("callbacks", call_args.kwargs)

            self.assertEqual(g.callbacks, [])

    def test_creates_deep_and_quick_llms(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            def _side_effect(*args, **kwargs):
                client = MagicMock()
                client.get_llm.return_value = (
                    f"llm:{kwargs.get('provider')}:{kwargs.get('model')}"
                )
                return client

            mock_create_llm.side_effect = _side_effect

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            g = TradingAgentsGraph(config=self.config)

            cfg = self.config
            primary_provider = cfg["llm_provider"]

            def expected_chain(model_key, fallback_key, model_override=None):
                primary = (primary_provider, model_override or cfg[model_key])
                rest = [
                    (e["provider"], e["model"])
                    for e in cfg[fallback_key]
                    if (e["provider"], e["model"]) != primary
                ]
                return [primary, *rest]

            # Constructor builds: base deep chain, quick chain, then one extra
            # chain for the research_manager role override. Trader and PM
            # overrides equal the base deep model, so they share its chain.
            expected = (
                expected_chain("deep_think_llm", "deep_think_fallback")
                + expected_chain("quick_think_llm", "quick_think_fallback")
                + expected_chain(
                    "deep_think_llm",
                    "deep_think_fallback",
                    model_override=cfg["deep_think_llm_roles"]["research_manager"],
                )
            )
            created = [
                (c.kwargs["provider"], c.kwargs["model"])
                for c in mock_create_llm.call_args_list
            ]
            self.assertEqual(created, expected)

            # Primary LLMs (first tier of each chain) are returned unpatched
            # by patch_invoke_with_fallback when invoked with mock strings.
            self.assertEqual(
                g.deep_thinking_llm,
                f"llm:{primary_provider}:{cfg['deep_think_llm']}",
            )
            self.assertEqual(
                g.quick_thinking_llm,
                f"llm:{primary_provider}:{cfg['quick_think_llm']}",
            )

            # Role LLMs: trader/PM share the base deep chain; the research
            # manager gets a dedicated chain for its override model.
            self.assertIs(g.deep_think_role_llms["trader"], g.deep_thinking_llm)
            self.assertIs(
                g.deep_think_role_llms["portfolio_manager"], g.deep_thinking_llm
            )
            self.assertEqual(
                g.deep_think_role_llms["research_manager"],
                f"llm:{primary_provider}:"
                f"{cfg['deep_think_llm_roles']['research_manager']}",
            )
            # GraphSetup receives the role mapping.
            self.assertIs(
                mock_graph_setup.call_args.kwargs["role_llms"],
                g.deep_think_role_llms,
            )

    def test_creates_components(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog") as mock_memory_log,
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator") as mock_propagator,
            patch("tradingagents.graph.trading_graph.Reflector") as mock_reflector,
            patch("tradingagents.graph.trading_graph.SignalProcessor") as mock_signal,
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_memory_log_instance = MagicMock()
            mock_memory_log.return_value = mock_memory_log_instance

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            mock_propagator_instance = MagicMock()
            mock_propagator.return_value = mock_propagator_instance

            mock_reflector_instance = MagicMock()
            mock_reflector.return_value = mock_reflector_instance

            mock_signal_instance = MagicMock()
            mock_signal.return_value = mock_signal_instance

            g = TradingAgentsGraph(config=self.config)

            self.assertIs(g.memory_log, mock_memory_log_instance)
            self.assertIs(g.propagator, mock_propagator_instance)
            self.assertIs(g.reflector, mock_reflector_instance)
            self.assertIs(g.signal_processor, mock_signal_instance)
            self.assertIs(g.graph_setup, mock_setup_instance)
            self.assertIs(g.graph, mock_workflow.compile())

    def test_config_used_for_set_config(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config") as mock_set_config,
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            TradingAgentsGraph(config=self.config)

            mock_set_config.assert_called_once_with(self.config)

    def test_directories_created(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs") as mock_makedirs,
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            TradingAgentsGraph(config=self.config)

            self.assertEqual(mock_makedirs.call_count, 2)
            mock_makedirs.assert_any_call(self.config["data_cache_dir"], exist_ok=True)
            mock_makedirs.assert_any_call(self.config["results_dir"], exist_ok=True)

    def test_conditional_logic_created_with_config_values(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        custom_config = dict(self.config)
        custom_config["max_debate_rounds"] = 3
        custom_config["max_risk_discuss_rounds"] = 2

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
            patch("tradingagents.graph.trading_graph.ConditionalLogic") as mock_cl,
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            TradingAgentsGraph(config=custom_config)

            mock_cl.assert_called_once_with(
                max_debate_rounds=3, max_risk_discuss_rounds=2
            )

    def test_propagator_created_with_recur_limit(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        custom_config = dict(self.config)
        custom_config["max_recur_limit"] = 200

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator") as mock_propagator,
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            TradingAgentsGraph(config=custom_config)

            mock_propagator.assert_called_once_with(max_recur_limit=200)

    def test_debug_mode_stored(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with (
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.os.makedirs"),
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create_llm,
            patch("tradingagents.graph.trading_graph.TradingMemoryLog"),
            patch("tradingagents.graph.trading_graph.GraphSetup") as mock_graph_setup,
            patch("tradingagents.graph.trading_graph.Propagator"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
        ):
            mock_llm = MagicMock()
            mock_llm.get_llm.return_value = MagicMock()
            mock_create_llm.return_value = mock_llm

            mock_setup_instance = MagicMock()
            mock_workflow = MagicMock()
            mock_setup_instance.setup_graph.return_value = mock_workflow
            mock_graph_setup.return_value = mock_setup_instance

            g = TradingAgentsGraph(config=self.config, debug=True)

            self.assertTrue(g.debug)


# ---------------------------------------------------------------------------
# _create_fallback_llm / _fallback_to_legacy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class CreateFallbackLlmTests(unittest.TestCase):
    """Tests for _create_fallback_llm and _fallback_to_legacy.

    Covers lines 207-225 (except ValueError handling) and 233-240
    (_fallback_to_legacy method).
    """

    def _make_graph(self, config_overrides=None):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            g.config = {
                "llm_provider": "sensenova",
                "deep_think_llm": "deepseek-v4-flash",
                "quick_think_llm": "sensenova-6.8-flash-lite",
                "backend_url": "https://api.example.com",
                "deep_think_fallback": [
                    {"provider": "sensenova", "model": "deepseek-v4-flash"},
                    {"provider": "modelscope", "model": "deepseek-ai/DeepSeek-V4-Pro"},
                ],
                "quick_think_fallback": [
                    {"provider": "sensenova", "model": "sensenova-6.8-flash-lite"},
                    {"provider": "modelscope", "model": "stepfun-ai/Step-3.7-Flash"},
                ],
                **(config_overrides or {}),
            }
            return g

    def test_primary_provider_missing_api_key_logs_warning(self):
        """Primary tier (i==0) ValueError with API key msg → logger.warning + continue."""
        g = self._make_graph()
        mock_client = MagicMock()
        mock_client.get_llm.return_value = "fallback_llm"

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            # Primary (i=0) fails with API key error, fallback (i=1) succeeds
            mock_create.side_effect = [
                ValueError("API key not set for sensenova"),  # primary
                mock_client,  # fallback
            ]
            with patch("tradingagents.graph.trading_graph.logger") as mock_logger:
                result = g._create_fallback_llm("quick_think_fallback", {})

        self.assertEqual(result, "fallback_llm")
        # Primary failure logged as warning (i==0)
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        self.assertIn("API key", warning_msg)
        # Fallback success means no info log
        mock_logger.info.assert_not_called()

    def test_fallback_provider_missing_api_key_logs_info(self):
        """Fallback tier (i>0) ValueError with API key msg → logger.info + continue."""
        g = self._make_graph()
        mock_client = MagicMock()
        mock_client.get_llm.return_value = "primary_llm"

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            # Primary succeeds, fallback (i=1) fails with API key error
            mock_create.side_effect = [
                mock_client,
                ValueError("API key not set"),
            ]
            with patch("tradingagents.graph.trading_graph.logger") as mock_logger:
                result = g._create_fallback_llm("quick_think_fallback", {})

        # Only primary succeeded, len=1 → returns primary directly
        self.assertEqual(result, "primary_llm")
        # Fallback API key miss logged as info
        mock_logger.info.assert_called_once()
        info_msg = mock_logger.info.call_args[0][0]
        self.assertIn("Skipping fallback tier", info_msg)

    def test_all_tiers_missing_api_key_falls_to_legacy(self):
        """When all tiers fail due to API key, falls back to _fallback_to_legacy."""
        g = self._make_graph()
        mock_client = MagicMock()
        mock_client.get_llm.return_value = "legacy_llm"

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            # 2 fallback tiers + 1 _fallback_to_legacy call = 3 side_effects
            mock_create.side_effect = [
                ValueError("API key not set"),
                ValueError("API key not set"),
                mock_client,
            ]
            with patch("tradingagents.graph.trading_graph.logger"):
                result = g._create_fallback_llm("quick_think_fallback", {})

        # All tiers failed → falls to _fallback_to_legacy → returns an LLM
        self.assertEqual(result, "legacy_llm")

    def test_no_fallback_config_calls_fallback_to_legacy_directly(self):
        """When config_key is not in config, calls _fallback_to_legacy."""
        g = self._make_graph()
        # Make a config that doesn't include the fallback key
        g.config.pop("quick_think_fallback", None)

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_client = MagicMock()
            mock_client.get_llm.return_value = "legacy_llm"
            mock_create.return_value = mock_client

            result = g._create_fallback_llm("quick_think_fallback", {})
            self.assertEqual(result, "legacy_llm")

    def test_fallback_to_legacy_deep_think_uses_deep_model(self):
        """_fallback_to_legacy with 'deep' in config_key → deep_think_llm model."""
        g = self._make_graph()

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_client = MagicMock()
            mock_client.get_llm.return_value = "deep_legacy"
            mock_create.return_value = mock_client

            result = g._fallback_to_legacy("deep_think_fallback", {})
            self.assertEqual(result, "deep_legacy")
            mock_create.assert_called_once_with(
                provider="sensenova",
                model="deepseek-v4-flash",
                base_url="https://api.example.com",
            )

    def test_non_api_key_value_error_propagates(self):
        """ValueError not about API key → re-raised (line 222)."""
        g = self._make_graph()

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_create.side_effect = ValueError("unexpected error")
            with (
                patch("tradingagents.graph.trading_graph.logger"),
                self.assertRaises(ValueError),
            ):
                g._create_fallback_llm("quick_think_fallback", {})

    def test_fallback_to_legacy_quick_think_uses_quick_model(self):
        """_fallback_to_legacy without 'deep' in config_key → quick_think_llm model."""
        g = self._make_graph()

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_client = MagicMock()
            mock_client.get_llm.return_value = "quick_legacy"
            mock_create.return_value = mock_client

            result = g._fallback_to_legacy("quick_think_fallback", {})
            self.assertEqual(result, "quick_legacy")
            mock_create.assert_called_once_with(
                provider="sensenova",
                model="sensenova-6.8-flash-lite",
                base_url="https://api.example.com",
            )

    def test_primary_comes_from_model_config_not_fallback_head(self):
        """The configured model key is the primary; a differing fallback head
        becomes tier 2 instead of silently shadowing the configured model."""
        g = self._make_graph({"deep_think_llm": "glm-5.2"})
        mock_client = MagicMock()
        mock_client.get_llm.return_value = "llm"

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_create.return_value = mock_client
            g._create_fallback_llm("deep_think_fallback", {})

        models = [c.kwargs["model"] for c in mock_create.call_args_list]
        self.assertEqual(
            models, ["glm-5.2", "deepseek-v4-flash", "deepseek-ai/DeepSeek-V4-Pro"]
        )
        # Only tiers on the primary provider get the configured backend_url.
        base_urls = [c.kwargs["base_url"] for c in mock_create.call_args_list]
        self.assertEqual(
            base_urls, ["https://api.example.com", "https://api.example.com", None]
        )

    def test_fallback_head_duplicating_primary_is_skipped(self):
        """A fallback entry identical to the primary provider+model is not
        created twice."""
        g = self._make_graph()  # deep_think_llm == deep_think_fallback[0]
        mock_client = MagicMock()
        mock_client.get_llm.return_value = "llm"

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_create.return_value = mock_client
            g._create_fallback_llm("deep_think_fallback", {})

        models = [c.kwargs["model"] for c in mock_create.call_args_list]
        self.assertEqual(models, ["deepseek-v4-flash", "deepseek-ai/DeepSeek-V4-Pro"])

    def test_model_override_replaces_primary(self):
        """model_override swaps the primary model on the configured provider."""
        g = self._make_graph()
        mock_client = MagicMock()
        mock_client.get_llm.return_value = "llm"

        with patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create:
            mock_create.return_value = mock_client
            g._create_fallback_llm(
                "deep_think_fallback", {}, model_override="glm-5.2"
            )

        first = mock_create.call_args_list[0]
        self.assertEqual(first.kwargs["provider"], "sensenova")
        self.assertEqual(first.kwargs["model"], "glm-5.2")
        self.assertEqual(first.kwargs["base_url"], "https://api.example.com")
        # The old fallback head is retained as the next tier.
        self.assertEqual(
            mock_create.call_args_list[1].kwargs["model"], "deepseek-v4-flash"
        )


@pytest.mark.unit
class CreateRoleLlmTests(unittest.TestCase):
    """Tests for TradingAgentsGraph._create_role_llms."""

    def _make_graph(self, config_overrides=None):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            g.config = {
                "llm_provider": "sensenova",
                "deep_think_llm": "glm-5.2",
                "quick_think_llm": "sensenova-6.8-flash-lite",
                "backend_url": "https://api.example.com",
                "deep_think_fallback": [
                    {"provider": "sensenova", "model": "deepseek-v4-flash"},
                ],
                "deep_think_llm_roles": None,
                **(config_overrides or {}),
            }
            g.deep_thinking_llm = MagicMock(name="base_deep")
            return g

    def test_no_role_config_shares_base_chain(self):
        g = self._make_graph({"deep_think_llm_roles": None})
        with patch(
            "tradingagents.graph.trading_graph.create_llm_client"
        ) as mock_create:
            role_llms = g._create_role_llms({})

        mock_create.assert_not_called()
        for role in ("research_manager", "trader", "portfolio_manager"):
            self.assertIs(role_llms[role], g.deep_thinking_llm)

    def test_override_equal_to_base_model_shares_base_chain(self):
        g = self._make_graph({"deep_think_llm_roles": {"trader": "glm-5.2"}})
        with patch(
            "tradingagents.graph.trading_graph.create_llm_client"
        ) as mock_create:
            role_llms = g._create_role_llms({})

        mock_create.assert_not_called()
        self.assertIs(role_llms["trader"], g.deep_thinking_llm)

    def test_differing_override_builds_dedicated_chain(self):
        g = self._make_graph(
            {"deep_think_llm_roles": {"research_manager": "deepseek-v4-flash"}}
        )
        mock_client = MagicMock()
        mock_client.get_llm.return_value = MagicMock(name="rm_llm")

        with patch(
            "tradingagents.graph.trading_graph.create_llm_client",
            return_value=mock_client,
        ) as mock_create:
            role_llms = g._create_role_llms({})

        # Primary override model + the non-duplicate fallback tier would be
        # built; here the single fallback tier duplicates the override, so
        # only one client is created and returned unpatched.
        mock_create.assert_called_once()
        self.assertEqual(
            mock_create.call_args.kwargs["model"], "deepseek-v4-flash"
        )
        self.assertIsNot(role_llms["research_manager"], g.deep_thinking_llm)
        self.assertIs(role_llms["trader"], g.deep_thinking_llm)
        self.assertIs(role_llms["portfolio_manager"], g.deep_thinking_llm)

    def test_unknown_role_is_warned_and_ignored(self):
        g = self._make_graph(
            {"deep_think_llm_roles": {"not_a_role": "some-model"}}
        )
        with (
            patch("tradingagents.graph.trading_graph.create_llm_client") as mock_create,
            patch("tradingagents.graph.trading_graph.logger") as mock_logger,
        ):
            role_llms = g._create_role_llms({})

        mock_create.assert_not_called()
        mock_logger.warning.assert_called_once()
        self.assertIn("not_a_role", mock_logger.warning.call_args[0][1])
        for role in ("research_manager", "trader", "portfolio_manager"):
            self.assertIs(role_llms[role], g.deep_thinking_llm)


# ---------------------------------------------------------------------------
# _fetch_crypto_returns (pure logic via patching network calls)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class FetchCryptoReturnsTests(unittest.TestCase):
    def _make_graph(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            return g

    def test_btc_to_eth_alpha(self):
        g = self._make_graph()
        btc_prices = json.dumps({"prices": [[1000000, 40000.0], [2000000, 44000.0]]}).encode()
        eth_prices = json.dumps({"prices": [[1000000, 3000.0], [2000000, 3150.0]]}).encode()

        def _urlopen(url, timeout=15):
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            if "bitcoin" in url:
                mock_resp.read.return_value = btc_prices
            else:
                mock_resp.read.return_value = eth_prices
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            raw, alpha, days = g._fetch_crypto_returns(
                "BTC", "2000-01-01", "2000-02-01", holding_days=5, benchmark="ETH"
            )
        self.assertIsNotNone(raw)
        self.assertIsNotNone(alpha)
        self.assertEqual(days, 5)
        self.assertAlmostEqual(raw, 0.1)  # (44000 - 40000) / 40000
        self.assertAlmostEqual(alpha, 0.1 - 0.05)  # raw - bench(0.05)

    def test_crypto_benchmark_not_in_map_returns_none_alpha(self):
        g = self._make_graph()
        mock_data = json.dumps({"prices": [[1000000, 40000.0], [2000000, 44000.0]]}).encode()

        def _urlopen(url, timeout=15):
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_data
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            raw, alpha, days = g._fetch_crypto_returns(
                "SOL", "2000-01-01", "2000-02-01", holding_days=5, benchmark="UNKNOWN"
            )
        self.assertIsNotNone(raw)
        self.assertIsNone(alpha)
        self.assertEqual(days, 5)


# ---------------------------------------------------------------------------
# Helper function tests for coin_id mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class FetchCryptoCoinIdTests(unittest.TestCase):
    def test_coin_id_mapping(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)

        # Test the coin_map logic via _fetch_crypto_returns by inspecting
        # the first urlopen call: we verify the coin_id resolution indirectly
        # by patching urlopen and checking the URL.
        urls_called = []

        def _capture_url(url, timeout=15):
            urls_called.append(url)
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.read.return_value = json.dumps({"prices": [[1, 100], [2, 110]]}).encode()
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=_capture_url):
            g._fetch_crypto_returns(
                "BTC-USD", "2000-01-01", "2000-01-02", holding_days=1, benchmark="SPY"
            )
        self.assertTrue(any("bitcoin" in url for url in urls_called))


# ---------------------------------------------------------------------------
# _fetch_returns
# ---------------------------------------------------------------------------


@pytest.mark.unit
class FetchReturnsTests(unittest.TestCase):
    def _make_graph(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        with patch.object(TradingAgentsGraph, "__init__", return_value=None):
            g = TradingAgentsGraph.__new__(TradingAgentsGraph)
            g.config = {}
            return g

    def test_successful_returns(self):
        g = self._make_graph()
        mock_stock = pd.DataFrame({
            "Close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        })
        mock_bench = pd.DataFrame({
            "Close": [200.0, 201.0, 202.0, 203.0, 204.0, 205.0],
        })

        with (
            patch("yfinance.Ticker") as mock_ticker,
        ):
            mock_stock_obj = MagicMock()
            mock_stock_obj.history.return_value = mock_stock
            mock_bench_obj = MagicMock()
            mock_bench_obj.history.return_value = mock_bench
            mock_ticker.side_effect = lambda t: mock_stock_obj if t == "AAPL" else mock_bench_obj

            raw, alpha, days = g._fetch_returns("AAPL", "2026-06-15", holding_days=5, benchmark="SPY")

        self.assertIsNotNone(raw)
        self.assertIsNotNone(alpha)
        self.assertEqual(days, 5)
        self.assertAlmostEqual(raw, (105.0 - 100.0) / 100.0)
        bench_ret = (205.0 - 200.0) / 200.0
        self.assertAlmostEqual(alpha, raw - bench_ret)

    def test_insufficient_data_returns_none(self):
        g = self._make_graph()
        with patch("yfinance.Ticker") as mock_ticker:
            mock_obj = MagicMock()
            mock_obj.history.return_value = pd.DataFrame({"Close": [100.0]})
            mock_ticker.return_value = mock_obj

            raw, alpha, days = g._fetch_returns("AAPL", "2026-06-15")
        self.assertIsNone(raw)
        self.assertIsNone(alpha)
        self.assertIsNone(days)

    def test_exception_returns_none(self):
        g = self._make_graph()
        with patch("yfinance.Ticker", side_effect=Exception("Network error")):
            raw, alpha, days = g._fetch_returns("AAPL", "2026-06-15")
        self.assertIsNone(raw)
        self.assertIsNone(alpha)
        self.assertIsNone(days)

    def test_crypto_returns_delegates(self):
        g = self._make_graph()
        with (
            patch.object(g, "_fetch_crypto_returns", return_value=(0.1, 0.05, 5)) as mock_crypto,
        ):
            raw, alpha, days = g._fetch_returns("BTC", "2026-06-15", asset_type="crypto")
        self.assertEqual((raw, alpha, days), (0.1, 0.05, 5))
        mock_crypto.assert_called_once()

    def test_holding_days_clamped_by_data_length(self):
        g = self._make_graph()
        mock_stock = pd.DataFrame({
            "Close": [100.0, 101.0],
        })
        mock_bench = pd.DataFrame({
            "Close": [200.0, 201.0, 202.0],
        })
        with patch("yfinance.Ticker") as mock_ticker:
            mock_stock_obj = MagicMock()
            mock_stock_obj.history.return_value = mock_stock
            mock_bench_obj = MagicMock()
            mock_bench_obj.history.return_value = mock_bench
            mock_ticker.side_effect = lambda t: mock_stock_obj if t == "AAPL" else mock_bench_obj

            raw, alpha, days = g._fetch_returns("AAPL", "2026-06-15", holding_days=10, benchmark="SPY")

        self.assertEqual(days, 1)
        self.assertAlmostEqual(raw, (101.0 - 100.0) / 100.0)

    def test_ashare_path_uses_load_ohlcv(self):
        """A-share ticker uses load_ohlcv instead of yfinance (lines 357-364)."""
        g = self._make_graph()
        mock_ohlcv = pd.DataFrame({
            "Date": ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-22"],
            "Close": [1500.0, 1510.0, 1520.0, 1530.0, 1540.0, 1550.0],
        })
        mock_bench = pd.DataFrame({
            "Close": [3000.0, 3010.0, 3020.0, 3030.0, 3040.0, 3050.0],
        })

        mock_bench_obj = MagicMock()
        mock_bench_obj.history.return_value = mock_bench

        with (
            patch("yfinance.Ticker", return_value=mock_bench_obj),
            patch("tradingagents.dataflows.akshare_common.is_a_share_ticker", return_value=True),
            patch("tradingagents.dataflows.stockstats_utils.load_ohlcv", return_value=mock_ohlcv),
        ):
            raw, alpha, days = g._fetch_returns(
                "600519.SS", "2026-06-15", holding_days=5, benchmark="000001.SS"
            )

        self.assertIsNotNone(raw)
        self.assertAlmostEqual(raw, (1550.0 - 1500.0) / 1500.0)

    def test_ashare_path_load_ohlcv_fails_uses_empty_df(self):
        """When load_ohlcv returns None, falls to empty DataFrame → insufficient data → None."""
        g = self._make_graph()
        mock_bench = pd.DataFrame({
            "Close": [3000.0, 3010.0],
        })

        mock_bench_obj = MagicMock()
        mock_bench_obj.history.return_value = mock_bench

        with (
            patch("yfinance.Ticker", return_value=mock_bench_obj),
            patch("tradingagents.dataflows.akshare_common.is_a_share_ticker", return_value=True),
            patch("tradingagents.dataflows.stockstats_utils.load_ohlcv", return_value=None),
        ):
            raw, alpha, days = g._fetch_returns(
                "600519.SS", "2026-06-15", holding_days=5, benchmark="000001.SS"
            )

        self.assertIsNone(raw)
        self.assertIsNone(alpha)
        self.assertIsNone(days)


# ---------------------------------------------------------------------------
# _resolve_pending_entries
# ---------------------------------------------------------------------------


@pytest.mark.unit
class ResolvePendingEntriesTests(unittest.TestCase):
    def test_no_pending_entries_does_nothing(self):
        g = _make_graph()
        g.memory_log.get_pending_entries.return_value = []
        g._resolve_pending_entries("AAPL")
        g.memory_log.batch_update_with_outcomes.assert_not_called()

    def test_skips_entries_for_other_tickers(self):
        g = _make_graph()
        g.memory_log.get_pending_entries.return_value = [
            {"ticker": "MSFT", "date": "2026-06-10", "decision": "Buy"},
        ]
        g._resolve_pending_entries("AAPL")
        g.memory_log.batch_update_with_outcomes.assert_not_called()

    def test_updates_same_ticker_entries(self):
        g = _make_graph()
        g.memory_log.get_pending_entries.return_value = [
            {"ticker": "AAPL", "date": "2026-06-10", "decision": "Buy"},
        ]
        g.reflector.reflect_on_final_decision.return_value = "Good call"

        with patch.object(g, "_fetch_returns", return_value=(0.05, 0.02, 5)):
            g._resolve_pending_entries("AAPL")

        g.memory_log.batch_update_with_outcomes.assert_called_once_with([
            {
                "ticker": "AAPL",
                "trade_date": "2026-06-10",
                "raw_return": 0.05,
                "alpha_return": 0.02,
                "holding_days": 5,
                "reflection": "Good call",
            }
        ])

    def test_skips_entry_when_returns_not_available(self):
        g = _make_graph()
        g.memory_log.get_pending_entries.return_value = [
            {"ticker": "AAPL", "date": "2026-06-10", "decision": "Buy"},
        ]

        with patch.object(g, "_fetch_returns", return_value=(None, None, None)):
            g._resolve_pending_entries("AAPL")

        g.memory_log.batch_update_with_outcomes.assert_not_called()

    def test_multiple_entries_partial_resolution(self):
        g = _make_graph()
        g.memory_log.get_pending_entries.return_value = [
            {"ticker": "AAPL", "date": "2026-06-10", "decision": "Buy"},
            {"ticker": "AAPL", "date": "2026-06-11", "decision": "Sell"},
        ]
        g.reflector.reflect_on_final_decision.return_value = "reflection"

        # Only first entry resolves; second returns None
        returns_side_effects = [
            (0.05, 0.02, 5),
            (None, None, None),
        ]

        with patch.object(g, "_fetch_returns", side_effect=returns_side_effects):
            g._resolve_pending_entries("AAPL")

        g.memory_log.batch_update_with_outcomes.assert_called_once()
        call_args = g.memory_log.batch_update_with_outcomes.call_args[0][0]
        self.assertEqual(len(call_args), 1)
        self.assertEqual(call_args[0]["trade_date"], "2026-06-10")


# ---------------------------------------------------------------------------
# _log_state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class LogStateTests(unittest.TestCase):
    def _make_graph(self):
        g = _make_graph()
        g.results_dir = tempfile.mkdtemp()
        g.config["results_dir"] = g.results_dir
        return g

    def test_writes_json_file(self):
        g = self._make_graph()
        final_state = g.propagator.create_initial_state.return_value
        final_state["company_of_interest"] = "AAPL"
        final_state["trade_date"] = "2026-06-15"

        g._log_state("2026-06-15", final_state)

        safe_ticker = "AAPL"
        log_dir = Path(g.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        log_path = log_dir / "full_states_log_2026-06-15.json"
        self.assertTrue(log_path.exists())
        with open(log_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["company_of_interest"], "AAPL")
        self.assertEqual(data["final_trade_decision"], "Hold")

    def test_updates_log_states_dict(self):
        g = self._make_graph()
        final_state = g.propagator.create_initial_state.return_value
        g._log_state("2026-06-15", final_state)
        self.assertIn("2026-06-15", g.log_states_dict)

    def test_writes_safe_ticker_path(self):
        g = self._make_graph()
        g.ticker = "../../evil"
        final_state = g.propagator.create_initial_state.return_value
        with patch("tradingagents.graph.trading_graph.safe_ticker_component", return_value="..__..__evil"):
            g._log_state("2026-06-15", final_state)
        log_dir = Path(g.config["results_dir"]) / "..__..__evil" / "TradingAgentsStrategy_logs"
        self.assertTrue((log_dir / "full_states_log_2026-06-15.json").exists())


# ---------------------------------------------------------------------------
# _run_graph
# ---------------------------------------------------------------------------


@pytest.mark.unit
class RunGraphTests(unittest.TestCase):
    def test_streams_graph_and_returns_signal(self):
        g = _make_graph()
        g.memory_log.get_past_context.return_value = "past context"
        g.resolve_instrument_context = MagicMock(return_value="instrument context")

        # Simulate a stream yielding chunks that build up the full state
        def mock_stream(init_state, **kwargs):
            yield {"analyst_market": {"market_report": "report1"}}
            yield {"final_node": {
                "market_report": "report1",
                "sentiment_report": "neutral",
                "news_report": "positive",
                "fundamentals_report": "strong",
                "governance_report": "fair",
                "industry_report": "growing",
                "investment_debate_state": {
                    "bull_history": [], "bear_history": [],
                    "history": [], "current_response": "",
                    "judge_decision": "bull",
                },
                "trader_investment_plan": {"action": "buy"},
                "risk_debate_state": {
                    "aggressive_history": [], "conservative_history": [],
                    "neutral_history": [], "history": [],
                    "judge_decision": "approve",
                },
                "investment_plan": {"size": 1000},
                "final_trade_decision": "Buy",
            }}

        g.graph.stream = mock_stream

        state, signal = g._run_graph("AAPL", "2026-06-15")

        self.assertEqual(state["final_trade_decision"], "Buy")
        self.assertEqual(signal, "Buy")
        # Verify state was stored
        self.assertEqual(g.curr_state, state)
        # Verify timings were collected
        self.assertEqual(len(g.node_timings), 2)
        # Verify memory log was called
        g.memory_log.store_decision.assert_called_once_with(
            ticker="AAPL", trade_date="2026-06-15",
            final_trade_decision="Buy",
        )

    def test_streams_with_non_dict_chunk(self):
        g = _make_graph()
        g.memory_log.get_past_context.return_value = "past"

        def mock_stream(init_state, **kwargs):
            yield {"node1": None}
            yield {"node2": {
                "market_report": "",
                "sentiment_report": "",
                "news_report": "",
                "fundamentals_report": "",
                "governance_report": "",
                "industry_report": "",
                "investment_debate_state": {
                    "bull_history": [], "bear_history": [],
                    "history": [], "current_response": "",
                    "judge_decision": "",
                },
                "trader_investment_plan": {},
                "risk_debate_state": {
                    "aggressive_history": [], "conservative_history": [],
                    "neutral_history": [], "history": [],
                    "judge_decision": "",
                },
                "investment_plan": {},
                "final_trade_decision": "Sell",
            }}

        g.graph.stream = mock_stream
        g.resolve_instrument_context = MagicMock(return_value="ctx")

        state, signal = g._run_graph("AAPL", "2026-06-15")
        self.assertEqual(state["final_trade_decision"], "Sell")

    def test_injects_verified_market_snapshot_when_empty(self):
        g = _make_graph()
        g.propagator.create_initial_state.return_value["verified_market_snapshot"] = ""
        g.memory_log.get_past_context.return_value = "past"
        g.resolve_instrument_context = MagicMock(return_value="ctx")

        captured_state = {}

        def mock_stream(init_state, **kwargs):
            captured_state.update(init_state)
            yield {"final_node": {
                "market_report": "",
                "sentiment_report": "",
                "news_report": "",
                "fundamentals_report": "",
                "governance_report": "",
                "industry_report": "",
                "investment_debate_state": {
                    "bull_history": [], "bear_history": [],
                    "history": [], "current_response": "",
                    "judge_decision": "",
                },
                "trader_investment_plan": {},
                "risk_debate_state": {
                    "aggressive_history": [], "conservative_history": [],
                    "neutral_history": [], "history": [],
                    "judge_decision": "",
                },
                "investment_plan": {},
                "final_trade_decision": "Hold",
            }}

        g.graph.stream = mock_stream

        with patch(
            "tradingagents.dataflows.market_data_validator.build_verified_market_snapshot",
            return_value="FRESH_SNAPSHOT",
        ) as mock_build:
            g._run_graph("AAPL", "2026-06-15")

        mock_build.assert_called_once_with("AAPL", "2026-06-15", refresh=True)
        self.assertEqual(captured_state["verified_market_snapshot"], "FRESH_SNAPSHOT")

    def test_snapshot_build_exception_logs_warning_and_continues(self):
        """When build_verified_market_snapshot raises, the exception should be
        logged and the graph should continue without a snapshot (lines 634-635)."""
        g = _make_graph()
        g.propagator.create_initial_state.return_value["verified_market_snapshot"] = ""
        g.memory_log.get_past_context.return_value = "past"
        g.resolve_instrument_context = MagicMock(return_value="ctx")

        def mock_stream(init_state, **kwargs):
            yield {"final_node": {
                "market_report": "",
                "sentiment_report": "",
                "news_report": "",
                "fundamentals_report": "",
                "governance_report": "",
                "industry_report": "",
                "investment_debate_state": {
                    "bull_history": [], "bear_history": [],
                    "history": [], "current_response": "",
                    "judge_decision": "",
                },
                "trader_investment_plan": {},
                "risk_debate_state": {
                    "aggressive_history": [], "conservative_history": [],
                    "neutral_history": [], "history": [],
                    "judge_decision": "",
                },
                "investment_plan": {},
                "final_trade_decision": "Buy",
            }}

        g.graph.stream = mock_stream

        with patch(
            "tradingagents.dataflows.market_data_validator.build_verified_market_snapshot",
            side_effect=RuntimeError("snapshot build failed"),
        ) as mock_build, patch(
            "tradingagents.graph.trading_graph.logger"
        ) as mock_logger:
            state, signal = g._run_graph("AAPL", "2026-06-15")

        # Graph should still complete successfully
        self.assertEqual(state["final_trade_decision"], "Buy")
        self.assertEqual(signal, "Buy")
        mock_build.assert_called_once()
        # Warning should be logged
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        self.assertIn("Could not build verified market snapshot", warning_msg)

    def test_checkpoint_thread_id_injected(self):
        g = _make_graph()
        g.config["checkpoint_enabled"] = True
        g.memory_log.get_past_context.return_value = "past"
        g.resolve_instrument_context = MagicMock(return_value="ctx")

        def mock_stream(init_state, **kwargs):
            yield {"n1": {"final_trade_decision": "Buy", "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "", "governance_report": "", "industry_report": "", "investment_debate_state": {"bull_history": [], "bear_history": [], "history": [], "current_response": "", "judge_decision": ""}, "trader_investment_plan": {}, "risk_debate_state": {"aggressive_history": [], "conservative_history": [], "neutral_history": [], "history": [], "judge_decision": ""}, "investment_plan": {}}}

        g.graph.stream = mock_stream

        with (
            patch("tradingagents.graph.trading_graph.thread_id", return_value="tid123"),
        ):
            g._run_graph("AAPL", "2026-06-15")

        self.assertEqual(g.propagator.get_graph_args.return_value["config"]["configurable"]["thread_id"], "tid123")

    def test_stream_with_debug_mode(self):
        g = _make_graph()
        g.debug = True
        g.memory_log.get_past_context.return_value = "past"
        g.resolve_instrument_context = MagicMock(return_value="ctx")

        mock_message = MagicMock()
        mock_message.pretty_print = MagicMock()

        def mock_stream(init_state, **kwargs):
            yield {"n1": {"messages": [mock_message], "final_trade_decision": "Buy", "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "", "governance_report": "", "industry_report": "", "investment_debate_state": {"bull_history": [], "bear_history": [], "history": [], "current_response": "", "judge_decision": ""}, "trader_investment_plan": {}, "risk_debate_state": {"aggressive_history": [], "conservative_history": [], "neutral_history": [], "history": [], "judge_decision": ""}, "investment_plan": {}}}

        g.graph.stream = mock_stream
        g._run_graph("AAPL", "2026-06-15")
        mock_message.pretty_print.assert_called_once()

    def test_checkpoint_cleared_on_success(self):
        g = _make_graph()
        g.config["checkpoint_enabled"] = True
        g.memory_log.get_past_context.return_value = "past"
        g.resolve_instrument_context = MagicMock(return_value="ctx")

        def mock_stream(init_state, **kwargs):
            yield {"n1": {"final_trade_decision": "Hold", "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "", "governance_report": "", "industry_report": "", "investment_debate_state": {"bull_history": [], "bear_history": [], "history": [], "current_response": "", "judge_decision": ""}, "trader_investment_plan": {}, "risk_debate_state": {"aggressive_history": [], "conservative_history": [], "neutral_history": [], "history": [], "judge_decision": ""}, "investment_plan": {}}}

        g.graph.stream = mock_stream

        with (
            patch("tradingagents.graph.trading_graph.thread_id", return_value="tid"),
            patch("tradingagents.graph.trading_graph.clear_checkpoint") as mock_clear,
        ):
            g._run_graph("AAPL", "2026-06-15")
            mock_clear.assert_called_once_with(
                "/tmp/tradingagents_test_cache", "AAPL", "2026-06-15"
            )


# ---------------------------------------------------------------------------
# propagate — checkpoint paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class PropagateTests(unittest.TestCase):
    def setUp(self):
        self.g = _make_graph()
        self.tmp_results = tempfile.mkdtemp()
        self.g.config["results_dir"] = self.tmp_results
        self.g.config["data_cache_dir"] = tempfile.mkdtemp()
        self.g.config["checkpoint_enabled"] = False

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_results, ignore_errors=True)
        shutil.rmtree(self.g.config["data_cache_dir"], ignore_errors=True)

    def test_without_checkpoint_resolves_ticker_and_runs(self):
        with (
            patch("tradingagents.ticker_resolver.resolve_ticker") as mock_resolve,
            patch.object(self.g, "_resolve_pending_entries") as mock_pending,
            patch.object(self.g, "_run_graph", return_value=({"final_trade_decision": "Buy"}, "Buy")) as mock_run,
        ):
            mock_resolve.return_value = {"ticker": "AAPL", "company_name": "Apple Inc."}
            state, signal = self.g.propagate("AAPL", "2026-06-15")
            self.assertEqual(self.g.ticker, "AAPL")
            mock_pending.assert_called_once_with("AAPL", asset_type="stock")
            mock_run.assert_called_once_with(
                "AAPL", "2026-06-15", asset_type="stock", confirmed_name="Apple Inc.",
                on_chunk=None, holdings_context=None, transactions_context=None,
                company_display_name=None,
            )

    def test_with_checkpoint_enabled_and_cached_result(self):
        self.g.config["checkpoint_enabled"] = True
        cached_state = {
            "final_trade_decision": "Buy",
            "company_of_interest": "AAPL",
            "trade_date": "2026-06-15",
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "governance_report": "",
            "industry_report": "",
            "investment_debate_state": {
                "bull_history": [], "bear_history": [],
                "history": [], "current_response": "",
                "judge_decision": "",
            },
            "trader_investment_plan": {},
            "risk_debate_state": {
                "aggressive_history": [], "conservative_history": [],
                "neutral_history": [], "history": [],
                "judge_decision": "",
            },
            "investment_plan": {},
        }
        # Create the actual state log file on disk
        log_dir = Path(self.tmp_results) / "AAPL" / "TradingAgentsStrategy_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "full_states_log_2026-06-15.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(cached_state, f)

        with (
            patch("tradingagents.ticker_resolver.resolve_ticker") as mock_resolve,
            patch("tradingagents.dataflows.utils.safe_ticker_component", return_value="AAPL"),
            patch.object(self.g, "_resolve_pending_entries"),
            patch("tradingagents.graph.trading_graph.clear_checkpoint") as mock_clear,
        ):
            mock_resolve.return_value = {"ticker": "AAPL", "company_name": "Apple Inc."}

            state, signal = self.g.propagate("AAPL", "2026-06-15")
            self.assertEqual(state["final_trade_decision"], "Buy")
            self.assertEqual(signal, "Buy")
            self.assertEqual(state["market"], "XNYS")
            self.assertEqual(
                state["analysis_dates"],
                {
                    "analysis_date": "2026-06-15",
                    "market_as_of_date": "2026-06-15",
                    "evidence_window_end": "2026-06-15",
                },
            )
            mock_clear.assert_called_once()

    def test_with_checkpoint_enabled_resumes_from_step(self):
        self.g.config["checkpoint_enabled"] = True

        with (
            patch("tradingagents.ticker_resolver.resolve_ticker") as mock_resolve,
            patch("tradingagents.dataflows.utils.safe_ticker_component", return_value="AAPL"),
            patch.object(self.g, "_resolve_pending_entries"),
            patch.object(self.g, "_run_graph", return_value=({"final_trade_decision": "Sell"}, "Sell")) as mock_run,
            patch("tradingagents.graph.trading_graph.get_checkpointer") as mock_get_cp,
            patch("tradingagents.graph.trading_graph.checkpoint_step", return_value=3),
        ):
            mock_resolve.return_value = {"ticker": "AAPL", "company_name": "Apple Inc."}
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = MagicMock()
            mock_get_cp.return_value = mock_cm

            state, signal = self.g.propagate("AAPL", "2026-06-15")
            self.assertEqual(state["final_trade_decision"], "Sell")
            mock_run.assert_called_once()

    def test_checkpointer_exit_on_exception(self):
        self.g.config["checkpoint_enabled"] = True

        with (
            patch("tradingagents.ticker_resolver.resolve_ticker") as mock_resolve,
            patch("tradingagents.dataflows.utils.safe_ticker_component", return_value="AAPL"),
            patch.object(self.g, "_resolve_pending_entries"),
            patch.object(self.g, "_run_graph", side_effect=ValueError("test error")),
            patch("tradingagents.graph.trading_graph.get_checkpointer") as mock_get_cp,
            patch("tradingagents.graph.trading_graph.checkpoint_step", return_value=None),
        ):
            mock_resolve.return_value = {"ticker": "AAPL", "company_name": "Apple Inc."}
            mock_cm = MagicMock()
            mock_get_cp.return_value = mock_cm

            with self.assertRaises(ValueError):
                self.g.propagate("AAPL", "2026-06-15")

            mock_cm.__exit__.assert_called_once()

    def test_cached_state_log_exception_continues(self):
        self.g.config["checkpoint_enabled"] = True
        # Create a corrupted state log file
        log_dir = Path(self.tmp_results) / "AAPL" / "TradingAgentsStrategy_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "full_states_log_2026-06-15.json"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("not valid json{{{")

        with (
            patch("tradingagents.ticker_resolver.resolve_ticker") as mock_resolve,
            patch("tradingagents.dataflows.utils.safe_ticker_component", return_value="AAPL"),
            patch.object(self.g, "_resolve_pending_entries"),
            patch.object(self.g, "_run_graph", return_value=({"final_trade_decision": "Buy"}, "Buy")) as mock_run,
            patch("tradingagents.graph.trading_graph.get_checkpointer") as mock_get_cp,
            patch("tradingagents.graph.trading_graph.checkpoint_step", return_value=None),
        ):
            mock_resolve.return_value = {"ticker": "AAPL", "company_name": "Apple Inc."}
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value = MagicMock()
            mock_get_cp.return_value = mock_cm

            state, signal = self.g.propagate("AAPL", "2026-06-15")
            self.assertEqual(state["final_trade_decision"], "Buy")
            mock_run.assert_called_once()

    def test_propagate_with_resolved_company_name(self):
        g = _make_graph()
        g.config["checkpoint_enabled"] = False
        g.config["results_dir"] = self.tmp_results
        with (
            patch("tradingagents.ticker_resolver.resolve_ticker") as mock_resolve,
            patch.object(g, "_resolve_pending_entries"),
            patch.object(g, "_run_graph", return_value=({"final_trade_decision": "Hold"}, "Hold")),
        ):
            mock_resolve.return_value = {"ticker": "AAPL", "company_name": "Apple Inc."}
            state, signal = g.propagate("Apple Inc.", "2026-06-15")
            self.assertEqual(g.ticker, "AAPL")


@pytest.mark.unit
class RateLimitPlumbingTests(unittest.TestCase):
    """_create_fallback_llm forwards per-provider requests_per_minute."""

    def test_rpm_passed_only_for_configured_providers(self):
        g = _construct_graph(
            config_override={"llm_requests_per_minute": {"sensenova": 15}}
        )
        calls = g._mocks["create_llm"].call_args_list
        self.assertTrue(calls, "expected create_llm_client to be called")

        saw_sensenova = False
        for c in calls:
            provider = c.kwargs.get("provider")
            if provider == "sensenova":
                saw_sensenova = True
                self.assertEqual(c.kwargs.get("requests_per_minute"), 15)
            else:
                # Providers absent from the map must not be rate-limited.
                self.assertNotIn("requests_per_minute", c.kwargs)
        self.assertTrue(saw_sensenova, "sensenova tier should have been created")

    def test_no_rpm_when_map_empty(self):
        g = _construct_graph(config_override={"llm_requests_per_minute": {}})
        for c in g._mocks["create_llm"].call_args_list:
            self.assertNotIn("requests_per_minute", c.kwargs)


if __name__ == "__main__":
    unittest.main()
