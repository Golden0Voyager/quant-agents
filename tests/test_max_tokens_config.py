"""Tests for the configurable output-token cap (#1204 upstream parity).

``max_tokens`` is a cross-provider knob like temperature: when set it must
reach the underlying chat client under the provider-correct key
(``max_output_tokens`` for Gemini); when unset the provider keeps its own
default. Values are validated: booleans, non-integers and non-positives
raise ValueError instead of producing a bogus provider kwarg.
"""

import importlib

import pytest

from tradingagents.llm_clients.factory import create_llm_client


@pytest.mark.unit
class TestMaxTokensForwarding:
    @pytest.mark.parametrize(
        "provider,model,attr",
        [
            ("openai", "gpt-4.1", "max_tokens"),
            ("anthropic", "claude-sonnet-4-6", "max_tokens"),
            ("google", "gemini-2.5-flash", "max_output_tokens"),
            ("deepseek", "deepseek-chat", "max_tokens"),
        ],
    )
    def test_max_tokens_reaches_client_when_set(self, provider, model, attr):
        llm = create_llm_client(
            provider=provider, model=model, max_tokens=8192, api_key="placeholder"
        ).get_llm()
        assert getattr(llm, attr) == 8192

    def test_max_tokens_omitted_leaves_provider_default(self):
        llm = create_llm_client(
            provider="openai", model="gpt-4.1", api_key="placeholder"
        ).get_llm()
        assert llm.max_tokens is None


@pytest.mark.unit
class TestMaxTokensEnvOverlay:
    def test_env_sets_max_tokens(self, monkeypatch):
        import tradingagents.default_config as dc

        monkeypatch.setenv("TRADINGAGENTS_MAX_TOKENS", "4096")
        importlib.reload(dc)
        assert dc.DEFAULT_CONFIG["max_tokens"] == 4096
        monkeypatch.delenv("TRADINGAGENTS_MAX_TOKENS", raising=False)
        importlib.reload(dc)

    def test_default_max_tokens_is_none(self, monkeypatch):
        import tradingagents.default_config as dc

        monkeypatch.delenv("TRADINGAGENTS_MAX_TOKENS", raising=False)
        importlib.reload(dc)
        assert dc.DEFAULT_CONFIG["max_tokens"] is None


@pytest.mark.unit
class TestCoerceMaxTokens:
    def _coerce(self, value):
        from tradingagents.graph.trading_graph import _coerce_max_tokens

        return _coerce_max_tokens(value)

    @pytest.mark.parametrize("value,expected", [(1, 1), (8192, 8192), ("4096", 4096)])
    def test_accepts_positive_ints_and_numeric_strings(self, value, expected):
        assert self._coerce(value) == expected

    @pytest.mark.parametrize("bad", [0, -1, "0", "-5"])
    def test_rejects_non_positive(self, bad):
        with pytest.raises(ValueError, match="> 0"):
            self._coerce(bad)

    @pytest.mark.parametrize("bad", [True, False])
    def test_rejects_booleans(self, bad):
        with pytest.raises(ValueError, match="boolean"):
            self._coerce(bad)

    @pytest.mark.parametrize("bad", ["abc", "1.5"])
    def test_rejects_non_integers(self, bad):
        with pytest.raises(ValueError, match="integer"):
            self._coerce(bad)

    @pytest.mark.parametrize("empty", [None, ""])
    def test_empty_means_unset(self, empty):
        assert self._coerce(empty) is None


@pytest.mark.unit
class TestProviderKwargsMaxTokens:
    """_get_provider_kwargs coerces and forwards max_tokens, or omits it."""

    def _kwargs_for(self, max_tokens):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
        graph.config = {"llm_provider": "openai", "max_tokens": max_tokens}
        return TradingAgentsGraph._get_provider_kwargs(graph)

    def test_numeric_string_coerced(self):
        assert self._kwargs_for("8192")["max_tokens"] == 8192

    def test_int_passthrough(self):
        assert self._kwargs_for(4096)["max_tokens"] == 4096

    def test_none_omitted(self):
        assert "max_tokens" not in self._kwargs_for(None)

    def test_empty_string_omitted(self):
        assert "max_tokens" not in self._kwargs_for("")

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            self._kwargs_for(0)
