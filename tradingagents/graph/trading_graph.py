# TradingAgents/graph/trading_graph.py

import json
import logging
import os
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.agents import *

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_company_announcements,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_industry_valuation,
    get_insider_transactions,
    get_institutional_holdings,
    get_macro_indicators,
    get_news,
    get_northbound_hold,
    get_restricted_release,
    get_stock_data,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.llm_clients.retry_utils import RetryConfig

from .analyst_execution import (
    AnalystWallTimeTracker,
    build_analyst_execution_plan,
)
from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .reflection import Reflector
from .setup import GraphSetup
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=None,
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        if selected_analysts is None:
            selected_analysts = ["market", "social", "news", "fundamentals"]
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()

        self.memory_log = TradingMemoryLog(self.config)
        self.node_timings: list[dict[str, Any]] = []

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            analyst_concurrency_limit=self.config.get("analyst_concurrency_limit", 1),
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.selected_analysts = selected_analysts
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        # Sampling temperature is cross-provider: forward it whenever set.
        # float() here so a value coming from a TRADINGAGENTS_TEMPERATURE env
        # string ("0.2") works the same as a programmatic float.
        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            kwargs["temperature"] = float(temperature)

        # Retry/backoff configuration for transient LLM errors.
        if self.config.get("llm_retry_enabled", True):
            kwargs["retry_config"] = RetryConfig(
                enabled=True,
                max_retries=int(self.config.get("llm_retry_max_retries", 3)),
                base_delay=float(self.config.get("llm_retry_base_delay", 2.0)),
            )
        else:
            kwargs["retry_config"] = RetryConfig(enabled=False)

        return kwargs

    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News, insider information and company announcements
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                    get_company_announcements,
                    get_macro_indicators,
                ]
            ),
            "governance": ToolNode(
                [
                    # Governance analysis tools
                    get_company_announcements,
                    get_insider_transactions,
                    get_news,
                    get_restricted_release,
                    get_institutional_holdings,
                    get_northbound_hold,
                ]
            ),
            "industry": ToolNode(
                [
                    # Industry valuation comparison
                    get_industry_valuation,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.
        """
        explicit = self.config.get("benchmark_ticker")
        if explicit:
            return explicit
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = ticker.upper()
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str = "SPY", asset_type: str = "stock",
    ) -> tuple[float | None, float | None, int | None]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline (resolved by the
        caller via ``_resolve_benchmark``). Returns ``(raw_return, alpha_return,
        actual_holding_days)`` or ``(None, None, None)`` if price data is
        unavailable (too recent, delisted, or network error).
        """
        from tradingagents.dataflows.symbol_utils import normalize_symbol

        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            if asset_type == "crypto":
                return self._fetch_crypto_returns(ticker, trade_date, end_str, holding_days, benchmark)

            # Normalize so the realized-return lookup hits the same instrument
            # the analysis priced (e.g. XAUUSD -> GC=F) (#984). The benchmark is
            # already a canonical Yahoo symbol from ``_resolve_benchmark``.
            stock = yf.Ticker(normalize_symbol(ticker)).history(start=trade_date, end=end_str)
            bench = yf.Ticker(benchmark).history(start=trade_date, end=end_str)

            if len(stock) < 2 or len(bench) < 2:
                return None, None, None

            actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0])
                / bench["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None

    def _fetch_crypto_returns(
        self, ticker: str, start_date: str, end_date: str,
        holding_days: int, benchmark: str,
    ) -> tuple[float | None, float | None, int | None]:
        """Fetch crypto returns via CoinGecko public API (no key required)."""
        import json
        import urllib.parse
        import urllib.request

        # Map common ticker symbols to CoinGecko coin IDs
        coin_map = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
            "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
            "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2",
        }
        coin_id = coin_map.get(ticker.upper().replace("-USD", "").replace("USDT", ""), ticker.lower())

        try:
            # CoinGecko /coins/{id}/market_chart/range?vs_currency=usd&from={unix}&to={unix}
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            start_unix = int(start_dt.timestamp())
            end_unix = int(end_dt.timestamp())
            url = (
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
                f"?vs_currency=usd&from={start_unix}&to={end_unix}"
            )
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
            prices = data.get("prices", [])
            if len(prices) < 2:
                return None, None, None
            start_price = prices[0][1]
            end_price = prices[-1][1]
            raw = (end_price - start_price) / start_price
            # For crypto benchmark, compare against BTC or ETH if available
            if benchmark.upper() in coin_map:
                bench_id = coin_map[benchmark.upper()]
                bench_url = (
                    f"https://api.coingecko.com/api/v3/coins/{bench_id}/market_chart/range"
                    f"?vs_currency=usd&from={start_unix}&to={end_unix}"
                )
                with urllib.request.urlopen(bench_url, timeout=15) as resp:
                    bench_data = json.loads(resp.read())
                bench_prices = bench_data.get("prices", [])
                if len(bench_prices) >= 2:
                    bench_start = bench_prices[0][1]
                    bench_end = bench_prices[-1][1]
                    bench_ret = (bench_end - bench_start) / bench_start
                    alpha = raw - bench_ret
                    return raw, alpha, holding_days
            # No benchmark available — alpha is None
            return raw, None, holding_days
        except Exception as e:
            logger.warning(
                "Could not resolve crypto outcome for %s on %s (will retry next run): %s",
                ticker, start_date, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str, asset_type: str = "stock") -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(
                ticker, entry["date"], benchmark=benchmark, asset_type=asset_type,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
                benchmark_name=benchmark,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def resolve_instrument_context(
        self, ticker: str, asset_type: str = "stock", confirmed_name: str | None = None,
    ) -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.

        ``confirmed_name`` is the user-confirmed company name from the CLI
        (resolved via akshare for A-shares). When provided, it takes priority
        over the yfinance identity to prevent name mismatches (#814 follow-up).
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity, confirmed_name=confirmed_name)

    def propagate(self, company_name, trade_date, asset_type: str = "stock", confirmed_name: str | None = None):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.

        ``confirmed_name`` is the user-confirmed company name (e.g. from
        akshare for A-shares). When provided, it overrides the yfinance
        identity to prevent name mismatches.
        """
        from tradingagents.ticker_resolver import resolve_ticker

        resolved = resolve_ticker(company_name)
        ticker = resolved["ticker"]
        resolved_name = confirmed_name or resolved.get("company_name", "")
        self.ticker = ticker

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(ticker, asset_type=asset_type)

        # If checkpointing is enabled, check whether a completed state log already
        # exists on disk.  When it does, we can return the cached result directly
        # instead of resuming from (or re-running) the graph.
        if self.config.get("checkpoint_enabled"):
            safe_ticker = safe_ticker_component(ticker)
            log_dir = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
            log_path = log_dir / f"full_states_log_{trade_date}.json"
            if log_path.exists() and log_path.stat().st_size > 0:
                try:
                    with open(log_path, encoding="utf-8") as f:
                        cached_state = json.load(f)
                    if cached_state.get("final_trade_decision"):
                        logger.info(
                            "Found completed state log for %s on %s, skipping graph run.",
                            ticker, trade_date,
                        )
                        self.curr_state = cached_state
                        # Clear any stale checkpoint so the next run starts fresh.
                        clear_checkpoint(
                            self.config["data_cache_dir"], ticker, str(trade_date)
                        )
                        return cached_state, self.process_signal(
                            cached_state["final_trade_decision"]
                        )
                except Exception:
                    pass

            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], ticker
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                self.config["data_cache_dir"], ticker, str(trade_date)
            )
            if step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s", step, ticker, trade_date
                )
            else:
                logger.info("Starting fresh for %s on %s", ticker, trade_date)

        try:
            return self._run_graph(ticker, trade_date, asset_type=asset_type, confirmed_name=resolved_name)
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock", confirmed_name: str | None = None):
        """Execute the graph and write the resulting state to disk and memory log."""
        # Initialize state — inject memory log context for PM and the
        # deterministically resolved instrument identity for all agents.
        past_context = self.memory_log.get_past_context(company_name)
        instrument_context = self.resolve_instrument_context(
            company_name, asset_type, confirmed_name=confirmed_name,
        )
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
        )
        args = self.propagator.get_graph_args()

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(company_name, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        # Always use stream() for per-node timing collection.
        # Override to "updates" mode so each chunk is {node_name: {changed_fields}}.
        timings = []
        merged_state: dict[str, Any] = dict(init_agent_state)
        t_stream_start = _time.perf_counter()
        t_prev = t_stream_start
        stream_args = {**args, "stream_mode": "updates"}

        # Wall-time tracker for per-analyst elapsed times.
        plan = build_analyst_execution_plan(self.selected_analysts)
        tracker = AnalystWallTimeTracker(plan)

        for chunk in self.graph.stream(init_agent_state, **stream_args):
            t_now = _time.perf_counter()
            # Each chunk is {node_name: state_update_dict}
            for node_name, state_update in chunk.items():
                timings.append({
                    "node": node_name,
                    "duration_s": round(t_now - t_prev, 2),
                })
                # Merge update into full state
                if isinstance(state_update, dict):
                    merged_state.update(state_update)
                    # Sync wall-time tracker per analyst
                    for spec in plan.specs:
                        if node_name == spec.agent_node:
                            tracker.mark_started(spec.key, started_at=t_now)
                        if state_update.get(spec.report_key):
                            tracker.mark_completed(spec.key, completed_at=t_now)
            t_prev = t_now
            if self.debug and merged_state.get("messages"):
                merged_state["messages"][-1].pretty_print()

        final_state = merged_state
        self.node_timings = timings
        self.analyst_wall_times = tracker.get_wall_times()
        self.analyst_wall_time_summary = tracker.format_summary()
        self.total_stream_time = round(_time.perf_counter() - t_stream_start, 2)

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        self.memory_log.store_decision(
            ticker=company_name,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )

        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "governance_report": final_state["governance_report"],
            "industry_report": final_state["industry_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
