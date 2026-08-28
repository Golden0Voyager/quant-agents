import os
from pathlib import Path

import questionary
from dotenv import find_dotenv, set_key
from rich.console import Console

from cli.models import AnalystType, AssetType
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import get_model_options

console = Console()

BACK_VALUE = "__back__"
BACK_LABEL = "← 返回上一层"

TICKER_INPUT_EXAMPLES = "SPY, 0700.HK, BTC-USD"

ANALYST_ORDER = [
    ("Market Analyst", AnalystType.MARKET),
    ("Sentiment Analyst", AnalystType.SOCIAL),
    ("News Analyst", AnalystType.NEWS),
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),
    ("Governance Analyst", AnalystType.GOVERNANCE),
    ("Industry Analyst", AnalystType.INDUSTRY),
]

ANALYST_DESCRIPTIONS: dict[AnalystType, str] = {
    AnalystType.MARKET: "行情、技术指标、资金流向",
    AnalystType.SOCIAL: "StockTwits、Reddit 社交情绪",
    AnalystType.NEWS: "个股新闻、全球宏观、公告、内幕交易",
    AnalystType.FUNDAMENTALS: "财务三表、业绩预告、行业估值",
    AnalystType.GOVERNANCE: "股东、质押、龙虎榜、北向资金",
    AnalystType.INDUSTRY: "行业景气度、宏观指标（CPI/PMI）",
}

CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


def get_ticker(allow_back: bool = False) -> str:
    """Prompt the user to enter a ticker symbol, preserving exchange suffixes.

    Uses questionary.text (not typer.prompt, which strips trailing dot-suffixes
    like ``000404.SH`` on some shells) and validates the symbol charset so an
    obvious typo is caught before the run starts.
    """
    ticker = questionary.text(
        f"Enter ticker symbol (e.g. {TICKER_INPUT_EXAMPLES}):",
        validate=lambda x: (
            not x.strip()
            or (all(ch.isalnum() or ch in ".,_ -^" for ch in x.strip()) and len(x.strip()) <= 128)
            or "Please enter a valid ticker symbol, e.g. AAPL, 000404.SZ, 0700.HK."
        ),
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if ticker is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        exit(1)

    return normalize_ticker_symbol(ticker) if ticker.strip() else "SPY"


def normalize_ticker_symbol(ticker: str) -> str:
    """Normalize ticker input while preserving exchange suffixes and Chinese characters.

    Auto-appends .SS/.SZ/.BJ for 6-digit Chinese A-share numeric codes.
    """
    raw = ticker.strip()
    if not raw:
        return raw

    # Already has an exchange suffix -> pass through
    if "." in raw:
        return raw.upper()

    # Pure numeric -> treat as A-share code and append suffix
    if raw.isdigit():
        from tradingagents.ticker_resolver import _append_a_share_suffix
        return _append_a_share_suffix(raw).upper()

    return raw.upper()


def detect_asset_type(ticker: str) -> AssetType:
    normalized_ticker = ticker.strip().upper()
    if normalized_ticker.endswith(CRYPTO_SUFFIXES):
        return AssetType.CRYPTO
    return AssetType.STOCK


def filter_analysts_for_asset_type(
    analysts: list[AnalystType], asset_type: AssetType, ticker: str | None = None
) -> list[AnalystType]:
    """Drop analysts that don't apply to this asset type or market.

    A-share tickers (``.SS``/``.SZ``/``.BJ``) default to skipping
    Sentiment Analyst because StockTwits/Reddit coverage for A-shares is
    effectively zero. Callers that explicitly want Sentiment on an A-share
    can ignore this helper or pass a non-A-share ticker.

    HK stocks (``.HK``) keep the Sentiment Analyst — some HK-listed tech
    stocks have meaningful coverage on international social platforms.

    Crypto drops Fundamentals (no on-chain fundamentals to analyze).
    US/other stocks keep all analysts.
    """
    if asset_type == AssetType.CRYPTO:
        analysts = [a for a in analysts if a != AnalystType.FUNDAMENTALS]
    if ticker and _is_ashare_ticker(ticker):
        analysts = [a for a in analysts if a != AnalystType.SOCIAL]
    return analysts


def _is_ashare_ticker(ticker: str) -> bool:
    """A-share tickers with exchange suffixes that StockTwits/Reddit don't cover."""
    upper = ticker.strip().upper()
    return upper.endswith((".SS", ".SZ", ".BJ"))


def get_analysis_date(allow_back: bool = False) -> str:
    """Prompt the user to enter a date in YYYY-MM-DD format."""
    import re
    from datetime import datetime

    def validate_date(date_str: str) -> bool:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    date = questionary.text(
        "Enter the analysis date (YYYY-MM-DD):",
        validate=lambda x: validate_date(x.strip())
        or "Please enter a valid date in YYYY-MM-DD format.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if date is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)
    if not date:
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)

    return date.strip()


def select_analysts(
    asset_type: AssetType = AssetType.STOCK, ticker: str | None = None, allow_back: bool = False
) -> list[AnalystType]:
    """Select analysts using an interactive checkbox.

    Analysts that don't apply to the current market are shown as disabled
    rows with a skip reason, so the user understands why they are unavailable.
    """
    all_analysts = [value for _, value in ANALYST_ORDER]
    available_analysts = filter_analysts_for_asset_type(
        all_analysts, asset_type, ticker=ticker
    )

    # Build a skip-reason map for disabled items.
    skip_reasons: dict[AnalystType, str] = {}
    if asset_type == AssetType.CRYPTO and AnalystType.FUNDAMENTALS not in available_analysts:
        skip_reasons[AnalystType.FUNDAMENTALS] = "Crypto: no on-chain fundamentals"
    if ticker and _is_ashare_ticker(ticker) and AnalystType.SOCIAL not in available_analysts:
        suffix = ticker.strip().upper()[-3:]
        if suffix in (".SS", ".SZ", ".BJ"):
            skip_reasons[AnalystType.SOCIAL] = "A股: StockTwits/Reddit 无覆盖"
        else:
            skip_reasons[AnalystType.SOCIAL] = f"{suffix}: StockTwits/Reddit 无覆盖"

    choices = []
    for display, value in ANALYST_ORDER:
        if value in available_analysts:
            desc = ANALYST_DESCRIPTIONS.get(value, "")
            label = f"{display}  ({desc})" if desc else display
            choices.append(questionary.Choice(label, value=value, checked=True))
        else:
            reason = skip_reasons.get(value, "Not applicable")
            choices.append(
                questionary.Choice(
                    f"{display}  [{reason}]", value=value, disabled=reason
                )
            )

    instr = "\n- Press Space to select/unselect analysts\n- Press 'a' to select/unselect all\n- Press Enter when done"
    if allow_back:
        instr += "\n- 按 Esc 返回上一层"

    selected = questionary.checkbox(
        "Select Your [Analysts Team]:",
        choices=choices,
        instruction=instr,
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
                ("disabled", "fg:gray italic"),
            ]
        ),
    ).ask()

    if selected is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)
    if not selected:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    return selected


def select_research_depth(allow_back: bool = False) -> int:
    """Select research depth using an interactive selection."""

    # Define research depth options with their corresponding values
    # Deep is first so power users can just hit Enter.
    DEPTH_OPTIONS = [
        ("Deep - Comprehensive research, in depth debate and strategy discussion", 5),
        ("Medium - Middle ground, moderate debate rounds and strategy discussion", 3),
        ("Shallow - Quick research, few debate and strategy discussion rounds", 1),
    ]

    choices = [
        questionary.Choice(display, value=value) for display, value in DEPTH_OPTIONS
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))

    choice = questionary.select(
        "Select Your [Research Depth]:",
        choices=choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]No research depth selected. Exiting...[/red]")
        exit(1)
    if choice == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]

    return choice


# Mainstream OpenRouter chat-LLM provider namespaces. We surface the newest
# models from these rather than the universal-newest, which is dominated by
# niche/experimental releases. These are the general-purpose chat providers;
# more enterprise/specialised namespaces (nvidia, cohere, amazon, ...) tend to
# ship research/safety variants as their newest, so they're left out of the
# shortlist. Provider names are stable (unlike model IDs), so this rarely needs
# touching; anything not here is still reachable via Custom ID.
_OPENROUTER_MAINSTREAM = {
    "openai", "anthropic", "google", "deepseek", "qwen", "mistralai",
    "meta-llama", "x-ai", "z-ai", "minimax", "moonshotai",
}


def _fetch_openrouter_models() -> list[tuple[str, str]]:
    """Fetch available models from the OpenRouter API."""
    import requests
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=10)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        # Newest first so the top-N shown really is the latest available — the
        # API currently returns this order, but sort explicitly so the prompt's
        # "latest available" label holds regardless of response ordering.
        models.sort(key=lambda m: m.get("created") or 0, reverse=True)
        return [(m.get("name") or m["id"], m["id"]) for m in models]
    except Exception as e:
        console.print(f"\n[yellow]Could not fetch OpenRouter models: {e}[/yellow]")
        return []


def _fetch_kimi_models() -> list[tuple[str, str]]:
    """Fetch available models from the Kimi Coding Plan API.

    Falls back to the built-in catalog when the API key is missing or the
    request fails so the CLI always remains usable.
    """
    import requests

    api_key = os.environ.get("KIMI_CODING_API_KEY")
    if not api_key:
        return []
    try:
        resp = requests.get(
            "https://api.kimi.com/coding/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        models = resp.json().get("data", [])
        models.sort(key=lambda m: m.get("created") or 0, reverse=True)
        return [(m.get("name") or m["id"], m["id"]) for m in models]
    except Exception as e:
        console.print(f"\n[yellow]Could not fetch Kimi models: {e}[/yellow]")
        return []


def _require_text(message: str, hint: str, allow_back: bool = False) -> str:
    """Prompt for a required value; exit cleanly if the user cancels.

    ``questionary.text(...).ask()`` returns None on Ctrl-C/Esc; mirror the
    exit-on-cancel behavior of the other required selections so a cancelled
    prompt never returns an empty model/deployment that would fail downstream.
    """
    response = questionary.text(
        message,
        validate=lambda x: len(x.strip()) > 0 or hint,
    ).ask()
    if response is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]Cancelled. Exiting...[/red]")
        exit(1)
    return response.strip()


def select_openrouter_model(mode: str, allow_back: bool = False) -> str:
    """Select an OpenRouter model from the newest available, or enter a custom ID.

    ``mode`` ("quick"/"deep") labels the prompt so the two consecutive
    OpenRouter selections are distinguishable, like the other providers (#1000).
    """
    models = _fetch_openrouter_models()  # newest first
    # Prefer the newest from mainstream providers so the shortlist isn't crowded
    # out by niche/experimental releases; fall back to all if none match.
    mainstream = [
        (name, mid) for name, mid in models
        if not mid.startswith("~")  # skip variant/alias duplicate routes
        and mid.split("/", 1)[0] in _OPENROUTER_MAINSTREAM
    ]
    top = (mainstream or models)[:5]

    choices = [questionary.Choice(name, value=mid) for name, mid in top]
    choices.append(questionary.Choice("Custom model ID", value="custom"))
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))

    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking] OpenRouter Model (latest available):",
        choices=choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style([
            ("selected", "fg:magenta noinherit"),
            ("highlighted", "fg:magenta noinherit"),
            ("pointer", "fg:magenta noinherit"),
        ]),
    ).ask()

    if choice is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]No model selected. Exiting...[/red]")
        exit(1)
    if choice == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    if choice == "custom":
        custom = _require_text(
            "Enter OpenRouter model ID (e.g. google/gemma-4-26b-a4b-it):",
            "Please enter a model ID.",
            allow_back=allow_back,
        )
        if custom == BACK_VALUE:
            return BACK_VALUE  # type: ignore[return-value]
        return custom
    return choice


def select_kimi_model(mode: str, allow_back: bool = False) -> str:
    """Select a Kimi model from the API or the built-in catalog.

    ``mode`` ("quick"/"deep") labels the prompt like the other providers.
    If the API list is unavailable, falls back to the hardcoded catalog.
    """
    from tradingagents.llm_clients.model_catalog import get_model_options

    fetched = _fetch_kimi_models()
    top = fetched[:6] if fetched else get_model_options("kimi", mode)

    choices = [questionary.Choice(name, value=mid) for name, mid in top]
    if not any(value == "custom" for _, value in top):
        choices.append(questionary.Choice("Custom model ID", value="custom"))
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))

    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking] Kimi Model:",
        choices=choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style([
            ("selected", "fg:magenta noinherit"),
            ("highlighted", "fg:magenta noinherit"),
            ("pointer", "fg:magenta noinherit"),
        ]),
    ).ask()

    if choice is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("\n[red]No model selected. Exiting...[/red]")
        exit(1)
    if choice == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    if choice == "custom":
        custom = _prompt_custom_model_id(allow_back=allow_back)
        if custom == BACK_VALUE:
            return BACK_VALUE  # type: ignore[return-value]
        return custom
    return choice


def _prompt_custom_model_id(allow_back: bool = False) -> str:
    """Prompt user to type a custom model ID."""
    return _require_text("Enter model ID:", "Please enter a model ID.", allow_back=allow_back)


def _select_model(provider: str, mode: str, allow_back: bool = False) -> str:
    """Select a model for the given provider and mode (quick/deep)."""
    if provider.lower() == "openrouter":
        return select_openrouter_model(mode, allow_back=allow_back)
    if provider.lower() == "kimi":
        return select_kimi_model(mode, allow_back=allow_back)

    if provider.lower() == "azure":
        azure = _require_text(
            f"Enter Azure deployment name ({mode}-thinking):",
            "Please enter a deployment name.",
            allow_back=allow_back,
        )
        if azure == BACK_VALUE:
            return BACK_VALUE  # type: ignore[return-value]
        return azure

    model_choices = [
        questionary.Choice(display, value=value)
        for display, value in get_model_options(provider, mode)
    ]
    if allow_back:
        model_choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))

    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking LLM Engine]:",
        choices=model_choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print(f"\n[red]No {mode} thinking llm engine selected. Exiting...[/red]")
        exit(1)
    if choice == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]

    if choice == "custom":
        custom = _prompt_custom_model_id(allow_back=allow_back)
        if custom == BACK_VALUE:
            return BACK_VALUE  # type: ignore[return-value]
        return custom

    return choice


def select_shallow_thinking_agent(provider, allow_back: bool = False) -> str:
    """Select shallow thinking llm engine using an interactive selection."""
    return _select_model(provider, "quick", allow_back=allow_back)


def select_deep_thinking_agent(provider, allow_back: bool = False) -> str:
    """Select deep thinking llm engine using an interactive selection."""
    return _select_model(provider, "deep", allow_back=allow_back)

def _llm_provider_table() -> list[tuple[str, str, str | None]]:
    """(display_name, provider_key, base_url) for every supported provider.

    Shared by the interactive picker and by env-driven configuration so an
    env-set provider resolves to the same default endpoint the menu uses.
    Ollama users can point at a remote ollama-serve via OLLAMA_BASE_URL
    (convention from the broader Ollama ecosystem); falls back to the
    localhost default when unset.
    """
    ollama_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
    return [
        ("SenseNova", "sensenova", "https://token.sensenova.cn/v1"),
        ("Kimi", "kimi", "https://api.kimi.com/coding/v1"),
        ("OpenAI", "openai", "https://api.openai.com/v1"),
        ("Google", "google", None),
        ("Anthropic", "anthropic", "https://api.anthropic.com/"),
        ("xAI", "xai", "https://api.x.ai/v1"),
        ("DeepSeek", "deepseek", "https://api.deepseek.com"),
        ("Qwen", "qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("GLM", "glm", "https://open.bigmodel.cn/api/paas/v4/"),
        ("MiniMax", "minimax", "https://api.minimax.io/v1"),
        ("OpenRouter", "openrouter", "https://openrouter.ai/api/v1"),
        ("Azure OpenAI", "azure", None),
        ("Ollama", "ollama", ollama_url),
    ]


def provider_default_url(provider_key: str) -> str | None:
    """Return the default backend URL for a provider key, or None if unknown."""
    key = provider_key.lower()
    for _, pk, url in _llm_provider_table():
        if pk == key:
            return url
    return None


def select_llm_provider(allow_back: bool = False) -> tuple[str, str | None]:
    """Select the LLM provider and its API endpoint."""
    PROVIDERS = _llm_provider_table()

    choices = [
        questionary.Choice(display, value=(provider_key, url))
        for display, provider_key, url in PROVIDERS
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))

    choice = questionary.select(
        "Select your LLM Provider:",
        choices=choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        if allow_back:
            return BACK_VALUE, None  # type: ignore[return-value]
        console.print("\n[red]No LLM provider selected. Exiting...[/red]")
        exit(1)
    if choice == BACK_VALUE:
        return BACK_VALUE, None  # type: ignore[return-value]

    provider, url = choice
    return provider, url


def ask_workers(allow_back: bool = False) -> int:
    """Ask user to choose the number of concurrent workers for batch analysis."""
    choices = [
        questionary.Choice("1 — 顺序执行，稳定可靠（1 个 Worker）", value=1),
        questionary.Choice("2 — 轻量并发，速度翻倍（2 个 Worker，推荐）", value=2),
        questionary.Choice("3 — 中等并发，适合多只股票（3 个 Worker）", value=3),
        questionary.Choice("5 — 高并发，需确保 API 限流允许（5 个 Worker）", value=5),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    choice = questionary.select(
        "并发 Worker 数量（每个 Worker 分析一只股票）:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:green noinherit"),
            ("highlighted", "fg:green noinherit"),
            ("pointer", "fg:green noinherit"),
        ]),
    ).ask()
    if choice is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        console.print("[yellow]未选择，默认使用 3 个 Worker[/yellow]")
        return 3
    if choice == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    return choice


def ask_openai_reasoning_effort(allow_back: bool = False) -> str:
    """Ask for OpenAI reasoning effort level."""
    choices = [
        questionary.Choice("Medium (Default)", "medium"),
        questionary.Choice("High (More thorough)", "high"),
        questionary.Choice("Low (Faster)", "low"),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    result = questionary.select(
        "Select Reasoning Effort:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()
    if result is None and allow_back:
        return BACK_VALUE  # type: ignore[return-value]
    if result == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    return result


def ask_anthropic_effort(allow_back: bool = False) -> str | None:
    """Ask for Anthropic effort level.

    Controls token usage and response thoroughness on Claude 4.5 / 4.6 / 4.7
    models. The API also accepts "max"; we expose low/medium/high as the
    common selection range.
    """
    choices = [
        questionary.Choice("High (recommended)", "high"),
        questionary.Choice("Medium (balanced)", "medium"),
        questionary.Choice("Low (faster, cheaper)", "low"),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    result = questionary.select(
        "Select Effort Level:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()
    if result is None and allow_back:
        return BACK_VALUE  # type: ignore[return-value]
    if result == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    return result


def ask_gemini_thinking_config(allow_back: bool = False) -> str | None:
    """Ask for Gemini thinking configuration.

    Returns thinking_level: "high" or "minimal".
    Client maps to appropriate API param based on model series.
    """
    choices = [
        questionary.Choice("Enable Thinking (recommended)", "high"),
        questionary.Choice("Minimal/Disable Thinking", "minimal"),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    result = questionary.select(
        "Select Thinking Mode:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:green noinherit"),
            ("highlighted", "fg:green noinherit"),
            ("pointer", "fg:green noinherit"),
        ]),
    ).ask()
    if result is None and allow_back:
        return BACK_VALUE  # type: ignore[return-value]
    if result == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    return result


def ask_glm_region(allow_back: bool = False) -> tuple[str, str]:
    """Ask which GLM platform (Z.AI international vs BigModel China) to use.

    Zhipu serves the same GLM models under two brands with separate
    accounts; keys aren't interchangeable. Returns (provider_key, backend_url).
    """
    choices = [
        questionary.Choice(
            "Z.AI — api.z.ai (international, uses ZHIPU_API_KEY)",
            value=("glm", "https://api.z.ai/api/paas/v4/"),
        ),
        questionary.Choice(
            "BigModel — open.bigmodel.cn (China, uses ZHIPU_CN_API_KEY)",
            value=("glm-cn", "https://open.bigmodel.cn/api/paas/v4/"),
        ),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    result = questionary.select(
        "Select GLM platform:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()
    if result is None and allow_back:
        return BACK_VALUE, None  # type: ignore[return-value]
    if result == BACK_VALUE:
        return BACK_VALUE, None  # type: ignore[return-value]
    return result


def ask_qwen_region(allow_back: bool = False) -> tuple[str, str]:
    """Ask which Qwen region (international vs China) to use.

    Alibaba DashScope exposes two endpoints with separate accounts —
    a key from one region does NOT authenticate against the other
    (fixes #758). Returns (provider_key, backend_url).
    """
    choices = [
        questionary.Choice(
            "International — dashscope-intl.aliyuncs.com (uses DASHSCOPE_API_KEY)",
            value=("qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ),
        questionary.Choice(
            "China — dashscope.aliyuncs.com (uses DASHSCOPE_CN_API_KEY)",
            value=("qwen-cn", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    result = questionary.select(
        "Select Qwen region:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()
    if result is None and allow_back:
        return BACK_VALUE, None  # type: ignore[return-value]
    if result == BACK_VALUE:
        return BACK_VALUE, None  # type: ignore[return-value]
    return result


def ask_minimax_region(allow_back: bool = False) -> tuple[str, str]:
    """Ask which MiniMax region (global vs China) to use.

    MiniMax exposes two endpoints with separate accounts — a key from
    one region does NOT authenticate against the other. Returns
    (provider_key, backend_url).
    """
    choices = [
        questionary.Choice(
            "Global — api.minimax.io (uses MINIMAX_API_KEY)",
            value=("minimax", "https://api.minimax.io/v1"),
        ),
        questionary.Choice(
            "China — api.minimaxi.com (uses MINIMAX_CN_API_KEY)",
            value=("minimax-cn", "https://api.minimaxi.com/v1"),
        ),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    result = questionary.select(
        "Select MiniMax region:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()
    if result is None and allow_back:
        return BACK_VALUE, None  # type: ignore[return-value]
    if result == BACK_VALUE:
        return BACK_VALUE, None  # type: ignore[return-value]
    return result


def confirm_ollama_endpoint(url: str) -> None:
    r"""Show the resolved Ollama endpoint after provider selection.

    Surfaces three things the user benefits from seeing before model
    selection: which URL we'll actually hit, where it came from
    (`OLLAMA_BASE_URL` vs default), and a soft warning if the URL is
    missing the scheme/port that ollama-serve expects. The warning is
    advisory only — we don't reject malformed input, since the user may
    be doing something deliberately unusual (e.g. a reverse-proxy path).
    """
    from_env = os.environ.get("OLLAMA_BASE_URL")
    origin = " (from OLLAMA_BASE_URL)" if from_env and from_env == url else ""
    console.print(f"[green]✓ Using Ollama at {url}{origin}[/green]")

    if not url.startswith(("http://", "https://")):
        console.print(
            f"[yellow]Note: {url!r} is missing a scheme. "
            f"Ollama-serve typically expects a URL like "
            f"http://<host>:11434/v1.[/yellow]"
        )
    elif ":11434" not in url and "://localhost" not in url and "://127.0.0.1" not in url:
        # Soft hint when the port differs from the ollama-serve default
        # and the host isn't local (where users sometimes proxy on :80).
        console.print(
            f"[yellow]Note: {url!r} doesn't include port 11434. "
            f"Make sure your remote ollama-serve listens on the port "
            f"shown above.[/yellow]"
        )


def ensure_api_key(provider: str) -> str | None:
    """Make sure the API key for `provider` is available in the environment.

    If the env var is already set, returns its value untouched. Otherwise
    interactively prompts the user, persists the value to the project's
    .env file via python-dotenv's set_key (creating .env if needed), and
    exports it into os.environ so the current process picks it up.

    Returns None for providers that do not require a key (e.g. ollama)
    and for providers not found in the canonical mapping.
    """
    env_var = get_api_key_env(provider)
    if env_var is None:
        return None  # ollama / unknown — no key check possible

    existing = os.environ.get(env_var)
    if existing:
        return existing

    console.print(
        f"\n[yellow]{env_var} is not set in your environment.[/yellow]"
    )
    key = questionary.password(
        f"Paste your {env_var} (will be saved to .env):",
        style=questionary.Style([
            ("text", "fg:cyan"),
            ("highlighted", "noinherit"),
        ]),
    ).ask()
    if not key:
        console.print(
            f"[red]Skipped. API calls will fail until {env_var} is set.[/red]"
        )
        return None

    env_path = find_dotenv(usecwd=True) or str(Path.cwd() / ".env")
    Path(env_path).touch(exist_ok=True)
    set_key(env_path, env_var, key)
    os.environ[env_var] = key
    console.print(f"[green]Saved {env_var} to {env_path}[/green]")
    return key


def ask_output_language(allow_back: bool = False) -> str:
    """Ask for report output language."""
    choices = [
        # Chinese first for A-share users who just want to hit Enter.
        questionary.Choice("Chinese (中文)", "Chinese"),
        questionary.Choice("English (default)", "English"),
        questionary.Choice("Japanese (日本語)", "Japanese"),
        questionary.Choice("Korean (한국어)", "Korean"),
        questionary.Choice("Hindi (हिन्दी)", "Hindi"),
        questionary.Choice("Spanish (Español)", "Spanish"),
        questionary.Choice("Portuguese (Português)", "Portuguese"),
        questionary.Choice("French (Français)", "French"),
        questionary.Choice("German (Deutsch)", "German"),
        questionary.Choice("Arabic (العربية)", "Arabic"),
        questionary.Choice("Russian (Русский)", "Russian"),
        questionary.Choice("Custom language", "custom"),
    ]
    if allow_back:
        choices.append(questionary.Choice(BACK_LABEL, value=BACK_VALUE))
    choice = questionary.select(
        "Select Output Language:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:yellow noinherit"),
            ("highlighted", "fg:yellow noinherit"),
            ("pointer", "fg:yellow noinherit"),
        ]),
    ).ask()

    # Output language has a sensible default, so a cancel falls back to English
    # rather than exiting the run (unlike the required model/provider prompts).
    if choice is None:
        if allow_back:
            return BACK_VALUE  # type: ignore[return-value]
        return "English"
    if choice == BACK_VALUE:
        return BACK_VALUE  # type: ignore[return-value]
    if choice == "custom":
        _raw = questionary.text(
            "Enter language name (e.g. Turkish, Vietnamese, Thai, Indonesian):",
            validate=lambda x: len(x.strip()) > 0 or "Please enter a language name.",
        ).ask()
        if _raw is None:
            if allow_back:
                return BACK_VALUE  # type: ignore[return-value]
            return "English"
        return _raw.strip() or "English"

    return choice
