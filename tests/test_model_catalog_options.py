"""Unit tests for ``get_model_options`` in the shared model catalog.

These tests target the uncovered ``get_model_options()`` function (line 252
in model_catalog.py). The function IS exercised in the integration-level
``test_llm_provider_registration.py`` and ``test_ollama_base_url.py``, but
those modules lack the ``@pytest.mark.unit`` marker and are excluded from
``-m unit`` coverage runs. This file ensures the function is covered during
unit-only test runs.
"""

from __future__ import annotations

import pytest

from tradingagents.llm_clients.model_catalog import get_model_options


@pytest.mark.unit
class TestGetModelOptions:
    """Covers the get_model_options function (line 252)."""

    @pytest.mark.parametrize(
        "provider,mode,expected_value",
        [
            ("openai", "quick", "gpt-5.4-mini"),
            ("openai", "deep", "gpt-5.5"),
            ("anthropic", "quick", "claude-sonnet-4-6"),
            ("anthropic", "deep", "claude-opus-4-8"),
            ("google", "quick", "gemini-3.5-flash"),
            ("google", "deep", "gemini-3.1-pro-preview"),
            ("xai", "quick", "grok-4.3"),
            ("xai", "deep", "grok-4.3"),
            ("deepseek", "quick", "deepseek-v4-flash"),
            ("deepseek", "deep", "deepseek-v4-pro"),
        ],
    )
    def test_returns_expected_first_option(
        self, provider: str, mode: str, expected_value: str,
    ) -> None:
        """Pin first-option model IDs for major providers."""
        options = get_model_options(provider, mode)
        first_label, first_value = options[0]
        assert first_value == expected_value, (
            f"{provider}/{mode} first value: {first_value!r} != {expected_value!r}"
        )

    @pytest.mark.parametrize(
        "provider,mode",
        [
            ("qwen", "quick"),
            ("qwen-cn", "deep"),
            ("glm", "quick"),
            ("glm-cn", "deep"),
            ("minimax", "quick"),
            ("minimax-cn", "deep"),
            ("kimi", "quick"),
            ("kimi", "deep"),
            ("mimo", "quick"),
            ("mimo", "deep"),
            ("sensenova", "quick"),
            ("sensenova", "deep"),
            ("agnes", "quick"),
            ("agnes", "deep"),
            ("modelscope", "quick"),
            ("modelscope", "deep"),
            ("nvidia", "quick"),
            ("nvidia", "deep"),
            ("ollama", "quick"),
            ("ollama", "deep"),
        ],
    )
    def test_shared_providers_return_non_empty_lists(
        self, provider: str, mode: str,
    ) -> None:
        """Every shared provider must return at least one option."""
        options = get_model_options(provider, mode)
        assert len(options) >= 1
        assert all(isinstance(label, str) for label, _ in options)
        assert all(isinstance(value, str) for _, value in options)

    def test_provider_case_insensitive(self) -> None:
        """Provider strings are lowercased before lookup."""
        options = get_model_options("OpenAI", "quick")
        assert any("Mini" in label for label, _ in options)

    def test_invalid_provider_raises_key_error(self) -> None:
        """Unknown provider raises KeyError (not a silent fallback)."""
        with pytest.raises(KeyError):
            get_model_options("not-a-real-provider", "quick")

    def test_invalid_mode_raises_key_error(self) -> None:
        """Unknown mode raises KeyError."""
        with pytest.raises(KeyError):
            get_model_options("openai", "turbo")

    def test_custom_sentinel_present_for_customizable_providers(self) -> None:
        """Providers with custom model ID support include the sentinel.

        DeepSeek, open-source hub providers (modelscope, ollama, nvidia),
        and Chinese platforms all expose a 'custom' entry so users can
        type any model ID. Major Western SaaS providers (OpenAI, Anthropic,
        Google, xAI) omit it because they only serve their own models.
        """
        with_custom = ("deepseek", "glm", "qwen", "minimax", "modelscope",
                       "nvidia", "ollama", "agnes")
        for provider in with_custom:
            options = get_model_options(provider, "quick")
            values = [v for _, v in options]
            assert "custom" in values, f"{provider} quick lacks 'custom' sentinel"
