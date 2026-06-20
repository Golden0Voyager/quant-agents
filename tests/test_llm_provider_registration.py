"""Provider registration invariants for the LLM client layer.

These tests pin the configuration knobs that the CLI / factory / prompt
construction all rely on. They are deliberately white-box (importing the
private ``_PROVIDER_BASE_URL`` dict) so any silent rename of the
provider string, the base URL, the env-var name, or the catalog model
ID trips a single, easy-to-localize test failure.

If you add a new provider, mirror the pattern here: factory entry,
base URL, env var, model catalog, client construction.

ModelScope and NVIDIA NIM were added in the v0.3 provider expansion;
this module covers them alongside Agnes AI.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE, create_llm_client
from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.llm_clients.openai_client import (
    _PROVIDER_BASE_URL,
    OpenAIClient,
    _resolve_provider_base_url,
)

# ---- Factory membership ----------------------------------------------------


def test_factory_recognises_agnes():
    """``agnes`` must be in the OpenAI-compatible family so the factory
    routes it to ``OpenAIClient`` (not Anthropic/Google/Azure)."""
    assert "agnes" in _OPENAI_COMPATIBLE


# ---- Base URL pinning ------------------------------------------------------


EXPECTED_AGNES_URL = "https://apihub.agnes-ai.com/v1"


def test_agnes_base_url_in_provider_dict():
    assert _PROVIDER_BASE_URL["agnes"] == EXPECTED_AGNES_URL


def test_agnes_base_url_resolver_returns_dict_value():
    assert _resolve_provider_base_url("agnes") == EXPECTED_AGNES_URL


def test_agnes_base_url_unknown_provider_returns_none():
    assert _resolve_provider_base_url("not-a-real-provider") is None


# ---- API-key env var mapping ----------------------------------------------


def test_agnes_api_key_env_name():
    """The provider's API key must live in AGNES_API_KEY (matches
    .env.example, README instructions, and the platform's dashboard)."""
    assert get_api_key_env("agnes") == "AGNES_API_KEY"


def test_agnes_api_key_env_case_insensitive():
    assert get_api_key_env("Agnes") == "AGNES_API_KEY"
    assert get_api_key_env("AGNES") == "AGNES_API_KEY"


# ---- Model catalog --------------------------------------------------------


def test_agnes_quick_model_options_listed():
    options = get_model_options("agnes", "quick")
    values = [value for _, value in options]
    assert "agnes-2.0-flash" in values
    assert "custom" in values


def test_agnes_deep_model_options_listed():
    options = get_model_options("agnes", "deep")
    values = [value for _, value in options]
    assert "agnes-2.0-flash" in values


def test_agnes_catalog_label_mentions_context_window():
    """The 256K context window and tool-calling support are the two
    features that make ``agnes-2.0-flash`` usable for TradingAgents. The
    label should advertise them so users know what they're picking."""
    options = get_model_options("agnes", "quick")
    labels = [label for label, _ in options]
    matched = [label for label in labels if "256K" in label]
    assert matched, f"Expected a 256K ctx label in {labels!r}"


# ---- Factory end-to-end construction --------------------------------------


def test_create_agnes_client_returns_openai_client():
    """``create_llm_client(provider="agnes", ...)`` must produce an
    ``OpenAIClient`` (i.e. routed through the OpenAI-compat branch) and
    resolve the provider default base URL lazily when get_llm() runs."""
    # Agnes routes through the OpenAI-compat factory branch and then
    # through the NormalizedChatOpenAI class for non-reasoning models.
    # We mock that class because it's where base_url / api_key actually
    # land in the LangChain constructor call.
    from tradingagents.llm_clients import openai_client as oc_mod
    with patch.dict(os.environ, {"AGNES_API_KEY": "sk-agnes-test"}):
        client = create_llm_client(provider="agnes", model="agnes-2.0-flash")
    assert isinstance(client, OpenAIClient)
    assert client.provider == "agnes"
    with patch.object(oc_mod, "NormalizedChatOpenAI", autospec=True) as mock_cls:
        mock_cls.return_value = object()
        client.get_llm()
    assert mock_cls.call_args.kwargs["base_url"] == EXPECTED_AGNES_URL
    # The api_key forwarded to LangChain must come from the AGNES_API_KEY
    # env var (we don't pin the literal value to avoid leaking real keys
    # in test logs).
    assert "AGNES_API_KEY" in get_api_key_env("agnes")
    assert mock_cls.call_args.kwargs["api_key"] == os.environ["AGNES_API_KEY"]


def test_create_agnes_client_explicit_base_url_wins():
    """An explicit base_url on the factory call beats the provider
    default — users need this when routing through a corporate proxy
    or a local agnes-ai mock server."""
    from tradingagents.llm_clients import openai_client as oc_mod
    proxy = "http://corp-proxy.internal:8080/v1"
    with patch.dict(os.environ, {"AGNES_API_KEY": "sk-agnes-test"}):
        client = create_llm_client(
            provider="agnes", model="agnes-2.0-flash", base_url=proxy,
        )
    with patch.object(oc_mod, "NormalizedChatOpenAI", autospec=True) as mock_cls:
        mock_cls.return_value = object()
        client.get_llm()
    assert mock_cls.call_args.kwargs["base_url"] == proxy


def test_get_llm_raises_when_agnes_api_key_missing():
    """If AGNES_API_KEY is unset, ``get_llm()`` must fail loudly with a
    message that names the missing variable — never silently fall back
    to an empty key or route to a wrong provider."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("AGNES_API_KEY", None)
        client = create_llm_client(provider="agnes", model="agnes-2.0-flash")
        with pytest.raises(ValueError, match="AGNES_API_KEY"):
            client.get_llm()


# ============================================================================
# ModelScope
# ============================================================================


EXPECTED_MODELSCOPE_URL = "https://api-inference.modelscope.cn/v1"


def test_factory_recognises_modelscope():
    assert "modelscope" in _OPENAI_COMPATIBLE


def test_modelscope_base_url_in_provider_dict():
    assert _PROVIDER_BASE_URL["modelscope"] == EXPECTED_MODELSCOPE_URL


def test_modelscope_api_key_env_name():
    assert get_api_key_env("modelscope") == "MODELSCOPE_API_KEY"


def test_modelscope_quick_model_options_listed():
    options = get_model_options("modelscope", "quick")
    values = [value for _, value in options]
    assert "deepseek-ai/DeepSeek-V4-Flash" in values
    assert "custom" in values


def test_modelscope_deep_model_options_listed():
    options = get_model_options("modelscope", "deep")
    values = [value for _, value in options]
    assert "deepseek-ai/DeepSeek-V4-Flash" in values


def test_create_modelscope_client_returns_openai_client():
    from tradingagents.llm_clients import openai_client as oc_mod
    with patch.dict(os.environ, {"MODELSCOPE_API_KEY": "sk-modelscope-test"}):
        client = create_llm_client(
            provider="modelscope", model="deepseek-ai/DeepSeek-V4-Flash"
        )
        assert isinstance(client, OpenAIClient)
        assert client.provider == "modelscope"
        with patch.object(oc_mod, "NormalizedChatOpenAI", autospec=True) as mock_cls:
            mock_cls.return_value = object()
            client.get_llm()
    assert mock_cls.call_args.kwargs["base_url"] == EXPECTED_MODELSCOPE_URL
    assert mock_cls.call_args.kwargs["api_key"] == "sk-modelscope-test"


# ============================================================================
# NVIDIA NIM
# ============================================================================


EXPECTED_NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


def test_factory_recognises_nvidia():
    assert "nvidia" in _OPENAI_COMPATIBLE


def test_nvidia_base_url_in_provider_dict():
    assert _PROVIDER_BASE_URL["nvidia"] == EXPECTED_NVIDIA_URL


def test_nvidia_api_key_env_name():
    assert get_api_key_env("nvidia") == "NVIDIA_API_KEY"


def test_nvidia_quick_model_options_listed():
    options = get_model_options("nvidia", "quick")
    values = [value for _, value in options]
    assert "deepseek-ai/deepseek-v4-pro" in values
    assert "custom" in values


def test_nvidia_deep_model_options_listed():
    options = get_model_options("nvidia", "deep")
    values = [value for _, value in options]
    assert "deepseek-ai/deepseek-v4-pro" in values


def test_create_nvidia_client_returns_openai_client():
    from tradingagents.llm_clients import openai_client as oc_mod
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "sk-nvidia-test"}):
        client = create_llm_client(
            provider="nvidia", model="deepseek-ai/deepseek-v4-pro"
        )
        assert isinstance(client, OpenAIClient)
        assert client.provider == "nvidia"
        with patch.object(oc_mod, "NormalizedChatOpenAI", autospec=True) as mock_cls:
            mock_cls.return_value = object()
            client.get_llm()
    assert mock_cls.call_args.kwargs["base_url"] == EXPECTED_NVIDIA_URL
    assert mock_cls.call_args.kwargs["api_key"] == "sk-nvidia-test"
