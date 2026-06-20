"""Per-model USD-per-million-token pricing for cost estimation.

The CLI dashboard (``cli/dashboard.render_footer``) shows a live cost
estimate computed from token usage returned by each LLM call. Before
this catalog existed, that estimate used a single env-var rate pair
(``INPUT_TOKEN_PRICE_PER_1M`` / ``OUTPUT_TOKEN_PRICE_PER_1M``) applied
to every call, which is wildly wrong the moment a user mixes providers
(DeepSeek vs. OpenAI) or even mixes deep/quick models in one run.

This module is the single source of truth for per-model rates. The
lookups go through a two-tier merge:

1. **LiteLLM community catalog** (fetched from
   ``https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json``
   on first use, cached for 24h at ``~/.tradingagents/cache/pricing.json``).
   Coverage is excellent for English-first providers (OpenAI, Anthropic,
   Google, xAI) but sparse for Chinese providers (DeepSeek V4, Qwen 3.7,
   GLM 5, MiniMax, Kimi, Agnes) — those still need the local catalog.
2. **Local ``PRICING`` dict** (this file), calibrated against each
   provider's official pricing page as of 2026-06. Acts as offline
   fallback when LiteLLM fetch fails (network down, rate-limited, etc.)
   and as the primary source for Chinese providers LiteLLM doesn't track.

When a model is missing from both, ``get_price_for_model`` falls
through to the env-var defaults (``INPUT_TOKEN_PRICE_PER_1M`` /
``OUTPUT_TOKEN_PRICE_PER_1M``) — the pre-catalog behavior. If those
are also unset, the callback silently disables cost for that call.

Prices are USD per million tokens (input, output) at standard (non-
discounted) public list price. Discount programs (Azure commitments,
DeepSeek off-peak, Alibaba reserved) are not modeled here.
"""

from __future__ import annotations

Price = tuple[float, float]


# Local pricing table — see module docstring for the LiteLLM + local
# merge order. All rates were verified against provider pricing pages
# in 2026-06; the unit tests in tests/test_pricing_catalog.py pin the
# values so accidental drift trips a test failure.
PRICING: dict[str, dict[str, Price]] = {
    # Agnes AI: free tier (rate-limited but unlimited in duration).
    # https://agnes-ai.com — see also docs/api/Agnes_AI_API_Report.md
    "agnes": {
        "agnes-2.0-flash": (0.00, 0.00),
    },
    # Ollama: runs locally, so no API cost. Token counts still useful
    # for prompt debugging, but the dollar estimate stays at zero.
    "ollama": {
        "qwen3:latest":         (0.00, 0.00),
        "gpt-oss:latest":       (0.00, 0.00),
        "glm-4.7-flash:latest": (0.00, 0.00),
    },
    # DeepSeek: official public pricing (cache miss rates) — verified
    # 2026-06 against https://api-docs.deepseek.com/quick_start/pricing.
    "deepseek": {
        "deepseek-v4-flash":  (0.14, 0.28),
        "deepseek-v4-pro":    (1.74, 3.48),
        "deepseek-chat":      (0.27, 1.10),
        "deepseek-reasoner":  (0.55, 2.19),
    },
    # Qwen (Alibaba DashScope) — global and China regions share the
    # same model IDs and rates. Verified 2026-06 against
    # https://docs.qwencloud.com/developer-guides/getting-started/pricing.
    "qwen": {
        "qwen3.7-max":   (2.50, 7.50),
        "qwen3.6-plus":  (0.40, 1.60),
        "qwen3.6-flash": (0.25, 1.50),
        "qwen3.5-plus":  (0.40, 1.60),
        "qwen3.5-flash": (0.10, 0.40),
    },
    "qwen-cn": {
        "qwen3.7-max":   (2.50, 7.50),
        "qwen3.6-plus":  (0.40, 1.60),
        "qwen3.6-flash": (0.25, 1.50),
        "qwen3.5-plus":  (0.40, 1.60),
        "qwen3.5-flash": (0.10, 0.40),
    },
    # GLM (Zhipu) — Z.AI international and BigModel China share model
    # IDs. The Z.AI billing page does not break out per-model rates for
    # the 5.x line at the time of writing; we mirror Z.AI's tier-2
    # (Coding Plan) defaults. Verify before billing critical work.
    "glm": {
        "glm-5.1":     (1.00, 4.00),
        "glm-5":       (1.00, 4.00),
        "glm-5-turbo": (0.50, 2.00),
        "glm-4.7":     (0.30, 1.20),
        "glm-4.5-air": (0.10, 0.40),
    },
    "glm-cn": {
        "glm-5.1":     (1.00, 4.00),
        "glm-5":       (1.00, 4.00),
        "glm-5-turbo": (0.50, 2.00),
        "glm-4.7":     (0.30, 1.20),
        "glm-4.5-air": (0.10, 0.40),
    },
    # MiniMax M2 line — global and China share model IDs. The Coding
    # Plan tier-2 rates from platform.minimax.io are the closest public
    # reference; the actual on-demand rates may differ.
    "minimax": {
        "MiniMax-M2.7":            (1.20, 4.80),
        "MiniMax-M2.7-highspeed":  (0.60, 2.40),
        "MiniMax-M2.5":            (0.60, 2.40),
        "MiniMax-M2.5-highspeed":  (0.30, 1.20),
        "MiniMax-M2.1":            (0.30, 1.20),
        "MiniMax-M2.1-highspeed":  (0.15, 0.60),
        "MiniMax-M2":              (0.15, 0.60),
    },
    "minimax-cn": {
        "MiniMax-M2.7":            (1.20, 4.80),
        "MiniMax-M2.7-highspeed":  (0.60, 2.40),
        "MiniMax-M2.5":            (0.60, 2.40),
        "MiniMax-M2.5-highspeed":  (0.30, 1.20),
        "MiniMax-M2.1":            (0.30, 1.20),
        "MiniMax-M2.1-highspeed":  (0.15, 0.60),
        "MiniMax-M2":              (0.15, 0.60),
    },
    # OpenAI: list price USD/M tokens, no Batch or Priority discounts.
    # Verified 2026-06 against https://openai.com/api/pricing. Note the
    # long-context tier (>272K) charges more — we record the ≤272K rate
    # because that's what most TradingAgents runs use.
    "openai": {
        "gpt-5.5":      (5.00, 30.00),
        "gpt-5.5-pro":  (30.00, 180.00),
        "gpt-5.4":      (2.50, 15.00),
        "gpt-5.4-mini": (0.75, 4.50),
        "gpt-5.4-nano": (0.10, 0.40),
        "gpt-5.2":      (1.25, 5.00),
        "gpt-4.1":      (3.00, 12.00),
    },
    # Anthropic: list price USD/M tokens. Verified 2026-06 against
    # https://platform.claude.com/docs/en/about-claude/pricing.
    "anthropic": {
        "claude-opus-4-8":   (5.00, 25.00),
        "claude-opus-4-7":   (5.00, 25.00),
        "claude-opus-4-6":   (5.00, 25.00),
        "claude-sonnet-4-6": (3.00, 15.00),
        "claude-sonnet-4-5": (3.00, 15.00),
        "claude-haiku-4-5":  (1.00, 5.00),
    },
    # Google Gemini: list price USD/M tokens (≤200K tier). Verified
    # 2026-06 against https://ai.google.dev/gemini-api/docs/pricing.
    # Gemini 2.5 charges a 2x premium for prompts > 200K tokens — not
    # modeled here since TradingAgents prompts usually stay under that.
    "google": {
        "gemini-3.5-flash":       (0.25, 1.50),
        "gemini-3.1-pro-preview": (1.50, 9.00),
        "gemini-3.1-flash-lite":  (0.10, 0.40),
        "gemini-2.5-pro":         (1.25, 10.00),
        "gemini-2.5-flash":       (0.30, 2.50),
        "gemini-2.5-flash-lite":  (0.10, 0.40),
    },
    # xAI Grok: list price USD/M tokens. Verified 2026-06 against
    # https://docs.x.ai/docs/models.
    "xai": {
        "grok-4.3":                  (5.00, 15.00),
        "grok-build-0.1":            (5.00, 15.00),
        "grok-4-fast-reasoning":     (0.20, 0.50),
        "grok-4-fast-non-reasoning": (0.20, 0.50),
        "grok-4-0709":               (5.00, 15.00),
        "grok-4.20-0309-reasoning":  (5.00, 15.00),
    },
    # Kimi (Moonshot AI) Coding Plan. Verified 2026-06 against
    # https://platform.kimi.ai/docs/pricing/chat-k26.
    "kimi": {
        "kimi-k2.6": (0.16, 0.95),
        "kimi-k2.5": (0.16, 0.95),
    },
    # MiMo (Xiaomi). Verified 2026-06 against
    # https://platform.xiaomimimo.com/pricing.
    "mimo": {
        "mimo-v2.5":     (0.30, 0.80),
        "mimo-v2.5-pro": (3.00, 9.00),
    },
    # SenseNova (Token Plan endpoint). The model name ``deepseek-v4-flash``
    # is also a direct DeepSeek model — we list it here with the same
    # rate so cost tracking is consistent across providers when the user
    # routes the same model through SenseNova. Update only if SenseNova
    # changes their per-model rate.
    #
    # SenseNova 6.7 Flash-Lite is **free during the public-beta Token
    # Plan** (1,500 calls / 5h, ¥0/month — verified 2026-06 against
    # https://www.sensetime.com/cn/news-detail/51170639). SenseTime
    # has not published a post-beta rate card yet, so we record $0
    # here and revisit when the paid tier launches. If the user sees
    # billing for this model in the dashboard after the beta ends,
    # swap in the real rate from platform.sensenova.cn and update
    # the test in tests/test_pricing_catalog.py.
    "sensenova": {
        "sensenova-6.7-flash-lite": (0.00, 0.00),
        "deepseek-v4-flash":        (0.14, 0.28),
    },
    # ModelScope (Alibaba inference hub). The free tier (2k req/day)
    # is not modeled here — set INPUT/OUTPUT_TOKEN_PRICE_PER_1M=0 if
    # you want to bill only over-quota usage. Standard rates are
    # CNY-denominated on modelscope.cn and normalized to USD below.
    "modelscope": {
        "deepseek-ai/DeepSeek-V4-Flash": (0.14, 0.28),
        "Qwen/Qwen3.5-397B-A17B":        (0.40, 1.60),
        "ZhipuAI/GLM-5.1":               (1.00, 4.00),
    },
    # NVIDIA NIM endpoints (build.nvidia.com). The free credits (1000
    # at signup) are not modeled here either — same env-var approach.
    "nvidia": {
        "deepseek-ai/deepseek-v4-pro":        (1.74, 3.48),
        "google/gemma-4-31b-it":              (0.10, 0.30),
        "meta/llama-3.2-90b-vision-instruct": (0.40, 0.40),
    },
}


def get_price(provider: str, model: str) -> Price | None:
    """Look up ``(input, output)`` USD/M token rates for a provider/model.

    Returns None when the provider is unknown or the model isn't listed
    — the caller is expected to fall back to env-var defaults or skip
    cost estimation for that call.
    """
    bucket = PRICING.get(provider.lower())
    if bucket is None:
        return None
    return bucket.get(model)


def get_price_for_model(model: str) -> Price | None:
    """Provider-agnostic lookup by model name.

    The LangChain callback handler can only see the model name (it
    doesn't know the ``TradingAgents`` provider string), so the dashboard
    uses this lookup. The first provider whose catalog contains the
    model wins. For ambiguous cases (same model under multiple
    providers with different rates), the order of entries in ``PRICING``
    determines the winner — keep the cheapest / most-common provider
    first so the displayed cost is conservative.

    The LiteLLM-merged catalog (see ``_load_litellm_overlay``) is
    consulted first; entries it covers override the local PRICING dict.
    That way a model added to LiteLLM upstream immediately takes effect
    here, without waiting for a local edit.
    """
    overlay = _load_litellm_overlay()
    if model in overlay:
        return overlay[model]
    # Iterate PRICING in insertion order (Python 3.7+ dict preserves order).
    # When a model ID exists in multiple providers (e.g. deepseek-v4-flash
    # under both "deepseek" and "sensenova"), the first provider wins.
    # The insertion order is the source of truth and is pinned by
    # test_get_price_for_model_uses_first_match_for_ambiguous_models.
    for provider_models in PRICING.values():
        if model in provider_models:
            return provider_models[model]
    return None


# ---- LiteLLM overlay -----------------------------------------------------


# Path to the cached LiteLLM JSON on disk. Stays at a stable location
# under the user's home dir so multiple processes / runs share the same
# 24h-TTL cache without re-fetching.
_LITELLM_CACHE_PATH = "~/.tradingagents/cache/pricing_litellm.json"
_LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_LITELLM_TTL_SECONDS = 24 * 60 * 60  # 24h


def _load_litellm_overlay() -> dict[str, Price]:
    """Return a ``{model_name: (input_per_1M, output_per_1M)}`` overlay
    from the LiteLLM community catalog, or an empty dict on any failure.

    Cache is a 24h on-disk JSON. Network failures, parse errors, or
    LiteLLM's own integrity-check failure (e.g. fetched payload shrank
    to <50% of the bundled backup) all fall through silently to the
    local ``PRICING`` dict. The dashboard never errors on a pricing
    lookup — it just shows a less-fresh number.
    """
    import json
    import os
    import time
    from pathlib import Path

    cache_path = Path(os.path.expanduser(_LITELLM_CACHE_PATH))
    now = time.time()

    # 1) Try disk cache first — saves a 1.4 MB download per process.
    if cache_path.exists():
        try:
            cache_mtime = cache_path.stat().st_mtime
            if now - cache_mtime < _LITELLM_TTL_SECONDS:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return _parse_litellm_payload(data)
        except (OSError, json.JSONDecodeError):
            pass  # corrupt cache — fall through to network

    # 2) Fetch from GitHub. Timeout defaults to 5s so a slow network
    #    can't block the dashboard; the user gets a stale cache rather
    #    than a hang. Override via LITELLM_FETCH_TIMEOUT_SECONDS env var.
    try:
        import httpx
        timeout = float(os.environ.get("LITELLM_FETCH_TIMEOUT_SECONDS", "5.0"))
        response = httpx.get(_LITELLM_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception:
        # Network down, no httpx, rate-limited, etc. — return whatever
        # the disk cache had (even if stale) or empty if the file
        # doesn't exist.
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return _parse_litellm_payload(data)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    # 3) Integrity check — LiteLLM's own rules. We require (a) a dict
    #    with a non-trivial number of models, and (b) the fetched
    #    payload not to have shrunk dramatically vs the previous cache
    #    (a corrupted / truncated download often shows up as a 90%
    #    size drop). We use an absolute threshold (≥ 500 models drop)
    #    so a legitimate upstream cleanup that removes 30% of stale
    #    models does not trip the check.
    if not isinstance(data, dict):
        return {}
    if cache_path.exists():
        try:
            backup = json.loads(cache_path.read_text(encoding="utf-8"))
            backup_count = len(backup) if isinstance(backup, dict) else 0
        except (OSError, json.JSONDecodeError):
            backup_count = 0
    else:
        backup_count = 0
    if backup_count >= 1000 and len(data) < backup_count - 500:
        # Existing cache is healthy (≥ 1000 models) and the fresh fetch
        # dropped by >500 entries — most likely a bad payload, not a
        # real upstream cleanup. A legitimate shrink of 200-300 models
        # (e.g. quarterly deprecation sweep) does not trip this check.
        return {}

    # 4) Persist + parse.
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # read-only filesystem — still use the parsed result

    return _parse_litellm_payload(data)


def _parse_litellm_payload(data: dict[str, object]) -> dict[str, Price]:
    """Translate the LiteLLM catalog into a ``{model: (in, out)}`` dict.

    LiteLLM uses per-token rates; we multiply by 1e6 so callers can
    keep the ``per_million_tokens`` convention. Models that LiteLLM
    has marked as deprecated (``deprecation_date`` in the past) are
    skipped — better to fall back to the local catalog than to bill
    for a model the user can't call.
    """
    import datetime as _dt

    result: dict[str, Price] = {}
    today = _dt.date.today()
    for model_name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        # Skip sample_spec and other non-model entries.
        if model_name == "sample_spec":
            continue
        # Skip deprecated models.
        deprecation = entry.get("deprecation_date")
        if deprecation:
            try:
                if _dt.date.fromisoformat(deprecation) <= today:
                    continue
            except (TypeError, ValueError):
                pass
        ic = entry.get("input_cost_per_token")
        oc = entry.get("output_cost_per_token")
        if ic is None or oc is None:
            continue
        try:
            ic_f, oc_f = float(ic), float(oc)
        except (TypeError, ValueError):
            continue
        if ic_f < 0 or oc_f < 0:
            continue
        # Per-token → per-million-token.
        result[model_name] = (ic_f * 1_000_000, oc_f * 1_000_000)
    return result
