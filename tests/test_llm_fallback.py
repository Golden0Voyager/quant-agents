"""Unit tests for the LLM provider fallback chain."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradingagents.llm_clients.fallback import patch_invoke_with_fallback


class FakeLLM:
    """Minimal stand-in for a LangChain chat model."""

    def __init__(self, side_effect=None, return_value=None):
        self._mock = MagicMock(side_effect=side_effect, return_value=return_value)

    def invoke(self, input, config=None, **kwargs):
        return self._mock(input, config, **kwargs)


@pytest.mark.unit
class TestFallbackChain:
    """``patch_invoke_with_fallback`` routes through the chain on failures."""

    def test_primary_succeeds(self):
        primary = FakeLLM(return_value="primary")
        fallback = FakeLLM(return_value="fallback")
        patched = patch_invoke_with_fallback(primary, [fallback])
        assert patched.invoke("input") == "primary"
        fallback._mock.assert_not_called()

    def test_fallback_on_transient_error(self):
        primary = FakeLLM(side_effect=ValueError("rate limit"))
        fallback = FakeLLM(return_value="fallback")

        patched = patch_invoke_with_fallback(primary, [fallback])
        assert patched.invoke("input") == "fallback"
        primary._mock.assert_called_once()
        fallback._mock.assert_called_once()

    def test_fallback_on_null_choices_error(self):
        primary = FakeLLM(
            side_effect=ValueError(
                "Received response with null value for 'choices'. "
                "Full response keys: ['id', 'choices', 'created', 'model', 'object', 'service_tier', "
                "'system_fingerprint', 'usage']"
            )
        )
        fallback = FakeLLM(return_value="fallback")

        patched = patch_invoke_with_fallback(primary, [fallback])
        assert patched.invoke("input") == "fallback"
        primary._mock.assert_called_once()
        fallback._mock.assert_called_once()

    def test_non_transient_error_raises_immediately(self):
        primary = FakeLLM(side_effect=ValueError("bad request"))
        fallback = FakeLLM(return_value="fallback")

        patched = patch_invoke_with_fallback(primary, [fallback])
        with pytest.raises(ValueError, match="bad request"):
            patched.invoke("input")
        fallback._mock.assert_not_called()

    def test_exhausted_chain_raises_last_error(self):
        primary = FakeLLM(side_effect=ValueError("rate limit"))
        fallback = FakeLLM(side_effect=ValueError("quota exceeded"))

        patched = patch_invoke_with_fallback(primary, [fallback])
        with pytest.raises(ValueError, match="quota exceeded"):
            patched.invoke("input")
