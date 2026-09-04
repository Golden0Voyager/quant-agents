import copy
import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    "TRADINGAGENTS_LLM_RETRY_ENABLED":    "llm_retry_enabled",
    "TRADINGAGENTS_LLM_RETRY_MAX_RETRIES": "llm_retry_max_retries",
    "TRADINGAGENTS_LLM_RETRY_BASE_DELAY": "llm_retry_base_delay",
    "TRADINGAGENTS_LLM_REQUEST_TIMEOUT":  "llm_request_timeout",
    "TRADINGAGENTS_RESULTS_DIR":          "results_dir",
    "TRADINGAGENTS_CACHE_DIR":            "data_cache_dir",
    "TRADINGAGENTS_MEMORY_LOG_PATH":      "memory_log_path",
    "TRADINGAGENTS_TUSHARE_ENABLED":     "tushare_enabled",
    "DISABLE_YFINANCE_FALLBACK":          "disable_yfinance_fallback",
    "INPUT_TOKEN_PRICE_PER_1M":           "input_token_price_per_1m",
    "OUTPUT_TOKEN_PRICE_PER_1M":          "output_token_price_per_1m",
}


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value."""
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


# Base configuration.  Use ``default_config()`` to obtain a deep copy with
# environment overrides applied; ``DEFAULT_CONFIG`` is kept as a backward-
# compatible alias to ``default_config()`` at import time.
_BASE_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.path.join(_TRADINGAGENTS_HOME, "logs"),
    "data_cache_dir": os.path.join(_TRADINGAGENTS_HOME, "cache"),
    # Tushare is optional and only appended to supported routes when enabled.
    "tushare_enabled": False,
    "memory_log_path": os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md"),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings (defaults aligned with personal usage: SenseNova Token Plan)
    "llm_provider": "sensenova",
    "deep_think_llm": "glm-5.2",
    "quick_think_llm": "sensenova-6.8-flash-lite",
    # Per-role deep-think model overrides for the three structured serial
    # decision roles (research_manager / trader / portfolio_manager). Roles
    # not listed — or mapped to None / the base model — share the
    # ``deep_think_llm`` chain; bull/bear researchers and risk debaters
    # always use ``deep_think_llm``. Splitting roles across models spreads
    # per-model quota (SenseNova plan: each model has its own 5h bucket).
    "deep_think_llm_roles": {
        "research_manager": "deepseek-v4-flash",
        "trader": "glm-5.2",
        "portfolio_manager": "glm-5.2",
    },
    # SenseNova Token Plan endpoint; upstream default is None (per-provider fallback)
    "backend_url": "https://token.sensenova.cn/v1",
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # LLM retry/backoff for transient provider errors (rate limits, timeouts,
    # 5xx). ``llm_retry_enabled`` can be set to false to disable retries.
    "llm_retry_enabled": True,
    "llm_retry_max_retries": 3,
    "llm_retry_base_delay": 2.0,
    # Per-request HTTP timeout (seconds) for every LLM call. Without it the
    # OpenAI-compatible SDKs wait on a half-open socket indefinitely — a
    # stalled connection then looks like a frozen run (observed: an 8-hour
    # silent hang). Must exceed the slowest legitimate deep-think call
    # (portfolio-manager decisions have been measured at ~380s); on timeout
    # the retry/backoff and fallback-chain logic takes over.
    "llm_request_timeout": 600.0,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "Chinese",
    # Token pricing overrides (fallback for unknown models in cost estimation)
    # Provider fallback chains: when the primary LLM returns a transient
    # error (quota exceeded, rate limit, 5xx), the system tries each
    # subsequent entry in order before giving up.
    # Each entry: {"provider": str, "model": str}
    # The first entry's provider and backend_url match the primary config;
    # subsequent entries use their provider's default endpoint.
    # Verified 2026-08-26: every tier below answers with_structured_output
    # (tool_choice) correctly. ModelScope renamed its DeepSeek deployments —
    # the new dated IDs (DeepSeek-V4-Flash-0731 / DeepSeek-V4-Pro-0813)
    # support tool_choice; the undated originals were retired server-side
    # (400 "no provider"). Qwen/Qwen3.8-27B is the only Qwen tier verified
    # for tool_choice — Qwen3.5-397B-A17B returns `choices: null` on
    # tool_choice requests. MiniMax/MiniMax-M3 (retired), moonshotai/Kimi-K3
    # (no inference provider) and Tencent-Hunyuan/Hy3 (null choices even on
    # plain calls) are all unusable and stay out.
    "quick_think_fallback": [
        {"provider": "sensenova",   "model": "sensenova-6.8-flash-lite"},
        {"provider": "sensenova",   "model": "deepseek-v4-flash"},
        {"provider": "modelscope",  "model": "deepseek-ai/DeepSeek-V4-Flash-0731"},
        {"provider": "modelscope",  "model": "stepfun-ai/Step-3.7-Flash"},
        {"provider": "modelscope",  "model": "Qwen/Qwen3.8-27B"},
        {"provider": "modelscope",  "model": "ZhipuAI/GLM-5.2"},
        {"provider": "openrouter",  "model": "nvidia/nemotron-3-ultra-550b-a55b:free"},
        {"provider": "openrouter",  "model": "nvidia/nemotron-3-super-120b-a12b:free"},
    ],
    "deep_think_fallback": [
        {"provider": "sensenova",   "model": "deepseek-v4-flash"},
        {"provider": "modelscope",  "model": "deepseek-ai/DeepSeek-V4-Pro-0813"},
        {"provider": "modelscope",  "model": "ZhipuAI/GLM-5.2"},
        {"provider": "modelscope",  "model": "stepfun-ai/Step-3.7-Flash"},
        {"provider": "openrouter",  "model": "nvidia/nemotron-3-ultra-550b-a55b:free"},
        {"provider": "openrouter",  "model": "nvidia/nemotron-3-super-120b-a12b:free"},
    ],
    # Client-side request pacing (requests per minute). Keys are either a
    # bare provider ("sensenova") or a provider/model pair
    # ("sensenova/deepseek-v4-flash"); the model-specific entry wins when both
    # are present. A process-wide shared token-bucket limiter per key caps
    # aggregate RPM across all batch workers and both think tiers, preventing
    # 429 bursts against low-quota plans. A provider/model not listed here is
    # not rate-limited client-side. Set to {} to disable pacing entirely.
    #
    # SenseNova Token Plan (2026-08-28 起) is credit-based: 通用积分 +
    # Flash-Lite 专属积分 pools, each 60k credits / rolling 5h and 600k /
    # rolling week, deducted by actual token usage at per-model rates (see
    # docs/sensenova-deepseek-integration.md). The values below are therefore
    # conservative call-rate pacers, NOT quota conversions — tune them against
    # the real per-model credit rates shown in the account's 积分明细.
    "llm_requests_per_minute": {
        "sensenova/sensenova-6.8-flash-lite": 5.0,
        "sensenova/deepseek-v4-flash": 1.7,
        "sensenova": 5.0,
    },
    "input_token_price_per_1m": None,
    "output_token_price_per_1m": None,
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    #
    # Note: A-share tickers (.SS/.SZ/.BJ) auto-route to smartmoney_db first,
    # then akshare, then yfinance as last resort (determined at runtime).
    # US stocks / crypto (AAPL, BTC-USD, …) route to quant_db_global first —
    # the local quant_core.db global_assets_bars archive — with yfinance as
    # online fallback when the archive is missing or stale.
    # 2026-05-20: 优先依赖 AkShare 作为 A 股外部数据源，yfinance 仅作为最后兜底。
    # 如需完全禁用 yfinance fallback，可设置环境变量 DISABLE_YFINANCE_FALLBACK=1。
    "data_vendors": {
        "core_stock_apis": "smartmoney_db,quant_db_global,akshare,yfinance",
        # hithink (同花顺官方 API) sits between the local DB and akshare for the
        # methods it implements (financial statements / financial indicators);
        # methods it does not implement filter it out at chain-build time.
        "technical_indicators": "smartmoney_db,hithink,akshare,yfinance",
        "fundamental_data": "smartmoney_db,hithink,akshare,yfinance",
        "news_data": "akshare,yfinance",  # news not stored locally
        "macro_data": "akshare,fred",        # akshare → FRED fallback
        "research_opinion": "akshare,smartmoney_db",  # analyst reports: AkShare online → local DB fallback
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Redirect database-backed tools to use local DB (smartmoney_db) first
        "get_margin_trading": "smartmoney_db,akshare",
        "get_dragon_tiger": "smartmoney_db,hithink,akshare",
        "get_block_trade": "smartmoney_db,akshare",
        "get_institutional_holdings": "smartmoney_db,akshare",
        "get_northbound_hold": "smartmoney_db,akshare",
        # CNINFO is available through the existing AkShare dependency and
        # provides a second announcement index after the local archive.
        "get_company_announcements": "smartmoney_db,cninfo,akshare",
    },
    # When True, A-share vendor chains never fall back to yfinance.
    "disable_yfinance_fallback": False,
    # Portfolio / holdings configuration
    "portfolio": {
        "data_path": os.path.expanduser("~/Code/quant_data/tradingagents_portfolio.json"),
        "sheet_id": os.getenv("PORTFOLIO_SHEET_ID"),  # Default Google Sheet ID
        "worksheet": "total",    # Default worksheet/tab name
        "auto_sync": False,      # Auto-sync before analysis if local data is stale
        "sync_stale_hours": 24,  # Consider local data stale after N hours
        "transaction_sheet_id": os.getenv("TRANSACTION_SHEET_ID"),  # Transaction history Sheet ID
        "transaction_worksheet": "stock transitions",  # Transaction history worksheet name
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
}


def default_config() -> dict:
    """Return a fresh deep copy of the base config with env overrides applied."""
    config = copy.deepcopy(_BASE_CONFIG)
    return _apply_env_overrides(config)


# Backward-compatible import-time reference.  New code should call
# ``default_config()`` to obtain an independent copy.
DEFAULT_CONFIG = default_config()
