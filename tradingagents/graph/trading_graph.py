# TradingAgents/graph/trading_graph.py

import json
import logging
import os
import time as _time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.agents import *

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_block_trade,
    get_cailianpress_telegrams,
    get_cashflow,
    get_chip_distribution,
    get_company_announcements,
    get_concept_board,
    get_dividend_history,
    get_dragon_tiger,
    get_earnings_estimates,
    get_earnings_forecast,
    get_fund_flow,
    get_fundamentals,
    get_global_news,
    get_historical_valuation,
    get_income_statement,
    get_index_daily,
    get_indicators,
    get_industry_valuation,
    get_insider_transactions,
    get_institutional_intelligence,
    get_limit_up_down,
    get_macro_indicators,
    get_margin_trading,
    get_news,
    get_northbound_hold,
    get_pledge_ratio,
    get_research_reports,
    get_restricted_release,
    get_sector_fund_flow,
    get_shareholder_count,
    get_stock_data,
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import (
    collect_route_diagnostics,
    get_route_diagnostics,
)
from tradingagents.dataflows.runtime_context import (
    get_runtime_data_context,
    runtime_data_context_for,
    use_runtime_data_context,
)
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.llm_clients.fallback import patch_invoke_with_fallback
from tradingagents.llm_clients.retry_utils import RetryConfig
from tradingagents.reporting import write_report_tree

from .analyst_execution import (
    AnalystWallTimeTracker,
    build_analyst_execution_plan,
)
from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, has_checkpoint, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .reflection import Reflector
from .setup import GraphSetup
from .signal_processing import SignalProcessor


def _lookup_rpm(
    rpm_map: dict[str, Any], provider: str, model: str | None
) -> Any:
    """Resolve the requests-per-minute cap for a provider/model pair.

    A model-specific ``"provider/model"`` entry wins over the bare provider
    entry because plans like the SenseNova Token Plan meter each model
    independently. Returns None when neither entry exists.
    """
    if model:
        rpm = rpm_map.get(f"{provider}/{model}")
        if rpm is not None:
            return rpm
    return rpm_map.get(provider)


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=None,
        debug=False,
        config: dict[str, Any] | None = None,
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

        self.deep_thinking_llm = self._create_fallback_llm("deep_think_fallback", llm_kwargs)
        self.quick_thinking_llm = self._create_fallback_llm("quick_think_fallback", llm_kwargs)
        self.deep_think_role_llms = self._create_role_llms(llm_kwargs)
        self.quick_think_role_llms = self._create_quick_role_llms(llm_kwargs)

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
            role_llms=self.deep_think_role_llms,
            analyst_llms=self.quick_think_role_llms,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_think_role_llms["reflector"])
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state: dict[str, Any] | None = None
        self.ticker: str | None = None
        self.log_states_dict: dict[str, dict[str, Any]] = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.selected_analysts = selected_analysts
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        # Any: _GeneratorContextManager generics aren't worth spelling out here.
        self._checkpointer_ctx: Any = None

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

        # Per-request HTTP timeout so a stalled socket fails fast into the
        # retry/fallback logic instead of hanging the run indefinitely.
        timeout = self.config.get("llm_request_timeout")
        if timeout:
            kwargs["timeout"] = float(timeout)

        # Retry/backoff configuration for transient LLM errors.
        if self.config.get("llm_retry_enabled", True):
            kwargs["retry_config"] = RetryConfig(
                enabled=True,
                max_retries=int(self.config.get("llm_retry_max_retries", 3)),
                base_delay=float(self.config.get("llm_retry_base_delay", 2.0)),
            )
        else:
            kwargs["retry_config"] = RetryConfig(enabled=False)  # pragma: no cover  -- retry-disabled config branch; default is enabled

        return kwargs

    def _create_role_llms(self, llm_kwargs: dict) -> dict[str, Any]:
        """Build per-role deep-think LLMs for roles that support overrides.

        Reads ``config["deep_think_llm_roles"]`` — a mapping of role name to a
        model override. Supported roles: the three structured serial decision
        roles (``research_manager`` / ``trader`` / ``portfolio_manager``) and
        the debaters (``bull_researcher`` / ``bear_researcher`` /
        ``aggressive_debater`` / ``neutral_debater`` /
        ``conservative_debater``). Roles without an override, or whose
        override equals the base ``deep_think_llm`` model, share the base
        deep-think chain so no duplicate clients are created.
        """
        return self._build_role_llms(
            llm_kwargs,
            roles=(
                "research_manager",
                "trader",
                "portfolio_manager",
                "bull_researcher",
                "bear_researcher",
                "aggressive_debater",
                "neutral_debater",
                "conservative_debater",
            ),
            base_llm=self.deep_thinking_llm,
            base_model=self.config.get("deep_think_llm"),
            config_key="deep_think_fallback",
            roles_config_key="deep_think_llm_roles",
        )

    def _create_quick_role_llms(self, llm_kwargs: dict) -> dict[str, Any]:
        """Build per-role quick-think LLMs for roles that support overrides.

        Reads ``config["quick_think_llm_roles"]`` — a mapping of role name to
        a model override. Supported roles: the six analysts (``market`` /
        ``social`` / ``news`` / ``fundamentals`` / ``governance`` /
        ``industry``) and the ``reflector``. Roles without an override, or
        whose override equals the base ``quick_think_llm`` model, share the
        base quick-think chain so no duplicate clients are created.
        """
        return self._build_role_llms(
            llm_kwargs,
            roles=(
                "market",
                "social",
                "news",
                "fundamentals",
                "governance",
                "industry",
                "reflector",
            ),
            base_llm=self.quick_thinking_llm,
            base_model=self.config.get("quick_think_llm"),
            config_key="quick_think_fallback",
            roles_config_key="quick_think_llm_roles",
        )

    def _build_role_llms(
        self,
        llm_kwargs: dict,
        roles: tuple[str, ...],
        base_llm: Any,
        base_model: str | None,
        config_key: str,
        roles_config_key: str,
    ) -> dict[str, Any]:
        """Shared per-role LLM builder backing both think tiers.

        Every role defaults to ``base_llm``; an entry in
        ``config[roles_config_key]`` gets a dedicated fallback chain built
        from ``config_key`` when it points somewhere other than the base
        model. Two value forms are accepted:

        - ``"some-model"`` — same provider as the tier's base, different
          model (legacy form).
        - ``{"provider": ..., "model": ...}`` — a different provider+model
          pair (cross-provider offload, e.g. routing debate roles to a
          free-tier provider).

        Unknown roles are warned about and ignored; empty, malformed, or
        base-equal overrides keep sharing the base chain.
        """
        role_llms: dict[str, Any] = dict.fromkeys(roles, base_llm)
        base_provider = self.config.get("llm_provider")
        for role, override in (self.config.get(roles_config_key) or {}).items():
            if role not in role_llms:
                logger.warning(f"Unknown {roles_config_key} entry %r ignored", role)
                continue
            if isinstance(override, dict):
                provider = override.get("provider")
                model = override.get("model")
                if not provider or not model:
                    logger.warning(
                        f"Malformed {roles_config_key} entry for %r ignored: %r",
                        role,
                        override,
                    )
                    continue
                if (provider, model) == (base_provider, base_model):
                    continue
            else:
                provider, model = None, override
                if not model or model == base_model:
                    continue
            role_llms[role] = self._create_fallback_llm(
                config_key,
                llm_kwargs,
                model_override=(
                    {"provider": provider, "model": model}
                    if provider
                    else model
                ),
            )
        return role_llms

    def _create_fallback_llm(self, config_key: str, llm_kwargs: dict, model_override: str | dict | None = None):
        """Create an LLM instance with provider fallback chain.

        The primary tier comes from the explicit model configuration —
        ``config["deep_think_llm"]`` / ``config["quick_think_llm"]`` (or
        *model_override* for per-role deep-think variants) combined with
        ``config["llm_provider"]`` and ``config["backend_url"]``. The
        fallback entries at ``config_key`` are appended after it, skipping
        any entry whose provider+model duplicates the primary, and the
        primary's ``invoke`` is patched to try each fallback on transient
        provider errors.

        *model_override* may be a bare model string (same provider as the
        tier base) or a ``{"provider": ..., "model": ...}`` dict for
        cross-provider offload; the tier base URL only applies when the
        override keeps the base provider.

        Fallback tiers whose API key is not set in the environment are
        silently skipped so the graph can start even when only the primary
        provider is configured.
        """
        fallback_config = self.config.get(config_key)
        if not fallback_config:  # pragma: no cover  -- legacy config without fallback
            return self._fallback_to_legacy(config_key, llm_kwargs)

        model_key = "deep_think_llm" if "deep" in config_key else "quick_think_llm"
        override_provider: str | None = None
        override_model: str | None = None
        if isinstance(model_override, dict):
            override_provider = model_override.get("provider")
            override_model = model_override.get("model")
        else:
            override_model = model_override
        primary_provider = override_provider or self.config.get("llm_provider")
        primary_model = override_model or self.config.get(model_key)
        if not primary_provider or not primary_model:  # pragma: no cover  -- defensive; both are set in default config
            return self._fallback_to_legacy(config_key, llm_kwargs)

        tiers = [{"provider": primary_provider, "model": primary_model}]
        tiers.extend(
            entry
            for entry in fallback_config
            if (entry["provider"], entry["model"]) != (primary_provider, primary_model)
        )

        rpm_map = self.config.get("llm_requests_per_minute") or {}
        llm_chain = []
        for i, entry in enumerate(tiers):
            # ``backend_url`` belongs to the tier base provider (it is the
            # SenseNova Token Plan endpoint). It must not leak onto tiers of
            # a cross-provider role override — those use their provider's
            # default endpoint.
            tier_base_url = (
                self.config.get("backend_url")
                if entry["provider"] == primary_provider and not override_provider
                else None
            )
            tier_kwargs = dict(llm_kwargs)
            tier_rpm = _lookup_rpm(rpm_map, entry["provider"], entry["model"])
            if tier_rpm:
                tier_kwargs["requests_per_minute"] = tier_rpm
            try:
                client = create_llm_client(
                    provider=entry["provider"],
                    model=entry["model"],
                    base_url=tier_base_url,
                    **tier_kwargs,
                )
                llm_chain.append(client.get_llm())
            except ValueError as exc:
                msg = str(exc).lower()
                if "api key" in msg or "not set" in msg:
                    if i == 0:
                        logger.warning(
                            "Primary LLM provider '%s' has no API key set — "
                            "graph will likely fail at runtime: %s",
                            entry["provider"], exc,
                        )
                    else:
                        logger.info(
                            "Skipping fallback tier %d (%s/%s): %s",
                            i, entry["provider"], entry["model"], exc,
                        )
                    continue
                raise

        if len(llm_chain) <= 1:
            return llm_chain[0] if llm_chain else self._fallback_to_legacy(config_key, llm_kwargs)

        return patch_invoke_with_fallback(llm_chain[0], llm_chain[1:])

    def _fallback_to_legacy(self, config_key: str, llm_kwargs: dict):
        """Fall back to the legacy single-provider path when no fallback
        tiers could be created (all API keys missing) or fallback is not
        configured."""
        model_key = "deep_think_llm" if "deep" in config_key else "quick_think_llm"
        provider = self.config["llm_provider"]
        legacy_kwargs = dict(llm_kwargs)
        legacy_rpm = _lookup_rpm(
            self.config.get("llm_requests_per_minute") or {},
            provider,
            self.config[model_key],
        )
        if legacy_rpm:
            legacy_kwargs["requests_per_minute"] = legacy_rpm
        client = create_llm_client(
            provider=provider,
            model=self.config[model_key],
            base_url=self.config.get("backend_url"),
            **legacy_kwargs,
        )
        return client.get_llm()

    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # Capital flow analysis
                    get_fund_flow,
                    get_sector_fund_flow,
                    get_chip_distribution,
                    get_limit_up_down,
                    get_index_daily,
                    get_verified_market_snapshot,
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
                    get_research_reports,
                    # Cailianpress flash news telegrams
                    get_cailianpress_telegrams,
                ]
            ),
            "governance": ToolNode(
                [
                    # Governance analysis tools
                    get_company_announcements,
                    get_insider_transactions,
                    get_news,
                    get_restricted_release,
                    get_institutional_intelligence,
                    get_northbound_hold,
                    get_margin_trading,
                    get_pledge_ratio,
                    get_dragon_tiger,
                    # Block trades (大宗交易) for institutional accumulation/distribution signals
                    get_block_trade,
                ]
            ),
            "industry": ToolNode(
                [
                    # Industry valuation comparison
                    get_industry_valuation,
                    get_concept_board,
                    # Macroeconomic indicators for sector-wide context
                    get_macro_indicators,
                    # Sector fund flow for capital rotation patterns
                    get_sector_fund_flow,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                    get_historical_valuation,
                    get_earnings_forecast,
                    get_earnings_estimates,
                    get_shareholder_count,
                    get_dividend_history,
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

        For A-share tickers, uses ``load_ohlcv`` (smartmoney_db → akshare chain)
        instead of ``yf.Ticker`` to avoid yfinance rate limits on A-share data.
        """
        from tradingagents.dataflows.symbol_utils import normalize_symbol

        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            # If the required holding period hasn't fully elapsed yet, skip
            # resolving this outcome — we can't fetch data from the future.
            if start + timedelta(days=holding_days) > today:
                return None, None, None

            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays

            # Cap the requested end date at today to avoid load_ohlcv's
            # stale-data check comparing real data against a future date.
            if end > today:
                end = today

            end_str = end.strftime("%Y-%m-%d")

            if asset_type == "crypto":
                return self._fetch_crypto_returns(ticker, trade_date, end_str, holding_days, benchmark)

            # Normalize so the realized-return lookup hits the same instrument
            # the analysis priced (e.g. XAUUSD -> GC=F) (#984). The benchmark is
            # already a canonical Yahoo symbol from ``_resolve_benchmark``.
            canonical = normalize_symbol(ticker)

            # A-share: use load_ohlcv (smartmoney_db → akshare) instead of yfinance
            # to avoid rate-limit failures on batch runs (#PR).
            from tradingagents.dataflows.akshare_common import is_a_share_ticker
            from tradingagents.dataflows.stockstats_utils import (
                load_index_ohlcv,
                load_ohlcv,
            )

            if is_a_share_ticker(canonical):
                full = load_ohlcv(canonical, end_str, refresh=False)
                if full is not None and not full.empty and "Close" in full.columns:
                    # load_ohlcv returns Date as a column; select rows in [trade_date, end_str].
                    # iloc[] positional access works fine without a DatetimeIndex.
                    mask = (full["Date"] >= trade_date) & (full["Date"] <= end_str)
                    stock = full.loc[mask].copy()
                else:
                    stock = pd.DataFrame()

                # The benchmark is resolved as a yfinance-style index symbol
                # (e.g. 399001.SZ / 000001.SS). Serve it from the local
                # index_daily table (→ akshare fallback) for the same
                # rate-limit reason; yfinance stays as the last resort.
                local_bench = load_index_ohlcv(benchmark, trade_date, end_str)
                if (
                    local_bench is not None
                    and not local_bench.empty
                    and "Close" in local_bench.columns
                ):
                    bmask = (local_bench["Date"] >= trade_date) & (
                        local_bench["Date"] <= end_str
                    )
                    bench = local_bench.loc[bmask].copy()
                else:
                    bench = yf.Ticker(benchmark).history(
                        start=trade_date, end=end_str
                    )
            else:
                stock = yf.Ticker(canonical).history(start=trade_date, end=end_str)
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
            if len(prices) < 2:  # pragma: no cover  -- <2 price points from CoinGecko edge case
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
        except Exception as e:  # pragma: no cover  -- CoinGecko network failure fallback
            logger.warning(
                "Could not resolve crypto outcome for %s on %s (will retry next run): %s",
                ticker, start_date, e,
            )
            return None, None, None  # pragma: no cover

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
            if raw is None or alpha is None:
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

    def propagate(
        self,
        company_name,
        trade_date,
        asset_type: str = "stock",
        confirmed_name: str | None = None,
        on_chunk=None,
        holdings_context: dict | None = None,
        transactions_context: list | None = None,
        company_display_name: str | None = None,
    ):
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

        ``on_chunk`` is an optional callable invoked with the merged state
        after every streamed node update, letting UI callers (batch runner)
        render live progress through this same execution path. The dict is
        the live merged state — callbacks must treat it as read-only.

        ``holdings_context`` / ``transactions_context`` / ``company_display_name``
        seed the initial state (portfolio position, transaction history, and
        the resolved display name) for callers that have them, e.g. the batch
        runner.
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
                        can_reuse = True
                        if holdings_context is not None:
                            holdings_expected = (
                                {
                                    t: h.to_dict() if hasattr(h, "to_dict") else h
                                    for t, h in holdings_context.items()
                                }
                                if isinstance(holdings_context, dict)
                                else {}
                            )
                            if cached_state.get("holdings_context") != holdings_expected:
                                logger.info(
                                    "Holdings context changed for %s on %s; invalidating cached state log.",
                                    ticker,
                                    trade_date,
                                )
                                can_reuse = False
                        if can_reuse and transactions_context is not None:
                            tx_expected = [
                                t.to_dict() if hasattr(t, "to_dict") else t
                                for t in transactions_context
                            ]
                            if cached_state.get("transactions_context") != tx_expected:
                                logger.info(
                                    "Transactions context changed for %s on %s; invalidating cached state log.",
                                    ticker,
                                    trade_date,
                                )
                                can_reuse = False

                        if can_reuse:
                            logger.info(
                                "Found completed state log for %s on %s, skipping graph run.",
                                ticker,
                                trade_date,
                            )
                            self.curr_state = cached_state
                            cached_state.setdefault("data_coverage", [])
                            cached_context = runtime_data_context_for(ticker, str(trade_date))
                            cached_state.setdefault("market", cached_context.market)
                            cached_state.setdefault("analysis_dates", asdict(cached_context.dates))
                            # Clear any stale checkpoint so the next run starts fresh.
                            clear_checkpoint(
                                self.config["data_cache_dir"], ticker, str(trade_date)
                            )
                            return cached_state, self.process_signal(
                                cached_state["final_trade_decision"]
                            )
                except Exception as exc:
                    # Degrading to a full re-run is the right outcome, but the
                    # cause must be observable — a bare pass here made
                    # malformed state logs and process_signal bugs silent.
                    logger.warning(
                        "Could not reuse cached state log for %s on %s; re-running: %s: %s",
                        ticker,
                        trade_date,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )

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

        runtime_context = runtime_data_context_for(ticker, str(trade_date))
        try:
            with use_runtime_data_context(runtime_context), collect_route_diagnostics() as route_diagnostics:
                result = self._run_graph(
                    ticker,
                    trade_date,
                    asset_type=asset_type,
                    confirmed_name=resolved_name,
                    on_chunk=on_chunk,
                    holdings_context=holdings_context,
                    transactions_context=transactions_context,
                    company_display_name=company_display_name,
                )
            final_state, signal = result
            final_state["data_coverage"] = [asdict(item) for item in route_diagnostics]
            return final_state, signal
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def save_reports(self, final_state, ticker, save_path=None) -> Path:
        """Write the markdown report tree for a completed run, like the CLI does.

        Programmatic callers get the same on-disk reports the CLI produces. Pass
        an explicit ``save_path`` or let it default under ``results_dir``.
        """
        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(self.config["results_dir"])
                / "reports"
                / f"{safe_ticker_component(ticker)}_{stamp}"
            )
        return write_report_tree(final_state, ticker, save_path)

    def _run_graph(
        self,
        company_name,
        trade_date,
        asset_type: str = "stock",
        confirmed_name: str | None = None,
        on_chunk=None,
        holdings_context: dict | None = None,
        transactions_context: list | None = None,
        company_display_name: str | None = None,
    ):
        """Execute the graph and write the resulting state to disk and memory log.

        ``on_chunk`` is an optional callable invoked with the merged state
        after every streamed node update (same shape as a "values"-mode
        chunk), so UI callers can render live progress through this shared
        execution path. The dict is the live merged state — callbacks must
        treat it as read-only.
        """
        # Initialize state — inject memory log context for PM and the
        # deterministically resolved instrument identity for all agents.
        past_context = self.memory_log.get_past_context(company_name)
        instrument_context = self.resolve_instrument_context(
            company_name, asset_type, confirmed_name=confirmed_name,
        )
        runtime_context = get_runtime_data_context() or runtime_data_context_for(
            company_name, str(trade_date)
        )
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
            holdings_context=holdings_context,
            transactions_context=transactions_context,
            market=runtime_context.market,
            analysis_dates=runtime_context.dates,
        )
        if company_display_name:
            init_agent_state["company_name"] = company_display_name

        # Config-level callbacks (tool execution tracking) ride on the same
        # handlers passed to the LLM constructor; LangChain dedups identical
        # handler instances when merging constructor + config callbacks, so
        # this mirrors the CLI/batch wiring without double counting.
        args = self.propagator.get_graph_args(callbacks=self.callbacks or None)

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        resume_from_checkpoint = False
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(company_name, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid
            resume_from_checkpoint = has_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )

        # Build the verified market snapshot once, with a forced refresh, so
        # every downstream agent shares the same ground-truth data. The Market
        # Analyst's get_stock_data tool goes through the unified vendor router,
        # whose OHLCV vendors share the same load_ohlcv cache, so this snapshot
        # and the analyst's raw data still come from the same source. If the
        # initial state already carries a snapshot (e.g. programmatic callers or
        # tests), keep it instead of recomputing. On a checkpoint resume the
        # snapshot is already in the checkpointed state, so skip the refresh
        # entirely.
        if not resume_from_checkpoint and not init_agent_state.get("verified_market_snapshot"):
            try:
                from tradingagents.dataflows.market_data_validator import (
                    build_verified_market_snapshot,
                )

                init_agent_state["verified_market_snapshot"] = (
                    build_verified_market_snapshot(
                        company_name,
                        str(trade_date),
                        refresh=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not build verified market snapshot for %s on %s: %s",
                    company_name,
                    trade_date,
                    exc,
                )

        # Build the verified fundamentals snapshot once, reusing the configured
        # vendor layer (smartmoney_db → akshare → ...). The Fundamentals Analyst
        # and Portfolio Manager are told to ground any fundamental numbers in it.
        if not resume_from_checkpoint and not init_agent_state.get("verified_fundamentals_snapshot"):
            try:
                from tradingagents.dataflows.market_data_validator import (
                    build_verified_fundamentals_snapshot,
                    render_fundamentals_snapshot,
                )

                fundamentals_dict = build_verified_fundamentals_snapshot(
                    company_name, str(trade_date)
                )
                init_agent_state["verified_fundamentals_snapshot"] = (
                    render_fundamentals_snapshot(fundamentals_dict)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not build verified fundamentals snapshot for %s on %s: %s",
                    company_name,
                    trade_date,
                    exc,
                )

        # Always use stream() for per-node timing collection.
        # Override to "updates" mode so each chunk is {node_name: {changed_fields}}.
        timings = []
        merged_state: dict[str, Any] = dict(init_agent_state)
        t_stream_start = _time.perf_counter()
        t_prev = t_stream_start
        stream_args = {**args, "stream_mode": "updates"}
        stream_input: dict[str, Any] | None = init_agent_state

        if resume_from_checkpoint:
            # A checkpoint exists for this thread: stream(None) resumes after
            # the last completed node instead of re-running the whole graph
            # from START (which would redo every analyst LLM call). Seed the
            # merged state from the checkpointed channel values so outputs of
            # already-completed nodes survive into final_state.
            try:
                checkpoint_values = self.graph.get_state(args["config"]).values
                if checkpoint_values:
                    merged_state = {**init_agent_state, **checkpoint_values}
                    stream_input = None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not load checkpoint state for %s on %s; starting fresh: %s",
                    company_name,
                    trade_date,
                    exc,
                )

        # Wall-time tracker for per-analyst elapsed times.
        plan = build_analyst_execution_plan(self.selected_analysts)
        tracker = AnalystWallTimeTracker(plan)
        last_printed = None

        for chunk in self.graph.stream(stream_input, **stream_args):
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
                            tracker.mark_started(spec.key, started_at=t_now)  # pragma: no cover  -- driven by full graph stream; unit tests mock individual nodes
                        if state_update.get(spec.report_key):
                            tracker.mark_completed(spec.key, completed_at=t_now)
            t_prev = t_now
            # Nodes after the trader don't append to messages, so the
            # same trailing message repeats across chunks. Print it only
            # when it changes (#1027, upstream 709fe2b).
            if self.debug and merged_state.get("messages"):
                msg = merged_state["messages"][-1]
                signature = (type(msg).__name__, getattr(msg, "content", None))
                if signature != last_printed:
                    msg.pretty_print()
                    last_printed = signature
            if on_chunk is not None:
                on_chunk(merged_state)

        final_state = merged_state
        final_state["data_coverage"] = [
            asdict(item) for item in get_route_diagnostics()
        ]
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
        holdings_ctx = final_state.get("holdings_context")
        if isinstance(holdings_ctx, dict):
            holdings_serializable = {
                t: h.to_dict() if hasattr(h, "to_dict") else h
                for t, h in holdings_ctx.items()
            }
        else:
            holdings_serializable = {}

        tx_ctx = final_state.get("transactions_context")
        if isinstance(tx_ctx, list):
            tx_serializable = [
                t.to_dict() if hasattr(t, "to_dict") else t for t in tx_ctx
            ]
        else:
            tx_serializable = []

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
            "data_coverage": final_state.get("data_coverage", []),
            "holdings_context": holdings_serializable,
            "transactions_context": tx_serializable,
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
