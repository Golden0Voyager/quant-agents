"""Per-model token pricing for cost estimation.

The CLI dashboard shows a live cost estimate computed from token usage
returned by each LLM call. This module is the single source of truth.

Lookup order (first match wins):
1. **LiteLLM community catalog** (auto-fetched, 24h cache from GitHub).
   Covers 2900+ models with excellent coverage for English-first providers
   (OpenAI, Anthropic, Google, xAI).
2. **User ``pricing.yaml``** (``<project-root>/pricing.yaml``). User-editable
   file that covers Chinese providers LiteLLM doesn't track (DeepSeek V4,
   Qwen 3.7, GLM 5, MiniMax, Kimi, Agnes, SenseNova, etc.) and allows
   overriding any rate without touching Python source.

When a model is missing from both, ``get_price_for_model`` falls through
to the env-var defaults (``INPUT_TOKEN_PRICE_PER_1M`` /
``OUTPUT_TOKEN_PRICE_PER_1M``). If those are also unset, the callback
silently disables cost for that call.

Prices in ``pricing.yaml`` are specified in the provider's **native currency**
(e.g. CNY for Chinese providers, USD for US providers) per million tokens
(input, output). The code automatically converts them to USD/M using the live
``get_usd_to_cny_rate()``. This ensures accuracy against original sources
without manual conversion work.
"""

from __future__ import annotations

from pathlib import Path

Price = tuple[float, float]

# ---- Default pricing -------------------------------------------------------
# Used only for first-time YAML generation (when pricing.yaml doesn't exist).
# After that, all edits go through pricing.yaml — this dict is never consulted
# at lookup time.

_DEFAULT_PRICING: dict[str, dict[str, Price]] = {
    # Agnes AI: free tier (rate-limited but unlimited in duration).
    # https://agnes-ai.com — see also docs/api/Agnes_AI_API_Report.md
    "agnes": {
        "agnes-2.0-flash": (0.00, 0.00),
    },
    # DeepSeek: official public pricing (cache miss rates) — verified
    # 2026-07 against https://api-docs.deepseek.com/quick_start/pricing.
    "deepseek": {
        "deepseek-v4-flash": (0.14, 0.28),
        "deepseek-v4-pro":   (0.435, 0.87),
    },
    # Kimi (Moonshot AI): verified 2026-07 against
    # https://platform.kimi.ai/docs/pricing/chat-k26 and
    # https://platform.kimi.ai/docs/pricing/chat-k27-code.
    # Uses cache-miss (standard) rates, same convention as all providers.
    "kimi": {
        "kimi-k2.6":   (0.95, 4.00),
        "kimi-k2.7-code": (0.95, 4.00),
        "kimi-k3":     (3.0, 15.0),
    },
    # ModelScope inference — used in fallback chains. Prices follow each
    # model's official provider rate since ModelScope passes through at
    # approximately the original model cost.
    "modelscope": {
        "deepseek-ai/DeepSeek-V4-Flash":   (0.14,  0.28),
        "deepseek-ai/DeepSeek-V4-Pro":     (0.435, 0.87),
        "stepfun-ai/Step-3.7-Flash":       (0.20,  1.15),
        "MiniMax/MiniMax-M3":              (0.30,  1.20),
        "Qwen/Qwen3.5-397B-A17B":         (0.60,  3.60),
        "ZhipuAI/GLM-5.2":                (1.20,  4.10),
    },
    # OpenRouter: free-tier models.
    "openrouter": {
        "nvidia/nemotron-3-ultra-550b-a55b:free": (0.0, 0.0),
        "nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
    },
    # SenseNova (Token Plan endpoint)
    "sensenova": {
        # Post-beta rate verified 2026-07: ¥1.5/M input, ¥4.5/M output.
        # Stored as native CNY values; converted to USD/M by _load_pricing_yaml().
        "sensenova-6.7-flash-lite": {"input": 1.5, "output": 4.5, "currency": "CNY"},
        "deepseek-v4-flash":        (0.14, 0.28),
    },
}


def get_price(provider: str, model: str) -> Price | None:
    """Look up ``(input, output)`` USD/M token rates for a provider/model.

    Checks the user ``pricing.yaml``. Returns None when the provider or
    model isn't listed — the caller falls back to env-var defaults or
    skips cost estimation for that call.
    """
    yaml_pricing = _load_pricing_yaml()
    bucket = yaml_pricing.get(provider.lower())
    if bucket is not None and model in bucket:
        return bucket[model]
    return None


def get_price_for_model(model: str) -> Price | None:
    """Provider-agnostic lookup by model name.

    The LangChain callback handler can only see the model name (it
    doesn't know the ``TradingAgents`` provider string), so the dashboard
    uses this lookup.

    Lookup order (first match wins):
    1. LiteLLM community catalog (auto-fetched, 24h cache)
    2. User ``pricing.yaml`` (``<project-root>/pricing.yaml``)
    """
    overlay = _load_litellm_overlay()
    if model in overlay:
        return overlay[model]
    yaml_pricing = _load_pricing_yaml()
    for provider_models in yaml_pricing.values():
        if model in provider_models:
            return provider_models[model]
    return None


# ---- User pricing.yaml -----------------------------------------------------

_PROJECT_ROOT: Path | None = None


def _pricing_yaml_path() -> Path:
    """Return absolute path to the project-root ``pricing.yaml``.

    Discovers the project root by walking up from this module's location
    (``tradingagents/llm_clients/pricing.py`` → project root), then
    looks for ``pricing.yaml`` there.
    """
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    return _PROJECT_ROOT / "pricing.yaml"


_PRICING_YAML: dict[str, dict[str, Price]] | None = None


def _load_pricing_yaml() -> dict[str, dict[str, Price]]:
    """Load the project-root ``pricing.yaml``, writing defaults on first use.

    Returns a ``{provider: {model: (input_usd, output_usd)}}`` dict where
    rates are in USD per million tokens, having been converted from the
    native currency specified in the yaml.
    """
    global _PRICING_YAML
    if _PRICING_YAML is not None:
        return _PRICING_YAML

    yaml_path = _pricing_yaml_path()
    if not yaml_path.exists():
        _write_default_pricing_yaml(yaml_path)

    try:
        import yaml as _yaml
        raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        _PRICING_YAML = {}
        return _PRICING_YAML

    if not isinstance(raw, dict):
        _PRICING_YAML = {}
        return _PRICING_YAML

    result: dict[str, dict[str, Price]] = {}
    for provider, models in raw.items():
        if not isinstance(models, dict):
            continue
        prices: dict[str, Price] = {}
        for model_name, entry in models.items():
            if isinstance(entry, dict):
                # New format: {input: 1.5, output: 4.5, currency: "CNY"}
                try:
                    in_rate = float(entry["input"])
                    out_rate = float(entry["output"])
                    currency = entry.get("currency", "USD").upper()

                    if currency == "CNY":
                        cny_rate = get_usd_to_cny_rate()
                        in_usd = in_rate / cny_rate
                        out_usd = out_rate / cny_rate
                    elif currency == "USD":
                        in_usd, out_usd = in_rate, out_rate
                    else:
                        # Unknown currency — treat as USD and warn.
                        import warnings as _w
                        _w.warn(
                            f"Unknown currency {currency!r} for model {model_name!r}, "
                            f"treating as USD",
                        )
                        in_usd, out_usd = in_rate, out_rate

                    prices[model_name] = (in_usd, out_usd)
                except (TypeError, ValueError, KeyError):
                    pass
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                # Legacy format: [input_usd, output_usd]
                try:
                    in_rate, out_rate = float(entry[0]), float(entry[1])
                    prices[model_name] = (in_rate, out_rate)
                except (TypeError, ValueError):
                    pass
        if prices:
            result[provider] = prices

    _PRICING_YAML = result
    return result


def _write_default_pricing_yaml(path: Path) -> None:
    """Write the default ``pricing.yaml`` with all known model prices."""
    lines = [
        "# Pricing configuration — USD per million tokens (input, output).",
        "#",
        "# Edit this file to add or override model prices without touching",
        "# Python source. This file is the primary local pricing source",
        "# (the LiteLLM community catalog is checked first, then this file).",
        "#",
        "# Format: `model_name: [input_price_per_1M, output_price_per_1M]`",
        "# All prices in USD. CNY equivalents are shown in trailing comments.",
        "",
    ]

    for provider in sorted(_DEFAULT_PRICING):
        models = _DEFAULT_PRICING[provider]
        lines.append(f"{provider}:")
        for model_name in sorted(models):
            entry = models[model_name]
            if isinstance(entry, dict):
                # Dict-format entries (native currency) — write as-is
                in_rate = entry["input"]
                out_rate = entry["output"]
                currency = entry.get("currency", "USD").upper()
                lines.append(f"  {model_name}:")
                lines.append(f"    input: {in_rate}")
                lines.append(f"    output: {out_rate}")
                lines.append(f"    currency: {currency}")
            else:
                # Legacy tuple/list — USD format
                in_rate, out_rate = entry
                lines.append(f"  {model_name}: [{in_rate}, {out_rate}]")
        lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass  # read-only filesystem — user can create manually


# ---- USD/CNY exchange rate ------------------------------------------------

_USD_CNY_CACHE: tuple[float, float] | None = None  # (rate, timestamp)


def get_usd_to_cny_rate() -> float:
    """Fetch live USD/CNY exchange rate, cached for 1 hour.

    Uses the free open.er-api.com public endpoint (no API key required).
    Falls back to 7.25 on any network or parse failure.
    """
    import time

    global _USD_CNY_CACHE
    now = time.time()
    if _USD_CNY_CACHE is not None and now - _USD_CNY_CACHE[1] < 3600:
        return _USD_CNY_CACHE[0]

    try:
        import httpx
        resp = httpx.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = float(data["rates"]["CNY"])
        if 1.0 < rate < 20.0:
            _USD_CNY_CACHE = (rate, now)
            return rate
    except Exception:
        pass

    _USD_CNY_CACHE = (7.25, now)
    return 7.25


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
    local ``pricing.yaml``. The dashboard never errors on a pricing
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

    # 3) Integrity check — LiteLLM's own rules.
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
