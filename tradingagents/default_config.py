import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings (defaults aligned with personal usage: SenseNova Token Plan)
    "llm_provider": "sensenova",
    "deep_think_llm": "deepseek-v4-flash",
    "quick_think_llm": "sensenova-6.7-flash-lite",
    # SenseNova Token Plan endpoint
    "backend_url": "https://token.sensenova.cn/v1",
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "Chinese",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 200,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # Note: A-share tickers (.SS/.SZ/.BJ) auto-route to smartmoney_db first,
    # then akshare, then yfinance as last resort.
    #
    # 2026-05-20: 优先依赖 AkShare 作为 A 股外部数据源，yfinance 仅作为最后兜底。
    # 如需完全禁用 yfinance fallback，可设置环境变量 DISABLE_YFINANCE_FALLBACK=1。
    "data_vendors": {
        "core_stock_apis": "smartmoney_db,akshare,yfinance",
        "technical_indicators": "smartmoney_db,akshare,yfinance",
        "fundamental_data": "smartmoney_db,akshare,yfinance",
        "news_data": "akshare,yfinance",  # news not stored locally
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Portfolio / holdings configuration
    "portfolio": {
        "data_path": os.path.expanduser("~/Code/data/quant_data/tradingagents_portfolio.json"),
        "sheet_id": os.getenv("PORTFOLIO_SHEET_ID"),  # Default Google Sheet ID
        "worksheet": "total",    # Default worksheet/tab name
        "auto_sync": False,      # Auto-sync before analysis if local data is stale
        "sync_stale_hours": 24,  # Consider local data stale after N hours
        "transaction_sheet_id": os.getenv("TRANSACTION_SHEET_ID"),  # Transaction history Sheet ID
        "transaction_worksheet": "stock transitions",  # Transaction history worksheet name
    },
}
